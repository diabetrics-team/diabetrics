"""Thursday rules, CGM fixtures, and escalation lifecycle."""

import json
import unittest
from datetime import UTC, datetime, timedelta, timezone
from functools import partial
from pathlib import Path

from app.alerts import evaluate
from app.alerts.escalation import ack, escalation_step
from app.forecast import predict
from app.models import ActionType, Alert, AlertKind, Forecast, Level, Mode, ModeState, Reading
from app.ns.clean import clean

DATA = Path(__file__).resolve().parents[1] / "fake_cgm" / "data"
NOW = datetime(2026, 11, 12, 12, tzinfo=UTC)
MSK = timezone(timedelta(hours=3))


def load(name):
    readings = clean(json.loads((DATA / f"{name}.json").read_text(encoding="utf-8")))
    facts = json.loads((DATA / "scenarios.json").read_text(encoding="utf-8"))[name]["facts"]
    return readings, facts


def replay(readings, mode):
    sent = []
    for i, reading in enumerate(readings, 1):
        history = readings[:i]
        sent.extend(evaluate(history, predict(history), mode, reading.ts, sent))
    return sent


class ArtifactTests(unittest.TestCase):
    def test_noise_spike_has_no_alerts(self):
        history, facts = load("noise_spike")
        self.assertTrue(facts["artifacts"])
        self.assertEqual(replay(history, ModeState()), [])

    def test_jump_waits_for_confirmation(self):
        history = [Reading(NOW - timedelta(minutes=10), 6.5),
                   Reading(NOW - timedelta(minutes=5), 2.8), Reading(NOW, 2.9)]
        self.assertEqual(evaluate(history[:2], Forecast(1, 1, -0.5), ModeState(), history[1].ts, []), [])
        [alert] = evaluate(history, None, ModeState(), NOW, [])
        self.assertIs(alert.kind, AlertKind.VERY_LOW)
        self.assertTrue(alert.needs_ack)

    def test_rejected_point_invalidates_overlapping_forecast(self):
        history = [Reading(NOW - timedelta(minutes=10), 6.5),
                   Reading(NOW - timedelta(minutes=5), 2.8), Reading(NOW, 6.5)]
        self.assertEqual(evaluate(history, Forecast(1, 1, -0.4), ModeState(), NOW, []), [])

    def test_jump_at_threshold_is_accepted(self):
        history = [Reading(NOW - timedelta(minutes=5), 5.4), Reading(NOW, 2.9)]
        [alert] = evaluate(history, None, ModeState(), NOW, [])
        self.assertIs(alert.kind, AlertKind.VERY_LOW)


class ForecastTests(unittest.TestCase):
    def test_level_depends_on_current_glucose_and_value_is_prediction(self):
        for value, level in [(4.99, Level.CRITICAL), (5.0, Level.IMPORTANT), (5.1, Level.IMPORTANT)]:
            with self.subTest(value=value):
                [alert] = evaluate([Reading(NOW, value)], Forecast(2.9, 2.8, 0), ModeState(), NOW, [])
                self.assertIs(alert.kind, AlertKind.PREDICTED_LOW)
                self.assertIs(alert.level, level)
                self.assertEqual(alert.value, 2.9)
                self.assertFalse(alert.needs_ack)

    def test_prediction_exactly_at_three_is_not_low(self):
        self.assertEqual(evaluate([Reading(NOW, 5.0)], Forecast(3.0, 2.5, 0), ModeState(), NOW, []), [])


