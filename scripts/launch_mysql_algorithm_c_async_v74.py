#!/usr/bin/env python3
"""Launch independent Algorithm-C v74 globally monotonic controllers."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_mysql_algorithm_c_async_v73 as predecessor


CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_async_scene_v74.py"
CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_four_scene_algorithm_c_qps_20260827_v26"
    / "global_monotonic_incumbent_contract.json"
)


def validate_contract() -> dict[str, Any]:
    contract = predecessor.load(CONTRACT)
    if (
        contract.get("schema_version")
        != "mysql_algorithm_c_global_monotonic_incumbent_contract_v1"
        or contract.get("release_version") != "v74"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("resume_invariant", {}).get(
            "next_reference_equals_previous_selected_incumbent"
        ) is not True
        or contract.get("rerun_policy", {}).get("algorithm_b_required") is not False
    ):
        raise RuntimeError("Algorithm C v74 launch contract differs")
    return contract


def main() -> int:
    old_controller = predecessor.CONTROLLER
    old_contract = predecessor.CONTRACT
    old_validate = predecessor.validate_contract
    predecessor.CONTROLLER = CONTROLLER
    predecessor.CONTRACT = CONTRACT
    predecessor.validate_contract = validate_contract
    try:
        return predecessor.main()
    finally:
        predecessor.CONTROLLER = old_controller
        predecessor.CONTRACT = old_contract
        predecessor.validate_contract = old_validate


if __name__ == "__main__":
    raise SystemExit(main())
