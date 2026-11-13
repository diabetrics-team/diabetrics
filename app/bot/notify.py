"""
Отправка уведомлений в Telegram.

Владелец: Dev 3.
pipeline вызывает notify(alert, chat_id) и notify_trusted(alert, trusted_chat_id, minutes).
Обе возвращают message_id отправленного сообщения — pipeline сохраняет его (db.mark_sent),
чтобы потом погасить кнопку «Я в порядке». Без Telegram возвращают None.

Объект Bot передаётся сюда один раз при старте: setup(bot). Если setup не вызывали
(тесты, `python -m app.pipeline`), уведомление печатается в консоль — Telegram не нужен.
Имя, тренд и наличие доверенного лица берутся из БД, если она подключена (db.configure).

aiogram и SQLAlchemy импортируются только когда нужны: pipeline и тесты Dev 1/2/4
работают, даже если они не установлены (как в requirements-dev2-dev.txt).
"""
from __future__ import annotations

import re
from datetime import timedelta
from typing import TYPE_CHECKING

from app import clock
from app.bot import texts
from app.bot.formatting import format_alert, format_trusted_alert
from app.models import Alert

if TYPE_CHECKING:
    from aiogram import Bot

_bot: Bot | None = None
TREND_WINDOW = timedelta(minutes=30)   # тренд для доверенного лица — по показаниям за полчаса


def setup(bot: Bot | None) -> None:
    global _bot
    _bot = bot


def _db():
    """app.db, если БД подключена; иначе None (pipeline.replay, тесты без БД)."""
    try:
        from app import db
    except ImportError:          # SQLAlchemy не установлен
        return None
    return db if db.is_configured() else None


async def notify(alert: Alert, chat_id: int) -> int | None:
    text = format_alert(alert)
    repo = _db()
    if alert.needs_ack and repo is not None:
        user = repo.get_user_by_chat(chat_id)
        if user is not None and repo.trusted_chat_ids(user.id):
            text += texts.ALERT_ESCALATION_HINT.format(minutes=user.escalation_minutes)
    if _bot is None:
        _print(chat_id, text, alert.needs_ack, alert)
        return None

    from app.bot.keyboards import ack_keyboard

    markup = ack_keyboard(alert.id) if alert.needs_ack and alert.id is not None else None
    sent = await _bot.send_message(chat_id, text, reply_markup=markup)
    return sent.message_id


async def notify_trusted(alert: Alert, trusted_chat_id: int, minutes_without_ack: int) -> int | None:
    """Доверенному лицу: кто не отвечает, сахар, тренд, сколько минут нет ответа."""
    name = direction = None
    repo = _db()
    if alert.user_id is not None and repo is not None:
        owner = repo.get_user(alert.user_id)
        name = owner.name if owner else None
        recent = repo.readings_since(alert.user_id, clock.now() - TREND_WINDOW)
        direction = recent[-1].direction if recent else None
    text = format_trusted_alert(alert, name, direction, minutes_without_ack)
    if _bot is None:
        _print(trusted_chat_id, text, alert.id is not None, alert, prefix="notify_trusted")
        return None

    from app.bot.keyboards import ack_keyboard

    markup = ack_keyboard(alert.id, texts.BUTTON_TRUSTED_OK) if alert.id is not None else None
    sent = await _bot.send_message(trusted_chat_id, text, reply_markup=markup)
    return sent.message_id


def _print(chat_id: int, text: str, button: bool, alert: Alert, prefix: str = "notify") -> None:
    plain = re.sub(r"<[^>]+>", "", text).replace("\n", " / ")   # без HTML-тегов, в одну строку
    mark = "  [кнопка]" if button else ""
    print(f"[{prefix} → {chat_id}] {plain} ({alert.created_at:%H:%M} UTC){mark}")
