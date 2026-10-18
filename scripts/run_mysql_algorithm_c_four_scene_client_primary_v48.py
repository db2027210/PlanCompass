#!/usr/bin/env python3
"""v138 Algorithm C with the calibrated v29 resource envelope."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_four_scene_client_primary_v47 as predecessor
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v48 as runtime


FREEZE = ROOT / "config/formal/frozen_mysql_four_scene_two_worker_20260920_v29/four_scene_effective_resource_successor.json"
FREEZE_SCHEMA = "mysql_four_scene_two_worker_paper_calibration_successor_v29"
OPTIMIZATION_CONTRACT = ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_client_primary_20260920_v138/outer_runtime_optimization.json"
CONTRACT = ROOT / "config/formal/frozen_mysql_algorithm_c_paper_baseline_20260920_v138/algorithm_c_paper_baseline_contract.json"
PARENT = predecessor.CONTRACT


def validate_contract() -> dict[str, Any]:
    contract = runtime.alg.load(CONTRACT)
    if (
        contract.get("release_version") != "v138-paper-resource-v29"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("candidate_search_requires_paper_baseline_pass") is not True
        or contract.get("tolerance_percentage_points") != 1.0
        or contract.get("resource_freeze", {}).get("sha256") != runtime.alg.sha(FREEZE)
        or contract.get("parent_contract", {}).get("sha256") != runtime.alg.sha(PARENT)
    ):
        raise RuntimeError("Algorithm C v138 resource contract differs")
    for name, raw in dict(contract.get("source_bindings") or {}).items():
        path = ROOT / str(raw.get("path") or "")
        if path.is_symlink() or not path.is_file() or runtime.alg.sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v138 source differs: {name}: {path}")
    return contract


def main() -> int:
    predecessor.validate_contract()
    validate_contract()
    previous = (
        predecessor.FREEZE, predecessor.FREEZE_SCHEMA,
        predecessor.OPTIMIZATION_CONTRACT, predecessor.validate_contract,
    )
    predecessor.FREEZE = FREEZE
    predecessor.FREEZE_SCHEMA = FREEZE_SCHEMA
    predecessor.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    predecessor.validate_contract = validate_contract
    try:
        return int(predecessor.main())
    finally:
        (predecessor.FREEZE, predecessor.FREEZE_SCHEMA,
         predecessor.OPTIMIZATION_CONTRACT, predecessor.validate_contract) = previous


if __name__ == "__main__":
    raise SystemExit(main())
