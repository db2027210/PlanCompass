#!/usr/bin/env python3
"""Freeze Top4-A v9 for launch-bound B sequence gates."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_xp_top4_algorithm_a_release_v1 as builder


builder.OUTPUT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_a_20260826_v9"
builder.CONTRACT = builder.OUTPUT / "algorithm_a_top4_contract.json"
builder.COORDINATION = builder.OUTPUT / "coordination_contract.json"
builder.SOURCES.update({
    "controller": ROOT / "scripts/run_mysql_algorithm_a_xp_top4_async_v9.py",
    "controller_predecessor": ROOT / "scripts/run_mysql_algorithm_a_xp_top4_async_v3.py",
    "scene_launcher": ROOT / "scripts/launch_mysql_algorithm_a_xp_top4_scene_v9.py",
    "current_authority_refresh": ROOT / "scripts/refresh_mysql_xp_combined_v4_live_authority_v2.py",
    "dynamic_noninterference_gate": ROOT / "scripts/mysql_cross_project_wave_coordination_v8.py",
    "resource_freeze": ROOT / "config/formal/frozen_mysql_xp_cpu_disjoint_slots_20260826_v4/resource_successor.json",
    "xp_reset": ROOT / "scripts/reset_mysql_xp_data_planes_four_worker_v1.py",
    "xp_four_worker_authority": ROOT / "scripts/run_mysql_xp_four_worker_authority_v1.py",
    "hybench_adaptive_runner_300s": ROOT / "config/formal/frozen_mysql_async_outer_20260820_v38/runtime/hybench-adaptive-runner-v4.jar",
})


def main() -> int:
    result = builder.main()
    coordination = builder.load(builder.COORDINATION)
    coordination.update({
        "maximum_quiet_wait_seconds": 180.0,
        "required_clean_seconds_unchanged": True,
        "dynamic_gate_release": "v63",
        "parallel_expansion_maximum_workers_per_scene": 4,
        "maximum_concurrent_xp_mysql_workers": 8,
    })
    builder.atomic(builder.COORDINATION, coordination)
    contract = builder.load(builder.CONTRACT)
    contract.update({
        "rolling_gate_observation_budget_seconds": 180,
        "resource_envelope_version": "20260826_v4",
        "algorithm_b_sequence_gate_mode": "launch_bound_terminal_state_sha256",
        "cpu_algorithm_b_predecessor_run": "runtime_launcher_argument",
        "io_algorithm_b_predecessor_run": "runtime_launcher_argument",
        "parallel_expansion_policy": "four_physical_workers_per_scene_at_each_wave_boundary",
        "maximum_concurrent_xp_mysql_workers": 8,
    })
    contract["source_bindings"]["coordination_contract"] = builder.binding(
        builder.COORDINATION
    )
    builder.atomic(builder.CONTRACT, contract)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
