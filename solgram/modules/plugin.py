"""Soluna-Gram module to manage plugins."""

import ast
import contextlib
from html import escape
from os import remove, path, sep
from os.path import exists
from re import I, search
from shutil import copyfile, move
from typing import Dict, List, Optional

from solgram import log, logs, working_dir
from solgram.common.plugin import plugin_remote_manager, plugin_manager
from solgram.common.reload import reload_all
from solgram.enums import Message
from solgram.listener import listener
from solgram.utils import lang, upload_attachment


def remove_plugin(name):
    plugin_directory = f"{working_dir}{sep}plugins{sep}"
    with contextlib.suppress(FileNotFoundError):
        remove(f"{plugin_directory}{name}.py")
    with contextlib.suppress(FileNotFoundError):
        remove(f"{plugin_directory}{name}.py.disabled")


def move_plugin(file_path):
    name = path.basename(file_path)[:-3]
    plugin_directory = f"{working_dir}{sep}plugins{sep}"
    remove_plugin(name)
    move(file_path, plugin_directory)


def build_install_summary(
    success_list: List[str], failed_list: List[str], no_need_list: List[str]
) -> str:
    text = f"<b>{lang('apt_name')}</b>\n\n"
    if success_list:
        text += lang("apt_install_success") + " : %s\n" % ", ".join(success_list)
    if failed_list:
        text += lang("apt_not_found") + " %s\n" % ", ".join(failed_list)
    if no_need_list:
        text += lang("apt_no_update") + " %s\n" % ", ".join(no_need_list)
    return text.rstrip()


def format_private_repo_error(error_detail: Dict[str, str]) -> str:
    error_type = error_detail.get("error", "auth_error")
    repo_name = error_detail.get("repo_name", lang("apt_private_repo_unknown"))
    contact = error_detail.get("contact", lang("apt_private_repo_contact"))
    if error_type == "token_expired":
        return lang("apt_private_repo_token_expired").format(
            repo_name=repo_name, contact=contact
        )
    if error_type == "forbidden":
        return lang("apt_private_repo_forbidden").format(repo_name=repo_name)
    if error_type == "invalid_token":
        return lang("apt_private_repo_invalid_token").format(repo_name=repo_name)
    return lang("apt_private_repo_auth_error").format(repo_name=repo_name)


def format_auth_error_messages(urls: Optional[List[str]] = None) -> str:
    messages = [
        format_private_repo_error(error)
        for error in plugin_manager.get_auth_errors(urls=urls)
    ]
    if not messages:
        return ""
    return f"{lang('apt_private_repo_errors')}\n" + "\n".join(messages)


def append_auth_error_messages(text: str, urls: Optional[List[str]] = None) -> str:
    auth_text = format_auth_error_messages(urls=urls)
    if not auth_text:
        return text
    if not text:
        return auth_text
    return f"{text}\n\n{auth_text}"


def format_remote_plugins_list() -> str:
    text = (
        f"**{lang('apt_repo_list')}**\n\n"
        + "\n\n".join(
            f"`{plugin.name}` / `{plugin.version}`\n  {plugin.des_short}"
            for plugin in plugin_manager.remote_plugins
        )
    )
    return append_auth_error_messages(text)


def copy_local_plugin_file(plugin_name: str) -> Optional[str]:
    plugin_directory = f"{working_dir}{sep}plugins{sep}"
    file_name = f"{plugin_name}.py"
    source_path = f"{plugin_directory}{file_name}"
    disabled_source_path = f"{source_path}.disabled"
    if exists(source_path):
        copyfile(source_path, file_name)
        return file_name
    if exists(disabled_source_path):
        copyfile(disabled_source_path, file_name)
        return file_name
    return None


