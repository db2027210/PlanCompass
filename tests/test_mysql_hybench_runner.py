from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory

from mysql_hybench_runner import _bounded_raw_log, _read_properties, _runtime_config


class HyBenchRawLogTest(unittest.TestCase):
    def test_small_log_is_unchanged(self):
        self.assertEqual(_bounded_raw_log("short\n", 100), "short\n")

    def test_large_log_is_bounded_and_retains_summary_and_tail(self):
        output = "head\n" + ("transaction detail\n" * 100) + "HYBENCH_METRICS_JSON={\"qps\":12.3}\nTAIL"

        compact = _bounded_raw_log(output, 240)

        self.assertLessEqual(len(compact), 240)
        self.assertIn("RAW_LOG_TRUNCATED", compact)
        self.assertIn("HYBENCH_METRICS_JSON", compact)
        self.assertTrue(compact.endswith("TAIL"))

    def test_xp_runtime_config_strips_custom_ap_routing(self):
        with TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            base = root / "base.props"
            target = root / "runtime.props"
            base.write_text(
                "ap_query_ids=9\n"
                "ap_query_weights=100\n"
                "ap_query_sequence=9\n"
                "ap_query_sequence_limit=1\n",
                encoding="utf-8",
            )
            args = SimpleNamespace(
                workload="xp", base_config=base, host="127.0.0.1", port=3306,
                database="hybench", user="root", password="CHANGE_ME",
                ap_clients=2, tp_clients=16, xap_clients=8, xtp_clients=24,
                random_seed=1, ap_query_sequence=None, ap_query_sequence_limit=0,
                tp_transaction_mix=None, tp_transaction_id=None,
            )

            _runtime_config(args, target, 10)

            values = _read_properties(target)
            for key in (
                "ap_query_ids", "ap_query_weights", "ap_query_sequence",
                "ap_query_sequence_limit",
            ):
                self.assertNotIn(key, values)


if __name__ == "__main__":
    unittest.main()
