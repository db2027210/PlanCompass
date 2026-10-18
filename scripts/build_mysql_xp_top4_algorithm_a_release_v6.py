#!/usr/bin/env python3
"""Freeze Top4-A v6 for the current CPU/IO Top4-B sequence gates."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_xp_top4_algorithm_a_release_v1 as builder


builder.OUTPUT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_a_20260826_v6"
builder.CONTRACT = builder.OUTPUT / "algorithm_a_top4_contract.json"
builder.COORDINATION = builder.OUTPUT / "coordination_contract.json"
builder.SOURCES.update({
    "controller": ROOT / "scripts/run_mysql_algorithm_a_xp_top4_async_v6.py",
    "controller_predecessor": ROOT / "scripts/run_mysql_algorithm_a_xp_top4_async_v3.py",
    "scene_launcher": ROOT / "scripts/launch_mysql_algorithm_a_xp_top4_scene_v6.py",
    "abc_pipeline_monitor": ROOT / "scripts/monitor_mysql_four_scene_abc_pipeline_v8.py",
    "monitor_launcher": ROOT / "scripts/launch_mysql_four_scene_abc_top4_monitor_v8.py",
    "current_authority_refresh": ROOT / "scripts/refresh_mysql_xp_combined_v4_live_authority_v2.py",
    "dynamic_noninterference_gate": ROOT / "scripts/mysql_cross_project_wave_coordination_v8.py",
    "resource_freeze": ROOT / "config/formal/frozen_mysql_xp_cpu_disjoint_slots_20260826_v4/resource_successor.json",
    "hybench_adaptive_runner_300s": ROOT / "config/formal/frozen_mysql_async_outer_20260820_v38/runtime/hybench-adaptive-runner-v4.jar",
})


def main() -> int:
    result = builder.main()
    coordination = builder.load(builder.COORDINATION)
    coordination["maximum_quiet_wait_seconds"] = 180.0
    coordination["required_clean_seconds_unchanged"] = True
    coordination["dynamic_gate_release"] = "v63"
    coordination["parallel_expansion_maximum_workers_per_scene"] = 4
    coordination["maximum_concurrent_xp_mysql_workers"] = 8
    builder.atomic(builder.COORDINATION, coordination)
    contract = builder.load(builder.CONTRACT)
    contract.update({
        "rolling_gate_observation_budget_seconds": 180,
        "resource_envelope_version": "20260826_v4",
        "cpu_algorithm_b_predecessor_run": (
            "v255_to_b_top4_fresh_20260826_attempt028_xp_cpu"
        ),
        "io_algorithm_b_predecessor_run": (
            "v257_to_b_top4_fresh_20260826_attempt020_xp_io"
        ),
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
