"""
Хранилище бота (app/bot/storage.py) и функции БД, добавленные для бота
(блок «для бота (Dev 3)» в app/db.py).

Владелец: Dev 3.
"""
from datetime import UTC, datetime

import pytest

pytest.importorskip("sqlalchemy")

from app import clock, db
from app.bot import storage
from app.bot.storage import BotUser
from app.models import Alert, AlertKind, Level

T0 = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def fresh_db():
    storage.reset()
    clock.freeze(T0)
    yield
    clock.unfreeze()


def uid(chat_id: int) -> int:
    return db.get_user_by_chat(chat_id).id


def test_name_is_saved_and_not_erased():
    storage.save_user(BotUser(1, "https://a.example", "t1", name="Аня"))
    storage.save_user(BotUser(1, "https://b.example", None))          # переподключение: имени нет
    user = storage.get_user(1)
    assert (user.ns_url, user.ns_token, user.name) == ("https://b.example", None, "Аня")


def test_invite_owner_before_accept():
    storage.save_user(BotUser(1, "https://a.example", None, name="Аня"))
    db.create_invite(uid(1), "code-1", T0)
    assert db.invite_owner("code-1").chat_id == 1
    assert db.invite_owner("nope") is None
    db.accept_invite("code-1", 2, "Боря", T0)
    assert db.invite_owner("code-1") is None                         # принятое — уже не приглашение


def test_drop_pending_keeps_confirmed():
    storage.save_user(BotUser(1, "https://a.example", None))
    db.create_invite(uid(1), "code-1", T0)
    db.accept_invite("code-1", 2, "Боря", T0)
    db.create_invite(uid(1), "code-2", T0)
    db.drop_pending_invites(uid(1))
    assert db.invite_owner("code-2") is None
    assert db.trusted_chat_ids(uid(1)) == [2]


def test_trusted_contacts_and_guarded_users():
    storage.save_user(BotUser(1, "https://a.example", None, name="Аня"))
    storage.save_user(BotUser(3, "https://c.example", None, name="Вера"))
    for owner, code, (chat, name) in [(1, "a", (2, "Боря")), (1, "b", (4, "Гена")), (3, "c", (2, "Боря"))]:
        db.create_invite(uid(owner), code, T0)
        db.accept_invite(code, chat, name, T0)
    db.create_invite(uid(1), "pending", T0)                            # непринятое не считается
    assert [(c.chat_id, c.name) for c in db.trusted_contacts(uid(1))] == [(2, "Боря"), (4, "Гена")]
    assert [u.name for u in db.guarded_users(2)] == ["Аня", "Вера"]
    assert db.guarded_users(5) == []


def test_alert_message_id():
    storage.save_user(BotUser(1, "https://a.example", None))
    alert = db.save_alert(Alert(Level.CRITICAL, AlertKind.VERY_LOW, T0, 2.8, needs_ack=True, user_id=uid(1)))
    assert db.alert_message_id(alert.id) is None
    db.mark_sent(alert.id, T0, message_id=42)
    assert db.alert_message_id(alert.id) == 42
    assert db.alert_message_id(9999) is None
