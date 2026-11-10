"""
Ответ на всё, что не подошло другим хендлерам.

Владелец: Dev 3.
"""
from __future__ import annotations

from aiogram import Router
from aiogram.types import Message

from app.bot import texts

router = Router(name="common")


# Ловит всё, что не подошло другим хендлерам. Этот роутер должен быть последним.
@router.message()
async def unknown(message: Message) -> None:
    await message.answer(texts.UNKNOWN)
