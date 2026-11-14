"""Regression checks for trusted delivery using the real pipeline and SQLite."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app import clock, db, pipeline
from app.models import Alert, AlertKind, Level, Reading


@pytest.mark.parametrize(("scenario", "expected"), [
    ("borderline", 0), ("fast_drop", 0), ("hypo_corrected", 0), ("night_hypo", 1),
])
def test_trusted_deliveries_in_official_scenarios(scenario, expected, tmp_path, monkeypatch):
    from test_pipeline import FakeNS, start_of

    entries = json.loads((Path("fake_cgm/data") / f"{scenario}.json").read_text())
    start = start_of(entries)
    duration = (max(e["date"] for e in entries) - min(e["date"] for e in entries)) // 60_000
    db.configure(f"sqlite:///{tmp_path / 'scenario.db'}")
    user = db.upsert_user(111, start, ns_url="http://fake")
    db.create_invite(user.id, "invite", start)
    db.accept_invite("invite", 222, "Trusted", start)
    delivered = []
    owner_alerts = []

    async def owner(alert, chat):
        owner_alerts.append(alert)
        return 1

    async def trusted(alert, chat, minutes):
        delivered.append(alert)

    monkeypatch.setattr(pipeline, "notify", owner)
    monkeypatch.setattr(pipeline, "notify_trusted", trusted)
    monkeypatch.setenv("CLOCK", "system")
    clock.reset()

    async def run():
        ns = FakeNS(entries)
        for minute in range(duration + 1):
            clock.freeze(start + timedelta(minutes=minute))
            await pipeline.process_user(user, db, ns)

    try:
        asyncio.run(run())
    finally:
        clock.reset()
    assert len(delivered) == expected
    assert all(a.kind is not AlertKind.PREDICTED_LOW for a in delivered)
    if scenario in {"borderline", "night_hypo"}:
        assert any(a.kind is AlertKind.PREDICTED_LOW and a.level is Level.CRITICAL
                   and not a.needs_ack for a in owner_alerts)


def test_new_episode_can_escalate_after_recovery_and_restart(tmp_path, monkeypatch):
    start = datetime(2026, 11, 14, 12, tzinfo=UTC)
    url = f"sqlite:///{tmp_path / 'restart.db'}"
    db.configure(url)
    user = db.upsert_user(111, start)
    db.create_invite(user.id, "invite", start)
    db.accept_invite("invite", 222, "Trusted", start)
    delivered = []

    async def notify(alert, chat, minutes):
        delivered.append(alert.id)

    def add(kind, minute, value):
        ts = start + timedelta(minutes=minute)
        db.save_readings(user.id, [Reading(ts, value)])
        return db.save_alert(Alert(Level.CRITICAL, kind, ts, value, True, user_id=user.id))

    monkeypatch.setattr(pipeline, "notify_trusted", notify)
    old_forecast = add(AlertKind.PREDICTED_LOW, 0, 2.8)
    first = add(AlertKind.LOW, 5, 3.7)
    add(AlertKind.VERY_LOW, 10, 2.9)
    asyncio.run(pipeline._escalate(user, db, start + timedelta(minutes=20)))
    assert delivered == [first.id]
    assert db.get_alert(old_forecast.id).escalated_at is None

    db.configure(url)
    asyncio.run(pipeline._escalate(user, db, start + timedelta(minutes=21)))
    assert delivered == [first.id]
    db.save_readings(user.id, [Reading(start + timedelta(minutes=25), 4.3)])
    second = add(AlertKind.VERY_LOW, 30, 2.8)
    asyncio.run(pipeline._escalate(user, db, start + timedelta(minutes=40)))
    assert delivered == [first.id, second.id]
