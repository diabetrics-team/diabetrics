"""
/start, /start invite_<код>, /help.

Владелец: Dev 3.
"""
from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.bot import storage, texts
from app.bot.handlers.onboarding import ask_url
from app.bot.keyboards import reconnect_keyboard

router = Router(name="start")


# Ссылка-приглашение t.me/<бот>?start=invite_<код> приходит как "/start invite_<код>".
# Этот хендлер должен стоять ВЫШЕ обычного /start, иначе тот перехватит сообщение.
@router.message(CommandStart(deep_link=True, magic=F.args.startswith("invite_")))
async def start_invite(message: Message) -> None:
    # TODO (День 3): достать код, найти пользователя в БД, привязать доверенное лицо.
    await message.answer(texts.INVITE_NOT_READY)


@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return
    # escape: включён HTML-режим, а в имени могут быть символы < > &
    await message.answer(texts.START.format(name=escape(message.from_user.first_name)))
    user = storage.get_user(message.from_user.id)
    if user is None:
        await ask_url(message, state)
    else:
        await state.clear()
        await message.answer(
            texts.ONBOARDING_ALREADY.format(url=escape(user.ns_url)),
            reply_markup=reconnect_keyboard(),
        )


@router.message(Command("help"))
async def help_(message: Message) -> None:
    await message.answer(texts.HELP)
