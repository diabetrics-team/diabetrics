"""
Отправка уведомлений в Telegram.

Владелец: Dev 3.
pipeline вызывает notify(alert, chat_id) и notify_trusted(alert, trusted_chat_id, minutes).
Обе возвращают message_id отправленного сообщения — pipeline сохраняет его (db.mark_sent),
чтобы потом погасить кнопку «Я в порядке». Без Telegram или если не доставлено — None.

Объект Bot передаётся сюда один раз при старте: setup(bot). Если setup не вызывали
(тесты, `python -m app.pipeline`), уведомление печатается в консоль — Telegram не нужен.
Имя, часовой пояс, тренд и доверенные лица берутся из БД, если она подключена (db.configure).

Если пользователь заблокировал бота, он помечается неактивным (db.set_active) — pipeline
перестаёт его опрашивать, пока он снова не напишет /start. Если бота заблокировало
доверенное лицо — об этом сообщается пользователю.

aiogram и SQLAlchemy импортируются только когда нужны: pipeline и тесты Dev 1/2/4
работают, даже если они не установлены (как в requirements-dev2-dev.txt).
"""
from __future__ import annotations

import logging
import re
from datetime import timedelta
from html import escape
from typing import TYPE_CHECKING

from app import clock
from app.bot import texts
from app.bot.formatting import format_alert, format_trusted_alert
from app.models import Alert

if TYPE_CHECKING:
    from aiogram import Bot

log = logging.getLogger(__name__)
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
    repo = _db()
    user = repo.get_user_by_chat(chat_id) if repo is not None else None
    text = format_alert(alert, user.tz if user is not None else None)
    if alert.needs_ack and user is not None and repo.trusted_chat_ids(user.id):
        text += texts.ALERT_ESCALATION_HINT.format(minutes=user.escalation_minutes)
    if _bot is None:
        _print(chat_id, text, alert.needs_ack)
        return None

    from aiogram.exceptions import TelegramForbiddenError

    from app.bot.keyboards import ack_keyboard

    markup = ack_keyboard(alert.id) if alert.needs_ack and alert.id is not None else None
    try:
        sent = await _bot.send_message(chat_id, text, reply_markup=markup)
    except TelegramForbiddenError:
        log.warning("notify: чат %s заблокировал бота — больше не опрашиваем", chat_id)
        if user is not None:
            repo.set_active(user.id, False)
        return None
    return sent.message_id


async def notify_trusted(alert: Alert, trusted_chat_id: int, minutes_without_ack: int) -> int | None:
    """Доверенному лицу: кто не отвечает, сахар, тренд, сколько минут нет ответа."""
    repo = _db()
    owner = repo.get_user(alert.user_id) if repo is not None and alert.user_id is not None else None
    direction = current = None
    if owner is not None:
        recent = repo.readings_since(owner.id, clock.now() - TREND_WINDOW)
        if recent:
            direction, current = recent[-1].direction, recent[-1].mmol
    text = format_trusted_alert(alert, owner.name if owner else None, direction, minutes_without_ack, current)
    if _bot is None:
        _print(trusted_chat_id, text, alert.id is not None, prefix="notify_trusted")
        return None

    from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError

    from app.bot.keyboards import ack_keyboard

    markup = ack_keyboard(alert.id, texts.BUTTON_TRUSTED_OK) if alert.id is not None else None
    try:
        sent = await _bot.send_message(trusted_chat_id, text, reply_markup=markup)
    except TelegramForbiddenError:
        log.warning("notify_trusted: доверенное лицо %s заблокировало бота", trusted_chat_id)
        if owner is not None:
            name = next((c.name for c in repo.trusted_contacts(owner.id) if c.chat_id == trusted_chat_id), None)
            try:
                await _bot.send_message(owner.chat_id, texts.TRUSTED_BLOCKED.format(name=escape(name or "—")))
            except TelegramAPIError:
                pass
        return None
    return sent.message_id


def _print(chat_id: int, text: str, button: bool, prefix: str = "notify") -> None:
    plain = re.sub(r"<[^>]+>", "", text).replace("\n", " / ")   # без HTML-тегов, в одну строку
    mark = "  [кнопка]" if button else ""
    print(f"[{prefix} → {chat_id}] {plain}{mark}")
