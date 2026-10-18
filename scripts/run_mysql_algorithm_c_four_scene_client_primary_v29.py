#!/usr/bin/env python3
"""Algorithm C v119 control-queue and sealed-lineage recovery."""

from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import audit_mysql_algorithm_c_global_incumbent_chain_v4 as chain_audit
from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v9 as checkpoint_v9
from scripts import mysql_docker_control_resilience_v3 as docker_resilience_v3
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v28 as predecessor


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260907_v119/"
    "algorithm_c_pg_latest_outer_contract.json"
)
OPTIMIZATION_CONTRACT = ROOT / (
    "config/formal/frozen_mysql_four_scene_algorithm_c_client_primary_20260907_v119/"
    "outer_runtime_optimization.json"
)
COORDINATION_CONTRACT = ROOT / (
    "config/formal/frozen_mysql_cross_project_wave_coordination_20260907_v119/"
    "coordination_contract.json"
)
runtime = predecessor.runtime
v22 = (
    predecessor.predecessor.predecessor.predecessor.predecessor.predecessor
)
v21 = v22.predecessor


def validate_contract() -> dict[str, Any]:
    contract = runtime.alg.load(CONTRACT)
    flow = dict(contract.get("outer_flow") or {})
    recovery = dict(contract.get("docker_control_queue_recovery") or {})
    lineage = dict(contract.get("sealed_lineage_anchor_audit") or {})
    if (
        contract.get("release_version")
        != "v119-docker-control-queue-sealed-lineage"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or flow.get("strict_higher_qps_acceptance") is not True
        or flow.get("two_rejections_before_incumbent_shrink") is not True
        or recovery.get("single_api_call_lock_scope") is not True
        or recovery.get("scene_wave_or_outer_lease") is not False
        or recovery.get("bounded_wait_seconds") != 900
        or recovery.get("partial_outer_credit") != 0
        or lineage.get("outer1_anchor_sha_verification") is not True
        or lineage.get("pre_reference_decisions_grant_new_credit") is not False
        or lineage.get("semantic_state_batch_filter_preserved") is not True
        or lineage.get("acceptance_or_candidate_selection_changed") is not False
    ):
        raise RuntimeError("Algorithm C v119 lifecycle contract differs")
    for name, raw in dict(contract.get("source_bindings") or {}).items():
        row = dict(raw or {})
        path = (ROOT / str(row.get("path") or "")).resolve()
        if (
            not path.is_file()
            or path.is_symlink()
            or runtime.alg.sha(path) != row.get("sha256")
        ):
            raise RuntimeError(f"Algorithm C v119 source differs: {name}: {path}")
    return contract


def main() -> int:
    old = {
        "contract": predecessor.CONTRACT,
        "optimization": predecessor.OPTIMIZATION_CONTRACT,
        "coordination": predecessor.COORDINATION_CONTRACT,
        "validate": predecessor.validate_contract,
        "audit": predecessor.chain_audit,
        "checkpoint": v22.checkpoint_v5,
        "docker": v21.docker_resilience,
    }
    predecessor.CONTRACT = CONTRACT
    predecessor.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    predecessor.COORDINATION_CONTRACT = COORDINATION_CONTRACT
    predecessor.validate_contract = validate_contract
    predecessor.chain_audit = chain_audit
    v22.checkpoint_v5 = checkpoint_v9
    v21.docker_resilience = docker_resilience_v3
    try:
        return int(predecessor.main())
    finally:
        v21.docker_resilience = old["docker"]
        v22.checkpoint_v5 = old["checkpoint"]
        predecessor.chain_audit = old["audit"]
        predecessor.validate_contract = old["validate"]
        predecessor.COORDINATION_CONTRACT = old["coordination"]
        predecessor.OPTIMIZATION_CONTRACT = old["optimization"]
        predecessor.CONTRACT = old["contract"]


if __name__ == "__main__":
    raise SystemExit(main())

