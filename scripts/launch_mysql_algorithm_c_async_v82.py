#!/usr/bin/env python3
"""Launch independent Algorithm-C v82 controllers."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_mysql_algorithm_c_async_v81 as predecessor


CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_async_scene_v82.py"
CONTRACT = (
    ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_cpu_a_slots_20260828_v38"
    / "global_monotonic_incumbent_contract.json"
)
MANAGEMENT_CPUSET = "484-487"


def validate_contract() -> dict[str, Any]:
    contract = predecessor.predecessor.predecessor.predecessor.predecessor.predecessor.predecessor.predecessor.load(
        CONTRACT
    )
    boundary = contract.get("algorithm_a_independent_check_boundary") or {}
    reset_policy = contract.get("worker_reset_management_serialization") or {}
    if (
        contract.get("release_version") != "v82"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or reset_policy.get("scope") != "single_worker_data_plane_replacement_only"
        or reset_policy.get("scene_wave_or_measurement_serialized") is not False
        or boundary.get("algorithm_a_role") != "independent_posthoc_check_only"
        or boundary.get("algorithm_a_feedback_to_b_or_c") is not False
    ):
        raise RuntimeError("Algorithm C v82 launch contract differs")
    return contract


def main() -> int:
    old = {
        "controller": predecessor.CONTROLLER,
        "contract": predecessor.CONTRACT,
        "validate": predecessor.validate_contract,
        "management": predecessor.MANAGEMENT_CPUSET,
    }
    predecessor.CONTROLLER = CONTROLLER
    predecessor.CONTRACT = CONTRACT
    predecessor.validate_contract = validate_contract
    predecessor.MANAGEMENT_CPUSET = MANAGEMENT_CPUSET
    try:
        return int(predecessor.main())
    finally:
        predecessor.CONTROLLER = old["controller"]
        predecessor.CONTRACT = old["contract"]
        predecessor.validate_contract = old["validate"]
        predecessor.MANAGEMENT_CPUSET = old["management"]


if __name__ == "__main__":
    raise SystemExit(main())

