"""
Ответ на всё, что не подошло другим хендлерам.

Владелец: Dev 3.
"""
from __future__ import annotations

from aiogram import Router
from aiogram.types import CallbackQuery, Message

from app.bot import texts

router = Router(name="common")


# Ловит всё, что не подошло другим хендлерам. Этот роутер должен быть последним.
@router.message()
async def unknown(message: Message) -> None:
    await message.answer(texts.UNKNOWN)


# Кнопки без обработчика: «Я в порядке» до Дня 3, кнопки старых меню после перезапуска бота.
# Без ответа Telegram крутит у кнопки «часики» до таймаута.
@router.callback_query()
async def unknown_button(callback: CallbackQuery) -> None:
    await callback.answer(texts.BUTTON_NOT_READY)
