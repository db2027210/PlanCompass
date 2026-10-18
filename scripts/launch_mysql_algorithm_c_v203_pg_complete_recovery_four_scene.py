#!/usr/bin/env python3
"""Launch four fresh MySQL C scenes with complete current-PG recovery."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_mysql_algorithm_c_v156_four_scene as base


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_complete_launch_20260929_v203/"
    "four_scene_launch_contract.json"
)
ALGORITHM_CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_complete_recovery_20260929_v202/"
    "algorithm_c_pg_complete_recovery_contract.json"
)
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v103.py"
REQUIRED_SCENES = set(base.SCENES)


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
        value.get("release_version") != "v203-four-scene-pg-complete-recovery"
        or value.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("all_four_scenes_start_concurrently") is not True
        or value.get("physical_workers_per_scene") != 2
        or value.get("maximum_concurrent_mysql_workers") != 8
        or value.get("four_scene_barrier") is not False
        or value.get("sibling_scene_wait") is not False
        or value.get("fresh_search_required") is not True
        or value.get("plan_capture_timeout_seconds") != 900
        or value.get("outer_boundary_refinement_before_due_shrink") is not True
        or value.get("empty_admission_progressive_recovery") is not True
        or value.get("algorithm_contract_sha256") != sha(ALGORITHM_CONTRACT)
    ):
        raise RuntimeError("Algorithm C v203 launch contract differs")
    for name, raw in dict(value.get("source_bindings") or {}).items():
        path = Path(str(raw.get("path") or ""))
        if not path.is_absolute():
            path = ROOT / path
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v203 launch source differs: {name}: {path}")
    if "--baseline-only" in sys.argv[1:]:
        raise RuntimeError("v203 formal launch requires four candidate-search scenes")
    scenes = {value for value in argument("--candidate-scenes").split(",") if value}
    if scenes != REQUIRED_SCENES:
        raise RuntimeError("v203 requires exactly all four active scenes")
    argument("--baseline-set")
    return value


def main() -> int:
    validate()
    old_controller, old_contract = base.CONTROLLER, base.CONTRACT
    base.CONTROLLER = CONTROLLER
    base.CONTRACT = CONTRACT
    try:
        result = int(base.main())
    finally:
        base.CONTROLLER, base.CONTRACT = old_controller, old_contract
    run_prefix = argument("--run-prefix")
    manifest_path = base.HISTORY / run_prefix / "launch_manifest.json"
    manifest = load(manifest_path)
    manifest.update({
        "schema_version": "mysql_algorithm_c_v203_pg_complete_recovery_four_scene_launch_v1",
        "release_version": "v203-four-scene-pg-complete-recovery",
        "algorithm_contract": str(ALGORITHM_CONTRACT),
        "algorithm_contract_sha256": sha(ALGORITHM_CONTRACT),
        "controller": str(CONTROLLER),
        "controller_sha256": sha(CONTROLLER),
        "fresh_search_required": True,
        "plan_capture_timeout_seconds": 900,
        "historical_interval_state_reused": False,
        "pg_exact_half_interval_second_miss": True,
        "pg_exact_nonoverlapping_region_partition": True,
        "pg_exact_region_identity_deduplication": True,
        "pg_exact_outer_boundary_refinement_before_due_shrink": True,
        "pg_exact_empty_admission_progressive_recovery": True,
        "accepted_expansion_keeps_binary_shrink": True,
        "rejected_expansion_restores_parent": True,
        "superseded_v196_v198_v201_rounds_reused": False,
    })
    base.atomic(manifest_path, manifest)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
