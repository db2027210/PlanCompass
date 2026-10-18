#!/usr/bin/env python3
"""Fresh PG-aligned C search with accepted expansion intervals preserved."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_four_scene_client_primary_v96 as release_v96
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v100 as predecessor
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v55 as runtime


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_expansion_commit_20260929_v199/"
    "algorithm_c_pg_expansion_commit_contract.json"
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
    correspondence = dict(value.get("pg_outer_correspondence") or {})
    if (
        value.get("release_version") != "v199-pg-accepted-expansion-commit"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("fresh_search_required") is not True
        or value.get("completed_v196_rounds_reusable") is not False
        or correspondence.get("rejected_expansion_restores_parent") is not True
        or correspondence.get("accepted_expansion_restores_parent") is not False
        or correspondence.get("accepted_expansion_keeps_binary_shrink") is not True
        or correspondence.get("first_valid_miss_expands_next_batch") is not True
        or correspondence.get("second_consecutive_valid_miss_bisects_incumbent") is not True
        or correspondence.get("terminal_normalized_width") != 0.01
        or correspondence.get("post_search_stability_confirmation") is not False
    ):
        raise RuntimeError("Algorithm C v199 PG expansion contract differs")
    for name, raw in dict(value.get("source_bindings") or {}).items():
        path = Path(str(raw.get("path") or ""))
        if not path.is_absolute():
            path = ROOT / path
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v199 source differs: {name}: {path}")
    return value


def main() -> int:
    validate_contract()
    predecessor.validate_contract()
    if "--fresh-search" not in sys.argv[1:]:
        raise RuntimeError("v199 changes interval state transitions and requires fresh search")
    old_runtime = release_v96.runtime
    release_v96.runtime = runtime
    try:
        return int(predecessor.main())
    finally:
        release_v96.runtime = old_runtime


if __name__ == "__main__":
    raise SystemExit(main())
