#!/usr/bin/env python3
"""Launch four fresh v210-bound MySQL Algorithm C scenes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_mysql_algorithm_c_v209_pg_startup_isolation_four_scene as predecessor


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_dynamic_gate_launch_20260929_v211/"
    "four_scene_launch_contract.json"
)
ALGORITHM_CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_dynamic_gate_20260929_v210/"
    "algorithm_c_pg_dynamic_gate_contract.json"
)
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v107.py"
REQUIRED_SCENES = set(predecessor.REQUIRED_SCENES)
base = predecessor.base


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def argument(flag: str) -> str:
    try:
        return sys.argv[sys.argv.index(flag) + 1]
    except (ValueError, IndexError) as error:
        raise RuntimeError(f"required argument missing: {flag}") from error


def validate() -> dict[str, Any]:
    value = load(CONTRACT)
    if (
        value.get("release_version") != "v211-four-scene-pg-dynamic-gate"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("all_four_scenes_start_concurrently") is not True
        or value.get("physical_workers_per_scene") != 2
        or value.get("fresh_search_required") is not True
        or value.get("dynamic_gate_binding_complete") is not True
        or value.get("algorithm_contract_sha256") != sha(ALGORITHM_CONTRACT)
    ):
        raise RuntimeError("Algorithm C v211 launch contract differs")
    for name, raw in dict(value.get("source_bindings") or {}).items():
        path = ROOT / str(raw.get("path") or "")
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v211 launch source differs: {name}: {path}")
    if "--baseline-only" in sys.argv[1:]:
        raise RuntimeError("v211 formal launch requires four candidate-search scenes")
    if {item for item in argument("--candidate-scenes").split(",") if item} != REQUIRED_SCENES:
        raise RuntimeError("v211 requires exactly all four active scenes")
    argument("--baseline-set")
    return value


def main() -> int:
    validate()
    old = {
        "validate": predecessor.validate,
        "controller": predecessor.CONTROLLER,
        "contract": predecessor.CONTRACT,
        "algorithm_contract": predecessor.ALGORITHM_CONTRACT,
    }
    predecessor.validate = validate
    predecessor.CONTROLLER = CONTROLLER
    predecessor.CONTRACT = CONTRACT
    predecessor.ALGORITHM_CONTRACT = ALGORITHM_CONTRACT
    try:
        result = int(predecessor.main())
    finally:
        predecessor.validate = old["validate"]
        predecessor.CONTROLLER = old["controller"]
        predecessor.CONTRACT = old["contract"]
        predecessor.ALGORITHM_CONTRACT = old["algorithm_contract"]
    manifest_path = base.HISTORY / argument("--run-prefix") / "launch_manifest.json"
    manifest = load(manifest_path)
    manifest.update({
        "schema_version": "mysql_algorithm_c_v211_pg_dynamic_gate_four_scene_launch_v1",
        "release_version": "v211-four-scene-pg-dynamic-gate",
        "contract": str(CONTRACT),
        "contract_sha256": sha(CONTRACT),
        "algorithm_contract": str(ALGORITHM_CONTRACT),
        "algorithm_contract_sha256": sha(ALGORITHM_CONTRACT),
        "controller": str(CONTROLLER),
        "controller_sha256": sha(CONTROLLER),
        "dynamic_gate_binding_complete": True,
        "exact_target_journal_classification": True,
        "effective_maximum_clean_window_wait_seconds": 120,
        "required_clean_window_seconds": 15,
        "superseded_v209_rounds_reused": False,
        "historical_interval_state_reused": False,
    })
    base.atomic(manifest_path, manifest)
    return result


if __name__ == "__main__":
    raise SystemExit(main())

