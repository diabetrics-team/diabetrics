"""
Хранилище бота: подключение к Nightscout, часовой пояс, режим.

Владелец: Dev 3 (API), Dev 1 (реализация на БД).
Было в памяти — теперь в SQLite через app/db.py, поэтому pipeline видит тех же
пользователей и режимы, а после перезапуска бота ничего не теряется.
API не менялся: BotUser, get_user, save_user, get_mode, set_mode, reset.

Если БД ещё не подключена (тесты бота, запуск без main.py), подключается
временная БД в памяти — поведение как у прежнего хранилища.
"""
from __future__ import annotations

from dataclasses import dataclass

from app import clock, db
from app.models import ModeState


@dataclass(slots=True)
class BotUser:
    telegram_id: int            # в личном чате совпадает с chat_id
    ns_url: str
    ns_token: str | None        # None — Nightscout открыт для чтения без токена
    tz: str | None = None       # IANA-имя, например "Europe/Moscow"


def _ensure_db() -> None:
    if not db.is_configured():
        db.configure("sqlite:///:memory:")


def get_user(telegram_id: int) -> BotUser | None:
    _ensure_db()
    u = db.get_user_by_chat(telegram_id)
    if u is None or u.ns_url is None:
        return None
    return BotUser(telegram_id=u.chat_id, ns_url=u.ns_url, ns_token=u.ns_token, tz=u.tz)


def save_user(user: BotUser) -> None:
    _ensure_db()
    db.upsert_user(
        user.telegram_id, clock.now(),
        ns_url=user.ns_url, ns_token=user.ns_token, tz=user.tz, is_active=True,
    )


def get_mode(telegram_id: int) -> ModeState:
    _ensure_db()
    u = db.get_user_by_chat(telegram_id)
    return db.get_mode(u.id) if u is not None else ModeState()


def set_mode(telegram_id: int, state: ModeState) -> None:
    _ensure_db()
    now = clock.now()
    u = db.get_user_by_chat(telegram_id) or db.upsert_user(telegram_id, now)
    db.set_mode(u.id, state, now)


def reset() -> None:
    """Для тестов: очистить всё (новая пустая БД в памяти)."""
    db.configure("sqlite:///:memory:")
