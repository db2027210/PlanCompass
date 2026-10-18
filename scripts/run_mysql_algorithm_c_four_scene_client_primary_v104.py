#!/usr/bin/env python3
"""Fresh complete-PG C search with every expanded-runtime dependency bound."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_four_scene_client_primary_v103 as predecessor
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v57 as runtime


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_runtime_binding_20260929_v204/"
    "algorithm_c_pg_runtime_binding_contract.json"
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
        value.get("release_version") != "v204-pg-runtime-binding"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("fresh_search_required") is not True
        or value.get("superseded_v203_rounds_reusable") is not False
        or value.get("expanded_runtime_interval_control_bound") is not True
        or value.get("expanded_runtime_recovery_policy_bound") is not True
        or value.get("expanded_runtime_rejection_cycle_bound") is not True
        or value.get("first_decision_path_tested") is not True
    ):
        raise RuntimeError("Algorithm C v204 runtime binding contract differs")
    for name, raw in dict(value.get("source_bindings") or {}).items():
        path = Path(str(raw.get("path") or ""))
        if not path.is_absolute():
            path = ROOT / path
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v204 source differs: {name}: {path}")
    return value


def main() -> int:
    validate_contract()
    predecessor.validate_contract()
    if "--fresh-search" not in sys.argv[1:]:
        raise RuntimeError("v204 runtime binding successor requires fresh search")
    old_runtime = predecessor.runtime
    predecessor.runtime = runtime
    try:
        return int(predecessor.main())
    finally:
        predecessor.runtime = old_runtime


if __name__ == "__main__":
    raise SystemExit(main())
