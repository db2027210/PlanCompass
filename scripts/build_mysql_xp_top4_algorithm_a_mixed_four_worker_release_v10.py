#!/usr/bin/env python3
"""Freeze XP Top4-A for the mixed-SSD v4 four-worker cohorts."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_xp_top4_algorithm_a_release_v1 as builder


builder.OUTPUT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_a_mixed_four_worker_20260827_v10"
builder.CONTRACT = builder.OUTPUT / "algorithm_a_top4_contract.json"
builder.COORDINATION = builder.OUTPUT / "coordination_contract.json"
builder.SOURCES.update({
    "builder": Path(__file__).resolve(),
    "controller": ROOT / "scripts/run_mysql_algorithm_a_xp_top4_mixed_four_worker_v10.py",
    "controller_predecessor": ROOT / "scripts/run_mysql_algorithm_a_xp_top4_async_v1.py",
    "scene_launcher": ROOT / "scripts/launch_mysql_algorithm_a_xp_top4_mixed_four_worker_v10.py",
    "scene_launcher_predecessor": ROOT / "scripts/launch_mysql_algorithm_a_xp_top4_scene_v9.py",
    "current_authority_refresh": ROOT / "scripts/refresh_mysql_xp_mixed_v4_live_authority_v3.py",
    "authority_refresh_predecessor": ROOT / "scripts/refresh_mysql_xp4_live_authority_from_reset_v1.py",
    "dynamic_noninterference_gate": ROOT / "scripts/mysql_cross_project_wave_coordination_v16.py",
    "resource_freeze": ROOT / "config/formal/frozen_mysql_four_scene_mixed_disk_four_worker_20260827_v4/four_scene_effective_resource_successor.json",
    "xp_reset": ROOT / "scripts/reset_mysql_xp_data_planes_mixed_four_worker_v2.py",
    "xp_reset_implementation": ROOT / "scripts/reset_mysql_xp_data_planes_mixed_four_worker_v1.py",
})


def main() -> int:
    result = builder.main()
    coordination = builder.load(builder.COORDINATION)
    coordination.update({
        "per_wave_quiet_seconds": 15.0,
        "maximum_quiet_wait_seconds": 600.0,
        "maximum_idle_load1": 512.0,
        "dynamic_gate_release": "v72",
        "parallel_expansion_maximum_workers_per_scene": 4,
        "maximum_concurrent_xp_mysql_workers": 8,
    })
    builder.atomic(builder.COORDINATION, coordination)
    contract = builder.load(builder.CONTRACT)
    contract.update({
        "rolling_gate_observation_budget_seconds": 600,
        "resource_envelope_version": "mixed_disk_20260827_v4",
        "algorithm_b_sequence_gate_mode": "launch_bound_terminal_state_sha256",
        "cpu_algorithm_b_predecessor_run": "runtime_launcher_argument",
        "io_algorithm_b_predecessor_run": "runtime_launcher_argument",
        "parallel_expansion_policy": "four_physical_workers_per_scene_at_each_wave_boundary",
        "maximum_concurrent_xp_mysql_workers": 8,
    })
    contract["source_bindings"]["coordination_contract"] = builder.binding(builder.COORDINATION)
    builder.atomic(builder.CONTRACT, contract)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
