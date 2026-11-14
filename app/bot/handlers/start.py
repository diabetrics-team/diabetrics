"""
/start и /help. Приглашение доверенного лица (/start invite_<код>) — в handlers/trusted.py.

Владелец: Dev 3.
"""
from __future__ import annotations

from html import escape

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app import db
from app.bot import storage, texts
from app.bot.handlers.onboarding import ask_url
from app.bot.keyboards import reconnect_keyboard

router = Router(name="start")


@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return
    # escape: включён HTML-режим, а в имени могут быть символы < > &
    await message.answer(texts.START.format(name=escape(message.from_user.first_name)))
    row = db.get_user_by_chat(message.from_user.id) if db.is_configured() else None
    if row is not None and not row.is_active:
        db.set_active(row.id, True)   # был заблокирован бот — раз пишет /start, снова опрашиваем
    user = storage.get_user(message.from_user.id)
    if user is None or not user.ns_url:
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
