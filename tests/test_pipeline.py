"""
pipeline.tick на подделках: БД — словари в памяти (FakeRepo, тот же API, что app/db.py),
Nightscout — FakeNS по JSON-сценарию, Telegram — список отправленного.
Время двигаем clock.freeze, как будто проходят минуты. Ни сети, ни SQLAlchemy не нужно.
"""
import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("httpx")

from app import clock, pipeline  # noqa: E402
from app.alerts.antispam import EPISODE_KINDS  # noqa: E402
from app.alerts.config import ALERT_COOLDOWN  # noqa: E402
from app.models import Alert, AlertKind, Level, ModeState, Reading  # noqa: E402
from app.ns.client import NSUnavailable  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "fake_cgm" / "data"


def raw(name):
    return json.loads((DATA / f"{name}.json").read_text())


@dataclass
class U:
    id: int
    chat_id: int
    ns_url: str = "http://fake"
    ns_token: str | None = None


class FakeRepo:
    def __init__(self, users):
        self.users = users
        self.readings = {u.id: [] for u in users}
        self.alerts: list[Alert] = []
        self.sent_at: dict[int, datetime] = {}
        self.trusted = {u.id: [] for u in users}
        self.saved_batches = []
        self.deliveries: dict[tuple[int, int], tuple[bool, int]] = {}

    def active_users(self):
        return list(self.users)

    def last_reading_ts(self, uid):
        return self.readings[uid][-1].ts if self.readings[uid] else None

    def save_readings(self, uid, rs):
        have = {r.ts for r in self.readings[uid]}
        new = [r for r in rs if r.ts not in have]
        self.saved_batches.append(len(rs))
        self.readings[uid] = sorted(self.readings[uid] + new, key=lambda r: r.ts)
        return new

    def readings_since(self, uid, since):
        return [r for r in self.readings[uid] if r.ts >= since]

    def get_mode(self, uid):
        return ModeState()

    def recent_alerts(self, uid, since):
        return [a for a in self.alerts if a.user_id == uid and a.created_at >= since]

    def latest_episode_alerts(self, uid, now):
        latest = {}
        for a in sorted(self.alerts, key=lambda alert: alert.created_at):
            if a.user_id == uid and a.created_at <= now and a.kind in EPISODE_KINDS:
                latest[a.kind, a.level] = a
        return list(latest.values())

    def save_alert(self, a, sent_at=None):
        a.id = len(self.alerts) + 1
        self.alerts.append(a)
        if sent_at:
            self.sent_at[a.id] = sent_at
        return a

    def mark_sent(self, aid, at, message_id=None):
        self.sent_at[aid] = at

    def last_sent_at(self, aid):
        return self.sent_at.get(aid)

    def open_alerts(self, uid):
        return [a for a in self.alerts if a.user_id == uid and a.needs_ack and a.acked_at is None]

    def mark_escalated(self, aid, at):
        a = self.alerts[aid - 1]
        if a.escalated_at is not None:
            return False
        a.escalated_at = at
        return True

    def trusted_chat_ids(self, uid):
        return self.trusted[uid]

    def trusted_delivery_state(self, aid):
        return {chat: tuple(v) for (a, chat), v in self.deliveries.items() if a == aid}

    def record_trusted_attempt(self, aid, chat, at, delivered):
        done, attempts = self.deliveries.get((aid, chat), (False, 0))
        self.deliveries[aid, chat] = (done or delivered, attempts + 1)
        return attempts + 1


class FakeNS:
    """Отдаёт записи сценария, которые «уже случились» к clock.now()."""

    def __init__(self, entries, fail=False):
        self.entries, self.fail = entries, fail
        self.calls = []

    def __call__(self, url, token):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    async def get_server_time(self):
        return int(clock.now().timestamp() * 1000)

    async def get_entries(self, since=None, count=None):
        if self.fail:
            raise NSUnavailable("нет связи")
        self.calls.append(since)
        now_ms = clock.now().timestamp() * 1000
        visible = [e for e in self.entries if e["date"] <= now_ms]
        if since is not None:
            visible = [e for e in visible if e["date"] > since.timestamp() * 1000]
        visible.sort(key=lambda e: -e["date"])
        return visible[:count] if count else visible


@pytest.fixture
def sent(monkeypatch):
    out = {"user": [], "trusted": []}

    async def fake_notify(alert, chat_id):
        out["user"].append((chat_id, alert.kind, alert.level, clock.now()))
        out.setdefault("ids", []).append((alert.id, clock.now()))

    async def fake_trusted(alert, chat_id, minutes):
        out["trusted"].append((chat_id, alert.id, minutes))

    monkeypatch.setattr(pipeline, "notify", fake_notify)
    monkeypatch.setattr(pipeline, "notify_trusted", fake_trusted)
    monkeypatch.setenv("CLOCK", "system")
    clock.reset()
    yield out
    clock.reset()


def run_scenario(repo, ns, start, minutes, step=1):
    for m in range(0, minutes + 1, step):
        clock.freeze(start + timedelta(minutes=m))
        asyncio.run(pipeline.tick(repo, ns))


def start_of(entries):
    return datetime.fromtimestamp(min(e["date"] for e in entries) / 1000, UTC)


