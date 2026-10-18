import unittest

from formal_mysql_random_walk_metrics import (
    MeasurementMetricError,
    cumulative_interval_rates,
    event_interval_rates,
)


class FrozenWindowMetricTests(unittest.TestCase):
    def test_event_rates_exclude_no_unregistered_events(self):
        epochs = [1000, 2000, 3000]
        self.assertEqual(
            event_interval_rates(epochs, [500, 1500, 1501, 2999], start_ms=0, end_ms=3000),
            (1.0, 2.0, 1.0),
        )

    def test_event_rates_reject_warmup_and_late_events(self):
        with self.assertRaisesRegex(MeasurementMetricError, "warm-up or post-deadline"):
            event_interval_rates([2000], [999], start_ms=1000, end_ms=2000)
        with self.assertRaisesRegex(MeasurementMetricError, "warm-up or post-deadline"):
            event_interval_rates([2000], [2001], start_ms=1000, end_ms=2000)

    def test_cumulative_rates_are_deltas_not_cumulative_averages(self):
        self.assertEqual(
            cumulative_interval_rates(
                [1000, 2000, 3000],
                [(900, 2), (1900, 5), (2900, 9)],
                start_ms=0,
                end_ms=3000,
            ),
            (2.0, 3.0, 4.0),
        )

    def test_cumulative_counter_may_not_regress(self):
        with self.assertRaisesRegex(MeasurementMetricError, "regressed"):
            cumulative_interval_rates(
                [1000, 2000], [(900, 2), (1900, 1)], start_ms=0, end_ms=2000
            )


if __name__ == "__main__":
    unittest.main()
