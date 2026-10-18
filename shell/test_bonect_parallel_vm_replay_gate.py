#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.bonect_parallel_vm_replay import (
    aggregate_results,
    baseline_gate,
    cached_anchor_matches,
    drift_gate,
    qps,
)


COST_PARAMS = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}


def control_row(candidate_id: str, db_qps: float, cpu: float) -> dict:
    return {
        "candidate_id": candidate_id,
        "params": dict(COST_PARAMS),
        "status": "ok",
        "db_delta": {"db_qps": db_qps},
        # Conflicting benchmark QPS proves DB QPS has precedence.
        "benchmark": {"xp_qps": "0.01"},
        "actual_resource": {"cpu": cpu, "memory": 1.0, "io": 50.0},
        "actual_utilization_pct": {"cpu": cpu, "memory": 20.0, "io": 30.0},
        "raw_resource_stats": {
            "stable_sample_count": {"cpu": 50, "memory": 50, "io": 50},
            "workload_exit": {"returncode": 0},
            "cpu_source": {"value": "vm_runtime_ssh"},
            "memory_source": {"value": "vm_meminfo_used"},
            "io_source": {"value": "vm_disk_device"},
        },
    }


def main() -> None:
    workers = [
        {
            "worker_id": f"w{index}",
            "scenario_configs": {"cpu": str(ROOT / "config/optimizer_config.json")},
            "config_override": {
                "metrics": {
                    "vm_runtime": {
                        "cpu_mode": "vm_busy",
                        "memory_mode": "vm_used",
                        "io_mode": "disk_device",
                    }
                }
            },
        }
        for index in range(4)
    ]
    rows = {}
    for index in range(4):
        worker_id = f"w{index}"
        rows[worker_id] = [
            control_row(f"__baseline__{worker_id}", 100.0 + index, 85.0 + index / 2),
            control_row(f"__anchor__{worker_id}", 120.0 + index * 1.2, 80.0 + index / 2),
        ]
    args = argparse.Namespace(
        max_baseline_qps_drift_pct=5.0,
        max_baseline_resource_drift_pp=3.0,
        max_anchor_qps_delta_spread_pp=5.0,
        max_anchor_resource_delta_spread_pp=3.0,
        scenario="cpu",
    )
    gate = drift_gate(rows, workers, args)
    assert gate["passed"], gate
    baseline_only_rows = {
        worker_id: [copy.deepcopy(worker_rows[0])]
        for worker_id, worker_rows in rows.items()
    }
    assert baseline_gate(baseline_only_rows, workers, args)["passed"]
    assert qps(rows["w0"][0]) == 100.0
    assert cached_anchor_matches(rows, workers, {"params": dict(COST_PARAMS)})

    fixed_horizon_rows = copy.deepcopy(rows)
    for worker_rows in fixed_horizon_rows.values():
        for row in worker_rows:
            row["raw_resource_stats"]["workload_exit"] = {
                "returncode": -15,
                "sigterm_treated_as_success": True,
                "fixed_horizon_reached": True,
            }
    assert drift_gate(fixed_horizon_rows, workers, args)["passed"]

    changed_anchor = dict(COST_PARAMS)
    changed_anchor["seq_page_cost"] = 2.0
    assert not cached_anchor_matches(rows, workers, {"params": changed_anchor})

    failed_rows = copy.deepcopy(rows)
    failed_rows["w2"][1]["raw_resource_stats"]["workload_exit"]["returncode"] = 143
    failed_gate = drift_gate(failed_rows, workers, args)
    assert not failed_gate["passed"]
    assert "anchor_workload_not_clean:w2" in failed_gate["failures"]

    bad_source_rows = copy.deepcopy(rows)
    bad_source_rows["w1"][1]["raw_resource_stats"]["cpu_source"]["value"] = "vm_pg_proc_cpu"
    bad_source_gate = drift_gate(bad_source_rows, workers, args)
    assert not bad_source_gate["passed"]
    assert any(
        failure.startswith("anchor_cpu_source_mismatch:w1:")
        for failure in bad_source_gate["failures"]
    )

    mixed_bottleneck_rows = copy.deepcopy(rows)
    mixed_bottleneck_rows["w2"][0]["actual_utilization_pct"]["io"] = 85.0
    mixed_gate = drift_gate(mixed_bottleneck_rows, workers, args)
    assert not mixed_gate["passed"]
    assert "baseline_non_target_bottleneck:w2:io" in mixed_gate["failures"]

    memory_workers = copy.deepcopy(workers[:1])
    memory_workers[0]["scenario_configs"] = {
        "memory": str(ROOT / "config/optimizer_config.json")
    }
    memory_args = copy.deepcopy(args)
    memory_args.scenario = "memory"
    memory_row = control_row("__baseline__w0", 100.0, 70.0)
    memory_row["actual_utilization_pct"].update(
        {"cpu": 70.0, "memory": 85.0, "io": 30.0}
    )
    valid_memory_gate = baseline_gate({"w0": [memory_row]}, memory_workers, memory_args)
    assert valid_memory_gate["passed"], valid_memory_gate
    memory_row["actual_utilization_pct"]["memory"] = 48.0
    invalid_memory_gate = baseline_gate({"w0": [memory_row]}, memory_workers, memory_args)
    assert not invalid_memory_gate["passed"]
    assert "baseline_target_not_bottleneck:w0:memory" in invalid_memory_gate["failures"]

    goal_workers = copy.deepcopy(workers[:2])
    for worker in goal_workers:
        worker["scenario_configs"] = {
            "memory": str(ROOT / "config/optimizer_config.json")
        }
    goal_rows = {}
    for index, worker in enumerate(goal_workers):
        worker_id = worker["worker_id"]
        baseline = control_row(f"__baseline__{worker_id}", 100.0, 55.0)
        baseline["actual_utilization_pct"] = {"cpu": 55.0, "memory": 85.0, "io": 35.0}
        anchor = control_row(f"__anchor__{worker_id}", 250.0 + index * 20.0, 85.0)
        anchor["actual_utilization_pct"] = {"cpu": 85.0, "memory": 60.0, "io": 20.0}
        goal_rows[worker_id] = [baseline, anchor]
    goal_args = copy.deepcopy(args)
    goal_args.scenario = "memory"
    strict_goal_gate = drift_gate(goal_rows, goal_workers, goal_args)
    assert not strict_goal_gate["passed"]
    assert strict_goal_gate["failures"] == ["anchor_qps_delta_spread"]
    goal_args.allow_goal_rich_anchor_qps_spread = True
    overridden_goal_gate = drift_gate(goal_rows, goal_workers, goal_args)
    assert overridden_goal_gate["passed"], overridden_goal_gate
    assert overridden_goal_gate["goal_rich_anchor_qps_spread_override"]["applied"]
    assert all(
        record["anchor_long_term_goal_hit"]
        for record in overridden_goal_gate["workers"]
    )

    candidate = {
        "candidate_id": "cand0",
        "params": dict(COST_PARAMS),
        "estimated_db_qps_delta_pct": 42.0,
        "plan_hash": "plan-0",
        "plan_delta_features": {"weighted_gather_count": -2.0},
    }
    actual = control_row("cand0", 125.0, 80.0)
    aggregate_gate = {
        "workers": [
            {
                "worker_id": "w0",
                "baseline_qps": 100.0,
                "baseline_utilization_pct": {"cpu": 85.0, "memory": 20.0, "io": 30.0},
                "baseline": control_row("__baseline__w0", 100.0, 85.0),
            }
        ]
    }
    aggregated = aggregate_results([candidate], {"w0": [actual]}, aggregate_gate)
    assert len(aggregated) == 1
    assert aggregated[0]["estimated_db_qps_delta_pct"] == 42.0
    assert aggregated[0]["plan_hash"] == "plan-0"
    assert aggregated[0]["plan_delta_features"] == {"weighted_gather_count": -2.0}
    assert aggregated[0]["db_qps_delta_pct"] == 25.0
    print("bonect parallel VM replay drift gate tests passed", flush=True)


if __name__ == "__main__":
    main()
