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


# ---------------------------------------------------------------- функции доступа (день 3) ---

from app import db  # noqa: E402
from app.models import Alert, AlertKind, Level, Mode, ModeState, Reading  # noqa: E402


@pytest.fixture
def mem():
    db.configure("sqlite:///:memory:")
    yield db


def _user(chat=111):
    return db.upsert_user(chat, NOW, ns_url="http://ns", ns_token="t", tz="Europe/Moscow")


def test_upsert_user_and_active(mem):
    u = _user()
    assert db.get_user_by_chat(111).id == u.id
    db.upsert_user(111, NOW, tz="Asia/Tomsk")
    assert db.get_user(u.id).tz == "Asia/Tomsk" and db.get_user(u.id).ns_url == "http://ns"
    db.upsert_user(222, NOW)                        # без Nightscout — pipeline его не опрашивает
    assert [x.chat_id for x in db.active_users()] == [111]
    db.set_active(u.id, False)
    assert db.active_users() == []
    with pytest.raises(ValueError):
        db.upsert_user(111, NOW, no_such_column=1)


def test_readings_saved_once_and_ordered(mem):
    u = _user()
    rs = [Reading(NOW + timedelta(minutes=5 * i), 5.0 + i, "Flat") for i in range(3)]
    assert len(db.save_readings(u.id, rs[::-1])) == 3
    assert db.save_readings(u.id, rs) == []        # повторно — ничего нового
    assert db.last_reading_ts(u.id) == rs[-1].ts
    assert db.readings_since(u.id, NOW + timedelta(minutes=5)) == rs[1:]


def test_mode_roundtrip(mem):
    u = _user()
    assert db.get_mode(u.id) == ModeState()
    st = ModeState(Mode.AFTER_MEAL, until=NOW + timedelta(hours=2))
    db.set_mode(u.id, st, NOW)
    assert db.get_mode(u.id) == st
    db.set_mode(u.id, ModeState(Mode.NIGHT), NOW)
    assert db.get_mode(u.id) == ModeState(Mode.NIGHT)


def test_alerts_ack_sent_escalated(mem):
    u = _user()
    a = db.save_alert(Alert(Level.CRITICAL, AlertKind.VERY_LOW, NOW, 2.8, True, user_id=u.id),
                      sent_at=NOW)
    assert a.id is not None
    assert [x.id for x in db.open_alerts(u.id)] == [a.id]
    assert db.recent_alerts(u.id, NOW - timedelta(hours=1))[0].kind is AlertKind.VERY_LOW
    assert db.last_sent_at(a.id) == NOW
    db.mark_sent(a.id, NOW + timedelta(minutes=5), message_id=42)
    assert db.last_sent_at(a.id) == NOW + timedelta(minutes=5)
    assert db.mark_escalated(a.id, NOW + timedelta(minutes=10)) is True
    assert db.mark_escalated(a.id, NOW + timedelta(minutes=11)) is False
    assert db.mark_ack(a.id, 111, NOW + timedelta(minutes=12)) is True
    assert db.mark_ack(a.id, 999, NOW + timedelta(minutes=13)) is False   # второе нажатие
    got = db.get_alert(a.id)
    assert got.acked_by == 111 and got.escalated_at == NOW + timedelta(minutes=10)
    assert db.open_alerts(u.id) == []
    with pytest.raises(ValueError):
        db.save_alert(Alert(Level.INFO, AlertKind.DIGEST, NOW))           # без user_id


def test_open_alerts_survive_restart(tmp_path):
    url = f"sqlite:///{tmp_path / 'd.db'}"
    db.configure(url)
    u = _user()
    a = db.save_alert(Alert(Level.CRITICAL, AlertKind.VERY_LOW, NOW, 2.8, True, user_id=u.id))
    db.configure(url)                               # «перезапуск»: новое подключение к тому же файлу
    [again] = db.open_alerts()
    assert again.id == a.id and again.needs_ack and again.created_at == NOW


