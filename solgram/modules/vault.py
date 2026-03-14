"""Soluna-Gram module to manage private plugin vault access."""

import asyncio
import contextlib
from html import escape
from typing import Any, Dict, List, Optional, Tuple

from pyrogram.enums import ParseMode
from pyrogram.errors import (
    ChatWriteForbidden,
    PeerIdInvalid,
    PrivacyPremiumRequired,
    UserNotMutualContact,
    UserPrivacyRestricted,
)

from solgram import logs
from solgram.config import Config
from solgram.enums import Client, Message
from solgram.listener import listener
from solgram.utils import (
    client as http_client,
    from_self,
    get_target_label,
    lang,
)

def get_vault_urls() -> Optional[Dict[str, str]]:
    if not Config.VAULT_URL or not Config.VAULT_ADMIN_KEY:
        return None
    vault_url = Config.VAULT_URL.rstrip("/")
    return {
        "vault_url": vault_url,
        "admin_url": f"{vault_url}/admin",
        "api_url": f"{vault_url}/api/",
    }


def extract_response_data(response) -> Any:
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError:
        return {}


def build_request_error(response) -> str:
    data = extract_response_data(response)
    detail = ""
    if isinstance(data, dict):
        detail = str(data.get("detail") or data.get("error") or "").strip()
    if not detail:
        detail = response.text.strip() or f"HTTP {response.status_code}"
    return f"{response.status_code}: {detail}"


async def vault_request(method: str, url: str, payload: Optional[Dict[str, Any]] = None) -> Any:
    response = await http_client.request(
        method,
        url,
        headers={"X-Admin-Key": Config.VAULT_ADMIN_KEY},
        json=payload,
    )
    if response.is_error:
        raise RuntimeError(build_request_error(response))
    return extract_response_data(response)


async def auto_delete_message(message: Message) -> None:
    await asyncio.sleep(120)
    with contextlib.suppress(Exception):
        await message.delete()


def parse_expire_days(args: List[str], has_target_arg: bool) -> Optional[int]:
    expire_args = args[2 if has_target_arg else 1 :]
    if not expire_args:
        return 30
    if len(expire_args) != 1 or not expire_args[0].isdigit():
        return None
    return int(expire_args[0])


