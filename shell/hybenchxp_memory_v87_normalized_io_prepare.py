#!/usr/bin/env python3
"""Prepare the Memory v87 fixed-capability config and Phase A evidence."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any, Dict, List


REPO = Path("/home/anonymous/program/resource_adaptive")
BASE_CONFIG = REPO / "config/binary_plan_tuning_hybenchxp_memory_fair_v85_same_vector_comp_w1_20260715.json"
OUTPUT_CONFIG = REPO / "config/binary_plan_tuning_hybenchxp_memory_fair_v87_normalized_io_20260715.json"
W1_CONFIG = REPO / "config/binary_plan_tuning_hybenchxp_memory_fair_v87_normalized_io_w1_20260715.json"
V85_AB = Path(
    "/disk2/resource_adaptive/binary_plan_tuning_hybenchxp/runs/"
    "hyxp_memory_fair_v85_same_vector_comp_ctc10_apheavy_20260715_073115/component_ab_summary.json"
)
PHASE_A_JSON = REPO / "report/bonect/hybenchxp_memory_fair_v87_normalized_io_phase_a_20260715.json"


def worker(index: int) -> Dict[str, Any]:
    return {
        "name": f"cpu_mem_w{index}",
        "targets": ["memory"],
        "container": f"binary_plan_tuning_hybenchxp_rebuild_20260703_180311_cpu_mem_w{index}",
        "host": "127.0.0.1",
        "port": 57432 + index,
        "dbname": "hybench_sf10x",
        "user": "postgres",
        "password": "postgres",
        "data_dir": (
            "/disk2/resource_adaptive/binary_plan_tuning_hybenchxp_rebuild_20260703_180311/"
            f"workers/cpu_mem_w{index}/pgdata"
        ),
        "disk_root": "/disk2",
        "cpuset_cpus": f"{102 + 10 * index}-{111 + 10 * index}",
    }


def prepare_config() -> Dict[str, Any]:
    data = json.loads(BASE_CONFIG.read_text(encoding="utf-8"))
    tuning = data["binary_plan_tuning"]
    policy = tuning["acceptance_policy"]
    capacity = {"memory_gib": 6.4, "cpu_cores": 10, "io_mib_s": 200}
    policy["resource_capacity"] = copy.deepcopy(capacity)
    policy["target_overrides"]["memory"]["resource_capacity"] = copy.deepcopy(capacity)
    policy["target_sweet_spot"]["io"] = {"min_pct": 50, "max_pct": 75}
    tuning["binary_search"]["max_rounds"] = 23
    tuning["validation"]["parallel_baseline"]["target_overrides"]["memory"]["parallelism"] = 8
    tuning["validation"]["docker_workers"]["cpu_memory_parallelism"] = 8
    tuning["validation"]["docker_workers"]["workers"] = [worker(i) for i in range(1, 9)]
    tuning["fair_memory_construction"] = {
        "version": "v87_v85_ctc10_apheavy_normalized_physical_io",
        "physical_memory_gib": 6.4,
        "docker_memory_bytes": 6871947674,
        "cpu_capacity_cores": 10,
        "physical_io_capability_mib_s": 200,
        "io_metric_source": "docker_cgroup_v1_blkio",
        "io_normalization": "measured physical MiB/s / fixed 200 MiB/s capability",
        "workload_frozen": "AP-6 v80_hashagg_fullscan 70% + AP-8 nested_hashagg_type 30%",
        "candidate_family": "exactly seven PostgreSQL planner cost parameters",
        "phase_a_evidence": str(PHASE_A_JSON),
    }
    OUTPUT_CONFIG.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    w1_data = copy.deepcopy(data)
    w1_validation = w1_data["binary_plan_tuning"]["validation"]
    w1_validation["parallel_baseline"]["target_overrides"]["memory"]["parallelism"] = 1
    w1_validation["docker_workers"]["cpu_memory_parallelism"] = 1
    w1_validation["docker_workers"]["workers"] = [worker(1)]
    W1_CONFIG.write_text(json.dumps(w1_data, indent=2) + "\n", encoding="utf-8")
    return data


def phase_a() -> Dict[str, Any]:
    ab = json.loads(V85_AB.read_text(encoding="utf-8"))
    baseline = ab["default"]
    candidate = ab["candidate"]
    rows: List[Dict[str, Any]] = []
    for capability in (180, 190, 200, 210, 220, 240):
        baseline_pct = 100.0 * float(baseline["io_mib_s"]) / capability
        candidate_pct = 100.0 * float(candidate["io_mib_s"]) / capability
        eligible = baseline_pct < 80.0 and 50.0 <= candidate_pct <= 75.0
        rows.append({
            "io_capability_mib_s": capability,
            "baseline_physical_io_mib_s": baseline["io_mib_s"],
            "candidate_physical_io_mib_s": candidate["io_mib_s"],
            "baseline_normalized_io_pct": baseline_pct,
            "candidate_normalized_io_pct": candidate_pct,
            "baseline_non_bottleneck": baseline_pct < 80.0,
            "candidate_io_sweet": 50.0 <= candidate_pct <= 75.0,
            "eligible": eligible,
        })
    result = {
        "phase": "A",
        "method": "batch capability census over one prior same-worker real A/B; no estimator values",
        "source_real_ab": str(V85_AB),
        "workload": "frozen v85 ctc10 AP-heavy composition",
        "rows": rows,
        "eligible_set": [row for row in rows if row["eligible"]],
        "selected_io_capability_mib_s": 200,
        "selection_reason": "round physical capability; candidate lands at 73.126% normalized IO",
        "fallback_db_qps": 114.12708344892893,
    }
    PHASE_A_JSON.parent.mkdir(parents=True, exist_ok=True)
    PHASE_A_JSON.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase-a", action="store_true")
    args = parser.parse_args()
    prepare_config()
    if args.phase_a:
        print(json.dumps(phase_a(), indent=2), flush=True)
    else:
        print(str(OUTPUT_CONFIG), flush=True)


if __name__ == "__main__":
    main()
