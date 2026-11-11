"""
Inline-клавиатуры и данные их кнопок.

Владелец: Dev 3.
CallbackData — «упаковка» данных кнопки в строку до 64 байт: ModeCb(kind="after_meal", minutes=150)
превращается в "mode:after_meal:150", а в хендлере обратно в объект.
"""
from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot import texts
from app.bot.formatting import format_duration
from app.models import Mode


class AckCb(CallbackData, prefix="ack"):
    """Кнопка «Я в порядке» — "ack:<id>", формат из заглушки notify.py каркаса."""
    alert_id: int


class ModeCb(CallbackData, prefix="mode"):
    kind: str          # значение Mode, например "after_meal"
    minutes: int = 0   # длительность временного режима; 0 — ещё не выбрана


class TzCb(CallbackData, prefix="tz"):
    name: str          # IANA-имя из texts.TIMEZONES


class OnboardingCb(CallbackData, prefix="onb"):
    action: str        # "no_token" | "restart"


def ack_keyboard(alert_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=texts.BUTTON_OK, callback_data=AckCb(alert_id=alert_id))
    return kb.as_markup()


def modes_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for mode, info in texts.MODES.items():
        kb.button(text=info["title"], callback_data=ModeCb(kind=mode.value))
    kb.adjust(2)   # по две кнопки в ряд
    return kb.as_markup()


def durations_keyboard(mode: Mode, options: tuple[int, ...]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for minutes in options:
        kb.button(text=format_duration(minutes), callback_data=ModeCb(kind=mode.value, minutes=minutes))
    kb.adjust(len(options))
    return kb.as_markup()


def tz_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for name, label in texts.TIMEZONES.items():
        kb.button(text=label, callback_data=TzCb(name=name))
    kb.adjust(2)
    return kb.as_markup()


def no_token_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=texts.BUTTON_NO_TOKEN, callback_data=OnboardingCb(action="no_token"))
    return kb.as_markup()


def reconnect_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=texts.BUTTON_RECONNECT, callback_data=OnboardingCb(action="restart"))
    return kb.as_markup()
