import contextlib
import subprocess
from importlib.util import find_spec
from os.path import exists
from typing import Iterable, Optional, Tuple

import httpx
from os import remove
from sys import executable
from asyncio import create_subprocess_shell, sleep
from asyncio.subprocess import PIPE

from pyrogram import filters
from pyrogram.errors import RPCError

from solgram.config import Config
from solgram import all_permissions, bot
from solgram.group_manager import enforce_permission
from solgram.single_utils import _status_sudo, get_sudo_list, Message, sqlite


def lang(text: str) -> str:
    """i18n"""
    return Config.lang_dict.get(text, Config.lang_default_dict.get(text, text))


def alias_command(command: str, disallow_alias: bool = False) -> str:
    """alias"""
    return command if disallow_alias else Config.alias_dict.get(command, command)


def normalize_command_name(command: str) -> str:
    """Normalize a permission-like input into a bare command name."""
    command = command.strip()
    if command.startswith("plugins_root."):
        return command.split(".", 1)[1]
    if command.startswith("plugins."):
        return command.split(".", 1)[1]
    return command


def check_command_scope(command: str) -> Optional[str]:
    """Return the plugin permission namespace for a registered top-level command."""
    command_name = normalize_command_name(command)
    if not command_name:
        return None
    plugin_permission = f"plugins.{command_name}"
    plugin_root_permission = f"plugins_root.{command_name}"
    for permission in all_permissions:
        if permission.name == plugin_permission:
            return "plugins"
        if permission.name == plugin_root_permission:
            return "plugins_root"
    return None


async def get_target_label(client, user_id: int) -> str:
    try:
        user = await client.get_users(user_id)
    except Exception:
        return str(user_id)
    if user.username:
        return f"@{user.username}"
    full_name = " ".join(
        value for value in [user.first_name, user.last_name] if value
    ).strip()
    return full_name or str(user_id)


async def resolve_target(
    client,
    message: Message,
    subcommand: Optional[str] = None,
    subcommands: Optional[Iterable[str]] = None,
    usage_key: str = "grant_usage",
) -> Tuple[Optional[int], bool, bool]:
    """Resolve a target user from reply, tg_id, or @username."""
    if message.reply_to_message:
        user = message.reply_to_message.from_user
        if not user:
            await message.edit(lang("grant_user_only"))
            return None, False, False
        return user.id, False, True

    args = message.parameter or []
    target_index = 1 if subcommand and subcommands and subcommand in subcommands else 0
    if target_index >= len(args):
        return None, False, True

    candidate = args[target_index]
    if candidate.isdigit():
        return int(candidate), True, True
    if candidate.startswith("@"):
        try:
            user = await client.get_users(candidate)
        except Exception:
            await message.edit(lang(usage_key))
            return None, True, False
        return user.id, True, True

    await message.edit(lang(usage_key))
    return None, True, False


async def attach_report(plaintext, file_name, reply_id=None, caption=None):
    """Attach plaintext as logs."""
    with open(file_name, "w+") as file:
        file.write(plaintext)
    try:
        await bot.send_document(
            "me",
            file_name,
            reply_to_message_id=reply_id,
            caption=caption,
        )
    except Exception:  # noqa
        return
    remove(file_name)


async def attach_log(plaintext, chat_id, file_name, reply_id=None, caption=None):
    """Attach plaintext as logs."""
    with open(file_name, "w+", encoding="utf-8") as file:
        file.write(plaintext)
    await bot.send_document(
        chat_id, file_name, reply_to_message_id=reply_id, caption=caption
    )
    remove(file_name)


async def upload_attachment(
    file_path, chat_id, reply_id, message_thread_id=None, caption=None, thumb=None
):
    """Uploads a local attachment file."""
    if not exists(file_path):
        return False
    try:
        await bot.send_document(
            chat_id,
            file_path,
            message_thread_id=message_thread_id,
            thumb=thumb,
            reply_to_message_id=reply_id,
            caption=caption,
        )
    except BaseException as exception:
        raise exception
    return True


async def execute(command, pass_error=True):
    """Executes command and returns output, with the option of enabling stderr."""
    executor = await create_subprocess_shell(
        command, stdout=PIPE, stderr=PIPE, stdin=PIPE
    )

    stdout, stderr = await executor.communicate()
    if pass_error:
        try:
            result = str(stdout.decode().strip()) + str(stderr.decode().strip())
        except UnicodeDecodeError:
            result = str(stdout.decode("gbk").strip()) + str(
                stderr.decode("gbk").strip()
            )
    else:
        try:
            result = str(stdout.decode().strip())
        except UnicodeDecodeError:
            result = str(stdout.decode("gbk").strip())
    return result


