#!/usr/bin/env python3
"""Fresh PG C search with exact one-shot boundary fallback semantics."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_four_scene_client_primary_v104 as predecessor
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v58 as runtime


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_boundary_fallback_20260929_v206/"
    "algorithm_c_pg_boundary_fallback_contract.json"
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
    if (
        value.get("release_version") != "v206-pg-boundary-fallback"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("fresh_search_required") is not True
        or value.get("superseded_v205_rounds_reusable") is not False
        or value.get("expanded_runtime_interval_control_bound") is not True
        or value.get("expanded_runtime_recovery_policy_bound") is not True
        or value.get("expanded_runtime_rejection_cycle_bound") is not True
        or value.get("first_decision_path_tested") is not True
        or value.get("empty_boundary_same_action_numeric_fallback") is not True
        or value.get("empty_boundary_due_shrink_without_third_miss") is not True
        or value.get("outer_boundary_distinct_parameter_identities") is not True
        or value.get("progressive_numeric_cursor_persisted") is not True
    ):
        raise RuntimeError("Algorithm C v206 boundary fallback contract differs")
    for name, raw in dict(value.get("source_bindings") or {}).items():
        path = Path(str(raw.get("path") or ""))
        if not path.is_absolute():
            path = ROOT / path
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v206 source differs: {name}: {path}")
    return value


def main() -> int:
    validate_contract()
    predecessor.validate_contract()
    if "--fresh-search" not in sys.argv[1:]:
        raise RuntimeError("v206 boundary fallback successor requires fresh search")
    old_runtime = predecessor.runtime
    predecessor.runtime = runtime
    try:
        return int(predecessor.main())
    finally:
        predecessor.runtime = old_runtime


if __name__ == "__main__":
    raise SystemExit(main())
