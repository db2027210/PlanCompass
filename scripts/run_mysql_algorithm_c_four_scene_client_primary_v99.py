#!/usr/bin/env python3
"""Fresh C search that prevents legacy client entries restoring rejection v2."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_outer_rejection_cycle_v3 as pg_rejection
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v13 as client_v13
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v30 as client_v30
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v98 as predecessor


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_async_pg_rejection_20260929_v193/"
    "algorithm_c_async_pg_rejection_contract.json"
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
        value.get("release_version") != "v193-async-pg-rejection-binding"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("fresh_search_required") is not True
        or value.get("runtime_rejection_policy") != pg_rejection.policy_contract()
        or value.get("legacy_runtime_overrides_prevented") != ["v13", "v30"]
        or value.get("workload_or_candidate_policy_changed") is not False
    ):
        raise RuntimeError("Algorithm C v193 async rejection contract differs")
    for name, raw in dict(value.get("source_bindings") or {}).items():
        path = ROOT / str(raw.get("path") or "")
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v193 source differs: {name}: {path}")
    return value


def main() -> int:
    validate_contract()
    if "--fresh-search" not in sys.argv[1:]:
        raise RuntimeError("v193 changes rejection lineage and requires fresh search")
    bindings = [
        (client_v13, "rejection_cycle"),
        (client_v13.latest_runtime, "rejection_cycle"),
        (client_v30, "rejection_cycle"),
        (client_v30.runtime, "rejection_cycle"),
    ]
    previous = [(owner, name, getattr(owner, name)) for owner, name in bindings]
    for owner, name in bindings:
        setattr(owner, name, pg_rejection)
    try:
        return int(predecessor.main())
    finally:
        for owner, name, value in reversed(previous):
            setattr(owner, name, value)


if __name__ == "__main__":
    raise SystemExit(main())