def extract_license(file_path: str) -> Optional[str]:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read())
    except (FileNotFoundError, OSError, SyntaxError, UnicodeDecodeError) as exc:
        logs.warning(f"读取插件声明失败: {exc}")
        return None
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (
                isinstance(target, ast.Name)
                and target.id == "__license__"
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                return node.value.value
    return None


def build_uploadpriv_caption(plugin_name: str, license_text: Optional[str]) -> str:
    notice = escape(license_text) if license_text else lang("apt_uploadpriv_default")
    return (
        f"<b>{lang('apt_name')}</b>\n\n"
        f"Soluna-Gram {escape(plugin_name)} plugin.\n\n"
        f"{notice}"
    )


async def send_plugin_file(
    message: Message, plugin_name: str, caption: str, uploading_key: str = "apt_uploading"
) -> bool:
    reply = message.reply_to_message
    reply_id = reply.id if reply else None
    file_name = copy_local_plugin_file(plugin_name)
    if not file_name:
        await message.edit(lang("apt_not_exist"))
        return False
    try:
        await message.edit(lang(uploading_key))
        await upload_attachment(
            file_name,
            message.chat.id,
            reply_id,
            message_thread_id=message.message_thread_id,
            thumb=f"solgram{sep}assets{sep}logo.jpg",
            caption=caption,
        )
        await message.safe_delete()
        return True
    finally:
        with contextlib.suppress(FileNotFoundError):
            remove(file_name)


async def install_remote_plugins(message: Message, plugin_names: List[str]) -> bool:
    await plugin_manager.load_remote_plugins()
    plugin_manager.load_local_plugins()
    message = await message.edit(lang("apt_processing"))
    success_list = []
    failed_list = []
    no_need_list = []
    for name in plugin_names:
        remote_plugin = plugin_manager.get_remote_plugin(name)
        if remote_plugin is None:
            failed_list.append(name)
            continue
        local_plugin = plugin_manager.get_local_plugin(name)
        source_info = plugin_manager.get_local_plugin_source(name)
        if local_plugin and source_info and not plugin_manager.plugin_need_update(name):
            no_need_list.append(name)
            continue
        if await plugin_manager.install_remote_plugin(name):
            success_list.append(name)
        else:
            failed_list.append(name)
    text = build_install_summary(success_list, failed_list, no_need_list)
    text = append_auth_error_messages(text)
    await log(text)
    await message.edit(text)
    return len(success_list) > 0


@listener(
    is_plugin=False,
    outgoing=True,
    command="apt",
    need_admin=True,
    diagnostics=False,
    description=lang("apt_des"),
    parameters=lang("apt_parameters"),
)
async def plugin(message: Message):
    args = message.parameter or []
    if not args:
        await message.edit(lang("arg_error"))
        return

    plugin_directory = f"{working_dir}{sep}plugins{sep}"
    subcommand = args[0]

    if subcommand == "install":
        if len(args) == 1:
            file_path = await plugin_manager.download_from_message(message)
            if file_path is None:
                await message.edit(lang("apt_no_py"))
                return
            plugin_name = path.basename(file_path)[:-3]
            plugin_manager.remove_plugin(plugin_name)
            move_plugin(file_path)
            await message.edit(
                f"<b>{lang('apt_name')}</b>\n\n"
                f"{lang('apt_plugin')} {plugin_name} {lang('apt_installed')}"
            )
            await log(f"{lang('apt_install_success')} {plugin_name}.")
            await reload_all()
            return
        if len(args) >= 2:
            if await install_remote_plugins(message, args[1:]):
                await reload_all()
            return
        await message.edit(lang("arg_error"))
        return

    if subcommand == "installs":
        if len(args) != 1:
            await message.edit(lang("arg_error"))
            return
        await plugin_manager.load_remote_plugins()
        if not plugin_manager.remote_plugins:
            await message.edit(append_auth_error_messages(lang("apt_repo_empty")))
            return
        if await install_remote_plugins(
            message, [plugin.name for plugin in plugin_manager.remote_plugins]
        ):
            await reload_all()
        return

    if subcommand == "list":
        if len(args) != 1:
            await message.edit(lang("arg_error"))
            return
        await plugin_manager.load_remote_plugins()
        if not plugin_manager.remote_plugins:
            await message.edit(append_auth_error_messages(lang("apt_repo_empty")))
            return
        await message.edit(format_remote_plugins_list())
        return

    if subcommand == "remove":
        if len(args) != 2:
            await message.edit(lang("arg_error"))
            return
        plugin_name = args[1]
        if "/" in plugin_name:
            await message.edit(lang("arg_error"))
            return
        if plugin_manager.remove_plugin(plugin_name):
            await message.edit(f"{lang('apt_remove_success')} {plugin_name}")
            await log(f"{lang('apt_remove')} {plugin_name}.")
            await reload_all()
        else:
            await message.edit(lang("apt_not_exist"))
        return

    if subcommand == "status":
        if len(args) != 1:
            await message.edit(lang("arg_error"))
            return
        plugin_manager.load_local_plugins()
        active_plugins, disabled_plugins, inactive_plugins = plugin_manager.get_plugins_status()
        active_plugins_string = ", ".join(active_plugins)
        inactive_plugins_string = ", ".join(i.name for i in inactive_plugins)
        disabled_plugins_string = ", ".join(i.name for i in disabled_plugins)
        if not active_plugins:
            active_plugins_string = f"`{lang('apt_no_running_plugins')}`"
        if not inactive_plugins:
            inactive_plugins_string = f"`{lang('apt_no_load_failed_plugins')}`"
        if not disabled_plugins:
            disabled_plugins_string = f"`{lang('apt_no_disabled_plugins')}`"
        output = (
            f"**{lang('apt_plugin_list')}**\n"
            f"{lang('apt_plugin_running')}: {active_plugins_string}\n"
            f"{lang('apt_plugin_disabled')}: {disabled_plugins_string}\n"
            f"{lang('apt_plugin_failed')}: {inactive_plugins_string}"
        )
        await message.edit(output)
        return

    if subcommand == "enable":
        if len(args) != 2:
            await message.edit(lang("arg_error"))
            return
        if plugin_manager.enable_plugin(args[1]):
            await message.edit(
                f"{lang('apt_plugin')} {args[1]} {lang('apt_enable')}"
            )
            await log(f"{lang('apt_enable')} {args[1]}.")
            await reload_all()
        else:
            await message.edit(lang("apt_not_exist"))
        return

    if subcommand == "disable":
        if len(args) != 2:
            await message.edit(lang("arg_error"))
            return
        if plugin_manager.disable_plugin(args[1]):
            await message.edit(
                f"{lang('apt_plugin')} {args[1]} {lang('apt_disable')}"
            )
            await log(f"{lang('apt_disable')} {args[1]}.")
            await reload_all()
        else:
            await message.edit(lang("apt_not_exist"))
        return

    if subcommand == "upload":
        if len(args) != 2:
            await message.edit(lang("arg_error"))
            return
        await send_plugin_file(
            message,
            args[1],
            f"<b>{lang('apt_name')}</b>\n\nSoluna-Gram {escape(args[1])} plugin.",
        )
        return

    if subcommand == "uploadpriv":
        if len(args) != 2:
            await message.edit(lang("arg_error"))
            return
        plugin_manager.load_local_version_map()
        plugin_name = args[1]
        source_info = plugin_manager.get_local_plugin_source(plugin_name)
        if source_info is None or source_info.get("auth_type") != "private":
            await message.edit(lang("apt_uploadpriv_not_private"))
            return
        file_name = copy_local_plugin_file(plugin_name)
        if not file_name:
            await message.edit(lang("apt_not_exist"))
            return
        license_text = extract_license(file_name)
        caption = build_uploadpriv_caption(plugin_name, license_text)
        try:
            await message.edit(lang("apt_uploading"))
            reply = message.reply_to_message
            await upload_attachment(
                file_name,
                message.chat.id,
                reply.id if reply else None,
                message_thread_id=message.message_thread_id,
                thumb=f"solgram{sep}assets{sep}logo.jpg",
                caption=caption,
            )
            await message.safe_delete()
        finally:
            with contextlib.suppress(FileNotFoundError):
                remove(file_name)
        return

    if subcommand == "update":
        if not exists(f"{plugin_directory}version.json"):
            await message.edit(lang("apt_why_not_install_a_plugin"))
            return
        plugin_manager.load_local_plugins()
        await plugin_manager.load_remote_plugins()
        updated_plugins = [
            plugin.name for plugin in await plugin_manager.update_all_remote_plugin()
        ]
        if not updated_plugins:
            text = (
                f"<b>{lang('apt_name')}</b>\n\n"
                + lang("apt_loading_from_online_but_nothing_need_to_update")
            )
            await message.edit(append_auth_error_messages(text))
        else:
            message = await message.edit(lang("apt_loading_from_online_and_updating"))
            text = (
                f"<b>{lang('apt_name')}</b>\n\n"
                + lang("apt_reading_list")
                + "\n"
                + "、".join(updated_plugins)
            )
            await message.edit(append_auth_error_messages(text))
            await reload_all()
        return

    if subcommand == "search":
        if len(args) == 1:
            await message.edit(lang("apt_search_no_name"))
            return
        if len(args) != 2:
            await message.edit(lang("arg_error"))
            return
        await plugin_manager.load_remote_plugins()
        plugin_name = args[1]
        search_result = []
        for plugin in plugin_manager.remote_plugins:
            if search(plugin_name, plugin.name, I):
                search_result.append(
                    f"`{plugin.name}` / `{plugin.version}`\n  {plugin.des_short}"
                )
        if not search_result:
            await message.edit(lang("apt_search_not_found"))
        else:
            await message.edit(
                f"{lang('apt_search_result_hint')}:\n\n" + "\n\n".join(search_result)
            )
        return

    if subcommand == "show":
        if len(args) == 1:
            await message.edit(lang("apt_search_no_name"))
            return
        if len(args) != 2:
            await message.edit(lang("arg_error"))
            return
        await plugin_manager.load_remote_plugins()
        plugin_name = args[1]
        search_result = ""
        for plugin in plugin_manager.remote_plugins:
            if plugin_name != plugin.name:
                continue
            search_support = (
                lang("apt_search_supporting")
                if plugin.supported
                else lang("apt_search_not_supporting")
            )
            search_result = (
                f"{lang('apt_plugin_name')}:`{plugin.name}`\n"
                f"{lang('apt_plugin_ver')}:`Ver  {plugin.version}`\n"
                f"{lang('apt_plugin_section')}:`{plugin.section}`\n"
                f"{lang('apt_plugin_maintainer')}:`{plugin.maintainer}`\n"
                f"{lang('apt_plugin_size')}:`{plugin.size}`\n"
                f"{lang('apt_plugin_support')}:{search_support}\n"
                f"{lang('apt_plugin_des_short')}:{plugin.des_short}"
            )
            break
        await message.edit(search_result or lang("apt_search_not_found"))
        return

    if subcommand == "export":
        if not exists(f"{plugin_directory}version.json"):
            await message.edit(lang("apt_why_not_install_a_plugin"))
            return
        plugin_manager.load_local_version_map()
        message = await message.edit(lang("stats_loading"))
        list_plugin = []
        for key, value in plugin_manager.version_map.items():
            version = value.get("version") if isinstance(value, dict) else value
            if not version:
                continue
            list_plugin.append(key)
        if not list_plugin:
            await message.edit(lang("apt_why_not_install_a_plugin"))
        else:
            await message.edit(",apt install " + " ".join(list_plugin))
        return

    await message.edit(lang("arg_error"))


@listener(
    is_plugin=False,
    outgoing=True,
    command="apt_source",
    need_admin=True,
    description=lang("apt_source_des"),
    parameters=lang("apt_source_parameters"),
)
async def apt_source(message: Message):
    args = message.parameter or []
    if not args:
        remotes = plugin_remote_manager.get_remotes()
        if not remotes:
            await message.edit(lang("apt_source_not_found"))
            return
        await message.edit(
            f"{lang('apt_source_header')}\n\n" + "\n".join(remote.text for remote in remotes),
            disable_web_page_preview=True,
        )
        return

    subcommand = args[0]
    if subcommand == "add":
        if len(args) >= 2 and args[1] == "private":
            if len(args) != 5 or args[3] != "--token":
                await message.edit(lang("arg_error"))
                return
            url = args[2]
            if not url.endswith("/"):
                url += "/"
            token = args[4]
            plugin_remote_manager.add_private_remote(url, token)
            await plugin_manager.load_remote_plugins(enable_cache=False)
            text = append_auth_error_messages(
                lang("apt_source_add_private_success"), urls=[url]
            )
            await message.edit(text)
            return
        if len(args) != 2:
            await message.edit(lang("arg_error"))
            return
        url = args[1]
        if not url.endswith("/"):
            url += "/"
        try:
            status = await plugin_manager.fetch_remote_url(url)
        except Exception:
            status = False
        if status:
            if plugin_remote_manager.add_remote(url):
                await message.edit(lang("apt_source_add_success"))
                await plugin_manager.load_remote_plugins(enable_cache=False)
            else:
                await message.edit(lang("apt_source_add_failed"))
        else:
            await message.edit(lang("apt_source_add_invalid"))
        return

    if subcommand == "del":
        if len(args) != 2:
            await message.edit(lang("arg_error"))
            return
        url = args[1]
        if not url.endswith("/"):
            url += "/"
        if plugin_remote_manager.remove_remote(url):
            await message.edit(lang("apt_source_del_success"))
            await plugin_manager.load_remote_plugins(enable_cache=False)
        else:
            await message.edit(lang("apt_source_del_failed"))
        return

    await message.edit(lang("arg_error"))
