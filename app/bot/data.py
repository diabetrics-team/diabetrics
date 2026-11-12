"""
Показания из Nightscout пользователя — для /now и /graph.

Владелец: Dev 3. Клиент — app/ns/client.py (Dev 1), очистка — app/ns/clean.py.
"""
from __future__ import annotations

from app import clock
from app.bot.storage import BotUser
from app.models import Reading
from app.ns.clean import clean
from app.ns.client import NightscoutClient


async def fetch_readings(user: BotUser, count: int) -> list[Reading]:
    """
    Последние count записей, очищенные, от старых к новым. Бросает NSError (app/ns/client.py).
    Заодно синхронизирует часы: с CLOCK=nightscout «сейчас» берётся с сервера
    (fake-NS живёт в виртуальном времени), с CLOCK=system ничего не меняется.
    """
    async with NightscoutClient(user.ns_url, user.ns_token) as ns:
        server_ms = await ns.get_server_time()
        raw = await ns.get_entries(count=count)
    clock.sync_with_server(server_ms)
    return clean(raw)
