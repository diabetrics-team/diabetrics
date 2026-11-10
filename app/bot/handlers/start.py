"""
/start, /start invite_<код>, /help.

Владелец: Dev 3.
"""
from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from app.bot import texts

router = Router(name="start")


# Ссылка-приглашение t.me/<бот>?start=invite_<код> приходит как "/start invite_<код>".
# Этот хендлер должен стоять ВЫШЕ обычного /start, иначе тот перехватит сообщение.
@router.message(CommandStart(deep_link=True, magic=F.args.startswith("invite_")))
async def start_invite(message: Message) -> None:
    # TODO (День 3): достать код, найти пользователя в БД, привязать доверенное лицо.
    await message.answer(texts.INVITE_NOT_READY)


@router.message(CommandStart())
async def start(message: Message) -> None:
    # TODO (День 2): после приветствия — онбординг (адрес Nightscout, токен, часовой пояс).
    name = message.from_user.first_name if message.from_user else "друг"
    # escape: включён HTML-режим, а в имени могут быть символы < > &
    await message.answer(texts.START.format(name=escape(name)))


@router.message(Command("help"))
async def help_(message: Message) -> None:
    await message.answer(texts.HELP)
