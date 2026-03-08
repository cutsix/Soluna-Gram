"""Soluna-Gram module for simplified plugin permission grants."""

from typing import List, Optional, Tuple

from solgram.dependence import get_sudo_list
from solgram.enums import Client, Message
from solgram.group_manager import (
    Permission,
    add_permission_for_user,
    permissions,
    remove_permission_for_user,
)
from solgram.listener import listener
from solgram.utils import (
    check_command_scope,
    get_target_label,
    lang,
    resolve_target,
)

GRANT_SUBCOMMANDS = {"revoke", "list", "reset"}


def get_subcommand(args: List[str]) -> Optional[str]:
    if args and args[0] in GRANT_SUBCOMMANDS:
        return args[0]
    return None


def get_plugin_permissions(user_id: int) -> List[Tuple[str, str, str]]:
    return [
        perm
        for perm in permissions.get_permissions_for_user(str(user_id))
        if perm[1].startswith("plugins.") and perm[2] == "access"
    ]


def extract_commands(
    args: List[str], has_target: bool, subcommand: Optional[str]
) -> List[str]:
    offset = 1 if subcommand else 0
    if has_target:
        offset += 1
    commands = []
    for name in args[offset:]:
        command_name = name.strip()
        if not command_name:
            continue
        if command_name.startswith(("plugins.", "plugins_root.")):
            command_name = command_name.split(".", 1)[1]
        if command_name not in commands:
            commands.append(command_name)
    return commands


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
    parameters="[revoke|list|reset] [target] [commands...]",
)
async def grant_command(client: Client, message: Message):
    args = message.parameter or []
    if not args:
        await message.edit(lang("grant_usage"))
        return

    subcommand = get_subcommand(args)
    target_id, has_target_arg, target_valid = await resolve_target(
        client, message, subcommand, GRANT_SUBCOMMANDS, "grant_usage"
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
        if extract_commands(args, has_target_arg, subcommand):
            await message.edit(lang("grant_usage"))
            return
        for perm in get_plugin_permissions(target_id):
            remove_permission_for_user(str(target_id), Permission(perm[1]))
        await message.edit(lang("grant_reset").format(user=target_label))
        return

    command_names = extract_commands(args, has_target_arg, subcommand)
    if not command_names:
        await message.edit(lang("grant_usage"))
        return

    warnings = []
    applicable_commands = []
    for command_name in command_names:
        scope = check_command_scope(command_name)
        if scope == "plugins":
            applicable_commands.append(command_name)
        elif scope == "plugins_root":
            warnings.append(lang("grant_plugin_root_warn").format(name=command_name))
        else:
            warnings.append(lang("grant_cmd_not_found").format(name=command_name))
            applicable_commands.append(command_name)

    for command_name in applicable_commands:
        permission = Permission(f"plugins.{command_name}")
        if subcommand == "revoke":
            remove_permission_for_user(str(target_id), permission)
        else:
            add_permission_for_user(str(target_id), permission)

    if subcommand == "revoke" and applicable_commands:
        text = lang("grant_revoked").format(
            user=target_label, plugins=", ".join(applicable_commands)
        )
    elif applicable_commands:
        text = lang("grant_granted").format(
            user=target_label, plugins=", ".join(applicable_commands)
        )
    else:
        text = ""
    if warnings:
        warning_text = "\n".join(warnings)
        text = f"{text}\n\n{warning_text}".strip()
    await message.edit(text)
