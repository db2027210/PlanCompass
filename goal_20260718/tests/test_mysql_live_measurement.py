from __future__ import annotations

import unittest

from mysql_live_measurement import _memory_value, summarize_samples


class FakeMemoryClient:
    def execute(self, sql: str) -> str:
        if "@@GLOBAL.innodb_buffer_pool_size" in sql:
            return "2.25\n"
        if "memory_summary_global_by_event_name" in sql:
            return "0.25\n"
        raise AssertionError(sql)


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

    def test_memory_modes_include_explicit_mysql_total(self):
        client = FakeMemoryClient()
        self.assertEqual(_memory_value(client, 3.5, "rss"), 3.5)
        self.assertEqual(_memory_value(client, 3.5, "sql_dynamic"), 0.25)
        self.assertEqual(_memory_value(client, 3.5, "sql_total"), 2.25)
        with self.assertRaises(ValueError):
            _memory_value(client, 3.5, "unknown")


if __name__ == "__main__":
    unittest.main()
