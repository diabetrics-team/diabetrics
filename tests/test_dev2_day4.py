"""Replay the official fixtures, including ticks with no new sensor reading."""

import json
import unittest
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from app.alerts.engine import evaluate, history_start_for_hysteresis
from app.forecast import predict
from app.models import Alert, AlertKind, Level, Mode, ModeState, Reading
from app.ns.clean import clean

DATA = Path(__file__).resolve().parents[1] / "fake_cgm" / "data"
MSK = timezone(timedelta(hours=3))
META = json.loads((DATA / "scenarios.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 11, 13, 12, tzinfo=UTC)


def hhmm(ts):
    return ts.astimezone(MSK).strftime("%H:%M")


def load(name):
    return clean(json.loads((DATA / f"{name}.json").read_text(encoding="utf-8")))


def ticks(name):
    readings = load(name)
    start = readings[0].ts
    count = 0
    for minute in range(META[name]["duration_min"] + 1):
        now = start + timedelta(minutes=minute)
        while count < len(readings) and readings[count].ts <= now:
            count += 1
        history = readings[:count]
        yield now, history, predict(history)


def replay(name, mode_at=lambda now: ModeState()):
    sent = []
    for now, history, forecast in ticks(name):
        sent.extend(evaluate(history, forecast, mode_at(now), now, sent))
    return sent


class OfficialScenarios(unittest.TestCase):
    def test_scenario_facts_and_notification_counts(self):
        expected_counts = {
            "normal": {},
            "night_hypo": {AlertKind.LOW: 1, AlertKind.VERY_LOW: 1},
            "meal": {AlertKind.FAST_RISE: 1, AlertKind.HIGH_1H: 1},
            "fast_drop": {AlertKind.FAST_DROP: 1, AlertKind.LOW: 1, AlertKind.VERY_LOW: 0},
            "hypo_corrected": {AlertKind.LOW: 1},
            "hypo_not_corrected": {AlertKind.LOW: 1},
            "hyper_long": {AlertKind.HIGH_1H: 1, AlertKind.VERY_HIGH_1H: 1, AlertKind.FAST_RISE: 0},
            "sensor_gap": {AlertKind.NO_DATA: 2},
            "noise_spike": {},
            "borderline": {AlertKind.LOW: 1, AlertKind.BACK_IN_RANGE: 1},
            "meal_overbolus": {AlertKind.LOW: 1, AlertKind.HIGH_1H: 1, AlertKind.FAST_RISE: 1},
            "sensor_lost": {AlertKind.VERY_LOW: 0},
            "dirty_data": {},
            "random_day": {AlertKind.FAST_RISE: 1, AlertKind.HIGH_1H: 1},
        }
        facts_by_kind = {
            AlertKind.LOW: "first_below_3_9",
            AlertKind.VERY_LOW: "first_below_3_0",
            AlertKind.HIGH_1H: "above_10_0_for_1h_at",
            AlertKind.VERY_HIGH_1H: "above_13_9_for_1h_at",
            AlertKind.FAST_DROP: "first_fast_drop",
            AlertKind.FAST_RISE: "first_fast_rise",
        }
        self.assertEqual(set(expected_counts), set(META))
        for name, expected in expected_counts.items():
            with self.subTest(name=name):
                facts = META[name]["facts"]
                self.assertEqual(len(load(name)), facts["points"])
                sent = replay(name)
                if not expected:
                    self.assertEqual(sent, [])
                for kind, count in expected.items():
                    found = [a for a in sent if a.kind is kind]
                    self.assertEqual(len(found), count, kind)
                    if found and kind in facts_by_kind:
                        self.assertEqual(hhmm(found[0].created_at), facts[facts_by_kind[kind]])
                if name == "borderline":
                    recovery = next(a for a in sent if a.kind is AlertKind.BACK_IN_RANGE)
                    self.assertGreater(recovery.value, 4.2)

    def test_modes_preserve_critical_alerts_for_each_scenario(self):
        for name in META:
            for mode in Mode:
                with self.subTest(name=name, mode=mode):
                    state = ModeState(mode)
                    sent = []
                    for now, history, forecast in ticks(name):
                        expected = {
                            a.kind for a in evaluate(history, forecast, ModeState(), now, sent)
                            if a.level is Level.CRITICAL
                        }
                        actual = evaluate(history, forecast, state, now, sent)
                        self.assertTrue(expected <= {a.kind for a in actual if a.level is Level.CRITICAL}, now)
                        self.assertTrue(all(a.needs_ack for a in actual if a.level is Level.CRITICAL))
                        sent.extend(actual)

    def test_night_prediction_precedes_observed_very_low(self):
        sent = replay("night_hypo", lambda now: ModeState(Mode.NIGHT))
        first_predicted = next(a for a in sent if a.kind is AlertKind.PREDICTED_LOW)
        first_observed = next(a for a in sent if a.kind is AlertKind.VERY_LOW)
        first_low = next(a for a in sent if a.kind is AlertKind.LOW)
        self.assertEqual(hhmm(first_observed.created_at), META["night_hypo"]["facts"]["first_below_3_0"])
        self.assertLess(first_predicted.created_at, first_observed.created_at)
        self.assertIs(first_low.level, Level.CRITICAL)

    def test_after_meal_fixture_is_quiet_while_mode_is_active(self):
        self.assertEqual(replay("meal", lambda now: ModeState(Mode.AFTER_MEAL)), [])

    def test_sensor_gap_escalates_level_and_resets_on_fresh_sample(self):
        sent = [a for a in replay("sensor_gap") if a.kind is AlertKind.NO_DATA]
        gap = META["sensor_gap"]["facts"]["gaps"][0]
        self.assertEqual([(hhmm(a.created_at), a.level) for a in sent], [
            (gap["no_data_20min_at"], Level.IMPORTANT), (gap["no_data_30min_at"], Level.CRITICAL),
        ])
        readings = load("sensor_gap")
        fresh = next(r for r in readings if hhmm(r.ts) == gap["data_back_at"])
        self.assertIsNone(predict([r for r in readings if r.ts <= fresh.ts]))

    def test_sensor_lost_stays_important_and_repeats_at_cooldown(self):
        sent = replay("sensor_lost")
        self.assertGreater(len(sent), 1)
        self.assertTrue(all(a.kind is AlertKind.NO_DATA and a.level is Level.IMPORTANT for a in sent))
        last_reading = load("sensor_lost")[-1]
        self.assertEqual(sent[0].created_at - last_reading.ts, timedelta(minutes=20))
        self.assertTrue(all(b.created_at - a.created_at == timedelta(minutes=15) for a, b in zip(sent, sent[1:])))


class HysteresisTests(unittest.TestCase):
    def test_low_only_rearms_after_confirmed_value_strictly_above_4_2(self):
        sent = [Alert(Level.IMPORTANT, AlertKind.LOW, NOW, 3.8)]
        for middle, expected in [(4.2, []), (4.21, [AlertKind.LOW])]:
            with self.subTest(middle=middle):
                readings = [Reading(NOW, 3.8), Reading(NOW + timedelta(minutes=5), middle),
                            Reading(NOW + timedelta(minutes=10), 3.8)]
                alerts = evaluate(readings, None, ModeState(), readings[-1].ts, sent)
                self.assertEqual([a.kind for a in alerts], expected)

    def test_high_only_rearms_below_9_5_and_uses_old_alerts(self):
        sent = [Alert(Level.IMPORTANT, AlertKind.HIGH_1H, NOW, 11)]
        for middle, expected in [(9.5, []), (9.49, [AlertKind.HIGH_1H])]:
            with self.subTest(middle=middle):
                start = NOW + timedelta(hours=3)
                readings = [Reading(start, middle)] + [
                    Reading(start + timedelta(minutes=i * 5), 11) for i in range(1, 14)
                ]
                alerts = evaluate(readings, None, ModeState(), readings[-1].ts, sent)
                self.assertEqual([a.kind for a in alerts], expected)
                if middle == 9.5:
                    self.assertLess(history_start_for_hysteresis(readings, sent, readings[-1].ts), NOW)

    def test_critical_upgrade_within_same_episode_is_sent(self):
        sent = [Alert(Level.IMPORTANT, AlertKind.LOW, NOW, 3.8)]
        readings = [Reading(NOW, 3.8), Reading(NOW + timedelta(minutes=5), 2.9)]
        [alert] = evaluate(readings, None, ModeState(Mode.DND), readings[-1].ts, sent)
        self.assertIs(alert.kind, AlertKind.VERY_LOW)
        self.assertIs(alert.level, Level.CRITICAL)

    def test_recovery_spike_does_not_rearm_episode(self):
        sent = [Alert(Level.IMPORTANT, AlertKind.LOW, NOW, 3.8)]
        readings = [Reading(NOW, 3.8), Reading(NOW + timedelta(minutes=5), 7.0),
                    Reading(NOW + timedelta(minutes=10), 3.8)]
        self.assertEqual(evaluate(readings, None, ModeState(), readings[-1].ts, sent), [])
