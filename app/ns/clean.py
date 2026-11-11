"""
Очистка сырого ответа Nightscout → список Reading.

Владелец: Dev 1.

1. только type == "sgv" (записи глюкометра "mbg", калибровки — мимо);
2. sgv — число ≥ 40 mg/dL (меньше — коды ошибок датчика, например 5);
3. время из "date" (мс, UTC), если его нет — из "dateString";
4. от старых к новым;
5. два показания ближе DUPLICATE_WINDOW_MS — одно и то же (второй загрузчик, +8 с).
   Оставляем первое; если у него нет direction, а у дубля есть — берём direction дубля;
6. mg/dL → ммоль/л: sgv / 18, округление до 0.01.
Битая запись (нет времени, sgv не число, вообще не словарь) пропускается, а не роняет pipeline.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.models import Reading

log = logging.getLogger(__name__)

MGDL_PER_MMOL = 18.0
MIN_VALID_SGV = 40              # меньше — коды ошибок датчика
DUPLICATE_WINDOW_MS = 150_000   # два показания ближе 2.5 мин = одно и то же
NO_DIRECTION = (None, "", "NOT COMPUTABLE", "NONE")


def _date_ms(e: dict) -> int | None:
    d = e.get("date")
    if isinstance(d, (int, float)) and not isinstance(d, bool) and d > 0:
        return int(d)
    ds = e.get("dateString")
    if isinstance(ds, str) and ds:
        try:
            dt = datetime.fromisoformat(ds.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:            # без пояса — UTC, как в Nightscout
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    return None


def _valid(e: Any) -> tuple[int, dict] | None:
    if not isinstance(e, dict) or e.get("type") != "sgv":
        return None
    sgv = e.get("sgv")
    if isinstance(sgv, bool) or not isinstance(sgv, (int, float)) or sgv < MIN_VALID_SGV:
        return None
    ms = _date_ms(e)
    return (ms, e) if ms is not None else None


def clean(raw: list[dict]) -> list[Reading]:
    # Только type == "sgv", sgv >= 40, без дублей, от старых к новым, в ммоль/л
    entries = sorted((v for v in map(_valid, raw) if v is not None), key=lambda v: v[0])
    dropped = len(raw) - len(entries)

    out: list[Reading] = []
    last_ms: int | None = None
    for ms, e in entries:
        direction = e.get("direction")
        direction = None if direction in NO_DIRECTION else direction
        if last_ms is not None and ms - last_ms < DUPLICATE_WINDOW_MS:
            dropped += 1
            prev = out[-1]
            if prev.direction is None and direction is not None:
                out[-1] = Reading(ts=prev.ts, mmol=prev.mmol, direction=direction)
            continue
        out.append(Reading(
            ts=datetime.fromtimestamp(ms / 1000, timezone.utc),
            mmol=round(e["sgv"] / MGDL_PER_MMOL, 2),
            direction=direction,
        ))
        last_ms = ms
    if dropped:
        log.debug("clean: отброшено %d из %d записей", dropped, len(raw))
    return out
