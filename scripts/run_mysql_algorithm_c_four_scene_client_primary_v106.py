#!/usr/bin/env python3
"""Fresh PG-C search with complete four-scene startup isolation binding."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_c_benchmark_probe_resilience_v1 as probe_retry
from scripts import mysql_algorithm_c_reset_affinity_v1 as reset_affinity
from scripts import mysql_cross_project_wave_coordination_v27 as coordination_v27
from scripts import mysql_cross_project_wave_coordination_v28 as coordination_v28
from scripts import provision_mysql_four_scene_benchmark_users_v5 as users
from scripts import reset_mysql_xp_data_planes_client_primary_v4 as reset_v4
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v105 as predecessor


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_startup_isolation_20260929_v208/"
    "algorithm_c_pg_startup_isolation_contract.json"
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
        value.get("release_version") != "v208-pg-startup-isolation"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("fresh_search_required") is not True
        or value.get("superseded_v207_rounds_reusable") is not False
        or value.get("sibling_descendant_gate_installed") is not True
        or value.get("xp_reset_management_affinity_frozen") is not True
        or value.get("read_only_probe_retry_bounded") is not True
        or value.get("algorithm_decision_logic_changed") is not False
    ):
        raise RuntimeError("Algorithm C v208 startup-isolation contract differs")
    for name, raw in dict(value.get("source_bindings") or {}).items():
        path = Path(str(raw.get("path") or ""))
        if not path.is_absolute():
            path = ROOT / path
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v208 startup source differs: {name}: {path}")
    return value


def main() -> int:
    validate_contract()
    predecessor.validate_contract()
    if "--fresh-search" not in sys.argv[1:]:
        raise RuntimeError("v208 startup-isolation successor requires fresh search")

    old_inventory = coordination_v27._inventory
    old_gate = coordination_v27.rolling_quiet_gate
    old_prepare = reset_v4._ORIGINAL_PREPARE
    old_probe = users._run_worker
    coordination_v28.install()
    reset_v4._ORIGINAL_PREPARE = reset_affinity.prepare_fresh_xp
    users._run_worker = probe_retry.wrap(old_probe)
    try:
        return int(predecessor.main())
    finally:
        users._run_worker = old_probe
        reset_v4._ORIGINAL_PREPARE = old_prepare
        coordination_v27.rolling_quiet_gate = old_gate
        coordination_v27._inventory = old_inventory


if __name__ == "__main__":
    raise SystemExit(main())

