"""
Отправка уведомлений в Telegram.

Владелец: Dev 3.
pipeline вызывает notify(alert, chat_id). Объект Bot бот передаёт сюда один раз при старте:
setup(bot). Если setup не вызывали (тесты, `python -m app.pipeline`), уведомление
печатается в консоль, как в заглушке каркаса, — Telegram не нужен.

aiogram импортируется только при реальной отправке: pipeline и тесты Dev 1/2/4
работают, даже если aiogram не установлен.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.bot.formatting import format_alert
from app.models import Alert

if TYPE_CHECKING:
    from aiogram import Bot

_bot: Bot | None = None


def setup(bot: Bot | None) -> None:
    global _bot
    _bot = bot


async def notify(alert: Alert, chat_id: int) -> None:
    text = format_alert(alert)
    if _bot is None:
        _print(alert, chat_id, text)
        return

    from app.bot.keyboards import ack_keyboard

    markup = ack_keyboard(alert.id) if alert.needs_ack and alert.id is not None else None
    await _bot.send_message(chat_id, text, reply_markup=markup)


async def notify_trusted(alert: Alert, trusted_chat_id: int, minutes_without_ack: int) -> None:
    # ЗАГЛУШКА до Дня 3: нужны имя пользователя и, по TODO, тренд — их нет в сигнатуре.
    print(f"[notify_trusted → {trusted_chat_id}] {alert.kind} {alert.value}, "
          f"без ответа {minutes_without_ack} мин")


def _print(alert: Alert, chat_id: int, text: str) -> None:
    plain = re.sub(r"<[^>]+>", "", text).replace("\n", " / ")   # без HTML-тегов, в одну строку
    button = "  [Я в порядке]" if alert.needs_ack else ""
    print(f"[notify → {chat_id}] {plain} ({alert.created_at:%H:%M} UTC){button}")
