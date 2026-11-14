"""Watchdog, ошибки Telegram, логи событий, состояние для /health."""
import asyncio
import logging
from datetime import timedelta

import pytest

pytest.importorskip("httpx")

from app import clock, pipeline  # noqa: E402
from app.models import AlertKind, Level  # noqa: E402
from tests.test_pipeline import FakeNS, FakeRepo, U, raw, start_of  # noqa: E402


class Repo(FakeRepo):
    def __init__(self, users):
        super().__init__(users)
        self.inactive = set()

    def active_users(self):
        return [u for u in self.users if u.id not in self.inactive]

    def set_active(self, uid, active):
        (self.inactive.discard if active else self.inactive.add)(uid)


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    monkeypatch.setenv("CLOCK", "system")
    clock.reset()
    pipeline._ns_down_since.clear()
    pipeline._ns_down_alerted.clear()
    yield
    clock.reset()


@pytest.fixture
def outbox(monkeypatch):
    out = []

    async def fake_notify(alert, chat_id):
        out.append(alert)
        return 100 + len(out)

    monkeypatch.setattr(pipeline, "notify", fake_notify)
    return out


def tick_at(repo, ns, t):
    clock.freeze(t)
    asyncio.run(pipeline.tick(repo, ns))


def test_watchdog_once_per_outage(outbox):
    entries = raw("normal")
    t0 = start_of(entries) + timedelta(hours=1)
    repo, ns = Repo([U(1, 111)]), FakeNS(entries)
    tick_at(repo, ns, t0)                                   # связь есть
    ns.fail = True
    for m in range(1, 31):                                  # 30 минут без связи
        tick_at(repo, ns, t0 + timedelta(minutes=m))
    down = [a for a in outbox if a.kind is AlertKind.NS_UNREACHABLE]
    assert len(down) == 1
    assert down[0].level is Level.IMPORTANT and down[0].created_at == t0 + timedelta(minutes=11)
    assert any(a.kind is AlertKind.NS_UNREACHABLE for a in repo.alerts)   # сохранено в БД
    assert any(a.kind is AlertKind.NO_DATA for a in outbox)       # no_data движка — само собой
    assert pipeline.HEALTH.ns_down == 1

    ns.fail = False                                         # связь вернулась
    tick_at(repo, ns, t0 + timedelta(minutes=31))
    assert pipeline.HEALTH.ns_down == 0
    ns.fail = True                                          # новый сбой — новое уведомление
    for m in range(32, 45):
        tick_at(repo, ns, t0 + timedelta(minutes=m))
    assert len([a for a in outbox if a.kind is AlertKind.NS_UNREACHABLE]) == 2


def test_short_outage_is_silent(outbox):
    entries = raw("normal")
    t0 = start_of(entries) + timedelta(hours=1)
    repo, ns = Repo([U(1, 111)]), FakeNS(entries, fail=True)
    for m in range(0, 9):
        tick_at(repo, ns, t0 + timedelta(minutes=m))
    assert [a for a in outbox if a.kind is AlertKind.NS_UNREACHABLE] == []


def test_blocked_user_is_deactivated(monkeypatch):
    class TelegramForbiddenError(Exception):
        pass

    async def blocked(alert, chat_id):
        raise TelegramForbiddenError("bot was blocked by the user")

    monkeypatch.setattr(pipeline, "notify", blocked)
    entries = raw("night_hypo")
    repo, ns = Repo([U(1, 111), U(2, 222)]), FakeNS(entries)
    tick_at(repo, ns, start_of(entries) + timedelta(hours=4))
    assert repo.inactive == {1, 2}
    tick_at(repo, ns, start_of(entries) + timedelta(hours=4, minutes=5))   # не падает
    assert pipeline.HEALTH.users == 0


def test_telegram_error_does_not_stop_tick(monkeypatch):
    async def flaky(alert, chat_id):
        raise RuntimeError("Telegram 502")

    monkeypatch.setattr(pipeline, "notify", flaky)
    entries = raw("night_hypo")
    repo, ns = Repo([U(1, 111)]), FakeNS(entries)
    tick_at(repo, ns, start_of(entries) + timedelta(hours=4))
    assert repo.alerts and pipeline.HEALTH.errors == 0   # сохранено, тик дошёл до конца


def test_events_are_logged_without_token(outbox, caplog):
    entries = raw("night_hypo")
    repo, ns = Repo([U(1, 111, ns_token="read-SECRET")]), FakeNS(entries)
    with caplog.at_level(logging.INFO, logger="app.pipeline"):
        for m in range(0, 241, 5):
            tick_at(repo, ns, start_of(entries) + timedelta(minutes=m))
    text = caplog.text
    assert "alert alert_id=" in text and "critical/very_low" in text
    assert "read-SECRET" not in text


def test_health_updated_each_tick(outbox):
    entries = raw("normal")
    repo, ns = Repo([U(1, 111), U(2, 222)]), FakeNS(entries)
    before = pipeline.HEALTH.last_tick_mono
    tick_at(repo, ns, start_of(entries) + timedelta(hours=1))
    assert pipeline.HEALTH.users == 2 and pipeline.HEALTH.last_tick_mono != before
