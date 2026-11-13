"""
/settings — когда писать доверенному лицу, часы ночного режима, часовой пояс.

Владелец: Dev 3. Всё выбирается кнопками, поэтому ввести «не тот формат» нельзя;
поддельные данные кнопок отбрасываются.
✂️ Пороги сахара здесь не настраиваются: в движке Dev 2 они общие (app/alerts/config.py).
"""
from __future__ import annotations

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from app.alerts.config import REPEAT_AFTER
from app.bot import storage, texts
from app.bot.formatting import format_settings
from app.bot.keyboards import (
    SettingsCb,
    cb_to_hhmm,
    escalation_keyboard,
    hhmm_to_cb,
    night_end_keyboard,
    night_start_keyboard,
    settings_keyboard,
    tz_keyboard,
)
from app.bot.options import ESCALATION_OPTIONS, NIGHT_END_OPTIONS, NIGHT_START_OPTIONS

router = Router(name="settings")
NIGHT_OFF = "00:00"   # night_start == night_end — расписание выключено (app/bot/night.py)


def _summary(chat_id: int) -> str:
    s = storage.get_settings(chat_id)
    return format_settings(s.escalation_minutes, s.night_start, s.night_end, s.tz)


async def _edit(message: Message, text: str, markup: InlineKeyboardMarkup) -> None:
    try:
        await message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        pass   # «message is not modified» — нажали «Назад», а показывать нечего нового


@router.message(Command("settings"))
async def settings_cmd(message: Message) -> None:
    if message.from_user is None:
        return
    if storage.get_user(message.from_user.id) is None:
        await message.answer(texts.NOW_NOT_CONNECTED)
        return
    await message.answer(_summary(message.from_user.id), reply_markup=settings_keyboard())


@router.callback_query(SettingsCb.filter())
async def settings_button(callback: CallbackQuery, callback_data: SettingsCb) -> None:
    chat_id = callback.from_user.id
    msg = callback.message
    if not isinstance(msg, Message) or storage.get_user(chat_id) is None:
        await callback.answer()
        return
    what, value = callback_data.what, callback_data.value

    # Шаг «показать варианты»
    if what == "esc" and not value:
        await callback.answer()
        repeat = int(REPEAT_AFTER.total_seconds() // 60)
        await _edit(msg, texts.SETTINGS_ASK_ESCALATION.format(repeat=repeat), escalation_keyboard())
        return
    if what == "night" and "-" not in value:
        await callback.answer()
        if not value:
            await _edit(msg, texts.SETTINGS_ASK_NIGHT_START, night_start_keyboard())
        elif value in {hhmm_to_cb(t) for t in NIGHT_START_OPTIONS}:
            start = cb_to_hhmm(value)
            await _edit(msg, texts.SETTINGS_ASK_NIGHT_END.format(start=start), night_end_keyboard(start))
        return
    if what == "tz" and not value:
        await callback.answer()
        await _edit(msg, texts.SETTINGS_ASK_TZ, tz_keyboard(for_settings=True))
        return

    # Шаг «сохранить»
    if what == "esc" and value in {str(m) for m in ESCALATION_OPTIONS}:
        storage.save_settings(chat_id, escalation_minutes=int(value))
    elif what == "night":
        start, _, end = value.partition("-")
        if start not in {hhmm_to_cb(t) for t in NIGHT_START_OPTIONS} or \
                end not in {hhmm_to_cb(t) for t in NIGHT_END_OPTIONS}:
            await callback.answer()
            return
        storage.save_settings(chat_id, night_start=cb_to_hhmm(start), night_end=cb_to_hhmm(end))
    elif what == "nightoff":
        storage.save_settings(chat_id, night_start=NIGHT_OFF, night_end=NIGHT_OFF)
    elif what == "tz" and value in texts.TIMEZONES:
        storage.save_settings(chat_id, tz=value)
    elif what != "menu":
        await callback.answer()             # поддельные данные кнопки — ничего не меняем
        return

    await callback.answer(texts.SETTINGS_SAVED if what != "menu" else None)
    await _edit(msg, _summary(chat_id), settings_keyboard())
