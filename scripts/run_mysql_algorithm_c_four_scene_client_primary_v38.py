#!/usr/bin/env python3
"""v128 Algorithm C with v22 optimization bound at the outermost runtime layer."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_four_scene_client_primary_v29 as optimization_controller
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v37 as predecessor


CONTRACT = ROOT / "config/formal/frozen_mysql_algorithm_c_paper_baseline_20260920_v128/algorithm_c_paper_baseline_contract.json"
PARENT = predecessor.CONTRACT
OPTIMIZATION_CONTRACT = predecessor.OPTIMIZATION_CONTRACT


def validate_contract() -> dict[str, Any]:
    from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v48 as runtime

    contract = runtime.alg.load(CONTRACT)
    if (
        contract.get("release_version") != "v128-outermost-v22-optimization-binding"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("candidate_search_requires_paper_baseline_pass") is not True
        or contract.get("tolerance_percentage_points") != 1.0
        or contract.get("parent_contract", {}).get("sha256") != runtime.alg.sha(PARENT)
    ):
        raise RuntimeError("Algorithm C v128 optimization binding contract differs")
    for name, raw in dict(contract.get("source_bindings") or {}).items():
        path = ROOT / str(raw.get("path") or "")
        if path.is_symlink() or not path.is_file() or runtime.alg.sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v128 source differs: {name}: {path}")
    return contract


def main() -> int:
    predecessor.validate_contract()
    validate_contract()
    previous = optimization_controller.OPTIMIZATION_CONTRACT
    optimization_controller.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    try:
        return int(predecessor.main())
    finally:
        optimization_controller.OPTIMIZATION_CONTRACT = previous


if __name__ == "__main__":
    raise SystemExit(main())
