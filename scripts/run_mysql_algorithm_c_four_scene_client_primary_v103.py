#!/usr/bin/env python3
"""Fresh MySQL C search with complete current PG recovery semantics."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_four_scene_client_primary_v102 as predecessor
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v56 as runtime


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_complete_recovery_20260929_v202/"
    "algorithm_c_pg_complete_recovery_contract.json"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def validate_contract() -> dict[str, Any]:
    value = load(CONTRACT)
    correspondence = dict(value.get("pg_search_correspondence") or {})
    if (
        value.get("release_version") != "v202-pg-complete-recovery"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("fresh_search_required") is not True
        or value.get("superseded_v196_v198_v201_rounds_reusable") is not False
        or correspondence.get("first_valid_miss_expands_next_batch") is not True
        or correspondence.get("measured_outer_bracket_before_due_shrink") is not True
        or correspondence.get("maximum_outer_bracket_attempts_per_interval") != 1
        or correspondence.get("second_valid_miss_bisects_without_bracket") is not True
        or correspondence.get("empty_admission_counts_as_miss") is not False
        or correspondence.get("empty_admission_declares_convergence") is not False
        or correspondence.get("progressive_numeric_radii")
        != [0.05, 0.10, 0.20, 0.40, 0.70, 1.00]
        or correspondence.get("numeric_exhaustion_fails_closed") is not True
        or correspondence.get("terminal_normalized_width") != 0.01
        or correspondence.get("post_search_stability_confirmation") is not False
        or correspondence.get("qps_used_for_inner_scoring") is not False
    ):
        raise RuntimeError("Algorithm C v202 PG recovery contract differs")
    for name, raw in dict(value.get("source_bindings") or {}).items():
        path = Path(str(raw.get("path") or ""))
        if not path.is_absolute():
            path = ROOT / path
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v202 source differs: {name}: {path}")
    return value


def main() -> int:
    validate_contract()
    predecessor.validate_contract()
    if "--fresh-search" not in sys.argv[1:]:
        raise RuntimeError("v202 changes search recovery state and requires fresh search")
    old_runtime = predecessor.runtime
    predecessor.runtime = runtime
    try:
        return int(predecessor.main())
    finally:
        predecessor.runtime = old_runtime


if __name__ == "__main__":
    raise SystemExit(main())
