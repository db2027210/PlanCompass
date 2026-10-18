#!/usr/bin/env python3
"""Fresh PG-exact C search with the matching v3 optimization authority."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_four_scene_client_primary_v13 as client_v13
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v66 as client_v66
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v99 as predecessor


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_rejection_authority_20260929_v195/"
    "algorithm_c_pg_rejection_authority_contract.json"
)
OPTIMIZATION = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_rejection_authority_20260929_v195/"
    "outer_runtime_optimization.json"
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
    optimization = load(OPTIMIZATION)
    if (
        value.get("release_version") != "v195-pg-rejection-optimization-authority"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("fresh_search_required") is not True
        or value.get("optimization_sha256") != sha(OPTIMIZATION)
        or (optimization.get("pg_search_outer") or {}).get(
            "outer_rejection_cycle_policy_id"
        ) != "mysql_c_pg_half_interval_two_reject_shrink_v3"
    ):
        raise RuntimeError("Algorithm C v195 optimization authority differs")
    for name, raw in dict(value.get("source_bindings") or {}).items():
        path = ROOT / str(raw.get("path") or "")
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v195 source differs: {name}: {path}")
    return value


def main() -> int:
    validate_contract()
    if "--fresh-search" not in sys.argv[1:]:
        raise RuntimeError("v195 requires fresh search")
    old_v13 = client_v13.OPTIMIZATION_CONTRACT
    old_v66 = client_v66.OPTIMIZATION_CONTRACT
    client_v13.OPTIMIZATION_CONTRACT = OPTIMIZATION
    client_v66.OPTIMIZATION_CONTRACT = OPTIMIZATION
    try:
        return int(predecessor.main())
    finally:
        client_v13.OPTIMIZATION_CONTRACT = old_v13
        client_v66.OPTIMIZATION_CONTRACT = old_v66


if __name__ == "__main__":
    raise SystemExit(main())
