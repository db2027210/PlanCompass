import tempfile
import unittest
from pathlib import Path

from prepare_mysql_dynamic_dispatch import build_tasks


class PrepareDynamicDispatchTests(unittest.TestCase):
    def test_only_available_benchmarks_are_emitted_and_endpoint_is_deferred(self):
        missing = [
            {"run_id":"A7-r1", "exp_id":"A7", "repeat_id":1,
             "benchmark":"hybench_ap", "bottleneck":"memory"},
            {"run_id":"A8-r1", "exp_id":"A8", "repeat_id":1,
             "benchmark":"job", "bottleneck":"memory"},
        ]
        workers = [{"worker_id":"hy-1", "benchmarks":["hybench_ap"]}]
        with tempfile.TemporaryDirectory() as tmp:
            tasks = build_tasks({}, missing, workers, Path(tmp))
        self.assertEqual([task["run_id"] for task in tasks], ["A7-r1"])
        self.assertIn("{port}", tasks[0]["command"])
        self.assertIn("{pid}", tasks[0]["command"])


if __name__ == "__main__":
    unittest.main()
