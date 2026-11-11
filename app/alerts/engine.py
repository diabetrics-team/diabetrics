"""Pure evaluation: all time and data are supplied by the caller."""

from collections.abc import Sequence
from datetime import datetime, timezone
from math import isfinite

from . import config
from .contracts import Alert, AlertLevel, Forecast, Reading


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamps must include a timezone")
    return value.astimezone(timezone.utc)


def _history(history: Sequence[Reading], now: datetime) -> list[Reading]:
    by_time: dict[datetime, Reading] = {}
    for reading in history:
        ts = _utc(reading.ts)
        if not isfinite(reading.value) or reading.value <= 0:
            raise ValueError("Reading.value must be finite and positive, in mmol/L")
        if ts > now:
            continue
        previous = by_time.get(ts)
        if previous is not None and previous.value != reading.value:
            raise ValueError("Conflicting readings at the same timestamp")
        by_time[ts] = Reading(ts=ts, value=reading.value)
    return sorted(by_time.values(), key=lambda reading: reading.ts)


def _sustained(history: Sequence[Reading], threshold: float) -> bool:
    latest = history[-1]
    if latest.value <= threshold:
        return False
    start = latest.ts
    newer = latest
    for reading in reversed(history[:-1]):
        if (reading.value <= threshold
                or newer.ts - reading.ts > config.MAX_READING_GAP):
            break
        start = reading.ts
        newer = reading
    return latest.ts - start >= config.HIGH_DURATION


def evaluate(
    history: Sequence[Reading],
    forecast: Forecast | None,
    mode: object,
    now: datetime,
    recent_alerts: Sequence[Alert],
) -> list[Alert]:
    """Return current rule matches without changing any arguments.

    Day 2: mode and recent_alerts are reserved for the later mode/anti-spam work.
    No modes suppress alerts yet. Calling twice returns the same matches; the
    caller must not deliver each polling result as a new message without dedup.
    Empty history gives [] because no monitoring-start timestamp is available.
    Forecast.rate must refer to this history's latest reading (caller contract).
    """
    now = _utc(now)
    readings = _history(history, now)
    if not readings:
        return []
    latest = readings[-1]
    age = now - latest.ts

    def alert(kind: str, level: AlertLevel | None = None) -> Alert:
        return Alert(kind=kind, level=level or config.LEVELS[kind],
                     value=latest.value, ts=now)

    if age >= config.NO_DATA_IMPORTANT:
        critical = age >= config.NO_DATA_CRITICAL and latest.value < config.LOW
        level = AlertLevel.CRITICAL if critical else AlertLevel.IMPORTANT
        return [alert("no_data", level)]

    result = []
    if latest.value < config.VERY_LOW:
        result.append(alert("very_low"))
    elif latest.value < config.LOW:
        result.append(alert("low"))

    if _sustained(readings, config.VERY_HIGH):
        result.append(alert("very_high_1h"))
    elif _sustained(readings, config.HIGH):
        result.append(alert("high_1h"))

    if forecast is not None and isfinite(forecast.rate):
        if forecast.rate < -config.FAST_RATE:
            result.append(alert("fast_drop"))
        elif forecast.rate > config.FAST_RATE:
            result.append(alert("fast_rise"))
    return result
