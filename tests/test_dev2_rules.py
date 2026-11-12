"""Rule boundaries and replay of the official CGM scenarios."""

import json
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.alerts import evaluate
from app.forecast import predict
from app.models import Alert, AlertKind, Forecast, Level, ModeState, Reading
from app.ns.clean import clean


NOW = datetime(2026, 11, 11, 12, tzinfo=timezone.utc)


def series(values, step=5):
    return [Reading(NOW - timedelta(minutes=(len(values) - 1 - i) * step), value)
            for i, value in enumerate(values)]


def check(history, forecast=None, now=NOW):
    return evaluate(history, forecast, ModeState(), now, [])


def make_forecast(rate):
    return Forecast(mmol_20=6.0, mmol_30=6.0, rate=rate)


def kinds(alerts):
    return [alert.kind for alert in alerts]


class ValueTests(unittest.TestCase):
    def test_low_boundaries(self):
        for value, expected in [(2.9, ["very_low"]), (3.0, ["low"]),
                                (3.89, ["low"]), (3.9, []), (10, []), (14, [])]:
            with self.subTest(value=value):
                self.assertEqual(kinds(check(series([value]))), expected)

    def test_low_level(self):
        self.assertEqual(check(series([3.4]))[0].level, Level.IMPORTANT)

    def test_very_low_level(self):
        alert = check(series([2.9]))[0]
        self.assertIsInstance(alert, Alert)
        self.assertIs(alert.kind, AlertKind.VERY_LOW)
        self.assertIs(alert.level, Level.CRITICAL)
        self.assertTrue(alert.needs_ack)


class RateTests(unittest.TestCase):
    def test_rate_thresholds(self):
        for rate, expected in [(-0.18, ["fast_drop"]), (-0.17, []), (0, []),
                               (0.17, []), (0.18, ["fast_rise"])]:
            with self.subTest(rate=rate):
                alerts = check(series([6]), make_forecast(rate))
                self.assertEqual(kinds(alerts), expected)
                for alert in alerts:
                    self.assertIs(alert.level, Level.IMPORTANT)
                    self.assertFalse(alert.needs_ack)

    def test_rate_is_taken_from_forecast(self):
        self.assertEqual(kinds(check(series([6, 6, 6, 6]), make_forecast(-0.2))), ["fast_drop"])
        self.assertEqual(check(series([10, 8, 6, 4]), make_forecast(0)), [])

    def test_no_forecast_has_no_rate_alert(self):
        self.assertEqual(check(series([10, 8, 6, 4])), [])

    def test_invalid_forecast_does_not_hide_value_alert(self):
        for rate in [float("nan"), float("inf"), -float("inf")]:
            self.assertEqual(kinds(check(series([2.8]), make_forecast(rate))), ["very_low"])

    def test_low_and_fast_drop_can_coexist(self):
        self.assertEqual(kinds(check(series([3.5]), make_forecast(-0.2))), ["low", "fast_drop"])


class DurationTests(unittest.TestCase):
    def test_high_at_one_hour(self):
        self.assertEqual(kinds(check(series([11] * 13))), ["high_1h"])

    def test_high_before_one_hour(self):
        self.assertEqual(check(series([11] * 12)), [])

    def test_very_high_has_priority(self):
        self.assertEqual(kinds(check(series([14] * 13))), ["very_high_1h"])

    def test_threshold_equality_is_not_high(self):
        self.assertEqual(check(series([10] * 13)), [])
        self.assertEqual(kinds(check(series([13.9] * 13))), ["high_1h"])

    def test_short_very_high_keeps_sustained_high(self):
        self.assertEqual(kinds(check(series([11] * 12 + [15]))), ["high_1h"])

    def test_return_to_threshold_breaks_duration(self):
        self.assertEqual(check(series([11] * 6 + [10] + [11] * 6)), [])

    def test_large_gap_breaks_duration(self):
        readings = series([11] * 16)
        readings = [r for i, r in enumerate(readings) if i not in (6, 7)]
        self.assertEqual(check(readings), [])

    def test_exact_ten_minute_gap_allowed(self):
        self.assertEqual(kinds(check(series([11] * 7, step=10))), ["high_1h"])

    def test_wall_clock_does_not_extend_observed_duration(self):
        self.assertEqual(check(series([11] * 12), now=NOW + timedelta(minutes=5)), [])


