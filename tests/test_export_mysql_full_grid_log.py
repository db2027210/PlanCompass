from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from experiment_matrix_mysql import evaluate_acceptance, load_manifest
from export_mysql_full_grid_log import build_document


class ExportFullGridLogTests(unittest.TestCase):
    def test_baseline_is_first_and_selected_candidate_is_last(self):
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory)
            baseline = {
                "params": {"io_block_read_cost": 1.0},
                "resource": {"cpu": 2.0, "memory": 7.2, "io": 20.0,
                             "qps": 100.0, "latency_p95_ms": 80.0,
                             "latency_p99_ms": 100.0, "explain_samples": ["base plan"]},
                "score": 0.5,
            }
            (task / "baseline.json").write_text(json.dumps(baseline))
            rows = [
                {"candidate_id": "not-selected", "status": "ok",
                 "params": {"io_block_read_cost": 2.0}, "actual_score": 0.6,
                 "elapsed_sec": 1.0,
                 "actual_resource": {"cpu": 2.1, "memory": 7.0, "io": 21.0,
                                     "qps": 110.0, "latency_p95_ms": 70.0,
                                     "latency_p99_ms": 90.0, "explain_samples": ["p1"]}},
                {"candidate_id": "selected", "status": "ok",
                 "params": {"io_block_read_cost": 0.5}, "actual_score": 0.8,
                 "elapsed_sec": 1.0,
                 "actual_resource": {"cpu": 2.2, "memory": 6.56, "io": 22.0,
                                     "qps": 130.0, "latency_p95_ms": 60.0,
                                     "latency_p99_ms": 70.0, "explain_samples": ["p2"]}},
            ]
            (task / "full_replay_results.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows))
            (task / "full_replay_summary.json").write_text(json.dumps({
                "status": "completed", "full_grid_candidate_count": 2,
            }))
            document = build_document(
                task, exp_id="A7", benchmark="hybench_ap", bottleneck="memory",
                repeat_id=1, machine={"cpu_cores": 4.0, "mem_gb": 8.0,
                                      "disk_bw_mb": 100.0}, bottleneck_target=.9,
            )
        self.assertEqual(document["outer_loop"][0]["params"], baseline["params"])
        self.assertEqual(document["outer_loop"][-1]["params"], rows[1]["params"])
        self.assertFalse(document["result"]["bad_config"])
        self.assertTrue(evaluate_acceptance(document, load_manifest()["acceptance"])["passed"])


if __name__ == "__main__":
    unittest.main()
