#!/usr/bin/env python3
"""Launch independent four-worker XP Algorithm-C v77 controllers."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_mysql_algorithm_c_async_v76 as predecessor


CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_async_scene_v77.py"
CONTRACT = (
    ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_cpu_a_slots_20260828_v29"
    / "global_monotonic_incumbent_contract.json"
)
MANAGEMENT_CPUSET = "484-487"


def validate_contract() -> dict[str, Any]:
    contract = predecessor.predecessor.predecessor.load(CONTRACT)
    if (
        contract.get("schema_version")
        != "mysql_algorithm_c_global_monotonic_incumbent_contract_v1"
        or contract.get("release_version") != "v77"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("parallel_execution", {}).get(
            "physical_workers_per_scene_initial"
        ) != 4
        or contract.get("parallel_execution", {}).get(
            "four_scene_wave_barrier"
        ) is not False
        or contract.get("stability_cap_isolation", {}).get(
            "failed_lane_only_excluded"
        ) is not True
    ):
        raise RuntimeError("Algorithm C v77 launch contract differs")
    return contract


def main() -> int:
    launcher = predecessor.predecessor.predecessor
    old = {
        "controller": launcher.CONTROLLER,
        "contract": launcher.CONTRACT,
        "validate": launcher.validate_contract,
        "management": launcher.MANAGEMENT_CPUSET,
    }
    launcher.CONTROLLER = CONTROLLER
    launcher.CONTRACT = CONTRACT
    launcher.validate_contract = validate_contract
    launcher.MANAGEMENT_CPUSET = MANAGEMENT_CPUSET
    try:
        return int(launcher.main())
    finally:
        launcher.CONTROLLER = old["controller"]
        launcher.CONTRACT = old["contract"]
        launcher.validate_contract = old["validate"]
        launcher.MANAGEMENT_CPUSET = old["management"]


if __name__ == "__main__":
    raise SystemExit(main())
