#!/usr/bin/env python3
"""v124 Algorithm C against the page-aligned v22 resource envelope."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_four_scene_client_primary_v1 as base
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v33 as predecessor


FREEZE = ROOT / "config/formal/frozen_mysql_four_scene_two_worker_20260920_v22/four_scene_effective_resource_successor.json"
FREEZE_SCHEMA = "mysql_four_scene_two_worker_paper_calibration_successor_v22"
CONTRACT = ROOT / "config/formal/frozen_mysql_algorithm_c_paper_baseline_20260920_v124/algorithm_c_paper_baseline_contract.json"
PARENT = predecessor.CONTRACT


def validate_contract() -> dict[str, Any]:
    contract = predecessor.runtime.alg.load(CONTRACT)
    if (
        contract.get("release_version") != "v124-paper-resource-v22"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("candidate_search_requires_paper_baseline_pass") is not True
        or contract.get("tolerance_percentage_points") != 1.0
        or contract.get("resource_freeze", {}).get("sha256") != predecessor.runtime.alg.sha(FREEZE)
        or contract.get("parent_contract", {}).get("sha256") != predecessor.runtime.alg.sha(PARENT)
    ):
        raise RuntimeError("Algorithm C v124 resource envelope contract differs")
    for name, raw in dict(contract.get("source_bindings") or {}).items():
        path = ROOT / str(raw.get("path") or "")
        if path.is_symlink() or not path.is_file() or predecessor.runtime.alg.sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v124 source differs: {name}: {path}")
    return contract


def main() -> int:
    validate_contract()
    old = (base.FREEZE, base.FREEZE_SCHEMA)
    base.FREEZE, base.FREEZE_SCHEMA = FREEZE, FREEZE_SCHEMA
    try:
        return int(predecessor.main())
    finally:
        base.FREEZE, base.FREEZE_SCHEMA = old


if __name__ == "__main__":
    raise SystemExit(main())