def get_user_list(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        users = data.get("users") or data.get("data") or []
        if isinstance(users, list):
            return [item for item in users if isinstance(item, dict)]
    return []


def format_expire_text(value: Any) -> str:
    if value in (None, "", 0, "0"):
        return lang("vault_expire_forever")
    return str(value)


async def get_vault_contact(urls: Dict[str, str]) -> str:
    try:
        data = await vault_request("GET", f"{urls['admin_url']}/info")
    except Exception:
        return urls["vault_url"]
    if isinstance(data, dict):
        contact = str(data.get("contact") or "").strip()
        if contact:
            return contact
    return urls["vault_url"]


async def resolve_vault_target(
    client: Client, message: Message
) -> Tuple[Optional[int], bool, bool]:
    if message.reply_to_message:
        user = message.reply_to_message.from_user
        if not user:
            await message.edit(lang("vault_usage"))
            return None, False, False
        return user.id, False, True

    args = message.parameter or []
    if len(args) < 2:
        return None, False, True

    candidate = args[1].strip()
    if candidate.isdigit():
        return int(candidate), True, True

    if candidate.startswith("@"):
        try:
            user = await client.get_users(candidate)
        except Exception:
            await message.edit(lang("vault_usage"))
            return None, True, False
        return user.id, True, True

    await message.edit(lang("vault_usage"))
    return None, True, False


@listener(
    is_plugin=False,
    outgoing=True,
    command="vault",
    need_admin=True,
    description=lang("vault_des"),
    parameters=lang("vault_parameters"),
)
async def vault_command(client: Client, message: Message):
    if not from_self(message):
        with contextlib.suppress(Exception):
            await message.reply(lang("vault_owner_only"))
        return

    args = message.parameter or []
    if not args:
        await message.edit(lang("vault_usage"))
        return

    urls = get_vault_urls()
    if urls is None:
        await message.edit(lang("vault_config_missing"))
        return

    subcommand = args[0]
    if subcommand == "auth":
        target_id, has_target_arg, target_valid = await resolve_vault_target(
            client, message
        )
        if not target_valid:
            return
        if target_id is None:
            await message.edit(lang("vault_usage"))
            return
        expire_days = parse_expire_days(args, has_target_arg)
        if expire_days is None:
            await message.edit(lang("vault_usage"))
            return
        try:
            data = await vault_request(
                "POST",
                f"{urls['admin_url']}/authorize",
                {"tgid": target_id, "expire_days": expire_days},
            )
        except Exception as exc:
            await message.edit(lang("vault_request_failed").format(error=exc))
            return
        token = ""
        if isinstance(data, dict):
            token = str(data.get("token") or data.get("jwt_token") or "")
        if not token:
            await message.edit(lang("vault_response_invalid"))
            return
        expire_text = (
            lang("vault_expire_forever")
            if expire_days == 0
            else lang("vault_expire_days").format(days=expire_days)
        )
        text = (
            f"🔐 <b>{lang('vault_token_title')}</b>\n\n"
            f"{lang('vault_repo_label')}<code>{escape(urls['api_url'])}</code>\n\n"
            f"{lang('vault_token_label')}<code>{escape(token)}</code>\n\n"
            f"{lang('vault_expire_label')}{escape(expire_text)}\n\n"
            f"{lang('vault_command_label')}\n\n"
            f"<code>,apt_source add private {escape(urls['api_url'])} --token {escape(token)}</code>\n\n"
            f"{lang('vault_auto_delete_notice')}"
        )
        try:
            sent_message = await client.send_message(
                target_id, text, parse_mode=ParseMode.HTML
            )
        except (
            ChatWriteForbidden,
            PeerIdInvalid,
            PrivacyPremiumRequired,
            UserNotMutualContact,
            UserPrivacyRestricted,
        ) as exc:
            logs.warning(f"vault auth 因私聊限制发送 Token 失败: {exc}")
            contact = await get_vault_contact(urls)
            await message.edit(
                lang("vault_auth_contact_required").format(contact=contact)
            )
            return
        except Exception as exc:
            logs.warning(f"vault auth 发送 Token 失败: {exc}")
            await message.edit(lang("vault_auth_send_failed"))
            return
        asyncio.create_task(auto_delete_message(sent_message))
        await message.edit(
            lang("vault_auth_success").format(
                user=await get_target_label(client, target_id)
            )
        )
        return

    if subcommand in {"ban", "remove"}:
        target_id, has_target_arg, target_valid = await resolve_vault_target(
            client, message
        )
        if not target_valid:
            return
        expected_args = 2 if has_target_arg else 1
        if target_id is None or len(args) != expected_args:
            await message.edit(lang("vault_usage"))
            return
        endpoint = "ban" if subcommand == "ban" else "user"
        method = "POST" if subcommand == "ban" else "DELETE"
        try:
            await vault_request(
                method, f"{urls['admin_url']}/{endpoint}", {"tgid": target_id}
            )
        except Exception as exc:
            await message.edit(lang("vault_request_failed").format(error=exc))
            return
        label = await get_target_label(client, target_id)
        key = "vault_ban_success" if subcommand == "ban" else "vault_remove_success"
        await message.edit(lang(key).format(user=label))
        return

    if subcommand == "list":
        if len(args) != 1:
            await message.edit(lang("vault_usage"))
            return
        try:
            data = await vault_request("GET", f"{urls['admin_url']}/users")
        except Exception as exc:
            await message.edit(lang("vault_request_failed").format(error=exc))
            return
        users = get_user_list(data)
        if not users:
            await message.edit(lang("vault_list_empty"))
            return
        lines = [f"**{lang('vault_list_title')}**", ""]
        for user in users:
            tgid = user.get("tgid") or user.get("user_id") or user.get("id") or "?"
            status = user.get("status", "active")
            expire_at = format_expire_text(user.get("expire_at"))
            status_text = (
                lang("vault_status_banned")
                if status == "banned"
                else lang("vault_status_active")
            )
            lines.append(f"`{tgid}` | {status_text}")
            lines.append(f"{lang('vault_expire_label_plain')}{expire_at}")
            lines.append("")
        await message.edit("\n".join(lines).rstrip())
        return

    if subcommand == "info":
        if len(args) != 1:
            await message.edit(lang("vault_usage"))
            return
        try:
            data = await vault_request("GET", f"{urls['admin_url']}/info")
        except Exception as exc:
            await message.edit(lang("vault_request_failed").format(error=exc))
            return
        if not isinstance(data, dict):
            data = {}
        repo_name = data.get("repo_name") or data.get("name") or "-"
        plugin_count = data.get("plugin_count", data.get("plugins", 0))
        user_count = data.get("user_count", data.get("authorized_users", 0))
        if isinstance(plugin_count, list):
            plugin_count = len(plugin_count)
        if isinstance(user_count, list):
            user_count = len(user_count)
        contact = data.get("contact", "-")
        text = (
            f"**{lang('vault_info_title')}**\n\n"
            f"{lang('vault_info_repo_name')}`{repo_name}`\n"
            f"{lang('vault_info_plugin_count')}`{plugin_count}`\n"
            f"{lang('vault_info_user_count')}`{user_count}`\n"
            f"{lang('vault_info_contact')} {contact}\n"
            f"{lang('vault_info_api_url')}`{urls['api_url']}`"
        )
        await message.edit(text)
        return

    await message.edit(lang("vault_usage"))