class NoDataTests(unittest.TestCase):
    def test_age_thresholds(self):
        for minutes, value, expected in [
            (19, 6, None), (20, 6, Level.IMPORTANT),
            (29, 3.5, Level.IMPORTANT), (30, 3.5, Level.CRITICAL),
            (30, 3.9, Level.IMPORTANT), (30, 6, Level.IMPORTANT),
        ]:
            with self.subTest(minutes=minutes, value=value):
                result = check(series([value]), now=NOW + timedelta(minutes=minutes))
                if expected is None:
                    self.assertEqual(result, [])
                else:
                    self.assertEqual(kinds(result), ["no_data"])
                    self.assertIs(result[0].level, expected)
                    self.assertEqual(result[0].needs_ack, expected is Level.CRITICAL)

    def test_stale_rate_and_value_are_not_reissued(self):
        result = check(series([2.9]), make_forecast(-0.3), NOW + timedelta(minutes=20))
        self.assertEqual(kinds(result), ["no_data"])
        self.assertEqual(result[0].value, 2.9)

    def test_empty_history_has_no_known_age(self):
        self.assertEqual(check([]), [])

    def test_no_data_without_new_reading(self):
        history = series([3.5])
        self.assertEqual(check(history), check(history))
        self.assertEqual(check(history, now=NOW + timedelta(minutes=30))[0].level,
                         Level.CRITICAL)


class InputTests(unittest.TestCase):
    def test_unsorted_history(self):
        history = series([11] * 13)
        self.assertEqual(check(list(reversed(history))), check(history))

    def test_duplicate_does_not_extend_duration(self):
        history = series([11] * 12)
        self.assertEqual(check(history * 3), [])

    def test_conflicting_duplicates_fail(self):
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            check([Reading(NOW, 3), Reading(NOW, 7)])

    def test_future_readings_do_not_hide_current_low(self):
        history = series([3.5]) + [Reading(NOW + timedelta(minutes=5), 6)]
        self.assertEqual(kinds(check(history)), ["low"])

    def test_only_future_readings(self):
        self.assertEqual(check([Reading(NOW + timedelta(minutes=5), 6)]), [])

    def test_aware_non_utc_is_converted(self):
        local = NOW.astimezone(timezone(timedelta(hours=3)))
        self.assertEqual(check([Reading(local, 3.5)])[0].created_at, NOW)

    def test_naive_timestamps_fail(self):
        with self.assertRaisesRegex(ValueError, "timezone"):
            check([Reading(NOW.replace(tzinfo=None), 6)])
        with self.assertRaisesRegex(ValueError, "timezone"):
            check(series([6]), now=NOW.replace(tzinfo=None))

    def test_invalid_readings_fail(self):
        for value in [float("nan"), float("inf"), 0, -1]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                check(series([value]))

    def test_pure_repeatable_and_no_input_mutation(self):
        history = series([3.5, 3.4])
        forecast = make_forecast(-0.2)
        recent = [Alert(kind=AlertKind.LOW, level=Level.IMPORTANT, created_at=NOW, value=3.5)]
        mode = ModeState()
        before = deepcopy((history, forecast, mode, recent))
        first = evaluate(history, forecast, mode, NOW, recent)
        second = evaluate(history, forecast, mode, NOW, recent)
        self.assertEqual(first, second)
        self.assertEqual((history, forecast, mode, recent), before)


class CgmScenarioTests(unittest.TestCase):
    DATA = Path(__file__).resolve().parents[1] / "fake_cgm" / "data"
    MSK = timezone(timedelta(hours=3))
    FACTS_BY_KIND = {
        AlertKind.LOW: "first_below_3_9",
        AlertKind.VERY_LOW: "first_below_3_0",
        AlertKind.HIGH_1H: "above_10_0_for_1h_at",
        AlertKind.VERY_HIGH_1H: "above_13_9_for_1h_at",
        AlertKind.FAST_DROP: "first_fast_drop",
        AlertKind.FAST_RISE: "first_fast_rise",
    }

    def check_scenario(self, name):
        meta = json.loads((self.DATA / "scenarios.json").read_text(encoding="utf-8"))
        facts = meta[name]["facts"]
        readings = clean(json.loads((self.DATA / f"{name}.json").read_text(encoding="utf-8")))
        self.assertEqual(len(readings), facts["points"])
        first_times = {}
        for i, reading in enumerate(readings, start=1):
            history = readings[:i]
            for alert in check(history, predict(history), now=reading.ts):
                first_times.setdefault(alert.kind, alert.created_at.astimezone(self.MSK).strftime("%H:%M"))
        expected = {kind: facts[key] for kind, key in self.FACTS_BY_KIND.items() if facts[key] is not None}
        self.assertEqual(first_times, expected)

    def test_normal(self):
        self.check_scenario("normal")

    def test_night_hypo(self):
        self.check_scenario("night_hypo")

    def test_fast_drop(self):
        self.check_scenario("fast_drop")

    def test_hyper_long(self):
        self.check_scenario("hyper_long")

    def test_meal(self):
        self.check_scenario("meal")


if __name__ == "__main__":
    unittest.main()
