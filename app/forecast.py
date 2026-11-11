"""
Прогноз сахара на 20 и 30 минут.

Владелец: Dev 4.
Линейная регрессия (МНК) по точкам за последние WINDOW минут.
Возвращает None, если:
  - меньше MIN_POINTS точек в окне;
  - в окне есть разрыв больше GAP_MIN;
  - в окне есть физиологически невозможный выброс (> ARTIFACT_RATE ммоль/л/мин).

Сигнатуру predict(history) не менять — на неё завязаны pipeline (Dev 1) и движок (Dev 2).
"""
from __future__ import annotations

from datetime import timedelta

from app.models import Forecast, Reading

WINDOW = timedelta(minutes=15)
MIN_POINTS = 4
GAP_MIN = timedelta(minutes=10)
ARTIFACT_RATE = 0.5  # ммоль/л/мин — быстрее не бывает физиологически


def _slope(points: list[Reading]) -> float:
    """МНК-наклон, ммоль/л/мин. points — от старых к новым, len >= 2."""
    x0 = points[0].ts
    xs = [(p.ts - x0).total_seconds() / 60 for p in points]
    ys = [p.mmol for p in points]
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return 0.0
    return num / den


def _has_artifact(points: list[Reading]) -> bool:
    """Скачок туда-обратно быстрее ARTIFACT_RATE — это шум датчика, не тренд."""
    for a, b, c in zip(points, points[1:], points[2:]):
        dt1 = (b.ts - a.ts).total_seconds() / 60
        dt2 = (c.ts - b.ts).total_seconds() / 60
        if dt1 <= 0 or dt2 <= 0:
            continue
        r1 = (b.mmol - a.mmol) / dt1
        r2 = (c.mmol - b.mmol) / dt2
        if abs(r1) > ARTIFACT_RATE and abs(r2) > ARTIFACT_RATE and r1 * r2 < 0:
            return True
    return False


def predict(history: list[Reading]) -> Forecast | None:
    """history — от старых к новым. None, если прогноз построить нельзя."""
    if len(history) < MIN_POINTS:
        return None

    last = history[-1]
    window = [r for r in history if last.ts - r.ts <= WINDOW]
    if len(window) < MIN_POINTS:
        return None

    # разрыв внутри окна
    for a, b in zip(window, window[1:]):
        if b.ts - a.ts > GAP_MIN:
            return None

    if _has_artifact(window):
        return None

    rate = _slope(window)
    return Forecast(
        mmol_20=round(last.mmol + rate * 20, 2),
        mmol_30=round(last.mmol + rate * 30, 2),
        rate=round(rate, 3),
    )