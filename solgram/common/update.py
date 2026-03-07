from sys import executable

from solgram.utils import execute


async def update(force: bool = False):
    await execute("git fetch origin release")
    if force:
        await execute("git reset --hard origin/release")
    else:
        await execute("git pull origin release")
    await execute(f"{executable} -m pip install --upgrade -r requirements.txt")
    await execute(f"{executable} -m pip install -r requirements.txt")
