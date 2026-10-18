#!/usr/bin/env python3
"""v125 Algorithm C after in-place v22 calibration and cold-pool preparation."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_four_scene_client_primary_v34 as predecessor


CONTRACT = ROOT / "config/formal/frozen_mysql_algorithm_c_paper_baseline_20260920_v125/algorithm_c_paper_baseline_contract.json"
PARENT = predecessor.CONTRACT


def validate_contract() -> dict[str, Any]:
    contract = predecessor.predecessor.runtime.alg.load(CONTRACT)
    if (
        contract.get("release_version") != "v125-inplace-resource-and-cold-pool"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("candidate_search_requires_paper_baseline_pass") is not True
        or contract.get("tolerance_percentage_points") != 1.0
        or contract.get("container_stop_restart_or_replacement_performed") is not False
        or contract.get("resource_freeze", {}).get("sha256")
        != predecessor.predecessor.runtime.alg.sha(predecessor.FREEZE)
        or contract.get("parent_contract", {}).get("sha256")
        != predecessor.predecessor.runtime.alg.sha(PARENT)
    ):
        raise RuntimeError("Algorithm C v125 in-place resource contract differs")
    for name, raw in dict(contract.get("source_bindings") or {}).items():
        path = ROOT / str(raw.get("path") or "")
        if path.is_symlink() or not path.is_file() or predecessor.predecessor.runtime.alg.sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v125 source differs: {name}: {path}")
    return contract


def main() -> int:
    validate_contract()
    return int(predecessor.main())


if __name__ == "__main__":
    raise SystemExit(main())
