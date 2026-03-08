"""Soluna-Gram module for simplified plugin permission grants."""

from typing import List, Optional, Tuple

from solgram import all_permissions
from solgram.dependence import get_sudo_list
from solgram.enums import Client, Message
from solgram.group_manager import (
    Permission,
    add_permission_for_user,
    permissions,
    remove_permission_for_user,
)
from solgram.listener import listener
from solgram.utils import lang

SUBCOMMANDS = {"revoke", "list", "reset"}


def get_subcommand(args: List[str]) -> Optional[str]:
    if args and args[0] in SUBCOMMANDS:
        return args[0]
    return None


def is_registered_plugin(plugin_name: str) -> bool:
    permission_name = f"plugins.{plugin_name}"
    return any(permission.name == permission_name for permission in all_permissions)


def get_plugin_permissions(user_id: int) -> List[Tuple[str, str, str]]:
    return [
        perm
        for perm in permissions.get_permissions_for_user(str(user_id))
        if perm[1].startswith("plugins.") and perm[2] == "access"
    ]


def extract_plugins(
    args: List[str], has_target: bool, subcommand: Optional[str]
) -> List[str]:
    offset = 1 if subcommand else 0
    if has_target:
        offset += 1
    plugins = []
    for name in args[offset:]:
        plugin_name = name.strip()
        if not plugin_name:
            continue
        if plugin_name.startswith("plugins."):
            plugin_name = plugin_name.split(".", 1)[1]
        if plugin_name not in plugins:
            plugins.append(plugin_name)
    return plugins


async def get_target_label(client: Client, user_id: int) -> str:
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
    client: Client, message: Message, subcommand: Optional[str]
) -> Tuple[Optional[int], bool, bool]:
    if message.reply_to_message:
        user = message.reply_to_message.from_user
        if not user:
            await message.edit(lang("grant_user_only"))
            return None, False, False
        return user.id, False, True

    args = message.parameter or []
    target_index = 1 if subcommand else 0
    if target_index >= len(args):
        return None, False, True

    candidate = args[target_index]
    if candidate.isdigit():
        return int(candidate), True, True
    if candidate.startswith("@"):
        try:
            user = await client.get_users(candidate)
        except Exception:
            await message.edit(lang("grant_usage"))
            return None, True, False
        return user.id, True, True
    await message.edit(lang("grant_usage"))
    return None, True, False


async def render_grant_overview(client: Client) -> str:
    sudo_users = get_sudo_list()
    if not sudo_users:
        return f"__{lang('sudo_no_one')}__"
    lines = [f"**{lang('grant_list_title')}**", ""]
    for user_id in sudo_users:
        label = await get_target_label(client, user_id)
        plugin_perms = [
            perm[1].split(".", 1)[1] for perm in get_plugin_permissions(user_id)
        ]
        lines.append(f"{label} ({user_id})")
        if plugin_perms:
            lines.append("  " + ", ".join(f"`{name}`" for name in plugin_perms))
        else:
            lines.append(f"  {lang('grant_no_plugins')}")
        lines.append("")
    return "\n".join(lines).rstrip()


async def render_user_permissions(client: Client, user_id: int) -> str:
    label = await get_target_label(client, user_id)
    plugin_perms = [
        perm[1].split(".", 1)[1] for perm in get_plugin_permissions(user_id)
    ]
    lines = [lang("grant_list_user").format(user=label)]
    if plugin_perms:
        lines.extend(f"  ✓ `{name}`" for name in plugin_perms)
    else:
        lines.append(f"  {lang('grant_no_plugins')}")
    return "\n".join(lines)


@listener(
    is_plugin=False,
    command="grant",
    need_admin=True,
    description=lang("grant_des"),
    parameters="[revoke|list|reset] [target] [plugins...]",
)
async def grant_command(client: Client, message: Message):
    args = message.parameter or []
    if not args:
        await message.edit(lang("grant_usage"))
        return

    subcommand = get_subcommand(args)
    target_id, has_target_arg, target_valid = await resolve_target(
        client, message, subcommand
    )
    if not target_valid:
        return

    if subcommand == "list":
        if target_id is None and not has_target_arg:
            await message.edit(await render_grant_overview(client))
            return
        if target_id is None or len(args) != (2 if has_target_arg else 1):
            await message.edit(lang("grant_usage"))
            return
        if target_id not in get_sudo_list():
            await message.edit(
                lang("grant_not_sudo").format(
                    user=await get_target_label(client, target_id)
                )
            )
            return
        await message.edit(await render_user_permissions(client, target_id))
        return

    if target_id is None:
        await message.edit(lang("grant_usage"))
        return

    target_label = await get_target_label(client, target_id)
    if target_id not in get_sudo_list():
        await message.edit(lang("grant_not_sudo").format(user=target_label))
        return

    if subcommand == "reset":
        if extract_plugins(args, has_target_arg, subcommand):
            await message.edit(lang("grant_usage"))
            return
        for perm in get_plugin_permissions(target_id):
            remove_permission_for_user(str(target_id), Permission(perm[1]))
        await message.edit(lang("grant_reset").format(user=target_label))
        return

    plugin_names = extract_plugins(args, has_target_arg, subcommand)
    if not plugin_names:
        await message.edit(lang("grant_usage"))
        return

    warnings = [
        lang("grant_plugin_warn").format(name=name)
        for name in plugin_names
        if not is_registered_plugin(name)
    ]
    for plugin_name in plugin_names:
        permission = Permission(f"plugins.{plugin_name}")
        if subcommand == "revoke":
            remove_permission_for_user(str(target_id), permission)
        else:
            add_permission_for_user(str(target_id), permission)

    if subcommand == "revoke":
        text = lang("grant_revoked").format(
            user=target_label, plugins=", ".join(plugin_names)
        )
    else:
        text = lang("grant_granted").format(
            user=target_label, plugins=", ".join(plugin_names)
        )
    if warnings:
        text += "\n\n" + "\n".join(warnings)
    await message.edit(text)
