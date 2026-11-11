"""
/now — сахар сейчас: значение, тренд, возраст показания, прогноз +20 мин, режим.

Владелец: Dev 3.
"""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app import clock
from app.bot import nightscout, storage, texts
from app.bot.formatting import format_now
from app.forecast import predict
from app.ns.clean import clean

router = Router(name="now")


@router.message(Command("now"))
async def now_cmd(message: Message) -> None:
    user = storage.get_user(message.from_user.id) if message.from_user else None
    if user is None:
        await message.answer(texts.NOW_NOT_CONNECTED)
        return
    try:
        server_ms = await nightscout.get_server_time(user.ns_url, user.ns_token)
        raw = await nightscout.get_entries(user.ns_url, user.ns_token)
    except nightscout.NsBadToken:
        await message.answer(texts.ERROR_NS_BAD_TOKEN)
        return
    except nightscout.NsUnavailable:
        await message.answer(texts.ERROR_NS_UNAVAILABLE)
        return

    # С CLOCK=nightscout «сейчас» берётся с сервера (fake-NS живёт в виртуальном времени),
    # иначе «обновлено N мин назад» разойдётся с данными. С CLOCK=system ничего не меняется.
    clock.sync_with_server(server_ms)
    now = clock.now()
    readings = clean(raw)
    text = format_now(readings, predict(readings), storage.get_mode(user.telegram_id), now)
    await message.answer(text)
