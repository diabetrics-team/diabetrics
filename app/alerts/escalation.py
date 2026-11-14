"""Escalation decisions and confirmation for critical alerts."""
from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from datetime import datetime, timedelta

from app import clock
from app.models import Action, ActionType, Alert, AlertKind, Level

from .config import REPEAT_AFTER, TRUSTED_AFTER_MINUTES


LOW_ESCALATION_KINDS = frozenset({AlertKind.LOW, AlertKind.VERY_LOW, AlertKind.STILL_LOW})


def escalation_step(
    open_alerts: Sequence[Alert],
    now: datetime,
    *,
    repeated_alert_ids: Collection[int] = (),
    trusted_after_minutes: int = TRUSTED_AFTER_MINUTES,
    low_recovered_at: datetime | None = None,
    low_episode_escalated: bool = False,
) -> list[Action]:
    """Return due actions. The caller records successful delivery before the next tick.

    Persist the sent repeat in repeated_alert_ids (loaded from storage each tick).
    After notifying a trusted contact, persist Alert.escalated_at. Acknowledged
    or escalated alerts produce no further actions.
    """
    trusted_after = timedelta(minutes=trusted_after_minutes)
    if trusted_after <= REPEAT_AFTER:
        raise ValueError("Trusted-contact delay must be greater than the repeat delay")

    actions = []
    for alert in sorted(open_alerts, key=lambda item: item.created_at):
        if (
            alert.id is None or alert.level is not Level.CRITICAL or not alert.needs_ack
            or alert.kind is AlertKind.PREDICTED_LOW
            or alert.acked_at is not None or alert.escalated_at is not None
        ):
            continue
        is_low = alert.kind in LOW_ESCALATION_KINDS
        if is_low and low_recovered_at is not None and alert.created_at <= low_recovered_at:
            continue
        age = now - alert.created_at
        if age >= trusted_after:
            if is_low and low_episode_escalated:
                continue
            if is_low:
                low_episode_escalated = True
            actions.append(Action(
                ActionType.NOTIFY_TRUSTED, alert,
                {"minutes_without_ack": age // timedelta(minutes=1)},
            ))
        elif age >= REPEAT_AFTER and alert.id not in repeated_alert_ids:
            actions.append(Action(ActionType.REPEAT, alert))
    return actions


def ack(
    alert_id: int | Alert,
    who: int,
    now: datetime | None = None,
    *,
    mark_ack: Callable[[int, int, datetime], Alert | None] | None = None,
) -> Alert | None:
    """Confirm by ID through the database, or update an already authorized alert.

    The database checks that who is the owner or a confirmed trusted contact
    and atomically saves the first confirmation. mark_ack is a test adapter.
    The loaded-Alert form is for callers that have already checked access.
    """
    at = clock.now() if now is None else now
    if isinstance(alert_id, Alert):
        if alert_id.needs_ack and alert_id.acked_at is None:
            alert_id.acked_at = at
            alert_id.acked_by = who
        return alert_id
    if mark_ack is not None:
        return mark_ack(alert_id, who, at)

    from app import db

    return db.get_alert(alert_id) if db.mark_ack(alert_id, who, at) else None