class ModeTests(unittest.TestCase):
    def test_night_upgrades_low_and_dnd_suppresses_important(self):
        history = [Reading(NOW, 3.5)]
        for mode, expected in [(Mode.NORMAL, Level.IMPORTANT), (Mode.NIGHT, Level.CRITICAL),
                               (Mode.WORK, Level.IMPORTANT), (Mode.DND, None)]:
            with self.subTest(mode=mode):
                alerts = evaluate(history, None, ModeState(mode), NOW, [])
                self.assertEqual([a.level for a in alerts], [] if expected is None else [expected])
                if alerts:
                    self.assertEqual(alerts[0].needs_ack, expected is Level.CRITICAL)

    def test_night_suppresses_noncritical_alerts(self):
        low_at = NOW - timedelta(minutes=5)
        prior_low = Alert(Level.CRITICAL, AlertKind.LOW, low_at, 3.5, True)
        cases = [
            ([Reading(low_at, 3.5), Reading(NOW, 5.0)], None, [prior_low], AlertKind.BACK_IN_RANGE),
            ([Reading(NOW, 6.0)], Forecast(2.9, 2.8, 0), [], AlertKind.PREDICTED_LOW),
        ]
        for history, forecast, recent, kind in cases:
            with self.subTest(kind=kind):
                normal = evaluate(history, forecast, ModeState(), NOW, recent)
                self.assertEqual([alert.kind for alert in normal], [kind])
                self.assertEqual(evaluate(history, forecast, ModeState(Mode.NIGHT), NOW, recent), [])

    def test_night_notifies_of_sensor_loss_after_twenty_minutes(self):
        readings, facts = load("sensor_lost")
        mode = ModeState(Mode.NIGHT)
        last = readings[-1].ts
        self.assertEqual(evaluate(readings, None, mode, last + timedelta(minutes=19), []), [])
        now = last + timedelta(minutes=20)
        [alert] = evaluate(readings, None, mode, now, [])
        self.assertIs(alert.kind, AlertKind.NO_DATA)
        self.assertIs(alert.level, Level.IMPORTANT)
        self.assertEqual(alert.created_at.astimezone(MSK).strftime("%H:%M"),
                         facts["gaps"][0]["no_data_20min_at"])
        self.assertFalse(alert.needs_ack)
        self.assertEqual(evaluate(readings, None, ModeState(Mode.DND), now, []), [])

    def test_critical_alerts_are_visible_in_each_mode(self):
        for mode in Mode:
            with self.subTest(mode=mode):
                [alert] = evaluate([Reading(NOW, 2.9)], None, ModeState(mode), NOW, [])
                self.assertIs(alert.kind, AlertKind.VERY_LOW)
                self.assertTrue(alert.needs_ack)

    def test_meal_overbolus_keeps_low_and_suppresses_only_meal_kinds(self):
        readings, facts = load("meal_overbolus")
        until = readings[-1].ts + timedelta(minutes=1)
        normal = replay(readings, ModeState())
        after_meal = replay(readings, ModeState(Mode.AFTER_MEAL, until))
        muted = {AlertKind.FAST_RISE, AlertKind.HIGH_1H}
        self.assertTrue(muted <= {a.kind for a in normal})
        self.assertTrue(muted.isdisjoint(a.kind for a in after_meal))
        self.assertEqual(
            [a for a in after_meal if a.level is not Level.INFO],
            [a for a in normal if a.kind not in muted and a.level is not Level.INFO],
        )
        first_low = next(a for a in after_meal if a.kind is AlertKind.LOW)
        self.assertEqual(first_low.created_at.astimezone(MSK).strftime("%H:%M"), facts["first_below_3_9"])

    def test_after_meal_expires(self):
        history = [Reading(NOW, 7)]
        forecast = Forecast(11, 13, 0.2)
        state = ModeState(Mode.AFTER_MEAL, NOW)
        [alert] = evaluate(history, forecast, state, NOW, [])
        self.assertIs(alert.kind, AlertKind.FAST_RISE)

    def test_hypo_corrected_and_not_corrected(self):
        for name, expected in [("hypo_corrected", False), ("hypo_not_corrected", True)]:
            with self.subTest(name=name):
                readings, _ = load(name)
                # Simulate enabling the mode at 12:40, five minutes after the first LOW.
                i = next(i for i, r in enumerate(readings) if r.ts.astimezone(MSK).strftime("%H:%M") == "12:40")
                until = readings[i].ts + timedelta(minutes=15)
                state = ModeState(Mode.AFTER_HYPO, until)
                sent = []
                for j in range(i + 1, len(readings)):
                    ts = readings[j].ts
                    new = evaluate(readings[:j + 1], None, state, ts, sent)
                    if ts < until:
                        self.assertEqual(new, [])
                    sent.extend(new)
                still_low = [a for a in sent if a.kind is AlertKind.STILL_LOW]
                self.assertEqual(len(still_low), int(expected))
                if expected:
                    self.assertEqual(still_low[0].created_at, until)
                    self.assertIs(still_low[0].level, Level.CRITICAL)
                    self.assertTrue(still_low[0].needs_ack)

    def test_recheck_requires_fresh_reading_and_fires_once(self):
        state = ModeState(Mode.AFTER_HYPO, NOW)
        prior = Reading(NOW - timedelta(minutes=5), 3.5)
        self.assertEqual(evaluate([prior], None, state, NOW, []), [])
        history = [prior, Reading(NOW, 3.6)]
        [alert] = evaluate(history, None, state, NOW, [])
        self.assertIs(alert.kind, AlertKind.STILL_LOW)
        self.assertEqual(evaluate(history, None, state, NOW + timedelta(minutes=1), [alert]), [])

    def test_timer_starts_at_mode_activation_not_at_first_low(self):
        readings, facts = load("hypo_corrected")
        first_low = next(r for r in readings if r.ts.astimezone(MSK).strftime("%H:%M") == facts["first_below_3_9"])
        until = first_low.ts + timedelta(minutes=15)
        state = ModeState(Mode.AFTER_HYPO, until)
        sent = replay(readings, state)
        [alert] = [a for a in sent if a.kind is AlertKind.STILL_LOW]
        self.assertEqual(alert.created_at, until)
        self.assertLess(alert.value, 3.9)


