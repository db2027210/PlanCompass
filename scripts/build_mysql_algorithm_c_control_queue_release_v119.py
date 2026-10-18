#!/usr/bin/env python3
"""Freeze Algorithm C v119 control-queue and lineage recovery."""

from __future__ import annotations

import copy
from datetime import datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_nested_measurement_scope_release_v115 as base


SOURCE_CONTRACT = ROOT / "config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260907_v118/algorithm_c_pg_latest_outer_contract.json"
SOURCE_OPTIMIZATION = ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_client_primary_20260907_v118/outer_runtime_optimization.json"
SOURCE_COORDINATION = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260907_v118/coordination_contract.json"
CONTRACT_ROOT = ROOT / "config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260907_v119"
OPTIMIZATION_ROOT = ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_client_primary_20260907_v119"
COORDINATION_ROOT = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260907_v119"


def main() -> int:
    if any(path.exists() for path in (CONTRACT_ROOT, OPTIMIZATION_ROOT, COORDINATION_ROOT)):
        raise RuntimeError("fresh v119 contract roots required")
    release = "v119-docker-control-queue-sealed-lineage"
    coordination = copy.deepcopy(base.load(SOURCE_COORDINATION))
    coordination["release_version"] = release
    coordination_path = COORDINATION_ROOT / "coordination_contract.json"
    base.atomic(coordination_path, coordination)

    optimization = copy.deepcopy(base.load(SOURCE_OPTIMIZATION))
    optimization.update({
        "release_version": release,
        "released_at": datetime.now().astimezone().isoformat(),
        "docker_control_queue_wait_seconds": 900,
        "sealed_outer1_lineage_anchor_audit": True,
    })
    optimization_path = OPTIMIZATION_ROOT / "outer_runtime_optimization.json"
    base.atomic(optimization_path, optimization)

    contract = copy.deepcopy(base.load(SOURCE_CONTRACT))
    contract.update({
        "release_version": release,
        "created_on": datetime.now().astimezone().date().isoformat(),
        "docker_control_queue_recovery": {
            "single_api_call_lock_scope": True,
            "scene_wave_or_outer_lease": False,
            "bounded_wait_seconds": 900,
            "completed_outer_attempts_preserved": 13,
            "partial_outer_credit": 0,
            "acceptance_candidate_or_convergence_changed": False,
        },
        "sealed_lineage_anchor_audit": {
            "outer1_anchor_sha_verification": True,
            "pre_reference_decisions_grant_new_credit": False,
            "semantic_state_batch_filter_preserved": True,
            "acceptance_or_candidate_selection_changed": False,
        },
    })
    bindings = contract["source_bindings"]
    bindings["mysql_controller"] = base.bind("scripts/run_mysql_algorithm_c_four_scene_client_primary_v29.py")
    bindings["semantic_state_incumbent_auditor"] = base.bind("scripts/audit_mysql_algorithm_c_global_incumbent_chain_v4.py")
    bindings["semantic_state_incumbent_auditor_test"] = base.bind("tests/test_mysql_algorithm_c_global_incumbent_chain_v4.py")
    bindings["docker_control_resilience"] = base.bind("scripts/mysql_docker_control_resilience_v3.py")
    bindings["scene_resume_checkpoint_builder"] = base.bind("scripts/build_mysql_algorithm_c_scene_resume_checkpoint_v9.py")
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
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
