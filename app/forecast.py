"""
Прогноз сахара на 20 и 30 минут.

Владелец: Dev 4. ЗАГЛУШКА: наклон по двум крайним точкам за 15 мин.
Заменить на: линейная регрессия (numpy.polyfit) по 4 точкам, None при разрыве или выбросе.
Сигнатуру не менять — на неё завязаны pipeline (Dev 1) и движок (Dev 2).
"""
from __future__ import annotations

from datetime import timedelta

from app.models import Forecast, Reading

WINDOW = timedelta(minutes=15)


def predict(history: list[Reading]) -> Forecast | None:
    """history — от старых к новым. None, если прогноз построить нельзя."""
    if len(history) < 2:
        return None
    last = history[-1]
    window = [r for r in history if last.ts - r.ts <= WINDOW]
    if len(window) < 2:
        return None
    minutes = (window[-1].ts - window[0].ts).total_seconds() / 60
    if minutes <= 0:
        return None
    rate = (window[-1].mmol - window[0].mmol) / minutes
    return Forecast(
        mmol_20=round(last.mmol + rate * 20, 2),
        mmol_30=round(last.mmol + rate * 30, 2),
        rate=round(rate, 3),
    )
