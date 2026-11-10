"""
Единые часы. ВСЕ модули берут «сейчас» только отсюда: clock.now().
Никаких datetime.now() в коде — иначе ускоренное демо и тесты разъедутся.

Владелец: Dev 1.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

_MODE = os.getenv("CLOCK", "system")      # system | nightscout
_offset = timedelta(0)                    # для CLOCK=nightscout
_frozen: datetime | None = None           # для тестов


def now() -> datetime:
    # Текущее время, UTC 
    if _frozen is not None:
        return _frozen
    return datetime.now(timezone.utc) + _offset


def sync_with_server(server_time_epoch_ms: int) -> None:
    """
    pipeline вызывает после каждого запроса /api/v1/status.json.
    В режиме CLOCK=nightscout часы подстраиваются под виртуальное время fake-NS.
    """
    global _offset
    if _MODE != "nightscout":
        return
    server = datetime.fromtimestamp(server_time_epoch_ms / 1000, timezone.utc)
    _offset = server - datetime.now(timezone.utc)


# --- для тестов ---

def freeze(at: datetime) -> None:
    global _frozen
    _frozen = at


def unfreeze() -> None:
    global _frozen
    _frozen = None