def test_night_hypo_end_to_end(sent):
    entries = raw("night_hypo")
    repo, ns = FakeRepo([U(1, 111)]), FakeNS(entries)
    run_scenario(repo, ns, start_of(entries), 480)
    kinds = [k for _, k, _, _ in sent["user"]]
    assert AlertKind.LOW in kinds and AlertKind.VERY_LOW in kinds
    assert any(lvl is Level.CRITICAL for _, _, lvl, _ in sent["user"])
    assert len(repo.readings[1]) == 97                        # facts.points night_hypo


def test_each_reading_saved_once(sent):
    entries = raw("normal")
    repo, ns = FakeRepo([U(1, 111)]), FakeNS(entries)
    t = start_of(entries) + timedelta(hours=1)
    for _ in range(5):                                         # время стоит, тики идут
        clock.freeze(t)
        asyncio.run(pipeline.tick(repo, ns))
    assert sum(repo.saved_batches[1:]) == 0                    # после первого тика — ничего нового
    assert ns.calls[0] is None and all(c is not None for c in ns.calls[1:])  # дальше только since


def test_no_spam_while_low_persists(sent):
    entries = raw("night_hypo")
    repo, ns = FakeRepo([U(1, 111)]), FakeNS(entries)
    run_scenario(repo, ns, start_of(entries), 480)
    times = [t for _, k, _, t in sent["user"] if k is AlertKind.LOW]
    assert times and all(b - a >= ALERT_COOLDOWN for a, b in zip(times, times[1:]))


def test_normal_is_quiet(sent):
    entries = raw("normal")
    run_scenario(FakeRepo([U(1, 111)]), FakeNS(entries), start_of(entries), 360, step=5)
    assert [x for x in sent["user"] if x[2] is not Level.INFO] == []


def test_ns_error_does_not_stop_other_users(sent, monkeypatch):
    entries = raw("night_hypo")
    good, bad = FakeNS(entries), FakeNS(entries, fail=True)
    repo = FakeRepo([U(1, 111, "http://bad"), U(2, 222, "http://good")])
    factory = lambda url, token: bad if url == "http://bad" else good  # noqa: E731
    clock.freeze(start_of(entries) + timedelta(hours=5))
    asyncio.run(pipeline.tick(repo, factory))
    assert repo.readings[1] == [] and len(repo.readings[2]) > 0


def test_bug_in_rules_does_not_crash_tick(sent, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("баг в правиле")

    monkeypatch.setattr(pipeline, "evaluate", boom)
    entries = raw("normal")
    clock.freeze(start_of(entries) + timedelta(hours=1))
    asyncio.run(pipeline.tick(FakeRepo([U(1, 111)]), FakeNS(entries)))   # не падает


def test_escalation_is_idempotent(sent):
    entries = raw("night_hypo")
    repo, ns = FakeRepo([U(1, 111)]), FakeNS(entries)
    repo.trusted[1] = [999]
    run_scenario(repo, ns, start_of(entries) + timedelta(hours=4), 60)
    crit = [a for a in repo.alerts if a.needs_ack]
    assert crit, "к этому времени должно быть критическое"
    assert sent["trusted"] == [(999, a.id, 10) for a in crit]
    for a in crit:
        times = [t for aid, t in sent["ids"] if aid == a.id]
        assert times == [a.created_at, a.created_at + timedelta(minutes=5)]


def test_open_critical_is_not_duplicated(sent):
    """Пока very_low без «Я в порядке», второе very_low не создаётся — только повторы."""
    entries = raw("night_hypo")
    repo, ns = FakeRepo([U(1, 111)]), FakeNS(entries)
    run_scenario(repo, ns, start_of(entries), 480)
    assert len([a for a in repo.alerts if a.kind is AlertKind.VERY_LOW]) == 1


def test_long_outage_keeps_last_reading_past_history_window(sent):
    entries = raw("sensor_lost")
    repo, ns = FakeRepo([U(1, 111)]), FakeNS(entries)
    last = datetime.fromtimestamp(max(e["date"] for e in entries) / 1000, UTC)
    clock.freeze(last)
    asyncio.run(pipeline.tick(repo, ns))
    clock.freeze(last + timedelta(hours=3))
    asyncio.run(pipeline.tick(repo, ns))
    assert [a.level for a in repo.alerts if a.kind is AlertKind.NO_DATA] == [Level.IMPORTANT]


def test_old_low_episode_does_not_restart_when_recent_window_expires(sent):
    start = datetime(2026, 11, 12, 12, tzinfo=UTC)
    repo = FakeRepo([U(1, 111)])
    repo.save_alert(Alert(Level.IMPORTANT, AlertKind.LOW, start, 3.8, user_id=1), sent_at=start)
    repo.readings[1] = [Reading(start + timedelta(minutes=5 * i), 3.8) for i in range(49)]
    clock.freeze(repo.readings[1][-1].ts)
    asyncio.run(pipeline.tick(repo, FakeNS([])))
    assert len(repo.alerts) == 1
    assert sent["user"] == []


def test_server_clock_without_ns_skips_evaluate(sent, monkeypatch):
    monkeypatch.setenv("CLOCK", "nightscout")
    clock.reset()
    called = []
    monkeypatch.setattr(pipeline, "evaluate", lambda *a: called.append(1) or [])
    asyncio.run(pipeline.tick(FakeRepo([U(1, 111)]), FakeNS([], fail=True)))
    assert called == []
