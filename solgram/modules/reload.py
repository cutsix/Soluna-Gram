from solgram import read_context
from solgram.common.reload import reload_all
from solgram.enums import Message
from solgram.listener import listener
from solgram.services import scheduler
from solgram.utils import lang


@listener(
    is_plugin=False, command="reload", need_admin=True, description=lang("reload_des")
)
async def reload_plugins(message: Message):
    """To reload plugins."""
    await reload_all()
    await message.edit(lang("reload_ok"))


@scheduler.scheduled_job("cron", hour="4", id="reload.clear_read_context")
async def clear_read_context_cron():
    read_context.clear()
