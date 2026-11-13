"""Cooldowns and glucose episodes derived from confirmed readings and sent alerts."""

from collections.abc import Sequence
from datetime import datetime

from app.models import Alert, AlertKind, Level, Reading

from . import config

LOW_KINDS = frozenset({AlertKind.LOW, AlertKind.VERY_LOW})
HIGH_KINDS = frozenset({AlertKind.HIGH_1H, AlertKind.VERY_HIGH_1H})
EPISODE_KINDS = LOW_KINDS | HIGH_KINDS | {AlertKind.NO_DATA}
_RANK = {Level.INFO: 0, Level.IMPORTANT: 1, Level.CRITICAL: 2}
_SEVERE_KINDS = {AlertKind.VERY_LOW, AlertKind.VERY_HIGH_1H}


def _reset_at(readings: Sequence[Reading], kinds: frozenset[AlertKind]) -> datetime | None:
    if kinds == LOW_KINDS:
        return next((r.ts for r in reversed(readings) if r.mmol > config.LOW_RESET), None)
    return next((r.ts for r in reversed(readings) if r.mmol < config.HIGH_RESET), None)


def history_start(readings: Sequence[Reading], sent: Sequence[Alert]) -> datetime | None:
    """Request earlier readings only if an older glucose episode might still be open."""
    latest = readings[-1]
    if latest.mmol < config.LOW:
        candidates = (LOW_KINDS,)
    elif latest.mmol > config.HIGH:
        candidates = (HIGH_KINDS,)
    elif config.LOW_RESET < latest.mmol < config.HIGH_RESET:
        candidates = (LOW_KINDS, HIGH_KINDS)
    else:
        return None
    unresolved = set().union(*(kinds for kinds in candidates if _reset_at(readings[:-1], kinds) is None))
    oldest = min((a.created_at for a in sent if a.kind in unresolved and a.created_at < readings[0].ts), default=None)
    return None if oldest is None else oldest - 2 * config.MAX_READING_GAP


def recovered(readings: Sequence[Reading], sent: Sequence[Alert], now: datetime) -> bool:
    latest = readings[-1]
    if not config.LOW_RESET < latest.mmol < config.HIGH_RESET:
        return False
    if any(a.kind is AlertKind.BACK_IN_RANGE and latest.ts <= a.created_at <= now for a in sent):
        return False
    earlier = readings[:-1]
    for kinds in (LOW_KINDS, HIGH_KINDS):
        reset = _reset_at(earlier, kinds)
        if any(
            a.kind in kinds and a.created_at <= now and (reset is None or a.created_at >= reset)
            for a in sent
        ):
            return True
    return False


def is_duplicate(
    alert: Alert,
    sent: Sequence[Alert],
    readings: Sequence[Reading],
    last_received_at: datetime,
    now: datetime,
) -> bool:
    if alert.kind is AlertKind.BACK_IN_RANGE:
        return False  # recovered() already ties it to the reset reading

    family = next((kinds for kinds in (LOW_KINDS, HIGH_KINDS) if alert.kind in kinds), None)
    reset = None if family is None else _reset_at(readings, family)
    for previous in sent:
        if previous.created_at > now or _RANK[previous.level] < _RANK[alert.level]:
            continue
        if family is not None:
            # A night-mode LOW can already be critical, but VERY_LOW is a new threshold.
            if alert.kind in _SEVERE_KINDS and previous.kind != alert.kind:
                continue
            if previous.kind in family and (reset is None or previous.created_at >= reset):
                return True
            continue
        if previous.kind != alert.kind:
            continue
        if alert.kind is AlertKind.NO_DATA:
            if last_received_at >= previous.created_at:
                continue
            if previous.level is Level.CRITICAL:
                return True  # one escalation chain per signal outage
        if previous.needs_ack and previous.acked_at is None and previous.escalated_at is None:
            return True
        if now - previous.created_at < config.ALERT_COOLDOWN:
            return True
    return False
