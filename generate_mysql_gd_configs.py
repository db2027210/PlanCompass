"""Generate real-run configs for the A25/E25/B7 MySQL gradient matrix."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any, Dict

from experiment_matrix_mysql import expand_runs, load_manifest


ROOT = Path("/disk2/resource_adaptive_mysql/gd_20260718")
WORKSPACE = Path(__file__).resolve().parent
ACTIVE = [
    "io_block_read_cost", "memory_block_read_cost", "row_evaluate_cost", "key_compare_cost",
    "disk_temptable_create_cost", "disk_temptable_row_cost",
    "memory_temptable_create_cost", "memory_temptable_row_cost",
]
SENSITIVITY = {
    "io_block_read_cost": {"cpu": 0.10, "memory": 0.05, "io": -0.30},
    "memory_block_read_cost": {"cpu": 0.05, "memory": -0.20, "io": 0.10},
    "row_evaluate_cost": {"cpu": -0.25, "memory": 0.00, "io": -0.10},
    "key_compare_cost": {"cpu": -0.20, "memory": -0.10, "io": -0.05},
    "disk_temptable_create_cost": {"cpu": 0.05, "memory": 0.10, "io": -0.30},
    "disk_temptable_row_cost": {"cpu": 0.05, "memory": 0.10, "io": -0.30},
    "memory_temptable_create_cost": {"cpu": 0.10, "memory": -0.25, "io": 0.10},
    "memory_temptable_row_cost": {"cpu": 0.10, "memory": -0.25, "io": 0.10},
}
CONTAINERS = {
    "hybench_ap": "ra_mysql_gd_hybench", "hybench_tp": "ra_mysql_gd_hybench",
    "hybench_xp": "ra_mysql_gd_hybench", "job": "ra_mysql_gd_job",
    "tpcc": "ra_mysql_gd_benchbase", "tpch": "ra_mysql_gd_tpch",
}
PORTS = {"hybench_ap": 57751, "hybench_tp": 57751, "hybench_xp": 57751, "job": 57752, "tpcc": 57750, "tpch": 57753}
DATABASES = {"hybench_ap": "hybench", "hybench_tp": "hybench", "hybench_xp": "hybench", "job": "job", "tpcc": "tpcc10", "tpch": "tpch"}
BOTTLENECK_CAPABILITY = {
    "hybench_ap": {"cpu": 4.0, "memory": 2.0, "io": 100.0},
    "job": {"cpu": 4.0, "memory": 7.15, "io": 100.0},
    "tpch": {"cpu": 4.0, "memory": 2.2, "io": 100.0},
    "hybench_tp": {"cpu": 1.5, "memory": 8.0, "io": 100.0},
    "tpcc": {"cpu": 0.80, "memory": 8.0, "io": 100.0},
    "hybench_xp": {"cpu": 4.0, "memory": 8.0, "io": 0.57},
}
TOP_JOB = "1a.sql,1b.sql,1d.sql,6c.sql,11a.sql,11b.sql,21a.sql,21b.sql,21c.sql,27a.sql,27b.sql,27c.sql,31b.sql,33a.sql,33b.sql,33c.sql"


def _command(benchmark: str, pid: int, run_id: str, repeat_id: int) -> list[str]:
    output = ROOT / run_id / "measurements"
    if benchmark.startswith("hybench_"):
        workload = benchmark.rsplit("_", 1)[1]
        command = [
            "python", "mysql_hybench_runner.py", "--workload", workload, "--port", "57751",
            "--mysqld-pid", str(pid), "--warmup-duration-sec", "3", "--duration-sec", "10",
            "--random-seed", str(20260718 + repeat_id), "--output-root", str(output),
        ]
        if workload == "ap": command += ["--ap-clients", "2"]
        elif workload == "tp": command += ["--tp-clients", "16"]
        else: command += ["--xap-clients", "2", "--xtp-clients", "16"]
        return command
    if benchmark == "job":
        return [
            "python", "mysql_job_runner.py", "--port", "57752", "--mysqld-pid", str(pid),
            "--warmup-duration-sec", "5", "--duration-sec", "15", "--concurrency", "4",
            "--query-timeout-ms", "5000", "--query-names", TOP_JOB, "--output-root", str(output),
        ]
    return [
        "python", "mysql_benchmark_runner.py", "--benchmark", benchmark,
        "--port", str(PORTS[benchmark]), "--database", DATABASES[benchmark], "--mysqld-pid", str(pid),
        "--terminals", "32" if benchmark == "tpcc" else "4",
        "--warmup-duration-sec", "5", "--duration-sec", "15", "--output-root", str(output),
    ]


def _targets(benchmark: str, bottleneck: str) -> tuple[Dict[str, float], Dict[str, Any], Dict[str, float]]:
    if bottleneck != "none":
        capability = copy.deepcopy(BOTTLENECK_CAPABILITY[benchmark])
        target = "memory" if bottleneck == "memory" else bottleneck
        ranges = {
            "cpu": {"min_pct": 5.0, "max_pct": 95.0, "hard_overload_pct": 100.0},
            "memory": {"min_pct": 5.0, "max_pct": 95.0, "hard_overload_pct": 100.0},
            "io": {"min_pct": 0.0, "max_pct": 95.0, "hard_overload_pct": 100.0},
        }
        # A 0.94 calibrated baseline must fall by 5--10 percentage points and
        # end below 0.90, so guide the optimizer toward the exact [0.84, 0.89] gate.
        ranges[target] = {"min_pct": 84.0, "max_pct": 89.0, "hard_overload_pct": 90.0}
        weights = {"cpu": 1.0, "memory": 1.0, "io": 1.0}
        weights[target] = 4.0
        return capability, ranges, weights
    capability = {"cpu": 4.0, "memory": 8.0, "io": 100.0}
    ranges_by_benchmark = {
        "hybench_ap": {"cpu": (65, 88), "memory": (20, 88), "io": (0, 90)},
        "job": {"cpu": (85, 95), "memory": (75, 92), "io": (0, 90)},
        "tpch": {"cpu": (70, 92), "memory": (20, 88), "io": (0, 90)},
        "hybench_tp": {"cpu": (60, 88), "memory": (20, 88), "io": (1, 90)},
        "tpcc": {"cpu": (70, 92), "memory": (20, 88), "io": (1, 90)},
        "hybench_xp": {"cpu": (70, 92), "memory": (25, 88), "io": (1, 90)},
    }
    ranges = {
        key: {"min_pct": low, "max_pct": high, "hard_overload_pct": 100.0}
        for key, (low, high) in ranges_by_benchmark[benchmark].items()
    }
    return capability, ranges, {"cpu": 2.0, "memory": 1.0, "io": 1.0}


def build_config(run: Any, pids: Dict[str, int]) -> Dict[str, Any]:
    benchmark = run.benchmark
    capability, target_ranges, weights = _targets(benchmark, run.bottleneck)
    run_id = run.run_id
    target = "memory" if run.bottleneck == "memory" else run.bottleneck
    return {
        "mysql": {"mysql_binary": "mysql", "host": "127.0.0.1", "port": PORTS[benchmark],
                  "user": "root", "password": "CHANGE_ME", "database": DATABASES[benchmark],
                  "engine_name": "default", "device_type": 0, "query_timeout_sec": 30},
        "machine_spec": {"cpu_cores": 4, "mem_gb": 8, "disk_bw_mb": 100},
        "capability": capability,
        "cost_parameters": {"active": ACTIVE, "ranges": {name: {"min_multiplier": 0.25, "max_multiplier": 4.0} for name in ACTIVE}},
        "sensitivity_table": SENSITIVITY,
        "scoring": {"overload_threshold": 0.90, "score_mode": "band_target", "resource_weights": weights,
                    "resource_target_ranges": target_ranges},
        "optimizer": {
            "eta": 2.0, "max_iter": 5, "patience": 5, "gamma": 0.8, "min_eta": 0.001,
            "max_param_delta_ratio": 0.2, "min_param_delta_rel_pct": 10.0, "max_param_delta_rel_pct": 20.0,
            "adaptive_rejection_enabled": True, "adaptive_rejection_stop_after": 10,
            "exploration_acceptance_enabled": True, "exploration_acceptance_rel_tol": 0.02,
            "no_update_patience": 5, "aggressive_trial_enabled": True,
            "aggressive_trial_after_no_improve": 2, "aggressive_trial_scale_multiplier": 2.0,
            "target_bottleneck_resources": [] if run.bottleneck == "none" else [target],
            "target_accept_guard_upper_util": 0.90,
            "convergence_target_util": 0.865 if run.bottleneck != "none" else 0.80,
            "convergence_tolerance": 0.025, "post_convergence_explore_iter": 1,
        },
        "estimator": {"type": "command", "command": _command(benchmark, pids[benchmark], run_id, run.repeat_id),
                      "cwd": str(WORKSPACE), "timeout_sec": 300, "environment": {}, "final_revalidation": True},
        "output": {"directory": str(ROOT / run_id / "optimizer"), "verbose": False},
        "commit_best": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=WORKSPACE / "config/mysql_experiment_manifest_A25_E25_B7.json")
    parser.add_argument("--output-dir", type=Path, default=WORKSPACE / "config/generated/mysql_gd_A25_20260718")
    parser.add_argument("--pids-json", required=True, help="explicit benchmark-to-host-PID JSON from a prior docker inspect")
    args = parser.parse_args()
    runs = expand_runs(load_manifest(args.manifest))
    supplied = json.loads(args.pids_json)
    pids = {benchmark: int(supplied[benchmark]) for benchmark in CONTAINERS}
    if any(value <= 0 for value in pids.values()):
        raise ValueError("all supplied mysqld PIDs must be positive")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for run in runs:
        path = args.output_dir / f"{run.run_id}.json"
        path.write_text(json.dumps(build_config(run, pids), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"generated": len(runs), "directory": str(args.output_dir.resolve()), "pids": pids}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
