#!/usr/bin/env python3
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_xp_top4_algorithm_b_release_v1 as builder

builder.OUTPUT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_20260826_v26"
builder.CONTROLLER = ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v17.py"
builder.COORDINATION = builder.OUTPUT / "coordination_contract.json"
builder.CONTRACT = builder.OUTPUT / "algorithm_b_top4_contract.json"
builder.BOOTSTRAP_VERSION = "v26"
builder.SOURCES.update({
    "controller": builder.CONTROLLER,
    "controller_predecessor": ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v16.py",
    "four_lane_scheduler": ROOT / "scripts/mysql_algorithm_b_fresh_parallel_4x_scheduler_v26.py",
    "dynamic_noninterference_gate": ROOT / "scripts/mysql_cross_project_wave_coordination_v8.py",
    "scene_scoped_post_reset_settle": ROOT / "scripts/mysql_xp_post_reset_scene_scoped_settle_v1.py",
    "xp_four_worker_resource_freeze": ROOT / "config/formal/frozen_mysql_xp_cpu_disjoint_slots_20260826_v4/resource_successor.json",
    "current_authority_refresh": ROOT / "scripts/refresh_mysql_xp_combined_v4_live_authority_v2.py",
    "workload_adapter_300s_drain": ROOT / "mysql_workload_adapter_v2.py",
    "scene_launcher": ROOT / "scripts/launch_mysql_algorithm_b_cpu_rank13_continuation_v4.py",
})


def main() -> int:
    result = builder.main()
    coordination = builder.load(builder.COORDINATION)
    coordination["maximum_quiet_wait_seconds"] = 180.0
    coordination["dynamic_gate_release"] = "v63"
    builder.atomic(builder.COORDINATION, coordination)
    contract = builder.load(builder.CONTRACT)
    contract.update({
        "baseline_accounting": {
            "physical_lane_count": 4,
            "aggregate_divisor": "validated_lane_count",
            "two_lane_literal_prohibited": True,
        },
        "calibration_candidate_batches": [
            [1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12], [13, 14, 15]
        ],
        "calibration_replays_per_scene": 15,
        "calibration_parallelism": 4,
        "technical_continuation": {
            "source_run": "v234_to_b_top4_fresh_20260826_attempt021_xp_cpu",
            "completed_calibration_points_reused": list(range(1, 13)) + [14, 15],
            "failed_calibration_points_replayed": [13],
            "partial_failed_wave_reuse_limited_to_valid_attributable_lanes": True,
            "accepted_or_rejected_outer_credit_increment": 0,
        },
        "graceful_quiescence_seconds": 300,
        "post_reset_settle_policy": "scene_reset_complete_plus_syncfs_sibling_io_diagnostic_only",
        "whole_device_quiet_required": False,
        "rolling_gate_observation_budget_seconds": 180,
        "abandoned_unlaunched_predecessor_release": "20260826_v25",
    })
    contract["source_bindings"]["coordination_contract"] = builder.binding(builder.COORDINATION)
    builder.atomic(builder.CONTRACT, contract)
    for suffix in ("cpu", "io"):
        path = builder.HISTORY / f"v161_to_b_top4_fresh_bootstrap_20260826_v26_{suffix}" / "bootstrap_manifest.json"
        manifest = builder.load(path)
        manifest["release_contract_sha256"] = builder.sha(builder.CONTRACT)
        builder.atomic(path, manifest)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
