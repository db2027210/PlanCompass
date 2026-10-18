#!/usr/bin/env python3
"""Resume C with v21 pruning and the v62 reset-aware wave lease."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mysql_algorithm_c_plan_replay_pruning_v3 as pruning_v3
from scripts import mysql_co_resident_pg_isolation_v10 as isolation
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v21 as v21


v20 = v21.v20
v18 = v20.v19.v18
runtime = v21.runtime
alg = v21.alg
PREDECESSOR_CONTRACT = (
    ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260817_v61"
    / "coordination_contract.json"
)
ORIGINAL_STATE_DOCUMENT = v21.state_document


def validate_wave_coordination_contract() -> dict[str, Any]:
    contract = alg.load(isolation.CONTRACT_PATH)
    predecessor = contract.get("predecessor") or {}
    budget = contract.get("wave_lease_budget_v62") or {}
    allowance = (
        (contract.get("co_resident_isolated_pg_allowance") or {})
        .get("thin_benchbase_tpch_driver_allowance") or {}
    )
    if (
        contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("release_version") != "v62"
        or predecessor.get("path") != str(PREDECESSOR_CONTRACT.relative_to(ROOT))
        or predecessor.get("sha256") != alg.sha(PREDECESSOR_CONTRACT)
        or float(contract.get("per_wave_quiet_seconds") or 0) != 15
        or float(contract.get("maximum_quiet_wait_while_held_seconds") or 0) != 60
        or int(contract.get("maximum_gate_attempts_per_wave") or 0) != 1
        or float(contract.get("maximum_wave_lease_seconds") or 0) != 1800
        or (contract.get("fairness") or {}).get(
            "mysql_maximum_wave_lease_seconds"
        ) != 1800
        or budget.get("formal_workload_maximum_seconds_unchanged") != 900
        or budget.get("per_workload_900_second_fail_close_unchanged") is not True
        or budget.get("release_between_candidate_batches_unchanged") is not True
        or budget.get("workload_resource_candidate_score_top5_binary_shrink_and_convergence_changed")
        is not False
        or budget.get("accepted_or_rejected_state_credit_from_attempt035") != 0
        or allowance.get("maximum_processes") != 10
        or allowance.get("maximum_rss_bytes_per_process") != 256 * 1024 * 1024
        or float(allowance.get("maximum_total_lifetime_cpu_cores") or 0) != 0.5
        or allowance.get("external_mutation") is not False
    ):
        raise RuntimeError("v62 reset-aware wave lease authority differs")
    return contract


def state_document(**kwargs: Any) -> dict[str, Any]:
    document = ORIGINAL_STATE_DOCUMENT(**kwargs)
    document["reset_aware_wave_lease_release"] = {
        "release_version": "v62/v22",
        "coordination_contract_path": str(isolation.CONTRACT_PATH),
        "coordination_contract_sha256": alg.sha(isolation.CONTRACT_PATH),
        "maximum_wave_lease_seconds": 1800,
        "formal_workload_timeout_seconds_unchanged": 900,
        "lease_includes_quiet_reset_measurement_plan_capture_and_aggregation": True,
        "accepted_or_rejected_state_credit_from_attempt035": 0,
        "workload_resource_candidate_score_top5_binary_shrink_and_convergence_changed": False,
    }
    return document


def main() -> int:
    isolation.install()
    runtime.WAVE_COORDINATION_CONTRACT = isolation.CONTRACT_PATH
    v18.isolation_v9 = isolation
    v18.validate_wave_coordination_contract = validate_wave_coordination_contract
    runtime.rank_and_prune_candidates = pruning_v3.rank_and_prune_candidates
    runtime.PLAN_PRUNING_CONTRACT_VERSION = pruning_v3.CONTRACT_VERSION
    runtime.PLAN_PRUNING_CONTRACT = v21.PLAN_PRUNING_CONTRACT
    v20.state_document = state_document
    return v20.main()


if __name__ == "__main__":
    raise SystemExit(main())
