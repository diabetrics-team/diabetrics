"""
/graph — график сахара за 3 / 6 / 24 ч. Картинку рисует plot() от Dev 4 (app/plot.py).

Владелец: Dev 3.
Пока app/plot.py нет в develop, команда отвечает «График скоро будет».
"""
from __future__ import annotations

import asyncio
import inspect
from datetime import timedelta
from zoneinfo import ZoneInfo

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from app import clock
from app.bot import storage, texts
from app.bot.data import fetch_readings
from app.bot.keyboards import GRAPH_HOURS, GraphCb, graph_keyboard
from app.models import Reading
from app.ns.client import NSAuthError, NSError

router = Router(name="graph")
DEFAULT_HOURS = 3


def load_plot():
    """plot() от Dev 4 или None, если модуля ещё нет."""
    try:
        from app.plot import plot
    except ImportError:
        return None
    return plot


def render(plot, readings: list[Reading], hours: int, tz: str | None) -> bytes:
    """
    plot(readings, hours, tz) из app/plot.py (Dev 4) → BytesIO с PNG.
    tz он ждёт объектом часового пояса, а в БД — строка вроде "Europe/Moscow".
    """
    zone = ZoneInfo(tz or "Europe/Moscow")
    kwargs = {"tz": zone} if "tz" in inspect.signature(plot).parameters else {}
    image = plot(readings, hours, **kwargs)
    return image.getvalue() if hasattr(image, "getvalue") else image


async def send_graph(chat: Message, user_id: int, hours: int) -> None:
    plot = load_plot()
    if plot is None:
        await chat.answer(texts.GRAPH_NOT_READY)
        return
    user = storage.get_user(user_id)
    if user is None or not user.ns_url:
        await chat.answer(texts.NOW_NOT_CONNECTED)
        return
    try:
        readings = await fetch_readings(user, count=hours * 12 + 12)   # шаг 5 мин + запас
    except NSAuthError:
        await chat.answer(texts.ERROR_NS_BAD_TOKEN)
        return
    except NSError:
        await chat.answer(texts.ERROR_NS_UNAVAILABLE)
        return
    since = clock.now() - timedelta(hours=hours)
    readings = [r for r in readings if r.ts >= since]
    if not readings:
        await chat.answer(texts.GRAPH_EMPTY.format(hours=hours))
        return
    # matplotlib считает долго (по меркам бота) — в отдельном потоке, чтобы бот не замирал
    image = await asyncio.to_thread(render, plot, readings, hours, user.tz)
    await chat.answer_photo(
        BufferedInputFile(image, filename="graph.png"),
        caption=texts.GRAPH_CAPTION.format(hours=hours),
        reply_markup=graph_keyboard(),
    )


@router.message(Command("graph"))
async def graph_cmd(message: Message) -> None:
    if message.from_user is not None:
        await send_graph(message, message.from_user.id, DEFAULT_HOURS)


@router.callback_query(GraphCb.filter())
async def graph_period(callback: CallbackQuery, callback_data: GraphCb) -> None:
    await callback.answer()
    if callback_data.hours in GRAPH_HOURS and isinstance(callback.message, Message):
        await send_graph(callback.message, callback.from_user.id, callback_data.hours)
