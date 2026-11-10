"""
Очистка сырого ответа Nightscout → список Reading.

Владелец: Dev 1. ЗАГЛУШКА, но рабочая: уже проходит сценарий dirty_data.
Что доделать: логировать выброшенные записи, учесть часовые сдвиги загрузчиков.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.models import Reading

MGDL_PER_MMOL = 18.0
MIN_VALID_SGV = 40          # меньше — коды ошибок датчика
DUPLICATE_WINDOW_MS = 150_000   # два показания ближе 2.5 мин = одно и то же


def clean(raw: list[dict]) -> list[Reading]:
    # Только type == "sgv", sgv >= 40, без дублей, от старых к новым, в ммоль/л
    out: list[Reading] = []
    last_ms: int | None = None
    for e in sorted(raw, key=lambda e: e.get("date", 0)):
        if e.get("type") != "sgv" or e.get("sgv", 0) < MIN_VALID_SGV:
            continue
        if last_ms is not None and e["date"] - last_ms < DUPLICATE_WINDOW_MS:
            continue
        direction = e.get("direction")
        out.append(Reading(
            ts=datetime.fromtimestamp(e["date"] / 1000, timezone.utc),
            mmol=round(e["sgv"] / MGDL_PER_MMOL, 2),
            direction=None if direction in (None, "NOT COMPUTABLE", "NONE") else direction,
        ))
        last_ms = e["date"]
    return out
