"""
Метрики за день: TIR (Time in Range), средний сахар, число гипо.

Владелец: Dev 4. Используется для команды /stats в боте.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.models import Reading

LOW = 3.9
HIGH = 10.0


@dataclass(frozen=True)
class DailyMetrics:
    tir_percent: float        # % времени в диапазоне 3.9–10.0
    average_mmol: float       # средний сахар
    hypo_count: int           # число эпизодов гипо (ниже 3.9)
    total_readings: int       # сколько всего показаний учтено


def calculate_daily(readings: list[Reading], now: datetime) -> DailyMetrics | None:
    """
    Считает метрики за последние 24 часа от now.
    Если данных нет или меньше 10 точек — возвращает None.
    """
    start_time = now - timedelta(hours=24)
    day_readings = [r for r in readings if start_time <= r.ts <= now]

    if len(day_readings) < 10:
        return None

    total = len(day_readings)
    in_range = sum(1 for r in day_readings if LOW <= r.mmol <= HIGH)
    tir = round((in_range / total) * 100, 1)
    avg = round(sum(r.mmol for r in day_readings) / total, 1)

    # Считаем эпизоды гипо (если сахар упал ниже 3.9 и потом вернулся выше)
    hypo_count = 0
    in_hypo = False
    for r in day_readings:
        if r.mmol < LOW:
            if not in_hypo:
                hypo_count += 1
                in_hypo = True
        else:
            in_hypo = False

    return DailyMetrics(
        tir_percent=tir,
        average_mmol=avg,
        hypo_count=hypo_count,
        total_readings=total,
    )