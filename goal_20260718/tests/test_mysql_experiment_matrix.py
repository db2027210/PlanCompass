from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from experiment_matrix_mysql import (
    DEFAULT_MANIFEST, audit_history, build_dry_run_plan, dispatch_to_idle_workers, evaluate_acceptance,
    expand_runs, load_manifest, schedule_preview, validate_result_schema,
)


def result_log(benchmark="hybench_ap", bottleneck="memory", repeat_id=1, final=0.82, qps_after=116.0, exp_id="A13"):
    return {
        "meta": {"exp_id": exp_id, "benchmark": benchmark, "db": "mysql8", "method": "outer_binary_inner_estimated_grid", "bottleneck": bottleneck, "bottleneck_target": 0.90, "repeat_id": repeat_id, "machine_spec": {"cpu_cores": 4, "mem_gb": 8, "disk_bw_mb": 100}},
        "outer_loop": [
            {"iter": 1, "params": {"test_knob": 1.0}, "qps": 100.0, "latency_p99_ms": 42.1, "latency_p95_ms": 38.7, "resource": {"cpu": 0.6, "mem": 0.90, "io": 0.3}, "utility": 0.7, "explain_samples": ["EXPLAIN ANALYZE"]},
            {"iter": 2, "params": {"test_knob": 2.0}, "qps": qps_after, "latency_p99_ms": 35.0, "latency_p95_ms": 30.0, "resource": {"cpu": 0.6, "mem": final, "io": final if bottleneck == "io" else 0.3}, "utility": 0.9, "explain_samples": ["EXPLAIN ANALYZE"]},
        ],
        "inner_loop_summary": {"total_iterations": 500, "wall_time_ms": 1200, "best_estimated_utility": 0.89, "best_params": {}},
        "result": {"final_params": {"test_knob": 2.0}, "delta_u_pct": 8.5, "qps_before": 100.0, "qps_after": qps_after, "p99_before_ms": 42.1, "p99_after_ms": 35.0, "bad_config": False, "converge_outer_iters": 2, "total_wall_time_s": 20.0},
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
        self.assertEqual(
            sorted(run.repeat_id for run in runs if run.exp_id == "B4"), [2, 3]
        )
        self.assertEqual(
            {run.exp_id for run in runs},
            {"A13", "A14", "A15", "A16", "A17", "A18",
             "E13", "E14", "E15", "E16", "E17", "E18",
             "B4", "B5", "B6"},
        )
        self.assertEqual(manifest["method"], "outer_binary_inner_estimated_grid")
        self.assertEqual(manifest["max_parallel"], 4)
        self.assertEqual(manifest["dispatch_policy"], "global_queue_idle_worker_first")
        self.assertEqual(manifest["dispatch_priority"][0], "runtime_resource_gate")
        self.assertEqual(manifest["runtime_resource_gates"]["max_write_intensive_parallel"], 1)
        self.assertEqual(manifest["runtime_resource_gates"]["max_write_intensive_parallel_per_storage_domain"], 1)
        self.assertEqual(len(manifest["workers"]), 4)
        self.assertEqual(
            {worker["worker_id"] for worker in manifest["workers"]},
            {"hybench_nvme0", "tpcc_nvme0", "hybench_sda", "analytics_sda"},
        )

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

    def test_none_requires_qps_and_at_least_one_resource_increase(self):
        manifest = load_manifest(DEFAULT_MANIFEST)
        good = result_log(bottleneck="none")
        good["outer_loop"][-1]["resource"]["cpu"] = 0.61
        self.assertTrue(evaluate_acceptance(good, manifest["acceptance"])["passed"])
        flat = result_log(bottleneck="none")
        flat["outer_loop"][-1]["resource"] = dict(flat["outer_loop"][0]["resource"])
        evaluated = evaluate_acceptance(flat, manifest["acceptance"])
        self.assertFalse(evaluated["passed"])
        self.assertFalse(evaluated["gates"]["at_least_one_resource_increased"])

    def test_cache_warm_drift_without_config_change_is_rejected(self):
        manifest = load_manifest(DEFAULT_MANIFEST)
        drift = result_log(bottleneck="none")
        drift["outer_loop"][-1]["resource"]["cpu"] = 0.61
        drift["result"]["final_params"] = dict(drift["outer_loop"][0]["params"])
        evaluated = evaluate_acceptance(drift, manifest["acceptance"])
        self.assertFalse(evaluated["passed"])
        self.assertFalse(evaluated["gates"]["config_changed"])

    def test_resource_conflicts_are_separated_and_dry_run_launches_nothing(self):
        missing = [
            {"run_id": "x", "resource_key": "same"},
            {"run_id": "y", "resource_key": "same"},
            {"run_id": "z", "resource_key": "other"},
        ]
        waves = schedule_preview(missing, 3)
        self.assertEqual([[row["run_id"] for row in wave] for wave in waves], [["x", "z"], ["y"]])
        manifest = load_manifest(DEFAULT_MANIFEST)
        plan = build_dry_run_plan(manifest, [])
        self.assertEqual(plan["mode"], "dry_run_only_no_benchmark_launched")
        self.assertEqual(plan["manifest_total_runs"], 18)
        for wave in plan["schedule"]["waves"]:
            workers = [row["worker_id"] for row in wave]
            self.assertEqual(len(workers), len(set(workers)))
            self.assertLessEqual(len(wave), 4)
        # Scenario diversity is best-effort and never invents more physical
        # workers than the four task-owned containers.
        self.assertGreaterEqual(
            len({row["bottleneck"] for row in plan["schedule"]["waves"][0]}), 3
        )
        write_benchmarks = set(manifest["runtime_resource_gates"]["write_intensive_benchmarks"])
        for wave in plan["schedule"]["waves"]:
            write_domains = [
                row["storage_domain"] for row in wave if row["benchmark"] in write_benchmarks
            ]
            self.assertEqual(len(write_domains), len(set(write_domains)))

    def test_dynamic_dispatch_uses_any_idle_compatible_worker(self):
        pending = [
            {"run_id": "A15-r1", "benchmark": "tpch"},
            {"run_id": "A17-r1", "benchmark": "tpcc"},
            {"run_id": "A13-r1", "benchmark": "hybench_ap"},
        ]
        idle = [
            {"worker_id": "hybench_1", "capabilities": ["hybench_ap"]},
            {"worker_id": "tpcc_1", "capabilities": ["tpcc"]},
            {"worker_id": "tpch_1", "capabilities": ["tpch"]},
        ]
        tick = dispatch_to_idle_workers(pending, idle, active_count=2, max_parallel=4)
        self.assertEqual(
            [(row["run_id"], row["worker_id"]) for row in tick["assignments"]],
            [("A15-r1", "tpch_1"), ("A17-r1", "tpcc_1")],
        )
        self.assertEqual([row["run_id"] for row in tick["pending"]], ["A13-r1"])

    def test_dynamic_dispatch_limits_write_intensive_scenarios(self):
        manifest = load_manifest(DEFAULT_MANIFEST)
        pending = [
            {"run_id": "A17-r1", "benchmark": "tpcc", "bottleneck": "cpu"},
            {"run_id": "A18-r1", "benchmark": "hybench_xp", "bottleneck": "io"},
            {"run_id": "A15-r1", "benchmark": "tpch", "bottleneck": "memory"},
        ]
        idle = [
            {"worker_id": "tpcc_1", "capabilities": ["tpcc"]},
            {"worker_id": "hybench_1", "capabilities": ["hybench_xp"]},
            {"worker_id": "tpch_1", "capabilities": ["tpch"]},
        ]
        tick = dispatch_to_idle_workers(
            pending, idle, active_count=1, max_parallel=4,
            active_benchmarks=["hybench_tp"],
            resource_gates=manifest["runtime_resource_gates"],
            host_disk_util_pct=6.0,
        )
        self.assertEqual([row["run_id"] for row in tick["assignments"]], ["A15-r1"])
        self.assertEqual([row["run_id"] for row in tick["pending"]], ["A17-r1", "A18-r1"])

    def test_dynamic_dispatch_parallelizes_write_work_across_storage_domains(self):
        manifest = load_manifest(DEFAULT_MANIFEST)
        pending = [
            {"run_id": "A17-r1", "benchmark": "tpcc", "bottleneck": "cpu"},
            {"run_id": "A18-r1", "benchmark": "hybench_xp", "bottleneck": "io"},
            {"run_id": "A15-r1", "benchmark": "tpch", "bottleneck": "memory"},
        ]
        idle = [
            {"worker_id": "tpcc_1", "capabilities": ["tpcc"], "storage_domain": "nvme0-root"},
            {"worker_id": "hybench_3", "capabilities": ["hybench_xp"], "storage_domain": "sda-disk3"},
            {"worker_id": "tpch_1", "capabilities": ["tpch"], "storage_domain": "sda-disk3"},
        ]
        tick = dispatch_to_idle_workers(
            pending, idle, active_count=1, max_parallel=4,
            active_assignments=[{"benchmark": "hybench_tp", "storage_domain": "nvme0-root"}],
            resource_gates=manifest["runtime_resource_gates"],
            storage_domain_util_pct={"nvme0-root": 20.0, "sda-disk3": 5.0},
        )
        self.assertEqual(
            [(row["run_id"], row["worker_id"]) for row in tick["assignments"]],
            [("A15-r1", "tpch_1"), ("A18-r1", "hybench_3")],
        )
        self.assertEqual([row["run_id"] for row in tick["pending"]], ["A17-r1"])


if __name__ == "__main__":
    unittest.main()
