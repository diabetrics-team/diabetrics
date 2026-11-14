"""
Ночной режим по расписанию (app/bot/night.py) — чистые функции, без БД и Telegram.

Владелец: Dev 3.
"""
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from app.alerts.config import REPEAT_AFTER
from app.alerts.escalation import escalation_step
from app.bot.night import effective_mode, night_until
from app.bot.options import ESCALATION_OPTIONS
from app.models import Mode, ModeState


@dataclass
class U:
    night_start: str = "23:00"
    night_end: str = "07:00"
    tz: str | None = "Europe/Moscow"


def utc(hh: int, mm: int = 0, day: int = 13) -> datetime:
    return datetime(2026, 11, day, hh, mm, tzinfo=UTC)


@pytest.mark.parametrize(("now", "until"), [
    (utc(19, 59), None),                    # 22:59 МСК — ещё день
    (utc(20, 0), utc(4, 0, day=14)),        # 23:00 МСК — ночь до 07:00 МСК следующего дня
    (utc(23, 30), utc(4, 0, day=14)),       # 02:30 МСК
    (utc(3, 59, day=14), utc(4, 0, day=14)),
    (utc(4, 0, day=14), None),              # 07:00 МСК — утро
])
def test_night_across_midnight_in_user_tz(now, until):
    assert night_until(U(), now) == until


def test_night_in_other_timezone_and_without_tz():
    ekb = U(tz="Asia/Yekaterinburg")        # UTC+5: 23:00 там = 18:00 UTC
    assert night_until(ekb, utc(18, 0)) == utc(2, 0, day=14)
    assert night_until(U(tz=None), utc(20, 0)) == utc(4, 0, day=14), "без tz — Москва"


def test_daytime_window_and_disabled_schedule():
    assert night_until(U("13:00", "15:00"), utc(11, 0)) == utc(12, 0)   # 14:00 МСК
    assert night_until(U("00:00", "00:00"), utc(21, 0)) is None          # «Не включать сам»
    assert night_until(U("23:xx", "07:00"), utc(21, 0)) is None          # битая настройка не роняет


def test_effective_mode_only_replaces_normal():
    night = utc(21, 0)
    assert effective_mode(ModeState(), U(), night) == ModeState(Mode.NIGHT, until=utc(4, 0, day=14))
    meal = ModeState(Mode.AFTER_MEAL, until=night + timedelta(hours=2))
    assert effective_mode(meal, U(), night) is meal, "ручной режим важнее расписания"
    expired = ModeState(Mode.AFTER_MEAL, until=night - timedelta(minutes=1))
    assert effective_mode(expired, U(), night).kind is Mode.NIGHT, "истёкший временный = обычный"
    assert effective_mode(ModeState(), U(), utc(12, 0)) == ModeState()


def test_user_without_schedule_fields_is_untouched():
    """Подделки пользователей в tests/test_pipeline.py — без night_start/night_end/tz."""
    @dataclass
    class Bare:
        id: int = 1
    state = ModeState()
    assert effective_mode(state, Bare(), utc(21, 0)) is state


def test_escalation_options_are_valid_for_dev2():
    """Все варианты из /settings больше повтора — иначе escalation_step бросит ValueError."""
    for minutes in ESCALATION_OPTIONS:
        assert timedelta(minutes=minutes) > REPEAT_AFTER
        escalation_step([], utc(12, 0), trusted_after_minutes=minutes)


def test_expired_after_hypo_is_kept_for_recheck_at_night():
    """Ночью истёкший «После коррекции гипо» не подменяется «Ночным», пока движок не перепроверил сахар."""
    until = utc(23, 30)                                   # 02:30 МСК — ночь по расписанию
    state = ModeState(Mode.AFTER_HYPO, until=until)
    assert effective_mode(state, U(), until - timedelta(minutes=1)) is state      # ещё действует
    assert effective_mode(state, U(), until) is state                             # момент перепроверки
    assert effective_mode(state, U(), until + timedelta(minutes=14)) is state
    later = effective_mode(state, U(), until + timedelta(minutes=15))             # окно прошло
    assert later.kind is Mode.NIGHT
