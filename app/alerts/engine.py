"""Pure evaluation: all time and data are supplied by the caller."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timezone
from math import isfinite

from app.models import Alert, AlertKind, Forecast, Level, ModeState, Reading

from . import config


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamps must include a timezone")
    return value.astimezone(timezone.utc)


def _history(history: Sequence[Reading], now: datetime) -> list[Reading]:
    by_time: dict[datetime, Reading] = {}
    for reading in history:
        ts = _utc(reading.ts)
        if not isfinite(reading.mmol) or reading.mmol <= 0:
            raise ValueError("Reading.mmol must be finite and positive, in mmol/L")
        if ts > now:
            continue
        previous = by_time.get(ts)
        if previous is not None and previous.mmol != reading.mmol:
            raise ValueError("Conflicting readings at the same timestamp")
        by_time[ts] = reading if reading.ts.tzinfo is timezone.utc else replace(reading, ts=ts)
    return sorted(by_time.values(), key=lambda reading: reading.ts)


def _sustained(history: Sequence[Reading], threshold: float) -> bool:
    latest = history[-1]
    if latest.mmol <= threshold:
        return False
    start = latest.ts
    newer = latest
    for reading in reversed(history[:-1]):
        if reading.mmol <= threshold or newer.ts - reading.ts > config.MAX_READING_GAP:
            break
        start = reading.ts
        newer = reading
    return latest.ts - start >= config.HIGH_DURATION


def evaluate(
    history: Sequence[Reading],
    forecast: Forecast | None,
    mode: ModeState,
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

    def alert(kind: AlertKind, level: Level | None = None) -> Alert:
        severity = config.LEVELS[kind] if level is None else level
        return Alert(
            kind=kind,
            level=severity,
            value=latest.mmol,
            created_at=now,
            needs_ack=severity is Level.CRITICAL,
        )

    if age >= config.NO_DATA_IMPORTANT:
        critical = age >= config.NO_DATA_CRITICAL and latest.mmol < config.LOW
        level = Level.CRITICAL if critical else Level.IMPORTANT
        return [alert(AlertKind.NO_DATA, level)]

    result = []
    if latest.mmol < config.VERY_LOW:
        result.append(alert(AlertKind.VERY_LOW))
    elif latest.mmol < config.LOW:
        result.append(alert(AlertKind.LOW))

    if _sustained(readings, config.VERY_HIGH):
        result.append(alert(AlertKind.VERY_HIGH_1H))
    elif _sustained(readings, config.HIGH):
        result.append(alert(AlertKind.HIGH_1H))

    if forecast is not None and isfinite(forecast.rate):
        if forecast.rate < -config.FAST_RATE:
            result.append(alert(AlertKind.FAST_DROP))
        elif forecast.rate > config.FAST_RATE:
            result.append(alert(AlertKind.FAST_RISE))
    return result
