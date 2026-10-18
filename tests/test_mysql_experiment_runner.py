from __future__ import annotations

import unittest

from mysql_experiment_runner import _is_bad_config


class BadConfigSemanticsTest(unittest.TestCase):
    def test_tail_latency_overrides_aggregate_query_time_false_positive(self):
        self.assertFalse(_is_bad_config(8891.121, 8830.323, True))

    def test_tail_latency_regression_over_ten_percent_is_bad(self):
        self.assertTrue(_is_bad_config(100.0, 111.0, False))

    def test_missing_latency_uses_legacy_signal(self):
        self.assertTrue(_is_bad_config(0.0, 0.0, True))


if __name__ == "__main__":
    unittest.main()
