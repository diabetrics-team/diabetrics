"""
Движок уведомлений: решает, какие уведомления нужны прямо сейчас.

Владелец: Dev 2. ЗАГЛУШКА: только low / very_low по последнему показанию, без режимов.
Должна остаться ЧИСТОЙ функцией: без БД, без Telegram, без clock.now() внутри —
время приходит параметром now. Тогда она тестируется на fake_cgm/data/*.json.
"""
from __future__ import annotations

from datetime import datetime

from app.models import Alert, AlertKind, Forecast, Level, ModeState, Reading

LOW = 3.9
VERY_LOW = 3.0


def evaluate(
    history: list[Reading],
    forecast: Forecast | None,
    mode: ModeState,
    now: datetime,
    recent_alerts: list[Alert],
) -> list[Alert]:
    """
    history — показания за последние ~2 часа, от старых к новым
    forecast — результат predict(), может быть None
    mode — текущий режим пользователя (учитывать mode.active(now))
    now — текущее время, UTC
    recent_alerts — уведомления этого пользователя за последний час (для антиспама)
    """
    if not history:
        return []
    v = history[-1].mmol
    if v < VERY_LOW:
        return [Alert(Level.CRITICAL, AlertKind.VERY_LOW, now, value=v, needs_ack=True)]
    if v < LOW:
        return [Alert(Level.IMPORTANT, AlertKind.LOW, now, value=v)]
    return []
