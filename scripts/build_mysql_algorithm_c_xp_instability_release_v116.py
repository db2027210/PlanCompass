#!/usr/bin/env python3
"""Freeze Algorithm C v116 candidate-local XP instability handling."""

from __future__ import annotations

import copy
from datetime import datetime
import json
from pathlib import Path

from scripts import build_mysql_algorithm_c_nested_measurement_scope_release_v115 as base


ROOT = Path(__file__).resolve().parents[1]
SOURCE_CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260907_v115/"
    "algorithm_c_pg_latest_outer_contract.json"
)
SOURCE_OPTIMIZATION = ROOT / (
    "config/formal/frozen_mysql_four_scene_algorithm_c_client_primary_20260907_v115/"
    "outer_runtime_optimization.json"
)
SOURCE_COORDINATION = ROOT / (
    "config/formal/frozen_mysql_cross_project_wave_coordination_20260907_v115/"
    "coordination_contract.json"
)
CONTRACT_ROOT = ROOT / "config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260907_v116"
OPTIMIZATION_ROOT = ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_client_primary_20260907_v116"
COORDINATION_ROOT = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260907_v116"


def main() -> int:
    if any(path.exists() for path in (CONTRACT_ROOT, OPTIMIZATION_ROOT, COORDINATION_ROOT)):
        raise RuntimeError("fresh v116 contract roots required")
    coordination = copy.deepcopy(base.load(SOURCE_COORDINATION))
    coordination["release_version"] = "v116-xp-candidate-local-instability"
    coordination_path = COORDINATION_ROOT / "coordination_contract.json"
    base.atomic(coordination_path, coordination)

    optimization = copy.deepcopy(base.load(SOURCE_OPTIMIZATION))
    optimization.update({
        "release_version": "v116-xp-candidate-local-instability",
        "released_at": datetime.now().astimezone().isoformat(),
        "xp_candidate_local_instability_exclusion": True,
    })
    optimization_path = OPTIMIZATION_ROOT / "outer_runtime_optimization.json"
    base.atomic(optimization_path, optimization)

    contract = copy.deepcopy(base.load(SOURCE_CONTRACT))
    contract.update({
        "release_version": "v116-xp-candidate-local-instability",
        "created_on": datetime.now().astimezone().date().isoformat(),
        "xp_candidate_local_instability": {
            "maximum_measurement_seconds": 900,
            "invalid_candidate_credit": 0,
            "valid_sibling_retained": True,
            "invalid_candidate_pruning_evidence_eligible": False,
            "stability_threshold_changed": False,
            "candidate_set_changed": False,
            "acceptance_or_convergence_changed": False,
        },
    })
    bindings = contract["source_bindings"]
    bindings["mysql_controller"] = base.bind(
        "scripts/run_mysql_algorithm_c_four_scene_client_primary_v26.py"
    )
    bindings["xp_candidate_instability_classifier"] = base.bind(
        "scripts/mysql_algorithm_c_xp_instability_exclusion_v1.py"
    )
    bindings["xp_candidate_instability_test"] = base.bind(
        "tests/test_mysql_algorithm_c_xp_instability_exclusion_v1.py"
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

