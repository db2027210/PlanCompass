#!/usr/bin/env python3
"""Freeze XP8 Algorithm-A v2 with refreshed measurement-adapter authority."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_xp_top4_algorithm_a_release_v1 as builder


OUTPUT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_a_mixed_eight_worker_20260827_v2"
builder.OUTPUT = OUTPUT
builder.CONTRACT = OUTPUT / "algorithm_a_top4_contract.json"
builder.COORDINATION = OUTPUT / "coordination_contract.json"
builder.SOURCES.update({
    "builder": Path(__file__).resolve(),
    "controller": ROOT / "scripts/run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v2.py",
    "scene_launcher": ROOT / "scripts/launch_mysql_algorithm_a_xp_top4_mixed_eight_worker_v2.py",
    "b_to_a_monitor": ROOT / "scripts/monitor_mysql_b_then_a_mixed_eight_worker_scene_v2.py",
    "current_authority_refresh": ROOT / "scripts/refresh_mysql_xp8_live_authority_from_reset_v1.py",
    "dynamic_noninterference_gate": ROOT / "scripts/mysql_cross_project_wave_coordination_v17.py",
    "resource_freeze": ROOT / "config/formal/frozen_mysql_xp_eight_worker_20260827_v1/xp_effective_resource_successor.json",
    "xp_reset": ROOT / "scripts/reset_mysql_xp_data_planes_mixed_eight_worker_scene_v1.py",
    "xp_reset_predecessor": ROOT / "scripts/reset_mysql_xp_data_planes_mixed_eight_worker_v1.py",
    "c49_measurement_adapter_runtime": ROOT / "scripts/run_mysql_algorithm_c_async_scene_v49.py",
    "c49_measurement_adapter_contract": ROOT / "config/formal/frozen_mysql_algorithm_a_c49_measurement_adapter_20260827_v1/algorithm_c_measurement_adapter_contract.json",
})


def main() -> int:
    result = builder.main()
    coordination = builder.load(builder.COORDINATION)
    coordination.update({
        "maximum_quiet_wait_seconds": 600.0,
        "maximum_idle_load1": 512.0,
        "dynamic_gate_release": "v73",
        "parallel_expansion_maximum_workers_per_scene": 8,
        "maximum_concurrent_xp_mysql_workers": 16,
        "docker_control_plane_required_per_wave": False,
    })
    builder.atomic(builder.COORDINATION, coordination)
    contract = builder.load(builder.CONTRACT)
    contract.update({
        "physical_replay_parallelism_per_scene": 8,
        "candidate_batches": [[1, 2, 3, 4, 5, 6, 7, 8]],
        "rolling_gate_observation_budget_seconds": 600,
        "resource_envelope_version": "mixed_xp_eight_worker_20260827_v1",
        "algorithm_b_sequence_gate_mode": "launch_bound_terminal_state_sha256",
        "parallel_expansion_policy": "eight_physical_workers_per_scene_at_each_wave_boundary",
        "maximum_concurrent_xp_mysql_workers": 16,
        "dynamic_gate_release": "v73",
        "measurement_adapter_successor": "c49_current_sources_for_algorithm_a_v1",
    })
    for scene in contract["scenes"].values():
        scene.update({"physical_workers": 8, "physical_waves_per_round": 2})
    contract["source_bindings"]["coordination_contract"] = builder.binding(
        builder.COORDINATION
    )
    builder.atomic(builder.CONTRACT, contract)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
