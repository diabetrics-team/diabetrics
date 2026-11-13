"""Pure evaluation: all time and data are supplied by the caller."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timezone
from math import isclose, isfinite

from app.models import Alert, AlertKind, Forecast, Level, Mode, ModeState, Reading

from . import antispam, config


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


def _confirmed_readings(readings: Sequence[Reading]) -> list[Reading]:
    """A large jump is accepted if the next point is closer to it than to baseline."""
    confirmed = [readings[0]]
    pending = None
    for reading in readings[1:]:
        baseline = confirmed[-1]
        if pending is not None:
            if (
                reading.ts - pending.ts <= config.MAX_READING_GAP
                and abs(reading.mmol - pending.mmol) < abs(reading.mmol - baseline.mmol)
            ):
                confirmed.append(pending)
                baseline = pending
            pending = None
        elapsed = reading.ts - baseline.ts
        if elapsed <= config.MAX_READING_GAP:
            rate = abs(reading.mmol - baseline.mmol) / (elapsed.total_seconds() / 60)
            if rate > config.ARTIFACT_RATE and not isclose(rate, config.ARTIFACT_RATE, rel_tol=1e-12):
                pending = reading
                continue
        confirmed.append(reading)
    return confirmed


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


def _visible(alert: Alert, active_mode: Mode) -> bool:
    if alert.level is Level.CRITICAL:
        return True
    if active_mode is Mode.DND:
        return False
    if active_mode is Mode.WORK:
        return alert.level is not Level.INFO
    if active_mode is Mode.AFTER_MEAL:
        return alert.kind not in (AlertKind.HIGH_1H, AlertKind.FAST_RISE)
    if active_mode is Mode.AFTER_HYPO:
        return alert.kind is not AlertKind.LOW
    return True


def history_start_for_hysteresis(
    history: Sequence[Reading], recent_alerts: Sequence[Alert], now: datetime,
) -> datetime | None:
    received = _history(history, now)
    if not received or now - received[-1].ts >= config.NO_DATA_IMPORTANT:
        return None
    return antispam.history_start(_confirmed_readings(received), recent_alerts)


def evaluate(
    history: Sequence[Reading],
    forecast: Forecast | None,
    mode: ModeState,
    now: datetime,
    recent_alerts: Sequence[Alert],
) -> list[Alert]:
    """Evaluate confirmed readings using the caller's forecast and clock.

    recent_alerts includes cooldown history, open critical alerts, and the last
    alert at each glucose episode severity, even if older than the cooldown.
    """
    now = _utc(now)
    received = _history(history, now)
    if not received:
        return []
    readings = _confirmed_readings(received)
    latest = readings[-1]
    age = now - received[-1].ts
    active_mode = mode.active(now)

    def alert(kind: AlertKind, level: Level | None = None, *, value: float = latest.mmol) -> Alert:
        severity = config.LEVELS[kind] if level is None else level
        return Alert(
            kind=kind,
            level=severity,
            value=value,
            created_at=now,
            needs_ack=severity is Level.CRITICAL,
        )

    def deliverable(item: Alert) -> bool:
        return _visible(item, active_mode) and not antispam.is_duplicate(
            item, recent_alerts, readings, received[-1].ts, now,
        )

    if age >= config.NO_DATA_IMPORTANT:
        critical = age >= config.NO_DATA_CRITICAL and latest.mmol < config.LOW
        level = Level.CRITICAL if critical else Level.IMPORTANT
        missing = alert(AlertKind.NO_DATA, level)
        return [missing] if deliverable(missing) else []

    result = []
    until = mode.until
    recheck_due = (
        mode.kind is Mode.AFTER_HYPO
        and until is not None
        and readings[0].ts <= until <= now
    )
    waiting_or_rechecking = False
    if recheck_due and until is not None:
        recheck = next((reading for reading in readings if reading.ts >= until), None)
        waiting_or_rechecking = recheck is None or recheck.ts == latest.ts
        if (
            recheck is not None
            and recheck.ts == latest.ts
            and recheck.mmol < config.LOW
            and not any(a.kind is AlertKind.STILL_LOW and a.created_at >= until for a in recent_alerts)
        ):
            result.append(alert(AlertKind.STILL_LOW))

    if latest.mmol < config.VERY_LOW:
        result.append(alert(AlertKind.VERY_LOW))
    elif latest.mmol < config.LOW and not waiting_or_rechecking:
        level = Level.CRITICAL if active_mode is Mode.NIGHT else None
        result.append(alert(AlertKind.LOW, level))

    if _sustained(readings, config.VERY_HIGH):
        result.append(alert(AlertKind.VERY_HIGH_1H))
    elif _sustained(readings, config.HIGH):
        result.append(alert(AlertKind.HIGH_1H))

    if forecast is not None and len(readings) != len(received):
        # A forecast made from an outlier in the 15-minute window is invalid.
        forecast_start = received[-1].ts - config.FORECAST_WINDOW
        kept = {reading.ts for reading in readings}
        if any(raw.ts >= forecast_start and raw.ts not in kept for raw in received):
            forecast = None
    if forecast is not None:
        if isfinite(forecast.rate):
            if forecast.rate < -config.FAST_RATE:
                result.append(alert(AlertKind.FAST_DROP))
            elif forecast.rate > config.FAST_RATE:
                result.append(alert(AlertKind.FAST_RISE))
        if isfinite(forecast.mmol_20) and forecast.mmol_20 < config.VERY_LOW:
            level = Level.CRITICAL if latest.mmol < config.PREDICTED_LOW_CRITICAL_BELOW else Level.IMPORTANT
            result.append(alert(AlertKind.PREDICTED_LOW, level, value=forecast.mmol_20))
    if active_mode is not Mode.AFTER_HYPO and not waiting_or_rechecking and antispam.recovered(
        readings, recent_alerts, now,
    ):
        result.append(alert(AlertKind.BACK_IN_RANGE, Level.INFO))
    return [item for item in result if deliverable(item)]
