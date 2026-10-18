#!/usr/bin/env python3
"""Fresh PG-exact C search with the sealed 900-second plan deadline."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_c_plan_capture_timeout_v1 as plan_timeout
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v96 as predecessor
from scripts import run_mysql_four_scene_algorithm_c_v2 as alg


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_exact_plan_timeout_20260929_v188/"
    "algorithm_c_plan_timeout_contract.json"
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
        value.get("release_version") != "v188-pg-exact-plan-capture-900s"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("fresh_search_required") is not True
        or value.get("plan_capture_timeout_seconds") != 900
        or value.get("plan_capture_read_only") is not True
        or value.get("plan_capture_outside_formal_measurement_wall_time") is not True
        or value.get("formal_measurement_timeout_changed") is not False
        or value.get("workload_or_candidate_policy_changed") is not False
    ):
        raise RuntimeError("Algorithm C v188 plan capture contract differs")
    for name, raw in dict(value.get("source_bindings") or {}).items():
        path = Path(str(raw.get("path") or ""))
        if not path.is_absolute():
            path = ROOT / path
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v188 source differs: {name}: {path}")
    return value


def main() -> int:
    validate_contract()
    if "--fresh-search" not in sys.argv[1:]:
        raise RuntimeError("v188 requires fresh search")
    old_explain = alg.explain
    alg.explain = plan_timeout.explain
    try:
        return int(predecessor.main())
    finally:
        alg.explain = old_explain


if __name__ == "__main__":
    raise SystemExit(main())
