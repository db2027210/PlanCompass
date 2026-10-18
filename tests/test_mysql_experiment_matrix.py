from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from experiment_matrix_mysql import (
    DEFAULT_MANIFEST, audit_history, build_dry_run_plan, evaluate_acceptance,
    expand_runs, load_manifest, schedule_preview, validate_result_schema,
)


def result_log(benchmark="hybench_ap", bottleneck="memory", repeat_id=1, final=0.82, qps_after=116.0):
    return {
        "meta": {"exp_id": "old", "benchmark": benchmark, "db": "mysql8", "method": "full_grid_real_replay", "bottleneck": bottleneck, "bottleneck_target": 0.90, "repeat_id": repeat_id, "machine_spec": {"cpu_cores": 4, "mem_gb": 8, "disk_bw_mb": 100}},
        "outer_loop": [
            {"iter": 1, "params": {}, "qps": 100.0, "latency_p99_ms": 42.1, "latency_p95_ms": 38.7, "resource": {"cpu": 0.6, "mem": 0.90, "io": 0.3}, "utility": 0.7, "explain_samples": ["EXPLAIN ANALYZE"]},
            {"iter": 2, "params": {}, "qps": qps_after, "latency_p99_ms": 35.0, "latency_p95_ms": 30.0, "resource": {"cpu": 0.6, "mem": final, "io": final if bottleneck == "io" else 0.3}, "utility": 0.9, "explain_samples": ["EXPLAIN ANALYZE"]},
        ],
        "inner_loop_summary": {"total_iterations": 500, "wall_time_ms": 1200, "best_estimated_utility": 0.89, "best_params": {}},
        "result": {"final_params": {}, "delta_u_pct": 8.5, "qps_before": 100.0, "qps_after": qps_after, "p99_before_ms": 42.1, "p99_after_ms": 35.0, "bad_config": False, "converge_outer_iters": 2, "total_wall_time_s": 20.0},
    }


class ManifestTest(unittest.TestCase):
    def test_manifest_expands_exactly_18_runs(self):
        manifest = load_manifest(DEFAULT_MANIFEST)
        runs = expand_runs(manifest)
        self.assertEqual(len(runs), 18)
        self.assertEqual(len({run.run_id for run in runs}), 18)
        self.assertEqual(sum(run.exp_id.startswith("A") for run in runs), 6)
        self.assertEqual(sum(run.exp_id.startswith("E") for run in runs), 6)
        self.assertEqual(sum(run.exp_id.startswith("B") for run in runs), 6)
        self.assertEqual({run.repeat_id for run in runs if run.exp_id.startswith("B")}, {2, 3})
        self.assertEqual(
            {run.exp_id for run in runs},
            {"A7", "A8", "A9", "A10", "A11", "A12",
             "E7", "E8", "E9", "E10", "E11", "E12",
             "B1", "B2", "B3"},
        )
        self.assertEqual(manifest["method"], "full_grid_real_replay")

    def test_manifest_rejects_three_additional_b_repeats(self):
        manifest = json.loads(DEFAULT_MANIFEST.read_text())
        for row in manifest["experiments"]:
            if row["exp_id"].startswith("B"):
                row.update({"runs": 3, "repeat_start": 1})
                row.pop("target_total_including_a", None)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wrong.json"
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "total including A is 3"):
                load_manifest(path)

    def test_complete_schema_and_all_acceptance_gates(self):
        manifest = load_manifest(DEFAULT_MANIFEST)
        good = result_log()
        self.assertEqual(validate_result_schema(good), [])
        accepted = evaluate_acceptance(good, manifest["acceptance"])
        self.assertTrue(accepted["passed"])
        self.assertTrue(all(accepted["gates"].values()))
        for bad in (result_log(final=0.89), result_log(final=0.79), result_log(qps_after=114.9)):
            self.assertFalse(evaluate_acceptance(bad, manifest["acceptance"])["passed"])
        incomplete = result_log(); del incomplete["result"]["final_params"]
        self.assertIn("missing result.final_params", validate_result_schema(incomplete))
        none_run = result_log(bottleneck="none")
        none_run["meta"]["bottleneck_target"] = None
        self.assertEqual(validate_result_schema(none_run), [])

    def test_history_matching_is_exact_and_one_file_is_used_once(self):
        manifest = load_manifest(DEFAULT_MANIFEST)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "valid.json").write_text(json.dumps(result_log()), encoding="utf-8")
            (root / "wrong_repeat.json").write_text(json.dumps(result_log(repeat_id=9)), encoding="utf-8")
            (root / "wrong_pair.json").write_text(json.dumps(result_log(benchmark="job", bottleneck="cpu")), encoding="utf-8")
            postgres = result_log(); postgres["meta"]["db"] = "pg17"
            (root / "wrong_db.json").write_text(json.dumps(postgres), encoding="utf-8")
            audit = audit_history(manifest, [root])
        matches = [row for row in audit["reused"] if row["benchmark"] == "hybench_ap" and row["bottleneck"] == "memory"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["repeat_id"], 1)
        self.assertEqual(audit["reused_count"], 1)
        self.assertEqual(audit["missing_count"], 17)

    def test_none_requires_qps_and_no_overloaded_resource(self):
        manifest = load_manifest(DEFAULT_MANIFEST)
        good = result_log(bottleneck="none")
        good["outer_loop"][-1]["resource"]["cpu"] = 0.61
        self.assertTrue(evaluate_acceptance(good, manifest["acceptance"])["passed"])
        flat = result_log(bottleneck="none")
        flat["outer_loop"][-1]["resource"] = dict(flat["outer_loop"][0]["resource"])
        evaluated = evaluate_acceptance(flat, manifest["acceptance"])
        self.assertTrue(evaluated["passed"])
        overloaded = result_log(bottleneck="none")
        overloaded["outer_loop"][0]["resource"]["cpu"] = .91
        self.assertFalse(evaluate_acceptance(overloaded, manifest["acceptance"])["passed"])

    def test_resource_conflicts_are_separated_and_dry_run_launches_nothing(self):
        missing = [
            {"run_id": "x", "resource_key": "same"},
            {"run_id": "y", "resource_key": "same"},
            {"run_id": "z", "resource_key": "other"},
        ]
        waves = schedule_preview(missing, 3)
        self.assertEqual([[row["run_id"] for row in wave] for wave in waves], [["x", "z"], ["y"]])
        plan = build_dry_run_plan(load_manifest(DEFAULT_MANIFEST), [])
        self.assertEqual(plan["mode"], "dry_run_only_no_benchmark_launched")
        self.assertEqual(plan["manifest_total_runs"], 18)
        self.assertEqual(plan["schedule"]["policy"], "global_oldest_compatible_task_first_work_stealing")
        self.assertEqual(plan["schedule"]["max_parallel"], 12)
        self.assertEqual(len(plan["schedule"]["global_queue"]), 18)
        self.assertTrue(all(row["eligible_workers"] for row in plan["schedule"]["global_queue"]))


if __name__ == "__main__":
    unittest.main()
