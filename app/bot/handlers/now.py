"""
/now — сахар сейчас: значение, тренд, возраст показания, прогноз +20 мин, режим.

Владелец: Dev 3.
"""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app import clock
from app.bot import storage, texts
from app.bot.data import fetch_readings
from app.bot.formatting import format_now
from app.forecast import predict
from app.ns.client import NSAuthError, NSError

router = Router(name="now")
HISTORY_COUNT = 36   # ≈ 3 часа при шаге 5 мин — с запасом для прогноза


@router.message(Command("now"))
async def now_cmd(message: Message) -> None:
    user = storage.get_user(message.from_user.id) if message.from_user else None
    if user is None or not user.ns_url:
        await message.answer(texts.NOW_NOT_CONNECTED)
        return
    try:
        readings = await fetch_readings(user, HISTORY_COUNT)
    except NSAuthError:
        await message.answer(texts.ERROR_NS_BAD_TOKEN)
        return
    except NSError:
        await message.answer(texts.ERROR_NS_UNAVAILABLE)
        return
    text = format_now(readings, predict(readings), storage.get_mode(user.telegram_id), clock.now())
    await message.answer(text)
