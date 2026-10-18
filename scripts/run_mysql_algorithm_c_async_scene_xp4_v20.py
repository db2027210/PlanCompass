#!/usr/bin/env python3
"""Run one four-worker XP Algorithm C scene with strict-QPS outer progress."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_outer_qps_strict_progress_v1 as qps_policy
from scripts import mysql_outer_rejection_cycle_v1 as rejection_cycle
from scripts import run_mysql_algorithm_c_async_scene_xp4_v1 as predecessor


runtime = predecessor.runtime
CONTRACT_ROOT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_c_xp_top4_qps_rejection_20260826_v9"
)
ASYNC_CONTRACT = CONTRACT_ROOT / "algorithm_c_xp4_async_contract.json"
OPTIMIZATION_CONTRACT = CONTRACT_ROOT / "optimization_contract.json"
COORDINATION_CONTRACT = CONTRACT_ROOT / "coordination_contract.json"


def validate_contract() -> dict[str, Any]:
    contract = runtime.alg.load(ASYNC_CONTRACT)
    if (
        contract.get("schema_version")
        != "mysql_algorithm_c_xp4_qps_rejection_contract_v1"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("outer_acceptance_policy") != qps_policy.policy_contract()
        or contract.get("outer_rejection_cycle_policy")
        != rejection_cycle.policy_contract()
        or contract.get("candidate_batches") != [[1, 2, 3, 4], [5]]
        or contract.get("physical_replay_parallelism") != 4
        or contract.get("semantic_top_k") != 5
        or contract.get("post_search_stability_confirmation") is not False
        or contract.get("fresh_search_required") is not True
        or contract.get("algorithm_a_changed") is not False
        or contract.get("native_xp_workload_unchanged") is not True
        or contract.get("lease_mode") != "none"
        or contract.get("four_scene_barrier") is not False
        or contract.get("sibling_scene_wait") is not False
    ):
        raise RuntimeError("Algorithm C XP4 strict-QPS contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        if not isinstance(binding, Mapping):
            raise RuntimeError("XP4 strict-QPS source binding is not an object")
        path = ROOT / str(binding.get("path") or "")
        if (
            not path.is_file()
            or path.is_symlink()
            or runtime.alg.sha(path) != str(binding.get("sha256") or "")
        ):
            raise RuntimeError(f"XP4 strict-QPS source binding differs: {path}")
    return contract


def install() -> None:
    predecessor.ASYNC_CONTRACT = ASYNC_CONTRACT
    predecessor.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    predecessor.COORDINATION_CONTRACT = COORDINATION_CONTRACT
    predecessor.validate_contract = validate_contract
    predecessor.install()
    runtime.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    runtime.WAVE_COORDINATION_CONTRACT = COORDINATION_CONTRACT
    runtime.pg_outer.outer_semantic_decision = (
        qps_policy.pg_outer_qps_strict_progress
    )
    runtime.pg_outer.policy_contract = qps_policy.policy_contract


def main() -> int:
    validate_contract()
    install()
    return runtime.main()


if __name__ == "__main__":
    raise SystemExit(main())
