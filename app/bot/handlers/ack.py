"""
Кнопка «Я в порядке» (ack:<id>): подтвердить критическое уведомление.

Владелец: Dev 3. Данные — app/db.py (Dev 1), подтверждение — ack() Dev 2.
Нажать может сам пользователь или его доверенное лицо. После нажатия кнопка гаснет,
а второй стороне приходит сообщение, кто подтвердил.
"""
from __future__ import annotations

from html import escape

from aiogram import Bot, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message

from app import clock, db
from app.alerts.escalation import ack
from app.bot import texts
from app.bot.formatting import format_local_time
from app.bot.handlers.trusted import send_safe
from app.bot.keyboards import AckCb

router = Router(name="ack")


async def _dim(callback: CallbackQuery, line: str = "") -> None:
    """Погасить кнопку у нажатого сообщения; line — дописать строку к тексту."""
    msg = callback.message
    if not isinstance(msg, Message) or msg.text is None:
        return
    try:
        if line:
            await msg.edit_text(msg.html_text + line)   # без reply_markup — кнопка исчезает
        else:
            await msg.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass                                            # уже изменено или слишком старое


@router.callback_query(AckCb.filter())
async def ack_pressed(callback: CallbackQuery, callback_data: AckCb, bot: Bot) -> None:
    alert = db.get_alert(callback_data.alert_id)
    owner = db.get_user(alert.user_id) if alert is not None else None
    if alert is None or owner is None:
        await callback.answer(texts.ACK_NOT_FOUND)
        await _dim(callback)
        return

    who = callback.from_user.id
    trusted = db.trusted_chat_ids(owner.id)
    if who != owner.chat_id and who not in trusted:      # данные кнопки можно подделать
        await callback.answer(texts.ACK_FORBIDDEN)
        return

    if alert.acked_at is not None:
        await callback.answer(texts.ACK_ALREADY)
        await _dim(callback)
        return
    alert = ack(alert, who, clock.now())                 # Dev 2: app/alerts/escalation.py
    if not db.mark_ack(alert.id, who, alert.acked_at):   # кто-то успел нажать одновременно
        await callback.answer(texts.ACK_ALREADY)
        await _dim(callback)
        return

    await callback.answer(texts.ACK_DONE)
    await _dim(callback, texts.ACKED_LINE.format(time=format_local_time(alert.acked_at, owner.tz)))

    if who != owner.chat_id:
        # Пользователю — кто подтвердил; и погасить кнопку у его уведомления.
        await send_safe(bot, owner.chat_id, texts.ACK_BY_TRUSTED.format(name=escape(callback.from_user.first_name)))
        message_id = db.alert_message_id(alert.id)
        if message_id is not None:
            try:
                await bot.edit_message_reply_markup(chat_id=owner.chat_id, message_id=message_id)
            except TelegramBadRequest:
                pass
    elif alert.escalated_at is not None:
        # Доверенным лицам уже написали — сообщить, что всё в порядке.
        for chat_id in trusted:
            await send_safe(bot, chat_id, texts.ACK_BY_OWNER.format(name=escape(owner.name or texts.TRUSTED_SOMEONE)))
