#!/usr/bin/env python3
"""Launch four fresh PG-exact MySQL C scenes with fixed hwloc discovery."""

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
    "config/formal/frozen_mysql_algorithm_c_pg_exact_hwloc_launch_20260929_v191/"
    "four_scene_launch_contract.json"
)
ALGORITHM_CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_hwloc_cpu_only_20260929_v190/"
    "algorithm_c_hwloc_contract.json"
)
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v98.py"
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
        value.get("release_version") != "v191-four-scene-pg-exact-hwloc-fresh-search"
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
        or value.get("accelerator_topology_discovery_disabled") is not True
        or value.get("algorithm_contract_sha256") != sha(ALGORITHM_CONTRACT)
    ):
        raise RuntimeError("Algorithm C v191 launch contract differs")
    for name, raw in dict(value.get("source_bindings") or {}).items():
        path = Path(str(raw.get("path") or ""))
        if not path.is_absolute():
            path = ROOT / path
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"Algorithm C v191 launch source differs: {name}: {path}")
    if "--baseline-only" in sys.argv[1:]:
        raise RuntimeError("v191 formal launch requires four candidate-search scenes")
    scenes = {value for value in argument("--candidate-scenes").split(",") if value}
    if scenes != REQUIRED_SCENES:
        raise RuntimeError("v191 requires exactly all four active scenes")
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
        "schema_version": "mysql_algorithm_c_v191_pg_exact_hwloc_four_scene_launch_v1",
        "release_version": "v191-four-scene-pg-exact-hwloc-fresh-search",
        "algorithm_contract": str(ALGORITHM_CONTRACT),
        "algorithm_contract_sha256": sha(ALGORITHM_CONTRACT),
        "controller": str(CONTROLLER),
        "controller_sha256": sha(CONTROLLER),
        "fresh_search_required": True,
        "plan_capture_timeout_seconds": 900,
        "plan_capture_outside_formal_measurement_wall_time": True,
        "accelerator_topology_discovery_disabled": True,
        "historical_interval_state_reused": False,
        "pg_exact_half_interval_second_miss": True,
        "pg_exact_nonoverlapping_region_partition": True,
        "pg_exact_region_identity_deduplication": True,
    })
    base.atomic(manifest_path, manifest)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
