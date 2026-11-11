"""
ВРЕМЕННОЕ хранилище бота в памяти: подключение к Nightscout, часовой пояс, режим.

Владелец: Dev 3. Всё стирается при перезапуске бота.
Когда Dev 1 выложит БД (app/db.py), заменить импорт `from app.bot import storage`
на функции БД. Имена get_mode / set_mode — как в TODO v2 у Dev 1 (день 3);
get_user / save_user — согласовать с Dev 1.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.models import ModeState


@dataclass(slots=True)
class BotUser:
    telegram_id: int            # в личном чате совпадает с chat_id
    ns_url: str
    ns_token: str | None        # None — Nightscout открыт для чтения без токена
    tz: str | None = None       # IANA-имя, например "Europe/Moscow"


_users: dict[int, BotUser] = {}
_modes: dict[int, ModeState] = {}


def get_user(telegram_id: int) -> BotUser | None:
    return _users.get(telegram_id)


def save_user(user: BotUser) -> None:
    _users[user.telegram_id] = user


def get_mode(telegram_id: int) -> ModeState:
    return _modes.get(telegram_id, ModeState())


def set_mode(telegram_id: int, state: ModeState) -> None:
    _modes[telegram_id] = state


def reset() -> None:
    """Для тестов: очистить всё."""
    _users.clear()
    _modes.clear()
