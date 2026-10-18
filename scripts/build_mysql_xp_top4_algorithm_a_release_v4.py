#!/usr/bin/env python3
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_xp_top4_algorithm_a_release_v1 as builder

builder.OUTPUT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_a_20260826_v4"
builder.CONTRACT = builder.OUTPUT / "algorithm_a_top4_contract.json"
builder.COORDINATION = builder.OUTPUT / "coordination_contract.json"
builder.SOURCES.update({
    "controller": ROOT / "scripts/run_mysql_algorithm_a_xp_top4_async_v4.py",
    "controller_predecessor": ROOT / "scripts/run_mysql_algorithm_a_xp_top4_async_v3.py",
    "scene_launcher": ROOT / "scripts/launch_mysql_algorithm_a_xp_top4_scene_v4.py",
    "current_authority_refresh": ROOT / "scripts/refresh_mysql_xp_combined_v4_live_authority_v2.py",
    "dynamic_noninterference_gate": ROOT / "scripts/mysql_cross_project_wave_coordination_v8.py",
    "resource_freeze": ROOT / "config/formal/frozen_mysql_xp_cpu_disjoint_slots_20260826_v4/resource_successor.json",
})


def main() -> int:
    result = builder.main()
    coordination = builder.load(builder.COORDINATION)
    coordination["maximum_quiet_wait_seconds"] = 180.0
    coordination["required_clean_seconds_unchanged"] = True
    coordination["dynamic_gate_release"] = "v63"
    builder.atomic(builder.COORDINATION, coordination)
    contract = builder.load(builder.CONTRACT)
    contract["rolling_gate_observation_budget_seconds"] = 180
    contract["resource_envelope_version"] = "20260826_v4"
    contract["source_bindings"]["coordination_contract"] = builder.binding(builder.COORDINATION)
    builder.atomic(builder.CONTRACT, contract)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
