from datetime import UTC, datetime, timedelta, timezone

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import select  # noqa: E402
from sqlalchemy.exc import IntegrityError, StatementError  # noqa: E402

from app.db import (  # noqa: E402
    AlertRow,
    ModeRow,
    ReadingRow,
    TrustedContact,
    User,
    init_db,
    make_engine,
    make_session_factory,
)

NOW = datetime(2026, 11, 11, 3, 0, tzinfo=UTC)


@pytest.fixture
def session():
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    with make_session_factory(engine)() as s:
        yield s


def test_tables_and_utc_roundtrip(session):
    msk = timezone(timedelta(hours=3))
    u = User(chat_id=123, tz="Asia/Tomsk", created_at=datetime(2026, 11, 11, 6, 0, tzinfo=msk))
    session.add(u)
    session.flush()
    session.add_all([
        ReadingRow(user_id=u.id, ts=NOW, mmol=3.2, direction="FortyFiveDown"),
        AlertRow(user_id=u.id, level="critical", kind="very_low", created_at=NOW,
                 value=2.8, needs_ack=True),
        ModeRow(user_id=u.id, kind="night", until=None, started_at=NOW),
        TrustedContact(user_id=u.id, invite_code="abc", created_at=NOW),
    ])
    session.commit()
    session.expire_all()

    u2 = session.scalar(select(User).where(User.chat_id == 123))
    assert u2.created_at == datetime(2026, 11, 11, 3, 0, tzinfo=UTC)  # МСК → UTC
    assert u2.created_at.tzinfo is not None
    assert u2.escalation_minutes == 10
    r = session.scalar(select(ReadingRow))
    assert r.ts == NOW


def test_reading_unique_per_user_ts(session):
    u = User(chat_id=1, created_at=NOW)
    session.add(u)
    session.flush()
    session.add(ReadingRow(user_id=u.id, ts=NOW, mmol=5.0))
    session.flush()
    session.add(ReadingRow(user_id=u.id, ts=NOW, mmol=5.0))
    with pytest.raises(IntegrityError):
        session.flush()


def test_naive_datetime_rejected(session):
    session.add(User(chat_id=2, created_at=datetime(2026, 11, 11, 3, 0)))
    with pytest.raises(StatementError, match="tzinfo"):
        session.flush()
