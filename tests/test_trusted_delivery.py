"""
Доставка эскалации доверенным лицам: временный сбой повторяется, доставленным — не шлём.

Сценарий из ревью: первая отправка на +10 мин падает (таймаут) → на +11 мин повтор
проходит → на +12 мин и дальше отправок больше нет.
"""
import asyncio
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("httpx")

from app import pipeline  # noqa: E402
from app.models import Alert, AlertKind, Level  # noqa: E402
from tests.test_pipeline import FakeRepo, U  # noqa: E402

T0 = datetime(2026, 11, 14, 3, 0, tzinfo=UTC)
USER, MOM, DAD = 111, 555, 777


class FlakyTrusted:
    """notify_trusted: первые fails[chat] вызовов для chat бросают таймаут."""

    def __init__(self, fails=None, result=1):
        self.fails = dict(fails or {})
        self.calls: list[tuple[int, datetime]] = []
        self.result = result

    async def __call__(self, alert, chat_id, minutes):
        self.calls.append((chat_id, self.now))
        if self.fails.get(chat_id, 0) > 0:
            self.fails[chat_id] -= 1
            raise asyncio.TimeoutError("Telegram не ответил")
        return self.result


async def _noop_notify(alert, chat_id):
    return None


def critical(repo, user_id=1):
    return repo.save_alert(Alert(Level.CRITICAL, AlertKind.VERY_LOW, T0, 2.8, True, user_id=user_id),
                           sent_at=T0)


def escalate_at(repo, user, trusted, minute):
    trusted.now = T0 + timedelta(minutes=minute)
    asyncio.run(pipeline._escalate(user, repo, trusted.now))


@pytest.fixture
def patched(monkeypatch):
    def apply(trusted):
        monkeypatch.setattr(pipeline, "notify_trusted", trusted)
        monkeypatch.setattr(pipeline, "notify", _noop_notify)
    return apply


def test_first_fails_then_delivered_then_nothing(patched):
    """Ровно сценарий из ревью."""
    user = U(1, USER)
    repo = FakeRepo([user])
    repo.trusted[1] = [MOM]
    alert = critical(repo)
    trusted = FlakyTrusted({MOM: 1})
    patched(trusted)

    escalate_at(repo, user, trusted, 10)          # таймаут
    assert alert.escalated_at is None             # эскалация не считается завершённой
    escalate_at(repo, user, trusted, 11)          # повтор проходит
    assert alert.escalated_at == T0 + timedelta(minutes=11)
    for m in range(12, 30):                       # дальше — тишина
        escalate_at(repo, user, trusted, m)
    assert [m for _, m in trusted.calls] == [T0 + timedelta(minutes=10), T0 + timedelta(minutes=11)]


def test_retry_only_undelivered_contact(patched):
    user = U(1, USER)
    repo = FakeRepo([user])
    repo.trusted[1] = [MOM, DAD]
    critical(repo)
    trusted = FlakyTrusted({DAD: 2})
    patched(trusted)
    for m in range(10, 20):
        escalate_at(repo, user, trusted, m)
    assert [c for c, _ in trusted.calls].count(MOM) == 1          # маме — один раз
    assert [c for c, _ in trusted.calls].count(DAD) == 3          # папе: сбой, сбой, доставлено


def test_gives_up_after_max_attempts(patched):
    user = U(1, USER)
    repo = FakeRepo([user])
    repo.trusted[1] = [MOM]
    alert = critical(repo)
    trusted = FlakyTrusted({MOM: 999})
    patched(trusted)
    for m in range(10, 40):
        escalate_at(repo, user, trusted, m)
    assert len(trusted.calls) == pipeline.TRUSTED_MAX_ATTEMPTS
    assert alert.escalated_at is not None                          # не долбим вечно


def test_ack_stops_retries(patched):
    user = U(1, USER)
    repo = FakeRepo([user])
    repo.trusted[1] = [MOM]
    alert = critical(repo)
    trusted = FlakyTrusted({MOM: 999})
    patched(trusted)
    escalate_at(repo, user, trusted, 10)
    alert.acked_at, alert.acked_by = T0 + timedelta(minutes=10, seconds=30), USER   # «Я в порядке»
    for m in range(11, 20):
        escalate_at(repo, user, trusted, m)
    assert len(trusted.calls) == 1


def test_permanent_failure_is_not_retried(patched):
    """Доверенный заблокировал бота: notify_trusted сам это обрабатывает и возвращает None."""
    user = U(1, USER)
    repo = FakeRepo([user])
    repo.trusted[1] = [MOM]
    alert = critical(repo)
    trusted = FlakyTrusted(result=None)
    patched(trusted)
    for m in range(10, 15):
        escalate_at(repo, user, trusted, m)
    assert len(trusted.calls) == 1 and alert.escalated_at is not None


def test_no_trusted_contact_closes_escalation(patched):
    user = U(1, USER)
    repo = FakeRepo([user])
    alert = critical(repo)
    trusted = FlakyTrusted()
    patched(trusted)
    escalate_at(repo, user, trusted, 10)
    assert trusted.calls == [] and alert.escalated_at is not None


# ------------------------------------------------------------ то же на настоящей SQLite ---

def test_reviewer_scenario_on_sqlite(patched):
    pytest.importorskip("sqlalchemy")
    from app import db

    db.configure("sqlite:///:memory:")
    owner = db.upsert_user(USER, T0, name="Тест", ns_url="http://fake", tz="Europe/Moscow")
    db.create_invite(owner.id, "inv", T0)
    db.accept_invite("inv", MOM, "Мама", T0)
    alert = db.save_alert(Alert(Level.CRITICAL, AlertKind.VERY_LOW, T0, 2.8, True, user_id=owner.id),
                          sent_at=T0)
    trusted = FlakyTrusted({MOM: 1})
    patched(trusted)

    escalate_at(db, owner, trusted, 10)                          # падает
    assert db.get_alert(alert.id).escalated_at is None
    assert db.trusted_delivery_state(alert.id) == {MOM: (False, 1)}
    escalate_at(db, owner, trusted, 11)                          # проходит
    assert db.trusted_delivery_state(alert.id) == {MOM: (True, 2)}
    assert db.get_alert(alert.id).escalated_at == T0 + timedelta(minutes=11)
    for m in range(12, 20):
        escalate_at(db, owner, trusted, m)
    assert len(trusted.calls) == 2
