#!/usr/bin/env python3
"""Launch independent Algorithm-C v90 IO continuation."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import launch_mysql_algorithm_c_async_v89 as predecessor


CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_async_scene_v90.py"
CONTRACT = ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_cpu_a_slots_20260828_v46/global_monotonic_incumbent_contract.json"
MANAGEMENT_CPUSET = "488-491"


def validate_contract() -> dict[str, Any]:
    contract = predecessor.predecessor.predecessor.predecessor.predecessor.predecessor.predecessor.predecessor.predecessor.predecessor.predecessor.predecessor.predecessor.predecessor.predecessor.predecessor.load(CONTRACT)
    hook = contract.get("exact_boundary_fallback_reuse_hook") or {}
    if (
        contract.get("release_version") != "v90"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or hook.get("scene") != "hybench_xp_io"
        or hook.get("physical_fallback_remeasurement") is not False
        or hook.get("accepted_or_rejected_credit") != 0
    ):
        raise RuntimeError("Algorithm C v90 launch contract differs")
    return contract


def main() -> int:
    old = {"controller": predecessor.CONTROLLER, "contract": predecessor.CONTRACT, "validate": predecessor.validate_contract, "management": predecessor.MANAGEMENT_CPUSET}
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
