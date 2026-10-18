#!/usr/bin/env python3
"""Freeze Top4-B v33 with score bridge and recovered IO-C gate."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_xp_top4_algorithm_b_release_v1 as builder

builder.OUTPUT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_20260826_v33"
builder.CONTROLLER = ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v24.py"
builder.COORDINATION = builder.OUTPUT / "coordination_contract.json"
builder.CONTRACT = builder.OUTPUT / "algorithm_b_top4_contract.json"
builder.BOOTSTRAP_VERSION = "v33"
builder.SOURCES.update({
    "controller": builder.CONTROLLER,
    "controller_predecessor": ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v13.py",
    "cpu_score_continuation_controller": ROOT / "scripts/run_mysql_algorithm_b_cpu_outer1_continuation_v25.py",
    "scene_launcher": ROOT / "scripts/launch_mysql_xp_top4_algorithm_b_scene_v15.py",
    "cpu_score_continuation_launcher": ROOT / "scripts/launch_mysql_algorithm_b_cpu_outer1_continuation_v10.py",
    "abc_pipeline_monitor": ROOT / "scripts/monitor_mysql_four_scene_abc_pipeline_v9.py",
    "monitor_launcher": ROOT / "scripts/launch_mysql_four_scene_abc_top4_monitor_v9.py",
    "four_lane_scheduler": ROOT / "scripts/mysql_algorithm_b_fresh_parallel_4x_scheduler_v26.py",
    "dynamic_noninterference_gate": ROOT / "scripts/mysql_cross_project_wave_coordination_v8.py",
    "scene_scoped_post_reset_settle": ROOT / "scripts/mysql_xp_post_reset_scene_scoped_settle_v1.py",
    "xp_four_worker_resource_freeze": ROOT / "config/formal/frozen_mysql_xp_cpu_disjoint_slots_20260826_v4/resource_successor.json",
    "current_authority_refresh": ROOT / "scripts/refresh_mysql_xp_combined_v4_live_authority_v2.py",
    "workload_adapter_300s_drain": ROOT / "mysql_workload_adapter_v2.py",
    "hybench_adaptive_runner_300s": ROOT / "config/formal/frozen_mysql_async_outer_20260820_v38/runtime/hybench-adaptive-runner-v4.jar",
    "hybench_adaptive_runner_builder": ROOT / "scripts/build_mysql_hybench_adaptive_runner_v4.py",
})


def main() -> int:
    result = builder.main()
    coordination = builder.load(builder.COORDINATION)
    coordination["maximum_quiet_wait_seconds"] = 180.0
    coordination["dynamic_gate_release"] = "v63"
    coordination["parallel_expansion_maximum_workers_per_scene"] = 4
    coordination["maximum_concurrent_xp_mysql_workers"] = 8
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
        "graceful_quiescence_seconds": 300,
        "score_api_raw_payload_bridge": True,
        "post_reset_settle_policy": "scene_reset_complete_plus_syncfs_sibling_io_diagnostic_only",
        "whole_device_quiet_required": False,
        "rolling_gate_observation_budget_seconds": 180,
        "io_algorithm_c_sequence_gate_path": (
            "history/mysql_four_scene_20260809/"
            "v263_to_c_top4_io_empty_wave_20260826_attempt024_xp_io/run_state.json"
        ),
        "parallel_expansion_policy": "four_physical_workers_per_scene_at_each_wave_boundary",
        "maximum_concurrent_xp_mysql_workers": 8,
    })
    contract["source_bindings"]["coordination_contract"] = builder.binding(builder.COORDINATION)
    builder.atomic(builder.CONTRACT, contract)
    for suffix in ("cpu", "io"):
        path = (
            builder.HISTORY
            / f"v161_to_b_top4_fresh_bootstrap_20260826_v33_{suffix}"
            / "bootstrap_manifest.json"
        )
        manifest = builder.load(path)
        manifest["release_contract_sha256"] = builder.sha(builder.CONTRACT)
        builder.atomic(path, manifest)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
