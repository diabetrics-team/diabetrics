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


def test_still_low_fires_under_night_schedule(outbox):
    """Ночь по расписанию не должна съедать перепроверку «После коррекции гипо».

    Пользователь получил low, нажал «Я в порядке», включил «После коррекции гипо» на 15 мин.
    Сахар не поднялся → через 15 мин должно прийти критическое still_low, даже ночью.
    """
    from app.models import Mode, ModeState

    entries = raw("hypo_not_corrected")                   # low в 12:35 МСК, остаётся ниже 3.9
    user = U(1, 111)
    user.night_start, user.night_end, user.tz = "12:00", "14:00", "Europe/Moscow"
    repo = Repo([user])
    state = {"mode": ModeState()}
    repo.get_mode = lambda uid: state["mode"]
    ns = FakeNS(entries)
    start = start_of(entries)
    for m in range(0, 121):
        now = start + timedelta(minutes=m)
        tick_at(repo, ns, now)
        low = next((a for a in repo.alerts if a.kind is AlertKind.LOW), None)
        if low is not None and state["mode"].kind is not Mode.AFTER_HYPO and now >= low.created_at + timedelta(minutes=5):
            for a in repo.alerts:                         # «Я в порядке» на всё открытое
                if a.needs_ack and a.acked_at is None:
                    a.acked_at, a.acked_by = now, 111
            state["mode"] = ModeState(Mode.AFTER_HYPO, until=now + timedelta(minutes=15))
    still = [a for a in repo.alerts if a.kind is AlertKind.STILL_LOW]
    assert len(still) == 1 and still[0].level is Level.CRITICAL and still[0].needs_ack


def test_failed_repeat_is_retried_next_tick(monkeypatch):
    """Повтор критического упал с таймаутом → на следующем тике ещё попытка, а не «уже повторяли»."""
    from datetime import UTC, datetime

    from app.models import Alert

    t0 = datetime(2026, 11, 14, 3, 0, tzinfo=UTC)
    user = U(1, 111)
    repo = Repo([user])
    alert = repo.save_alert(Alert(Level.CRITICAL, AlertKind.VERY_LOW, t0, 2.8, True, user_id=1), sent_at=t0)
    calls = []

    async def flaky(a, chat_id):
        calls.append(clock.now())
        if len(calls) == 1:
            raise TimeoutError("Telegram не ответил")
        return 500

    monkeypatch.setattr(pipeline, "notify", flaky)
    for m in (5, 6, 7, 8):
        clock.freeze(t0 + timedelta(minutes=m))
        asyncio.run(pipeline._escalate(user, repo, clock.now()))
    assert calls == [t0 + timedelta(minutes=5), t0 + timedelta(minutes=6)]   # сбой, успех, дальше тишина
    assert repo.last_sent_at(alert.id) == t0 + timedelta(minutes=6)
