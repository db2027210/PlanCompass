#!/usr/bin/env python3
"""Freeze Algorithm C v117 with the v8 XP-I/O checkpoint verifier."""

from __future__ import annotations

import copy
from datetime import datetime
import json
from pathlib import Path

from scripts import build_mysql_algorithm_c_nested_measurement_scope_release_v115 as base


ROOT = Path(__file__).resolve().parents[1]
SOURCE_CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260907_v116/"
    "algorithm_c_pg_latest_outer_contract.json"
)
SOURCE_OPTIMIZATION = ROOT / (
    "config/formal/frozen_mysql_four_scene_algorithm_c_client_primary_20260907_v116/"
    "outer_runtime_optimization.json"
)
SOURCE_COORDINATION = ROOT / (
    "config/formal/frozen_mysql_cross_project_wave_coordination_20260907_v116/"
    "coordination_contract.json"
)
CONTRACT_ROOT = ROOT / "config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260907_v117"
OPTIMIZATION_ROOT = ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_client_primary_20260907_v117"
COORDINATION_ROOT = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260907_v117"


def main() -> int:
    if any(path.exists() for path in (CONTRACT_ROOT, OPTIMIZATION_ROOT, COORDINATION_ROOT)):
        raise RuntimeError("fresh v117 contract roots required")
    release = "v117-xp-io-checkpoint-runtime-binding"
    coordination = copy.deepcopy(base.load(SOURCE_COORDINATION))
    coordination["release_version"] = release
    coordination_path = COORDINATION_ROOT / "coordination_contract.json"
    base.atomic(coordination_path, coordination)

    optimization = copy.deepcopy(base.load(SOURCE_OPTIMIZATION))
    optimization.update({
        "release_version": release,
        "released_at": datetime.now().astimezone().isoformat(),
        "xp_io_scene_checkpoint_runtime_binding": True,
    })
    optimization_path = OPTIMIZATION_ROOT / "outer_runtime_optimization.json"
    base.atomic(optimization_path, optimization)

    contract = copy.deepcopy(base.load(SOURCE_CONTRACT))
    contract.update({
        "release_version": release,
        "created_on": datetime.now().astimezone().date().isoformat(),
        "xp_io_scene_checkpoint_runtime_binding": {
            "checkpoint_verifier": "build_mysql_algorithm_c_scene_resume_checkpoint_v8.py",
            "completed_outer_attempts_preserved": 10,
            "partial_outer_credit": 0,
            "acceptance_or_convergence_changed": False,
        },
    })
    bindings = contract["source_bindings"]
    bindings["mysql_controller"] = base.bind(
        "scripts/run_mysql_algorithm_c_four_scene_client_primary_v27.py"
    )
    bindings["xp_io_scene_checkpoint_verifier"] = base.bind(
        "scripts/build_mysql_algorithm_c_scene_resume_checkpoint_v8.py"
    )
    bindings["kernel_coordination_contract"] = {
        "path": str(coordination_path.relative_to(ROOT)),
        "sha256": base.sha(coordination_path),
    }
    bindings["client_primary_optimization"] = {
        "path": str(optimization_path.relative_to(ROOT)),
        "sha256": base.sha(optimization_path),
    }
    contract_path = CONTRACT_ROOT / "algorithm_c_pg_latest_outer_contract.json"
    base.atomic(contract_path, contract)
    print(json.dumps({
        "status": "FROZEN_RUNTIME_VALIDATED",
        "contract": str(contract_path),
        "contract_sha256": base.sha(contract_path),
        "coordination": str(coordination_path),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
