#!/usr/bin/env python3
"""Launch independent Algorithm-C v81 mixed-cardinality controllers."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_mysql_algorithm_c_async_v80 as predecessor


CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_async_scene_v81.py"
CONTRACT = (
    ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_cpu_a_slots_20260828_v37"
    / "global_monotonic_incumbent_contract.json"
)
MANAGEMENT_CPUSET = "484-487"


def validate_contract() -> dict[str, Any]:
    contract = predecessor.predecessor.predecessor.predecessor.predecessor.predecessor.predecessor.load(
        CONTRACT
    )
    parallel = contract.get("parallel_execution") or {}
    if (
        contract.get("release_version") != "v81"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or parallel.get("physical_workers_by_scene") != {
            "job_memory": 2,
            "tpch_sf4_memory": 2,
            "hybench_xp_cpu": 4,
            "hybench_xp_io": 4,
        }
        or parallel.get("maximum_concurrent_mysql_workers") != 12
        or parallel.get("four_scene_wave_barrier") is not False
    ):
        raise RuntimeError("Algorithm C v81 launch contract differs")
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
