#!/usr/bin/env python3
"""Freeze Algorithm C v115 with a run-scoped nested measurement resolver."""

from __future__ import annotations

import copy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
SOURCE_CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260907_v114/"
    "algorithm_c_pg_latest_outer_contract.json"
)
SOURCE_OPTIMIZATION = ROOT / (
    "config/formal/frozen_mysql_four_scene_algorithm_c_client_primary_20260907_v114/"
    "outer_runtime_optimization.json"
)
SOURCE_COORDINATION = ROOT / (
    "config/formal/frozen_mysql_cross_project_wave_coordination_20260907_v114/"
    "coordination_contract.json"
)
CONTRACT_ROOT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260907_v115"
)
OPTIMIZATION_ROOT = ROOT / (
    "config/formal/frozen_mysql_four_scene_algorithm_c_client_primary_20260907_v115"
)
COORDINATION_ROOT = ROOT / (
    "config/formal/frozen_mysql_cross_project_wave_coordination_20260907_v115"
)


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=False)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def bind(path: str) -> dict[str, str]:
    target = ROOT / path
    if not target.is_file() or target.is_symlink():
        raise RuntimeError(f"regular source required: {target}")
    return {"path": path, "sha256": sha(target)}


def main() -> int:
    if any(path.exists() for path in (
        CONTRACT_ROOT, OPTIMIZATION_ROOT, COORDINATION_ROOT,
    )):
        raise RuntimeError("fresh v115 contract roots required")

    coordination = copy.deepcopy(load(SOURCE_COORDINATION))
    coordination["release_version"] = "v115-nested-measurement-run-scope"
    coordination_path = COORDINATION_ROOT / "coordination_contract.json"
    atomic(coordination_path, coordination)

    optimization = copy.deepcopy(load(SOURCE_OPTIMIZATION))
    optimization.update({
        "release_version": "v115-nested-measurement-run-scope",
        "released_at": datetime.now().astimezone().isoformat(),
        "nested_measurement_campaign_root": True,
    })
    optimization_path = OPTIMIZATION_ROOT / "outer_runtime_optimization.json"
    atomic(optimization_path, optimization)

    contract = copy.deepcopy(load(SOURCE_CONTRACT))
    contract.update({
        "release_version": "v115-nested-measurement-run-scope",
        "created_on": datetime.now().astimezone().date().isoformat(),
        "nested_measurement_campaign_root": {
            "nearest_outer_ancestor_parent_is_run_root": True,
            "top5_and_nested_expansion_share_run_namespace": True,
            "cross_run_quarantine_collision_prohibited": True,
            "completed_outer_credit_changed": False,
            "candidate_flow_changed": False,
            "acceptance_or_convergence_changed": False,
        },
    })
    bindings = contract["source_bindings"]
    bindings["mysql_controller"] = bind(
        "scripts/run_mysql_algorithm_c_four_scene_client_primary_v25.py"
    )
    bindings["measurement_campaign_root"] = bind(
        "scripts/mysql_algorithm_c_measurement_campaign_root_v2.py"
    )
    bindings["measurement_campaign_root_test"] = bind(
        "tests/test_mysql_algorithm_c_measurement_campaign_root_v2.py"
    )
    bindings["scene_resume_checkpoint_builder"] = bind(
        "scripts/build_mysql_algorithm_c_scene_resume_checkpoint_v6.py"
    )
    bindings["kernel_coordination_contract"] = {
        "path": str(coordination_path.relative_to(ROOT)),
        "sha256": sha(coordination_path),
    }
    bindings["client_primary_optimization"] = {
        "path": str(optimization_path.relative_to(ROOT)),
        "sha256": sha(optimization_path),
    }
    contract_path = CONTRACT_ROOT / "algorithm_c_pg_latest_outer_contract.json"
    atomic(contract_path, contract)
    print(json.dumps({
        "status": "FROZEN_RUNTIME_VALIDATED",
        "contract": str(contract_path),
        "contract_sha256": sha(contract_path),
        "coordination": str(coordination_path),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

