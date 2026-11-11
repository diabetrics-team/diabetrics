"""
/mode — меню режимов. У «После еды» — выбор длительности, «После коррекции гипо» — всегда 15 мин.

Владелец: Dev 3.
"""
from __future__ import annotations

from datetime import timedelta

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app import clock
from app.bot import storage, texts
from app.bot.formatting import format_duration, format_mode_menu
from app.bot.keyboards import ModeCb, durations_keyboard, modes_keyboard
from app.models import TEMPORARY_MODES, Mode, ModeState

router = Router(name="mode")

# Длительности временных режимов, минуты (models.py: «После еды» 2–3 ч, «После гипо» 15 мин).
# TODO: сверить с Dev 2.
AFTER_MEAL_OPTIONS = (120, 150, 180)
AFTER_HYPO_MINUTES = 15


@router.message(Command("mode"))
async def mode_menu(message: Message) -> None:
    if message.from_user is None:
        return
    state = storage.get_mode(message.from_user.id)
    await message.answer(format_mode_menu(state, clock.now()), reply_markup=modes_keyboard())


@router.callback_query(ModeCb.filter())
async def mode_chosen(callback: CallbackQuery, callback_data: ModeCb) -> None:
    await callback.answer()
    if not isinstance(callback.message, Message):
        return
    try:
        mode = Mode(callback_data.kind)
    except ValueError:                  # данные кнопки можно подделать
        return
    title = texts.MODES[mode]["title"]

    if mode is Mode.AFTER_MEAL and callback_data.minutes not in AFTER_MEAL_OPTIONS:
        # Первое нажатие (minutes=0) — спросить длительность.
        await callback.message.edit_text(
            texts.MODE_ASK_DURATION.format(title=title),
            reply_markup=durations_keyboard(mode, AFTER_MEAL_OPTIONS),
        )
        return

    if mode in TEMPORARY_MODES:
        minutes = AFTER_HYPO_MINUTES if mode is Mode.AFTER_HYPO else callback_data.minutes
        state = ModeState(mode, until=clock.now() + timedelta(minutes=minutes))
        text = texts.MODE_ON_TEMPORARY.format(title=title, duration=format_duration(minutes))
    else:
        state = ModeState(mode)
        text = texts.MODE_ON.format(title=title)

    storage.set_mode(callback.from_user.id, state)
    await callback.message.edit_text(text)   # убрать кнопки, чтобы не нажали второй раз
