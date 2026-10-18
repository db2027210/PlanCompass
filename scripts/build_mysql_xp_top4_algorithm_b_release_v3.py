#!/usr/bin/env python3
"""Freeze Top4 B v12 with four-lane baseline accounting."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_xp_top4_algorithm_b_release_v1 as predecessor


predecessor.OUTPUT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_20260826_v12"
predecessor.CONTROLLER = ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v3.py"
predecessor.COORDINATION = predecessor.OUTPUT / "coordination_contract.json"
predecessor.CONTRACT = predecessor.OUTPUT / "algorithm_b_top4_contract.json"
predecessor.BOOTSTRAP_VERSION = "v12"
predecessor.SOURCES.update({
    "controller": predecessor.CONTROLLER,
    "controller_predecessor": ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v1.py",
    "workload_adapter": ROOT / "mysql_workload_adapter.py",
    "current_authority_refresh": ROOT / "scripts/refresh_mysql_xp4_live_authority_from_reset_v1.py",
    "scene_launcher": ROOT / "scripts/launch_mysql_xp_top4_algorithm_b_scene_v3.py",
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
    contract["verified_baseline_technical_continuation"] = {
        "allowed": True,
        "required_predecessor_failure": "independent baseline lane count differs",
        "required_predecessor_outer_attempt": 0,
        "required_predecessor_accepted_rounds": 0,
        "required_predecessor_rejected_rounds": 0,
        "baseline_status": "PASS",
        "baseline_lane_count": 4,
        "calibration_or_outer_credit_reused": False,
    }
    predecessor.atomic(contract_path, contract)
    for suffix in ("cpu", "io"):
        manifest_path = (
            predecessor.HISTORY
            / f"v161_to_b_top4_fresh_bootstrap_20260826_v12_{suffix}"
            / "bootstrap_manifest.json"
        )
        manifest = predecessor.load(manifest_path)
        manifest["release_contract_sha256"] = predecessor.sha(contract_path)
        predecessor.atomic(manifest_path, manifest)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
