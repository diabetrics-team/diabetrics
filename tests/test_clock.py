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
    assert clock.server_rate() == pytest.approx(60)
    ns_clock.t += 30                                     # ещё 30 с без синхронизации
    assert clock.now() == T + timedelta(minutes=40)      # 10 + 30 с × 60


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
