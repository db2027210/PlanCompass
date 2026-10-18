#!/usr/bin/env python3
"""Freeze current CPU-A-slot four-worker XP Top4-A."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_xp_top4_algorithm_a_release_v1 as builder


builder.OUTPUT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_a_cpu_a_slots_four_worker_20260828_v13"
builder.CONTRACT = builder.OUTPUT / "algorithm_a_top4_contract.json"
builder.COORDINATION = builder.OUTPUT / "coordination_contract.json"
builder.SOURCES.update({
    "builder": Path(__file__).resolve(),
    "controller": ROOT / "scripts/run_mysql_algorithm_a_xp_top4_cpu_a_slots_four_worker_v1.py",
    "scene_launcher": ROOT / "scripts/launch_mysql_algorithm_a_xp_top4_cpu_a_slots_four_worker_scene_v1.py",
    "current_authority_refresh": ROOT / "scripts/refresh_mysql_cpu_a_slots_four_worker_scene_authority_v1.py",
    "dynamic_noninterference_gate": ROOT / "scripts/mysql_cross_project_wave_coordination_cpu_a_slots_v13.py",
    "resource_freeze": ROOT / "config/formal/frozen_mysql_four_scene_cpu_a_slots_four_worker_20260828_v3/four_scene_effective_resource_successor.json",
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
        "dynamic_gate_release": "cpu_a_slots_v13",
        "parallel_expansion_maximum_workers_per_scene": 4,
        "maximum_concurrent_xp_mysql_workers": 8,
        "docker_control_plane_required_per_wave": False,
    })
    builder.atomic(builder.COORDINATION, coordination)
    contract = builder.load(builder.CONTRACT)
    contract.update({
        "rolling_gate_observation_budget_seconds": 600,
        "resource_envelope_version": "cpu_a_slots_four_worker_20260828_v3",
        "algorithm_b_sequence_gate_mode": "launch_bound_terminal_state_sha256",
        "cpu_algorithm_b_predecessor_run": "runtime_launcher_argument",
        "io_algorithm_b_predecessor_run": "runtime_launcher_argument",
        "parallel_expansion_policy": "four_physical_workers_per_scene_at_each_wave_boundary",
        "maximum_concurrent_xp_mysql_workers": 8,
        "dynamic_gate_release": "cpu_a_slots_v13",
    })
    contract["source_bindings"]["coordination_contract"] = builder.binding(
        builder.COORDINATION
    )
    builder.atomic(builder.CONTRACT, contract)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
