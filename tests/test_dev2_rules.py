"""Synthetic rule tests, runnable with unittest and pytest.

These do not replace tests against the team's official scenarios.json facts.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest

from app.alerts import evaluate
from app.alerts.contracts import Alert, AlertLevel, Forecast, Reading


NOW = datetime(2026, 11, 11, 12, tzinfo=timezone.utc)


def series(values, step=5):
    return [Reading(NOW - timedelta(minutes=(len(values) - 1 - i) * step), value)
            for i, value in enumerate(values)]


def check(history, forecast=None, now=NOW):
    return evaluate(history, forecast, "normal", now, [])


def kinds(alerts):
    return [alert.kind for alert in alerts]


class ValueTests(unittest.TestCase):
    def test_low_boundaries(self):
        for value, expected in [(2.9, ["very_low"]), (3.0, ["low"]),
                                (3.89, ["low"]), (3.9, []), (10, []), (14, [])]:
            with self.subTest(value=value):
                self.assertEqual(kinds(check(series([value]))), expected)

    def test_low_level(self):
        self.assertEqual(check(series([3.4]))[0].level, AlertLevel.IMPORTANT)

    def test_very_low_level(self):
        self.assertEqual(check(series([2.9]))[0].level, AlertLevel.CRITICAL)


class RateTests(unittest.TestCase):
    def test_rate_thresholds(self):
        for rate, expected in [(-0.18, ["fast_drop"]), (-0.17, []), (0, []),
                               (0.17, []), (0.18, ["fast_rise"])]:
            with self.subTest(rate=rate):
                self.assertEqual(kinds(check(series([6]), Forecast(rate))), expected)

    def test_rate_is_taken_from_forecast(self):
        self.assertEqual(kinds(check(series([6, 6, 6, 6]), Forecast(-0.2))), ["fast_drop"])
        self.assertEqual(check(series([10, 8, 6, 4]), Forecast(0)), [])

    def test_no_forecast_has_no_rate_alert(self):
        self.assertEqual(check(series([10, 8, 6, 4])), [])

    def test_invalid_forecast_does_not_hide_value_alert(self):
        for rate in [float("nan"), float("inf"), -float("inf")]:
            self.assertEqual(kinds(check(series([2.8]), Forecast(rate))), ["very_low"])

    def test_low_and_fast_drop_can_coexist(self):
        self.assertEqual(kinds(check(series([3.5]), Forecast(-0.2))), ["low", "fast_drop"])


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
            (19, 6, None), (20, 6, AlertLevel.IMPORTANT),
            (29, 3.5, AlertLevel.IMPORTANT), (30, 3.5, AlertLevel.CRITICAL),
            (30, 3.9, AlertLevel.IMPORTANT), (30, 6, AlertLevel.IMPORTANT),
        ]:
            with self.subTest(minutes=minutes, value=value):
                result = check(series([value]), now=NOW + timedelta(minutes=minutes))
                if expected is None:
                    self.assertEqual(result, [])
                else:
                    self.assertEqual(kinds(result), ["no_data"])
                    self.assertEqual(result[0].level, expected)

    def test_stale_rate_and_value_are_not_reissued(self):
        result = check(series([2.9]), Forecast(-0.3), NOW + timedelta(minutes=20))
        self.assertEqual(kinds(result), ["no_data"])
        self.assertEqual(result[0].value, 2.9)

    def test_empty_history_has_no_known_age(self):
        self.assertEqual(check([]), [])

    def test_no_data_without_new_reading(self):
        history = series([3.5])
        self.assertEqual(check(history), check(history))
        self.assertEqual(check(history, now=NOW + timedelta(minutes=30))[0].level,
                         AlertLevel.CRITICAL)


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
        self.assertEqual(check([Reading(local, 3.5)])[0].ts, NOW)

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
        forecast = Forecast(-0.2)
        recent = [Alert("low", AlertLevel.IMPORTANT, 3.5, NOW)]
        mode = {"name": "normal"}
        before = deepcopy((history, forecast, mode, recent))
        first = evaluate(history, forecast, mode, NOW, recent)
        second = evaluate(history, forecast, mode, NOW, recent)
        self.assertEqual(first, second)
        self.assertEqual((history, forecast, mode, recent), before)


class SyntheticScenarioTests(unittest.TestCase):
    """Hand-built sequences; not the unavailable official facts fixtures."""

    def test_normal(self):
        for value in [5.5, 5.6, 5.7, 5.6, 5.5]:
            self.assertEqual(check(series([value]), Forecast(0.02)), [])

    def test_night_hypo(self):
        history = series([5.2, 4.7, 4.1, 3.8, 3.3, 2.9])
        observed = [kinds(check(history[:i], now=history[i-1].ts))
                    for i in range(1, len(history) + 1)]
        self.assertEqual(observed, [[], [], [], ["low"], ["low"], ["very_low"]])

    def test_fast_drop(self):
        history = series([8, 7, 6, 5])
        self.assertEqual(kinds(check(history, Forecast(-0.2))), ["fast_drop"])

    def test_hyper_long(self):
        history = series([14.5] * 19)
        self.assertEqual(check(history[:12], now=history[11].ts), [])
        self.assertEqual(kinds(check(history[:13], now=history[12].ts)), ["very_high_1h"])
        self.assertEqual(kinds(check(history)), ["very_high_1h"])


if __name__ == "__main__":
    unittest.main()
