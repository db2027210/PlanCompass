#!/usr/bin/env python3
"""Freeze Top4 B v13 with four-lane calibration and formal scheduling."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_xp_top4_algorithm_b_release_v1 as predecessor


predecessor.OUTPUT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_20260826_v13"
predecessor.CONTROLLER = ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v4.py"
predecessor.COORDINATION = predecessor.OUTPUT / "coordination_contract.json"
predecessor.CONTRACT = predecessor.OUTPUT / "algorithm_b_top4_contract.json"
predecessor.BOOTSTRAP_VERSION = "v13"
predecessor.SOURCES.update({
    "controller": predecessor.CONTROLLER,
    "controller_predecessor": ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v3.py",
    "controller_core": ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v1.py",
    "four_lane_scheduler": ROOT / "scripts/mysql_algorithm_b_fresh_parallel_4x_scheduler_v26.py",
    "workload_adapter": ROOT / "mysql_workload_adapter.py",
    "current_authority_refresh": ROOT / "scripts/refresh_mysql_xp4_live_authority_from_reset_v1.py",
    "scene_launcher": ROOT / "scripts/launch_mysql_xp_top4_algorithm_b_scene_v4.py",
})


def main() -> int:
    result = predecessor.main()
    contract_path = predecessor.CONTRACT
    contract = predecessor.load(contract_path)
    contract["baseline_accounting"] = {
        "physical_lane_count": 4,
        "aggregate_divisor": "validated_lane_count",
        "two_lane_literal_prohibited": True,
    }
    contract["calibration_candidate_batches"] = [
        [1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12], [13, 14, 15]
    ]
    contract["calibration_replays_per_scene"] = 15
    contract["calibration_parallelism"] = 4
    contract["calibration_design_or_result_changed"] = False
    contract["verified_baseline_technical_continuation"] = {
        "allowed": True,
        "required_predecessor_outer_attempt": 0,
        "required_predecessor_accepted_rounds": 0,
        "required_predecessor_rejected_rounds": 0,
        "baseline_status": "PASS", "baseline_lane_count": 4,
        "calibration_or_outer_credit_reused": False,
    }
    predecessor.atomic(contract_path, contract)
    for suffix in ("cpu", "io"):
        path = (
            predecessor.HISTORY
            / f"v161_to_b_top4_fresh_bootstrap_20260826_v13_{suffix}"
            / "bootstrap_manifest.json"
        )
        manifest = predecessor.load(path)
        manifest["release_contract_sha256"] = predecessor.sha(contract_path)
        predecessor.atomic(path, manifest)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
