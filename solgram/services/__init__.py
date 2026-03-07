from solgram import bot
from solgram import logs
from solgram.single_utils import sqlite
from solgram.scheduler import scheduler
from solgram.utils import client

__all__ = [
    "bot",
    "logs",
    "sqlite",
    "scheduler",
    "client",
]


def get(name: str):
    data = {
        "Client": bot,
        "Logger": logs,
        "SqliteDict": sqlite,
        "AsyncIOScheduler": scheduler,
        "AsyncClient": client,
    }
    return data.get(name)
