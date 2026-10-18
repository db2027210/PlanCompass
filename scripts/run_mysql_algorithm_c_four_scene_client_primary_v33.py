#!/usr/bin/env python3
"""v123 Algorithm C: retain v122 reset recovery and seal R0-only audit state."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_four_scene_client_primary_v31 as v31
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v32 as predecessor
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v48 as runtime


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_paper_baseline_20260920_v123/"
    "algorithm_c_paper_baseline_contract.json"
)
PARENT = predecessor.CONTRACT


def validate_contract() -> dict[str, Any]:
    contract = runtime.alg.load(CONTRACT)
    if (
        contract.get("release_version") != "v123-baseline-only-audit-state"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("candidate_search_requires_paper_baseline_pass") is not True
        or contract.get("tolerance_percentage_points") != 1.0
        or contract.get("baseline_only_persists_measured_incumbents") is not True
        or contract.get("parent_contract", {}).get("sha256") != runtime.alg.sha(PARENT)
    ):
        raise RuntimeError("Algorithm C v123 baseline-only audit contract differs")
    for name, raw in dict(contract.get("source_bindings") or {}).items():
        path = ROOT / str(raw.get("path") or "")
        if path.is_symlink() or not path.is_file() or runtime.alg.sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v123 source differs: {name}: {path}")
    return contract


def main() -> int:
    validate_contract()
    old = v31.runtime
    v31.runtime = runtime
    try:
        return int(predecessor.main())
    finally:
        v31.runtime = old


if __name__ == "__main__":
    raise SystemExit(main())
