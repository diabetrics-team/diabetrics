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
from app.bot.options import ESCALATION_OPTIONS, NIGHT_END_OPTIONS, NIGHT_START_OPTIONS
from app.models import Mode

GRAPH_HOURS = (3, 6, 24)


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


class LeaveCb(CallbackData, prefix="leave"):
    """Доверенное лицо отписывается от пользователя: его users.id (db.remove_trusted)."""
    owner_id: int


class GraphCb(CallbackData, prefix="graph"):
    hours: int


class SettingsCb(CallbackData, prefix="set"):
    """what: menu | esc | night | nightoff | tz; value — выбранное значение ("" — показать варианты).
    Двоеточие в данных кнопки — разделитель, поэтому время пишется без него: "2300-0700"."""
    what: str
    value: str = ""


def hhmm_to_cb(value: str) -> str:
    return value.replace(":", "")


def cb_to_hhmm(value: str) -> str:
    return f"{value[:2]}:{value[2:]}"


def ack_keyboard(alert_id: int, text: str = texts.BUTTON_OK) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=text, callback_data=AckCb(alert_id=alert_id))
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


def tz_keyboard(for_settings: bool = False) -> InlineKeyboardMarkup:
    """Онбординг — TzCb; из /settings — SettingsCb, плюс «Назад»."""
    kb = InlineKeyboardBuilder()
    for name, label in texts.TIMEZONES.items():
        data = SettingsCb(what="tz", value=name) if for_settings else TzCb(name=name)
        kb.button(text=label, callback_data=data)
    if for_settings:
        kb.button(text=texts.BUTTON_BACK, callback_data=SettingsCb(what="menu"))
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


def leave_keyboard(owners: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    """owners — пары (users.id, имя пользователя, которого подстраховываем)."""
    kb = InlineKeyboardBuilder()
    for owner_id, name in owners:
        kb.button(text=texts.BUTTON_LEAVE.format(name=name), callback_data=LeaveCb(owner_id=owner_id))
    kb.adjust(1)
    return kb.as_markup()


def graph_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for hours in GRAPH_HOURS:
        kb.button(text=f"{hours} ч", callback_data=GraphCb(hours=hours))
    kb.adjust(len(GRAPH_HOURS))
    return kb.as_markup()


def meal_keyboard() -> InlineKeyboardMarkup:
    """Под оценкой NutriLens: «Включить „После еды“» — дальше спросит длительность mode.py."""
    kb = InlineKeyboardBuilder()
    kb.button(text=texts.BUTTON_MEAL_MODE, callback_data=ModeCb(kind=Mode.AFTER_MEAL.value))
    return kb.as_markup()


def settings_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=texts.BUTTON_SET_ESCALATION, callback_data=SettingsCb(what="esc"))
    kb.button(text=texts.BUTTON_SET_NIGHT, callback_data=SettingsCb(what="night"))
    kb.button(text=texts.BUTTON_SET_TZ, callback_data=SettingsCb(what="tz"))
    kb.adjust(1)
    return kb.as_markup()


def escalation_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for minutes in ESCALATION_OPTIONS:
        kb.button(text=f"{minutes} мин", callback_data=SettingsCb(what="esc", value=str(minutes)))
    kb.button(text=texts.BUTTON_BACK, callback_data=SettingsCb(what="menu"))
    kb.adjust(len(ESCALATION_OPTIONS), 1)
    return kb.as_markup()


def night_start_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for start in NIGHT_START_OPTIONS:
        kb.button(text=start, callback_data=SettingsCb(what="night", value=hhmm_to_cb(start)))
    kb.button(text=texts.BUTTON_NIGHT_OFF, callback_data=SettingsCb(what="nightoff"))
    kb.button(text=texts.BUTTON_BACK, callback_data=SettingsCb(what="menu"))
    kb.adjust(len(NIGHT_START_OPTIONS), 1, 1)
    return kb.as_markup()


def night_end_keyboard(start: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for end in NIGHT_END_OPTIONS:
        value = f"{hhmm_to_cb(start)}-{hhmm_to_cb(end)}"
        kb.button(text=end, callback_data=SettingsCb(what="night", value=value))
    kb.button(text=texts.BUTTON_BACK, callback_data=SettingsCb(what="night"))
    kb.adjust(len(NIGHT_END_OPTIONS), 1)
    return kb.as_markup()
