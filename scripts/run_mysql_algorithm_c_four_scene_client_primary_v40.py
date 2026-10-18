#!/usr/bin/env python3
"""v130 Algorithm C with the baseline gate bound at the compiled runtime."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_c_paper_baseline_gate_v1 as paper_baseline_gate
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v39 as predecessor
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v48 as runtime


CONTRACT = ROOT / "config/formal/frozen_mysql_algorithm_c_paper_baseline_20260920_v130/algorithm_c_paper_baseline_contract.json"
PARENT = predecessor.CONTRACT


def validate_contract() -> dict[str, Any]:
    contract = runtime.alg.load(CONTRACT)
    if (
        contract.get("release_version") != "v130-baseline-gate-runtime-binding"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("candidate_search_requires_paper_baseline_pass") is not True
        or contract.get("tolerance_percentage_points") != 1.0
        or contract.get("parent_contract", {}).get("sha256") != runtime.alg.sha(PARENT)
    ):
        raise RuntimeError("Algorithm C v130 baseline gate contract differs")
    for name, raw in dict(contract.get("source_bindings") or {}).items():
        path = ROOT / str(raw.get("path") or "")
        if path.is_symlink() or not path.is_file() or runtime.alg.sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v130 source differs: {name}: {path}")
    return contract


def main() -> int:
    predecessor.validate_contract()
    validate_contract()
    had_binding = hasattr(runtime, "paper_baseline_gate")
    previous = getattr(runtime, "paper_baseline_gate", None)
    runtime.paper_baseline_gate = paper_baseline_gate
    try:
        return int(predecessor.main())
    finally:
        if had_binding:
            runtime.paper_baseline_gate = previous
        else:
            del runtime.paper_baseline_gate


if __name__ == "__main__":
    raise SystemExit(main())
