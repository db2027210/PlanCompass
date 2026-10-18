#!/usr/bin/env python3
"""Algorithm C v118 with semantic-state incumbent-chain auditing."""

from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import audit_mysql_algorithm_c_global_incumbent_chain_v3 as chain_audit
from scripts import run_mysql_algorithm_c_async_scene_v74 as v74
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v26 as predecessor


CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260907_v118/"
    "algorithm_c_pg_latest_outer_contract.json"
)
OPTIMIZATION_CONTRACT = ROOT / (
    "config/formal/frozen_mysql_four_scene_algorithm_c_client_primary_20260907_v118/"
    "outer_runtime_optimization.json"
)
COORDINATION_CONTRACT = ROOT / (
    "config/formal/frozen_mysql_cross_project_wave_coordination_20260907_v118/"
    "coordination_contract.json"
)
runtime = predecessor.runtime


def validate_contract() -> dict[str, Any]:
    contract = runtime.alg.load(CONTRACT)
    flow = dict(contract.get("outer_flow") or {})
    audit = dict(contract.get("semantic_state_incumbent_audit") or {})
    if (
        contract.get("release_version") != "v118-semantic-state-incumbent-audit"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or flow.get("strict_higher_qps_acceptance") is not True
        or flow.get("two_rejections_before_incumbent_shrink") is not True
        or audit.get("selected_semantic_batch_enforced") is not True
        or audit.get("excluded_semantic_batches_grant_state_credit") is not False
        or audit.get("candidate_selection_key_changed") is not False
        or audit.get("measurement_or_optimizer_state_changed") is not False
    ):
        raise RuntimeError("Algorithm C v118 lifecycle contract differs")
    for name, raw in dict(contract.get("source_bindings") or {}).items():
        row = dict(raw or {})
        source = (ROOT / str(row.get("path") or "")).resolve()
        if not source.is_file() or source.is_symlink() or runtime.alg.sha(source) != row.get("sha256"):
            raise RuntimeError(f"Algorithm C v118 source differs: {name}: {source}")
    return contract


def main() -> int:
    old = {
        "contract": predecessor.CONTRACT,
        "optimization": predecessor.OPTIMIZATION_CONTRACT,
        "coordination": predecessor.COORDINATION_CONTRACT,
        "validate": predecessor.validate_contract,
        "chain_audit": v74.chain_audit,
    }
    predecessor.CONTRACT = CONTRACT
    predecessor.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    predecessor.COORDINATION_CONTRACT = COORDINATION_CONTRACT
    predecessor.validate_contract = validate_contract
    v74.chain_audit = chain_audit
    try:
        return int(predecessor.main())
    finally:
        v74.chain_audit = old["chain_audit"]
        predecessor.validate_contract = old["validate"]
        predecessor.COORDINATION_CONTRACT = old["coordination"]
        predecessor.OPTIMIZATION_CONTRACT = old["optimization"]
        predecessor.CONTRACT = old["contract"]


if __name__ == "__main__":
    raise SystemExit(main())
