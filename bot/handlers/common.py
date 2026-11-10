from aiogram import Router
from aiogram.types import Message

from bot import texts

router = Router(name="common")


@router.message()
async def unknown(message: Message) -> None:
    await message.answer(texts.UNKNOWN)
