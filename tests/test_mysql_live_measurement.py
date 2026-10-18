from __future__ import annotations

import unittest

from mysql_live_measurement import summarize_samples


class LiveMeasurementTest(unittest.TestCase):
    def test_resource_average_drops_warmup(self):
        rows = [
            {"cpu": 9.0, "memory": 1.0, "io": 100.0},
            {"cpu": 1.0, "memory": 2.0, "io": 10.0},
            {"cpu": 3.0, "memory": 4.0, "io": 30.0},
        ]
        self.assertEqual(
            summarize_samples(rows, 1),
            {"cpu": 2.0, "memory": 3.0, "io": 20.0},
        )

    def test_empty_samples_are_rejected(self):
        with self.assertRaises(ValueError):
            summarize_samples([])


if __name__ == "__main__":
    unittest.main()
