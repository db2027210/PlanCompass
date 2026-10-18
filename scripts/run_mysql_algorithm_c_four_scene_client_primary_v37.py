#!/usr/bin/env python3
"""v127 Algorithm C with the v22 optimization and current-freeze kernel gate."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v30 as current_gate
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v24 as gate_controller
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v36 as predecessor


CONTRACT = ROOT / "config/formal/frozen_mysql_algorithm_c_paper_baseline_20260920_v127/algorithm_c_paper_baseline_contract.json"
PARENT = predecessor.CONTRACT
OPTIMIZATION_CONTRACT = ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_client_primary_20260920_v126/outer_runtime_optimization.json"


def validate_contract() -> dict[str, Any]:
    contract = predecessor.predecessor.predecessor.predecessor.runtime.alg.load(CONTRACT)
    if (
        contract.get("release_version") != "v127-current-optimization-resource-v22"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("candidate_search_requires_paper_baseline_pass") is not True
        or contract.get("tolerance_percentage_points") != 1.0
        or contract.get("parent_contract", {}).get("sha256")
        != predecessor.predecessor.predecessor.predecessor.runtime.alg.sha(PARENT)
    ):
        raise RuntimeError("Algorithm C v127 resource optimization contract differs")
    for name, raw in dict(contract.get("source_bindings") or {}).items():
        path = ROOT / str(raw.get("path") or "")
        if (path.is_symlink() or not path.is_file()
                or predecessor.predecessor.predecessor.predecessor.runtime.alg.sha(path) != raw.get("sha256")):
            raise RuntimeError(f"Algorithm C v127 source differs: {name}: {path}")
    return contract


def main() -> int:
    predecessor.validate_contract()
    validate_contract()
    previous = (gate_controller.kernel_gate, gate_controller.OPTIMIZATION_CONTRACT)
    gate_controller.kernel_gate = current_gate
    gate_controller.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    try:
        return int(predecessor.main())
    finally:
        gate_controller.kernel_gate, gate_controller.OPTIMIZATION_CONTRACT = previous


if __name__ == "__main__":
    raise SystemExit(main())
