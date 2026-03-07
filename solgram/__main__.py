import asyncio
import contextlib
from os import sep
from signal import signal as signal_fn, SIGINT, SIGTERM, SIGABRT
from sys import path, platform, exit

from pyrogram.errors import AuthKeyUnregistered

from solgram import bot, logs, working_dir
from solgram.common.reload import load_all
from solgram.single_utils import safe_remove
from solgram.utils import lang, process_exit

path.insert(1, f"{working_dir}{sep}plugins")


async def idle():
    task = None

    def signal_handler(_, __):
        if task:
            task.cancel()

    for s in (SIGINT, SIGTERM, SIGABRT):
        signal_fn(s, signal_handler)

    while True:
        task = asyncio.create_task(asyncio.sleep(600))
        try:
            await task
        except asyncio.CancelledError:
            break


async def console_bot():
    try:
        await bot.start()
    except AuthKeyUnregistered:
        safe_remove("solgram.session")
        exit()
    me = await bot.get_me()
    if me.is_bot:
        safe_remove("solgram.session")
        exit()
    logs.info(f"{lang('save_id')} {me.first_name}({me.id})")
    await load_all()
    await process_exit(start=True, _client=bot)


async def main():
    logs.info(lang("platform") + platform + lang("platform_load"))
    await console_bot()
    logs.info(lang("start"))

    try:
        await idle()
    finally:
        with contextlib.suppress(ConnectionError):
            await bot.stop()


if __name__ == "__main__":
    bot.loop.run_until_complete(main())
