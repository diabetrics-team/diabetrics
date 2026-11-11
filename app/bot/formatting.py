"""
Сборка сообщений из данных: уведомления, /now, меню режимов.

Владелец: Dev 3.
Чистые функции — без Telegram и сети, поэтому проверяются тестами на fake_cgm/data.
aiogram здесь не импортируется: notify (а через него pipeline) работает и без него.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

from app.bot import texts
from app.models import Alert, Forecast, ModeState, Reading

# Если последнее показание старше — прогноз не показываем: он построен по старым данным.
STALE_AFTER = timedelta(minutes=15)


def fmt_mmol(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}"


def format_duration(minutes: int) -> str:
    """125 -> «2 ч 5 мин», 120 -> «2 ч», 15 -> «15 мин»."""
    hours, mins = divmod(max(minutes, 0), 60)
    if hours and mins:
        return f"{hours} ч {mins} мин"
    if hours:
        return f"{hours} ч"
    return f"{mins} мин"


def format_age(ts: datetime, now: datetime) -> str:
    minutes = int((now - ts).total_seconds() // 60)
    if minutes < 1:
        return texts.AGE_JUST_NOW
    return texts.AGE_AGO.format(duration=format_duration(minutes))


def format_alert(alert: Alert) -> str:
    template = texts.ALERTS[alert.kind]
    return f"{texts.LEVEL_EMOJI[alert.level]} " + template.format(value=fmt_mmol(alert.value))


def describe_mode(state: ModeState, now: datetime) -> str:
    """«🟢 Обычный» или, для временного режима, «🍽 После еды, ещё 1 ч 40 мин»."""
    active = state.active(now)          # истёкший временный режим = NORMAL
    title = texts.MODES[active]["title"]
    if active is state.kind and state.until is not None:
        left = math.ceil((state.until - now).total_seconds() / 60)
        return texts.MODE_WITH_LEFT.format(title=title, left=format_duration(left))
    return title


def format_now(
    readings: list[Reading],
    forecast: Forecast | None,
    mode: ModeState,
    now: datetime,
) -> str:
    """Ответ на /now. readings — от старых к новым (как после ns/clean.py)."""
    if not readings:
        return texts.NOW_EMPTY
    last = readings[-1]
    arrow = texts.TREND_ARROWS.get(last.direction or "", "")
    lines = [
        texts.NOW_VALUE.format(value=fmt_mmol(last.mmol), trend=arrow).rstrip(),
        texts.NOW_AGE.format(age=format_age(last.ts, now)),
    ]
    if now - last.ts >= STALE_AFTER:
        lines.append(texts.NOW_STALE)
    elif forecast is None:
        lines.append(texts.NOW_NO_FORECAST)
    else:
        lines.append(texts.NOW_FORECAST.format(value=fmt_mmol(forecast.mmol_20)))
    lines.append(texts.NOW_MODE.format(mode=describe_mode(mode, now)))
    return "\n".join(lines)


def format_mode_menu(state: ModeState, now: datetime) -> str:
    modes = "\n".join(texts.MODE_LINE.format(**info) for info in texts.MODES.values())
    return texts.MODE_MENU.format(current=describe_mode(state, now), modes=modes)