def test_ack_by_id_checks_confirmed_access_in_storage(mem):
    from app.alerts.escalation import ack

    owner = _user()
    critical = db.save_alert(Alert(Level.CRITICAL, AlertKind.VERY_LOW, NOW, 2.8, True, user_id=owner.id))
    db.create_invite(owner.id, "pending", NOW)
    assert ack(critical.id, 555, NOW) is None
    db.accept_invite("pending", 555, "trusted", NOW)
    assert ack(critical.id, 999, NOW) is None
    confirmed = ack(critical.id, 555, NOW + timedelta(minutes=1))
    assert confirmed.acked_by == 555
    assert ack(critical.id, owner.chat_id, NOW + timedelta(minutes=2)) is None
    assert db.get_alert(critical.id).acked_at == NOW + timedelta(minutes=1)
    assert db.open_alerts(owner.id) == []


def test_latest_episode_alerts_keeps_older_severities_and_ignores_future(mem):
    u = _user()
    def save(kind, level, offset):
        return db.save_alert(Alert(level, kind, NOW + timedelta(hours=offset), user_id=u.id))

    save(AlertKind.LOW, Level.IMPORTANT, -7)
    low = save(AlertKind.LOW, Level.IMPORTANT, -6)
    night_low = save(AlertKind.LOW, Level.CRITICAL, -5)
    save(AlertKind.LOW, Level.IMPORTANT, 1)
    save(AlertKind.FAST_RISE, Level.IMPORTANT, -1)
    assert {a.id for a in db.latest_episode_alerts(u.id, NOW)} == {low.id, night_low.id}


def test_trusted_invite(mem):
    u = _user()
    db.create_invite(u.id, "abc", NOW)
    assert db.trusted_chat_ids(u.id) == []          # пока не принято
    owner = db.accept_invite("abc", 555, "Мама", NOW)
    assert owner.id == u.id
    assert db.accept_invite("abc", 666, "Чужой", NOW) is None   # код одноразовый
    assert db.trusted_chat_ids(u.id) == [555]
    assert db.remove_trusted(u.id) == 1 and db.trusted_chat_ids(u.id) == []


def test_alert_ids_are_not_reused_after_delete(mem):
    """После очистки уведомлений (scripts/demo.sh) новые не получают старые id."""
    from sqlalchemy import delete

    u = _user()
    first = db.save_alert(Alert(Level.CRITICAL, AlertKind.VERY_LOW, NOW, 2.8, True, user_id=u.id))
    db.record_trusted_attempt(first.id, 555, NOW, delivered=True)
    with db._session() as s, s.begin():
        s.execute(delete(AlertRow))
    second = db.save_alert(Alert(Level.CRITICAL, AlertKind.VERY_LOW, NOW, 2.8, True, user_id=u.id))
    assert second.id > first.id
    assert db.trusted_delivery_state(second.id) == {}       # старая доставка не «прилипла»


def test_switching_nightscout_forgets_old_source_data(mem):
    """Другой адрес Nightscout — другой источник: старые показания и тревоги удаляются."""
    u = _user()
    db.save_readings(u.id, [Reading(NOW + timedelta(hours=20), 3.1)])       # «из будущего» (fake-NS)
    old = db.save_alert(Alert(Level.CRITICAL, AlertKind.VERY_LOW, NOW, 2.8, True, user_id=u.id))
    db.record_trusted_attempt(old.id, 555, NOW, delivered=False)
    db.set_mode(u.id, ModeState(Mode.DND), NOW)
    db.create_invite(u.id, "inv", NOW)
    db.accept_invite("inv", 555, "Мама", NOW)

    db.upsert_user(111, NOW, ns_url="http://ns", ns_token="new-token")      # тот же адрес — ничего не трогаем
    assert db.last_reading_ts(u.id) is not None and db.open_alerts(u.id)

    db.upsert_user(111, NOW, ns_url="https://real.example")                 # другой источник
    assert db.last_reading_ts(u.id) is None
    assert db.open_alerts(u.id) == [] and db.recent_alerts(u.id, NOW - timedelta(days=1)) == []
    assert db.trusted_delivery_state(old.id) == {}
    assert db.get_mode(u.id) == ModeState(Mode.DND)                         # режим и доверенные остаются
    assert db.trusted_chat_ids(u.id) == [555]
    assert db.get_user(u.id).ns_url == "https://real.example"
