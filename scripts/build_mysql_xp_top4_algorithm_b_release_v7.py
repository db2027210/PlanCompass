#!/usr/bin/env python3
"""Freeze Top4 B v16 after passing the current-gap authority."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_xp_top4_algorithm_b_release_v1 as base


base.OUTPUT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_20260826_v16"
base.CONTROLLER = ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v7.py"
base.COORDINATION = base.OUTPUT / "coordination_contract.json"
base.CONTRACT = base.OUTPUT / "algorithm_b_top4_contract.json"
base.BOOTSTRAP_VERSION = "v16"
base.SOURCES.update({
    "controller": base.CONTROLLER,
    "controller_predecessor": ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v6.py",
    "controller_core": ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v1.py",
    "four_lane_scheduler": ROOT / "scripts/mysql_algorithm_b_fresh_parallel_4x_scheduler_v26.py",
    "dynamic_noninterference_gate": ROOT / "scripts/mysql_cross_project_wave_coordination_v7.py",
    "xp_four_worker_resource_freeze": (
        ROOT / "config/formal/frozen_mysql_xp_cpu_disjoint_slots_20260826_v4"
        / "resource_successor.json"
    ),
    "current_authority_refresh": ROOT / "scripts/refresh_mysql_xp_combined_v4_live_authority_v1.py",
    "slot_transition_capture": ROOT / "scripts/capture_mysql_xp_cpu_disjoint_slot_transition_v4.py",
    "scene_launcher": ROOT / "scripts/launch_mysql_xp_top4_algorithm_b_scene_v7.py",
})


def main() -> int:
    result = base.main()
    contract = base.load(base.CONTRACT)
    contract["baseline_accounting"] = {
        "physical_lane_count": 4, "aggregate_divisor": "validated_lane_count",
        "two_lane_literal_prohibited": True,
    }
    contract["calibration_candidate_batches"] = [
        [1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12], [13, 14, 15]
    ]
    contract["calibration_replays_per_scene"] = 15
    contract["calibration_parallelism"] = 4
    contract["verified_baseline_technical_continuation"] = {"allowed": False}
    contract["resource_envelope_transition"] = {
        "database_cpu_and_numa_affinity_only": True,
        "fresh_baseline_required": True,
        "pre_transition_calibration_measurement_credit": False,
        "algorithm_state_credit_increment": 0,
    }
    base.atomic(base.CONTRACT, contract)
    for suffix in ("cpu", "io"):
        path = (
            base.HISTORY / f"v161_to_b_top4_fresh_bootstrap_20260826_v16_{suffix}"
            / "bootstrap_manifest.json"
        )
        manifest = base.load(path)
        manifest["release_contract_sha256"] = base.sha(base.CONTRACT)
        base.atomic(path, manifest)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
