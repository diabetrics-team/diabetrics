from datetime import UTC, datetime, timedelta

import pytest

from app import clock

MS = 1_794_169_500_000  # 2026-11-08T20:25:00Z
T = datetime(2026, 11, 8, 20, 25, tzinfo=UTC)


class FakeMono:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


@pytest.fixture
def ns_clock(monkeypatch):
    monkeypatch.setenv("CLOCK", "nightscout")
    clock.reset()
    mono = FakeMono()
    clock._monotonic = mono
    yield mono
    clock.reset()


@pytest.fixture(autouse=True)
def _reset_after():
    yield
    clock.reset()


def test_system_is_utc_aware(monkeypatch):
    monkeypatch.setenv("CLOCK", "system")
    clock.reset()
    n = clock.now()
    assert n.utcoffset() == timedelta(0)
    assert not clock.uses_server_time()
    clock.sync_with_server(MS)        # в system игнорируется
    assert abs(clock.now() - datetime.now(UTC)) < timedelta(seconds=5)


def test_server_time_after_sync(ns_clock):
    clock.sync_with_server(MS)
    assert clock.now() == T
    ns_clock.t += 2
    assert clock.now() == T + timedelta(seconds=2)       # пока скорость не известна — ×1


def test_learns_speed_60(ns_clock):
    clock.sync_with_server(MS)
    ns_clock.t += 10                                     # 10 реальных секунд
    clock.sync_with_server(MS + 600_000)                 # на сервере прошло 10 минут
    assert clock.server_rate() == 1.0                    # одной оценке пока не верим
    ns_clock.t += 10
    clock.sync_with_server(MS + 1_200_000)               # подтвердилось: ×60
    assert clock.server_rate() == pytest.approx(60)
    ns_clock.t += 30                                     # ещё 30 с без синхронизации
    assert clock.now() == T + timedelta(minutes=50)      # 20 + 30 с × 60


def _speed_30(mono, start_ms=MS, syncs=3):
    ms = start_ms
    clock.sync_with_server(ms)
    for _ in range(syncs):
        mono.t += 5
        ms += 150_000                                    # 5 с × 30
        clock.sync_with_server(ms)
    return ms


def test_fake_ns_restart_jump_does_not_break_rate(ns_clock):
    """Перезапуск fake-NS: время прыгает на 3 дня вперёд. Скорость ×30 не должна стать ×9000."""
    _speed_30(ns_clock)
    assert clock.server_rate() == pytest.approx(30)
    ns_clock.t += 30                                     # fake-NS лежал 30 с
    jump = MS + 3 * 86_400_000
    clock.sync_with_server(jump)                         # дикая оценка ≈ ×8600
    assert clock.server_rate() == pytest.approx(30)
    ns_clock.t += 0.2                                    # pipeline считает «сейчас» сразу после
    assert clock.now() - datetime.fromtimestamp(jump / 1000, UTC) < timedelta(seconds=10)
    ns_clock.t += 5
    clock.sync_with_server(jump + 150_000)               # дальше обычные ×30
    assert clock.server_rate() == pytest.approx(30, rel=0.1)


def test_restart_jump_backwards(ns_clock):
    ms = _speed_30(ns_clock)
    ns_clock.t += 5
    clock.sync_with_server(ms - 3 * 86_400_000)          # время назад — скорость не трогаем
    assert clock.server_rate() == pytest.approx(30)


def test_new_speed_after_restart_is_learned(ns_clock):
    """Перезапустили с --speed 60 вместо 30 — через две синхронизации часы идут ×60."""
    ms = _speed_30(ns_clock)
    for _ in range(3):
        ns_clock.t += 5
        ms += 300_000
        clock.sync_with_server(ms)
    assert clock.server_rate() == pytest.approx(60)


def test_server_restart_keeps_rate(ns_clock):
    clock.sync_with_server(MS)
    ns_clock.t += 10
    clock.sync_with_server(MS - 3_600_000)               # fake-NS перезапустили с начала
    assert clock.server_rate() == 1.0
    assert clock.now() == T - timedelta(hours=1)


def test_mode_read_lazily(monkeypatch):
    clock.reset()
    monkeypatch.setenv("CLOCK", "nightscout")            # выставили ПОСЛЕ импорта модуля
    assert clock.uses_server_time()


def test_bad_mode(monkeypatch):
    monkeypatch.setenv("CLOCK", "banana")
    clock.reset()
    with pytest.raises(ValueError):
        clock.now()


def test_freeze_wins(ns_clock):
    clock.sync_with_server(MS)
    clock.freeze(datetime(2026, 1, 1, tzinfo=UTC))
    assert clock.now() == datetime(2026, 1, 1, tzinfo=UTC)
    clock.unfreeze()
    assert clock.now() == T
    with pytest.raises(ValueError):
        clock.freeze(datetime(2026, 1, 1))  # noqa: DTZ001 — проверяем отказ без tzinfo
