"""
Единые часы. ВСЕ модули берут «сейчас» только отсюда: clock.now().
Никаких datetime.now() в коде — иначе ускоренное демо и тесты разъедутся.

Владелец: Dev 1.

CLOCK=system      — системное UTC (прод).
CLOCK=nightscout  — виртуальное время fake-NS / Nightscout (serverTimeEpoch).
                    pipeline вызывает sync_with_server(ms) после каждого запроса status.json;
                    между синхронизациями время идёт с той же скоростью, что на сервере
                    (--speed 60 → ×60). Скорость часы вычисляют сами по двум синхронизациям.

Режим читается при первом вызове, а не при импорте: так успевает сработать load_dotenv().
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

_MAX_RATE = 10_000.0   # быстрее ×10000 fake-NS не крутят — значит, мусор

_mode: str | None = None              # system | nightscout; None — ещё не прочитан из окружения
_server_ms: int | None = None         # последний serverTimeEpoch
_mono_at_sync: float | None = None    # time.monotonic() в момент этой синхронизации
_rate = 1.0                           # секунд сервера за секунду реального времени
_frozen: datetime | None = None       # для тестов и pipeline.replay
_monotonic = time.monotonic           # подменяется в тестах


def _get_mode() -> str:
    global _mode
    if _mode is None:
        mode = os.getenv("CLOCK", "system").strip().lower()
        if mode not in ("system", "nightscout"):
            raise ValueError(f"CLOCK={mode!r}: допустимо 'system' или 'nightscout'")
        _mode = mode
    return _mode


def now() -> datetime:
    """Текущее время, UTC, с tzinfo."""
    if _frozen is not None:
        return _frozen
    if _get_mode() == "nightscout" and _server_ms is not None and _mono_at_sync is not None:
        elapsed = (_monotonic() - _mono_at_sync) * _rate
        return datetime.fromtimestamp(_server_ms / 1000, timezone.utc) + timedelta(seconds=elapsed)
    # system, либо nightscout до первой синхронизации
    return datetime.now(timezone.utc)


def sync_with_server(server_time_epoch_ms: int) -> None:
    """
    pipeline вызывает после каждого запроса /api/v1/status.json.
    В режиме CLOCK=nightscout часы подстраиваются под виртуальное время fake-NS.
    """
    global _server_ms, _mono_at_sync, _rate
    if _get_mode() != "nightscout":
        return
    mono = _monotonic()
    if _server_ms is not None and _mono_at_sync is not None and mono > _mono_at_sync:
        rate = (server_time_epoch_ms - _server_ms) / 1000 / (mono - _mono_at_sync)
        # время сервера пошло назад (fake-NS перезапустили) — скорость не трогаем
        if 0 < rate <= _MAX_RATE:
            _rate = rate
    _server_ms = int(server_time_epoch_ms)
    _mono_at_sync = mono


def uses_server_time() -> bool:
    """True, если pipeline должен синхронизировать часы с Nightscout."""
    return _get_mode() == "nightscout"


def server_rate() -> float:
    """Во сколько раз время сервера быстрее реального (для логов и отладки демо)."""
    return _rate


# --- для тестов ---

def freeze(at: datetime) -> None:
    global _frozen
    if at.tzinfo is None:
        raise ValueError("freeze: нужен datetime с tzinfo (UTC)")
    _frozen = at


def unfreeze() -> None:
    global _frozen
    _frozen = None


def reset() -> None:
    """Сбросить всё состояние часов и заново прочитать CLOCK при следующем вызове."""
    global _mode, _server_ms, _mono_at_sync, _rate, _frozen, _monotonic
    _mode = None
    _server_ms = None
    _mono_at_sync = None
    _rate = 1.0
    _frozen = None
    _monotonic = time.monotonic
