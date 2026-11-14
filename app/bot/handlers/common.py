"""
Ответ на всё, что не подошло другим хендлерам, и общий обработчик ошибок.

Владелец: Dev 3.
"""
from __future__ import annotations

import logging

from aiogram import Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, ErrorEvent, Message

from app.bot import texts

router = Router(name="common")
log = logging.getLogger(__name__)


# Ловит всё, что не подошло другим хендлерам. Этот роутер должен быть последним.
@router.message()
async def unknown(message: Message) -> None:
    await message.answer(texts.UNKNOWN)


# Кнопки без обработчика: кнопки старых меню после перезапуска бота и т. п.
# Без ответа Telegram крутит у кнопки «часики» до таймаута.
@router.callback_query()
async def unknown_button(callback: CallbackQuery) -> None:
    await callback.answer(texts.BUTTON_NOT_READY)


@router.errors()
async def on_error(event: ErrorEvent) -> None:
    """Непредвиденная ошибка в любом хендлере: подробности — в лог, пользователю — спокойный ответ."""
    log.error("ошибка в хендлере: %s", type(event.exception).__name__, exc_info=event.exception)
    update = event.update
    try:
        if update.message is not None:
            await update.message.answer(texts.ERROR_GENERIC)
        elif update.callback_query is not None:
            await update.callback_query.answer(texts.ERROR_GENERIC)
    except TelegramAPIError:
        pass   # не получилось и ответить — уже записано в лог
