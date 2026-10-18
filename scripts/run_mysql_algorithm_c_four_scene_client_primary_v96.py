#!/usr/bin/env python3
"""Fresh Algorithm C search with PostgreSQL-exact interval region semantics."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_four_scene_client_primary_v66 as predecessor
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v54 as runtime


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_interval_exact_20260929_v186/"
    "algorithm_c_pg_interval_exact_contract.json"
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
    loop = dict(value.get("pg_loop_correspondence") or {})
    regions = dict(value.get("retained_region_correspondence") or {})
    if (
        value.get("schema_version")
        != "mysql_algorithm_c_pg_interval_exact_release_v1"
        or value.get("release_version") != "v186-pg-interval-exact-fresh-search"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("fresh_search_required") is not True
        or value.get("historical_interval_state_reuse_prohibited") is not True
        or loop.get("first_complete_valid_miss") != "expand_next_batch"
        or loop.get("second_complete_valid_miss")
        != "keep_transformed_half_containing_incumbent"
        or loop.get("maximum_actual_normalized_width_threshold") != 0.01
        or loop.get("post_search_stability_confirmation") is not False
        or regions.get("complement_partition") != "non_overlapping_ordered_slabs"
        or regions.get("queued_and_visited_identity_deduplication") is not True
    ):
        raise RuntimeError("Algorithm C v186 PG interval contract differs")
    for name, raw in dict(value.get("source_bindings") or {}).items():
        path = Path(str(raw.get("path") or ""))
        if not path.is_absolute():
            path = ROOT / path
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v186 source differs: {name}: {path}")
    return value


def main() -> int:
    validate_contract()
    if "--fresh-search" not in sys.argv[1:]:
        raise RuntimeError("v186 changes interval lineage and requires a fresh search")
    old_runtime = predecessor.runtime
    predecessor.runtime = runtime
    try:
        return int(predecessor.main())
    finally:
        predecessor.runtime = old_runtime


if __name__ == "__main__":
    raise SystemExit(main())
