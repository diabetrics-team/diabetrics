"""
Ночной режим по расписанию: с users.night_start до users.night_end по часовому поясу users.tz.

Владелец: Dev 3. Чистые функции — без БД и Telegram: их вызывают pipeline (Dev 1) перед evaluate
и бот для /now и /mode. В БД ничего не переключается — режим вычисляется на лету.

Ручной выбор важнее расписания: ночь по расписанию действует, только если сейчас «Обычный».
Расписание выключено, если night_start == night_end (кнопка «Не включать сам» в /settings).
"""
from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.models import Mode, ModeState

DEFAULT_TZ = "Europe/Moscow"   # tz ещё не выбран в онбординге — как в остальном боте


def parse_hhmm(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def schedule_enabled(start: str | None, end: str | None) -> bool:
    return bool(start) and bool(end) and start != end


def night_until(user: Any, now: datetime) -> datetime | None:
    """Если у пользователя сейчас ночь по расписанию — когда она закончится (UTC), иначе None.

    user — любой объект с night_start, night_end, tz (users из БД). Нет этих полей —
    расписания нет (так ведут себя подделки пользователей в тестах pipeline).
    """
    start, end = getattr(user, "night_start", None), getattr(user, "night_end", None)
    if not schedule_enabled(start, end):
        return None
    try:
        begin, finish = parse_hhmm(start), parse_hhmm(end)
        tz = ZoneInfo(getattr(user, "tz", None) or DEFAULT_TZ)
    except (ValueError, KeyError):
        return None                     # испорченная настройка не должна ронять pipeline
    local = now.astimezone(tz)
    t = local.time()
    inside = begin <= t < finish if begin < finish else (t >= begin or t < finish)   # 23:00–07:00
    if not inside:
        return None
    end_at = datetime.combine(local.date(), finish, tzinfo=tz)
    if end_at <= local:
        end_at += timedelta(days=1)
    return end_at.astimezone(UTC)


def effective_mode(state: ModeState, user: Any, now: datetime) -> ModeState:
    """Режим с учётом расписания: «Обычный» ночью превращается в «Ночной» до конца ночи."""
    if state.active(now) is not Mode.NORMAL:
        return state
    until = night_until(user, now)
    return ModeState(Mode.NIGHT, until=until) if until is not None else state
