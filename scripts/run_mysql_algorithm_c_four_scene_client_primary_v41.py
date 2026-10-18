#!/usr/bin/env python3
"""v131 Algorithm C with the calibrated v23 resource envelope."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_four_scene_client_primary_v34 as resource_controller
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v35 as legacy_resource_controller
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v37 as inner_optimization_controller
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v38 as outer_optimization_controller
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v40 as predecessor
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v48 as runtime


FREEZE = ROOT / "config/formal/frozen_mysql_four_scene_two_worker_20260920_v23/four_scene_effective_resource_successor.json"
FREEZE_SCHEMA = "mysql_four_scene_two_worker_paper_calibration_successor_v23"
OPTIMIZATION_CONTRACT = ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_client_primary_20260920_v131/outer_runtime_optimization.json"
CONTRACT = ROOT / "config/formal/frozen_mysql_algorithm_c_paper_baseline_20260920_v131/algorithm_c_paper_baseline_contract.json"
PARENT = predecessor.CONTRACT


def validate_contract() -> dict[str, Any]:
    contract = runtime.alg.load(CONTRACT)
    if (
        contract.get("release_version") != "v131-paper-resource-v23"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("candidate_search_requires_paper_baseline_pass") is not True
        or contract.get("tolerance_percentage_points") != 1.0
        or contract.get("resource_freeze", {}).get("sha256") != runtime.alg.sha(FREEZE)
        or contract.get("parent_contract", {}).get("sha256") != runtime.alg.sha(PARENT)
    ):
        raise RuntimeError("Algorithm C v131 resource contract differs")
    for name, raw in dict(contract.get("source_bindings") or {}).items():
        path = ROOT / str(raw.get("path") or "")
        if path.is_symlink() or not path.is_file() or runtime.alg.sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v131 source differs: {name}: {path}")
    return contract


def main() -> int:
    predecessor.validate_contract()
    resource_controller.validate_contract()
    legacy_resource_controller.validate_contract()
    validate_contract()
    previous = (
        resource_controller.FREEZE, resource_controller.FREEZE_SCHEMA,
        resource_controller.validate_contract,
        legacy_resource_controller.validate_contract,
        inner_optimization_controller.OPTIMIZATION_CONTRACT,
        outer_optimization_controller.OPTIMIZATION_CONTRACT,
    )
    resource_controller.FREEZE = FREEZE
    resource_controller.FREEZE_SCHEMA = FREEZE_SCHEMA
    resource_controller.validate_contract = validate_contract
    legacy_resource_controller.validate_contract = validate_contract
    inner_optimization_controller.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    outer_optimization_controller.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    try:
        return int(predecessor.main())
    finally:
        (resource_controller.FREEZE, resource_controller.FREEZE_SCHEMA,
         resource_controller.validate_contract,
         legacy_resource_controller.validate_contract,
         inner_optimization_controller.OPTIMIZATION_CONTRACT,
         outer_optimization_controller.OPTIMIZATION_CONTRACT) = previous


if __name__ == "__main__":
    raise SystemExit(main())
