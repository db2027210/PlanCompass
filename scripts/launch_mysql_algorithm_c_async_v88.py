#!/usr/bin/env python3
"""Launch independent Algorithm-C v88 controllers."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_mysql_algorithm_c_async_v87 as predecessor


CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_async_scene_v88.py"
CONTRACT = (
    ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_cpu_a_slots_20260828_v44"
    / "global_monotonic_incumbent_contract.json"
)
MANAGEMENT_CPUSET = "484-487"


def validate_contract() -> dict[str, Any]:
    contract = predecessor.predecessor.predecessor.predecessor.predecessor.predecessor.predecessor.predecessor.predecessor.predecessor.predecessor.predecessor.predecessor.predecessor.load(CONTRACT)
    carry = contract.get("current_checkpoint_incumbent_baseline") or {}
    a_boundary = contract.get("algorithm_a_independent_check_boundary") or {}
    if (
        contract.get("release_version") != "v88"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or carry.get("scene") != "hybench_xp_cpu"
        or carry.get("worker_count") != 4
        or carry.get("accepted_or_rejected_credit") != 0
        or a_boundary.get("algorithm_a_role") != "independent_posthoc_check_only"
        or a_boundary.get("algorithm_a_feedback_to_b_or_c") is not False
    ):
        raise RuntimeError("Algorithm C v88 launch contract differs")
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
