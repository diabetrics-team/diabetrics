from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from bot import texts

router = Router(name="start")


@router.message(CommandStart(deep_link=True, magic=F.args.startswith("invite_")))
async def start_invite(message: Message) -> None:
    await message.answer(texts.INVITE_NOT_READY)


@router.message(CommandStart())
async def start(message: Message) -> None:
    name = message.from_user.first_name if message.from_user else "друг"
    await message.answer(texts.START.format(name=escape(name)))


@router.message(Command("help"))
async def help_(message: Message) -> None:
    await message.answer(texts.HELP)