def pip_install(
    package: str, version: Optional[str] = "", alias: Optional[str] = ""
) -> bool:
    """Auto install extra pypi packages"""
    if not alias:
        # when import name is not provided, use package name
        alias = package
    if find_spec(alias) is None:
        subprocess.call([executable, "-m", "pip", "install", f"{package}{version}"])
        if find_spec(package) is None:
            return False
    return True


async def edit_delete(
    message: Message,
    text: str,
    time: int = 5,
    parse_mode: Optional["enums.ParseMode"] = None,
    disable_web_page_preview: bool = None,
):
    sudo_users = get_sudo_list()
    from_id = message.from_user.id if message.from_user else message.sender_chat.id
    if from_id in sudo_users:
        reply_to = message.reply_to_message
        event = (
            await reply_to.reply(
                text,
                disable_web_page_preview=disable_web_page_preview,
                parse_mode=parse_mode,
            )
            if reply_to
            else await message.reply(
                text,
                disable_web_page_preview=disable_web_page_preview,
                parse_mode=parse_mode,
            )
        )
    else:
        event = await message.edit(
            text,
            disable_web_page_preview=disable_web_page_preview,
            parse_mode=parse_mode,
        )
    await sleep(time)
    return await event.delete()


def get_permission_name(is_plugin: bool, need_admin: bool, command: str) -> str:
    """Get permission name."""
    if is_plugin:
        return f"plugins_root.{command}" if need_admin else f"plugins.{command}"
    else:
        return f"system.{command}" if need_admin else f"modules.{command}"


def sudo_filter(permission: str):
    async def if_sudo(flt, _, message: Message):
        if not _status_sudo():
            return False
        try:
            from_id = (
                message.from_user.id if message.from_user else message.sender_chat.id
            )
            sudo_list = get_sudo_list()
            if from_id not in sudo_list:
                if message.chat.id in sudo_list:
                    return enforce_permission(message.chat.id, flt.permission)
                return False
            return enforce_permission(from_id, flt.permission)
        except Exception:  # noqa
            return False

    return filters.create(if_sudo, permission=permission)


def sudo_user_filter():
    async def if_sudo(_, __, message: Message):
        if not _status_sudo():
            return False
        try:
            from_id = (
                message.from_user.id if message.from_user else message.sender_chat.id
            )
            sudo_list = get_sudo_list()
            return from_id in sudo_list or message.chat.id in sudo_list
        except Exception:  # noqa
            return False

    return filters.create(if_sudo)


def from_self(message: Message) -> bool:
    if message.outgoing:
        return True
    return message.from_user.is_self if message.from_user else False


def from_msg_get_sudo_uid(message: Message) -> int:
    """Get the sudo uid from the message."""
    from_id = message.from_user.id if message.from_user else message.sender_chat.id
    return from_id if from_id in get_sudo_list() else message.chat.id


def check_manage_subs(message: Message) -> bool:
    return from_self(message) or enforce_permission(
        from_msg_get_sudo_uid(message), "modules.manage_subs"
    )


async def process_exit(start: int, _client, message=None):
    data = sqlite.get("exit_msg", {})
    cid, mid = data.get("cid", 0), data.get("mid", 0)
    if start and data and cid and mid:
        with contextlib.suppress(Exception):
            msg: Message = await _client.get_messages(cid, mid)
            if msg:
                await msg.edit(
                    (
                        (msg.text or msg.caption)
                        if msg.from_user.is_self and (msg.text or msg.caption)
                        else ""
                    )
                    + f'\n\n> {lang("restart_complete")}'
                )
        del sqlite["exit_msg"]
    if message:
        sqlite["exit_msg"] = {"cid": message.chat.id, "mid": message.id}


def format_exc(e: BaseException) -> str:
    if isinstance(e, RPCError):
        return f"<code>API [{e.CODE} {e.ID or e.NAME}] — {e.MESSAGE.format(value=e.value)}</code>"
    return f"<code>{e.__class__.__name__}: {e}</code>"


""" Init httpx client """
# 使用自定义 UA
headers = {
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/102.0.5005.72 Safari/537.36"
}
client = httpx.AsyncClient(timeout=10.0, headers=headers)