class EscalationTests(unittest.TestCase):
    def setUp(self):
        self.alert = Alert(Level.CRITICAL, AlertKind.VERY_LOW, NOW, 2.9, needs_ack=True, id=7)

    def test_repeat_then_notify_trusted_then_closed(self):
        def step(minutes, repeated=()):
            return escalation_step([self.alert], NOW + timedelta(minutes=minutes), repeated_alert_ids=repeated)

        self.assertEqual(step(4), [])
        [repeat] = step(5)
        self.assertIs(repeat.type, ActionType.REPEAT)
        self.assertEqual(step(6, {7}), [])
        [trusted] = step(10, {7})
        self.assertIs(trusted.type, ActionType.NOTIFY_TRUSTED)
        self.assertEqual(trusted.extra["minutes_without_ack"], 10)
        self.assertIsNone(self.alert.escalated_at)
        self.alert.escalated_at = NOW + timedelta(minutes=10)
        self.assertEqual(step(11, {7}), [])

    def test_user_configured_delay_and_restart_with_restored_state(self):
        at = NOW + timedelta(minutes=11)
        self.assertEqual(escalation_step([self.alert], at, repeated_alert_ids={7}, trusted_after_minutes=12), [])
        [action] = escalation_step([self.alert], at + timedelta(minutes=1),
                                   repeated_alert_ids={7}, trusted_after_minutes=12)
        self.assertIs(action.type, ActionType.NOTIFY_TRUSTED)

    def test_ack_stops_escalation_and_first_ack_is_retained(self):
        ack(self.alert, 100, NOW + timedelta(minutes=4))
        ack(self.alert, 200, NOW + timedelta(minutes=6))
        self.assertEqual(self.alert.acked_at, NOW + timedelta(minutes=4))
        self.assertEqual(self.alert.acked_by, 100)
        self.assertEqual(escalation_step([self.alert], NOW + timedelta(minutes=20)), [])

    def test_ack_by_id_delegates_atomic_and_authorized_storage(self):
        def mark_ack(alert_id, who, at):
            if alert_id != self.alert.id or who not in {100, 200}:
                return None
            return ack(self.alert, who, at)

        confirm = partial(ack, now=NOW, mark_ack=mark_ack)
        self.assertIsNone(confirm(7, 300))
        self.assertIsNone(confirm(8, 200))
        self.assertIsNone(self.alert.acked_at)
        self.assertIs(confirm(7, 200), self.alert)
        self.assertEqual(self.alert.acked_by, 200)

    def test_no_action_for_unpersisted_or_noncritical_alerts(self):
        alerts = [Alert(Level.CRITICAL, AlertKind.VERY_LOW, NOW, needs_ack=True),
                  Alert(Level.IMPORTANT, AlertKind.LOW, NOW, needs_ack=True, id=8)]
        self.assertEqual(escalation_step(alerts, NOW + timedelta(minutes=20)), [])
