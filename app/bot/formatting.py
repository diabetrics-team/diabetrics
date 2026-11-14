"""
Сборка сообщений из данных: уведомления, /now, меню режимов.

Владелец: Dev 3.
Чистые функции — без Telegram и сети, поэтому проверяются тестами на fake_cgm/data.
aiogram здесь не импортируется: notify (а через него pipeline) работает и без него.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

from app.bot import texts
from app.models import Alert, AlertKind, Forecast, ModeState, Reading

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


def format_local_time(moment: datetime, tz: str | None) -> str:
    """«03:12» в часовом поясе пользователя (по умолчанию — Москва, как в БД)."""
    return moment.astimezone(ZoneInfo(tz or "Europe/Moscow")).strftime("%H:%M")


def format_alert(alert: Alert, tz: str | None = None) -> str:
    """«🔴 03:12 · Очень низкий сахар: 2.8 ммоль/л…». Время — когда сработало, в поясе пользователя."""
    head = texts.ALERT_HEAD.format(
        emoji=texts.LEVEL_EMOJI[alert.level], time=format_local_time(alert.created_at, tz)
    )
    return head + texts.ALERTS[alert.kind].format(value=fmt_mmol(alert.value))


def format_trusted_alert(
    alert: Alert, name: str | None, direction: str | None, minutes: int, current: float | None = None,
) -> str:
    """Сообщение доверенному лицу: кто, о чём уведомление, сахар сейчас, тренд, сколько минут нет ответа.

    current — последнее показание. Без него берётся alert.value, кроме прогноза: там это не сахар.
    """
    if current is None and alert.kind is not AlertKind.PREDICTED_LOW:
        current = alert.value
    reason = texts.ALERTS[alert.kind].format(value=fmt_mmol(alert.value)).split("\n")[0]
    return texts.TRUSTED_ALERT.format(
        name=escape(name) if name else texts.TRUSTED_SOMEONE,
        reason=reason,
        value=fmt_mmol(current),
        trend=texts.TREND_ARROWS.get(direction or "", ""),
        minutes=minutes,
    ).replace(" \n", "\n")   # без стрелки не оставлять пробел в конце строки


def describe_mode(state: ModeState, now: datetime, scheduled: bool = False, tz: str | None = None) -> str:
    """«🔔 Обычный», «🍽 После еды, ещё 1 ч 40 мин» или «🌙 Ночной по расписанию, до 07:00»."""
    active = state.active(now)          # истёкший временный режим = NORMAL
    title = texts.MODES[active]["title"]
    if scheduled and state.until is not None:
        return texts.MODE_SCHEDULED.format(title=title, end=format_local_time(state.until, tz))
    if active is state.kind and state.until is not None:
        left = math.ceil((state.until - now).total_seconds() / 60)
        return texts.MODE_WITH_LEFT.format(title=title, left=format_duration(left))
    return title


def format_now(
    readings: list[Reading],
    forecast: Forecast | None,
    mode: ModeState,
    now: datetime,
    scheduled: bool = False,
    tz: str | None = None,
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
    lines.append(texts.NOW_MODE.format(mode=describe_mode(mode, now, scheduled, tz)))
    return "\n".join(lines)


def format_night(start: str, end: str) -> tuple[str, str] | None:
    """Пара (начало, конец) ночного расписания; None — расписание выключено."""
    return None if not start or not end or start == end else (start, end)


def format_mode_menu(
    state: ModeState, now: datetime, scheduled: bool = False, tz: str | None = None,
    night_start: str = "23:00", night_end: str = "07:00",
) -> str:
    modes = "\n".join(texts.MODE_LINE.format(**info) for info in texts.MODES.values())
    night = format_night(night_start, night_end)
    night_line = (texts.MODE_NIGHT_SCHEDULE.format(start=night[0], end=night[1]) if night
                  else texts.MODE_NIGHT_SCHEDULE_OFF)
    return texts.MODE_MENU.format(current=describe_mode(state, now, scheduled, tz), modes=modes, night=night_line)


def tz_label(tz: str | None) -> str:
    return texts.TIMEZONES.get(tz or "Europe/Moscow", tz or "")


def format_settings(minutes: int, night_start: str, night_end: str, tz: str | None) -> str:
    night = format_night(night_start, night_end)
    night_text = (texts.SETTINGS_NIGHT_RANGE.format(start=night[0], end=night[1]) if night
                  else texts.SETTINGS_NIGHT_OFF)
    return texts.SETTINGS.format(minutes=minutes, night=night_text, tz=tz_label(tz))
