#!/usr/bin/env python3
"""Resume B at Outer 6 with acceptance-aware Top-5 and full TPC-H plans."""

from __future__ import annotations

import copy
from pathlib import Path
import re
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import (
    run_mysql_four_scene_algorithm_b_independent_lane_failure_isolation_core_v18
    as base,
)
from scripts import (
    run_mysql_four_scene_algorithm_b_scene_independent_measurement_v2
    as independent_measurement,
)
from scripts import (
    run_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v17
    as predecessor,
)
from scripts import mysql_algorithm_b_tpch_full_suite_plan_ranker_v1 as plan_ranker


v12 = predecessor.v12
core = predecessor.core
alg = predecessor.alg
runtime = predecessor.runtime
SOURCE = (
    base.HISTORY
    / "v17_formal_parallel221_outer5_scene_independent_resume_20260818_attempt012"
)
RECOVERY_ROOT = (
    base.HISTORY / "v18_b_outer6_interrupted_reset_recovery_20260818_attempt001"
)
RECOVERY = RECOVERY_ROOT / "recovered_xp_binding.json"
PREPARED = RECOVERY_ROOT / "prepared_binding.json"
ACCOUNTS = RECOVERY_ROOT / "benchmark_accounts_binding.json"
PLAN_VALIDATION = (
    ROOT / "preflight/mysql_four_scene_20260809/"
    "tpch_q1_q22_plan_capture_v18_20260818.json"
)
SCHEDULER_CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_b_parallel_221_20260818_v18"
    / "algorithm_b_parallel_221_contract.json"
)
REUSED_BATCHES = {
    (outer, rank): SOURCE / f"outer_{outer:03d}/top5_batch_{rank}/batch_result.json"
    for outer in range(1, 6) for rank in range(1, 6)
}
_ORIGINAL_ATOMIC = predecessor._ORIGINAL_ATOMIC


def _regular(path: Path) -> dict[str, Any]:
    return dict(v12._regular_json(path))


def validate_source() -> dict[str, Any]:
    state = _regular(SOURCE / "run_state.json")
    if (
        state.get("algorithm") != "B"
        or state.get("formal_scope") != "mysql_only"
        or state.get("cross_engine_comparison_prohibited") is not True
        or state.get("status") != "RUNNING"
        or state.get("stage") != "ALGORITHM_B_OUTER_006_RANK_1_RESETTING_XP"
        or int(state.get("outer_attempt") or 0) != 5
        or state.get("accepted_rounds") != {
            "job_memory": 1,
            "tpch_sf4_memory": 0,
            "hybench_xp_cpu": 1,
            "hybench_xp_io": 0,
        }
        or state.get("rejected_outer_attempts") != {
            "job_memory": 4,
            "tpch_sf4_memory": 5,
            "hybench_xp_cpu": 4,
            "hybench_xp_io": 5,
        }
        or any(bool(value) for value in (state.get("converged") or {}).values())
        or (SOURCE / "outer_006/top5_batch_1/batch_result.json").exists()
    ):
        raise RuntimeError("v18 Outer-5 completed continuation boundary differs")
    for outer in (1, 2, 3, 4, 5):
        decisions = sorted((SOURCE / f"outer_{outer:03d}").glob("*.decision.json"))
        if len(decisions) != 4:
            raise RuntimeError(f"v18 source outer {outer} decision cardinality differs")
        for path in decisions:
            _regular(path)
    rankings = _regular(SOURCE / "outer_006/predicted_rankings.json")
    if (
        rankings.get("status") != "PASS"
        or int(rankings.get("outer_attempt") or 0) != 6
        or rankings.get("active_scenes") != list(base.SCENES)
        or any(len(rows) != 5 for rows in
               (rankings.get("selected_top_k") or {}).values())
    ):
        raise RuntimeError("v18 discarded partial Outer-6 ranking differs")
    for key, path in REUSED_BATCHES.items():
        batch = _regular(path)
        rank = key[1]
        if (
            int(batch.get("candidate_rank") or rank) != rank
            or not (batch.get("measurements") or {})
        ):
            raise RuntimeError(f"v18 reused batch identity differs: {key}")
    return state


def validate_recovery_and_plan_capture() -> None:
    recovery = _regular(RECOVERY)
    prepared = _regular(PREPARED)
    accounts = _regular(ACCOUNTS)
    validation = _regular(PLAN_VALIDATION)
    prepared_workers = {
        str(row.get("worker_id")): dict(row)
        for row in prepared.get("workers") or []
    }
    account_workers = {
        str(row.get("worker_id")): dict(row)
        for row in accounts.get("workers") or []
    }
    if (
        recovery.get("status") != "PASS"
        or recovery.get("source_completed_outer_attempt") != 5
        or recovery.get("source_outer6_real_replay_started") is not False
        or recovery.get("formal_state_credit_increment") != 0
        or recovery.get("job_tpch_container_mutation") is not False
        or recovery.get("external_container_mutation") is not False
        or prepared.get("status") != "PREPARED"
        or prepared.get("xp_reset_prepared_sha256") != alg.sha(RECOVERY)
        or len(prepared_workers) != 8
        or accounts.get("status") != "PASS"
        or accounts.get("prepared_binding_sha256") != alg.sha(PREPARED)
        or set(account_workers) != set(prepared_workers)
        or validation.get("status") != "PASS"
        or validation.get("query_ids") != list(range(1, 23))
        or validation.get("query_count") != 22
        or validation.get("read_only_explain_only") is not True
        or validation.get("workload_executed") is not False
        or validation.get("ranker_source_sha256")
        != alg.sha(Path(plan_ranker.__file__).resolve())
    ):
        raise RuntimeError("v18 recovered authority or Q1-Q22 validation differs")


def measure_resume(**kwargs: Any):
    output = Path(kwargs["output"])
    match = re.search(r"outer_(\d+)/top5_batch_(\d+)$", str(output))
    if match is not None:
        key = (int(match.group(1)), int(match.group(2)))
        source = REUSED_BATCHES.get(key)
        if source is not None:
            destination = output / "batch_result.json"
            predecessor.base._mirror_batch(source, destination)
            v12._mirror_supporting_wave_evidence(source, destination)
            document = alg.load(destination)
            state = kwargs["state"]
            state["stage"] = f"{kwargs['stage_prefix']}_RESTORED_VERIFIED"
            state["scene_independent_continuation_source"] = str(SOURCE)
            state["scene_independent_continuation_reused_batches"] = [
                [outer, rank] for outer, rank in sorted(REUSED_BATCHES)
                if (outer, rank) <= key
            ]
            alg.atomic(Path(kwargs["state_path"]), state)
            return copy.deepcopy(dict(document["measurements"])), state
    return core._measure_221(**kwargs)


def atomic_with_historical_identity(path: Path, value: Mapping[str, Any]) -> None:
    if path.name.endswith(".decision.json") and path.parent.name in {
        "outer_001", "outer_002", "outer_003", "outer_004", "outer_005",
    }:
        source = _regular(SOURCE / path.parent.name / path.name)
        if v12._decision_semantics(value) != v12._decision_semantics(source):
            raise RuntimeError(
                f"{path.parent.name} reconstructed decision differs: {path.name}"
            )
    if (
        path.name == "predicted_rankings.json"
        and path.parent.name in {
            "outer_001", "outer_002", "outer_003", "outer_004", "outer_005",
        }
    ):
        source = _regular(SOURCE / path.parent.name / "predicted_rankings.json")
        if (
            base.ranking_identity(value.get("selected_top_k") or {})
            != base.ranking_identity(source.get("selected_top_k") or {})
        ):
            raise RuntimeError(
                f"{path.parent.name} recomputed Top-5 differs from source"
            )
    _ORIGINAL_ATOMIC(path, value)


def state_document_v18(run_id: str) -> dict[str, Any]:
    original_contract = predecessor.SCHEDULER_CONTRACT
    try:
        predecessor.SCHEDULER_CONTRACT = SCHEDULER_CONTRACT
        document = predecessor.state_document_v17(run_id)
    finally:
        predecessor.SCHEDULER_CONTRACT = original_contract
    document["technical_continuation"].update({
        "kind": "outer6_acceptance_aware_full_tpch_plan_resume_v18",
        "source_run_root": str(SOURCE),
        "source_state_sha256": alg.sha(SOURCE / "run_state.json"),
        "completed_outer_decisions_reconstructed": [1, 2, 3, 4, 5],
        "outer1_through_outer5_candidate_ranks_reused": [1, 2, 3, 4, 5],
        "source_outer6_partial_ranking_reused": False,
        "source_outer6_real_replay_started": False,
        "outer6_fresh_ranking_and_replay_required": True,
        "four_scene_algorithmic_progress_independent": True,
        "historical_state_rewritten": False,
        "xp_recovery_binding_path": str(RECOVERY),
        "xp_recovery_binding_sha256": alg.sha(RECOVERY),
        "tpch_q1_q22_plan_validation_path": str(PLAN_VALIDATION),
        "tpch_q1_q22_plan_validation_sha256": alg.sha(PLAN_VALIDATION),
    })
    document["prepared_binding_path"] = str(PREPARED)
    document["prepared_binding_sha256"] = alg.sha(PREPARED)
    document["benchmark_accounts_path"] = str(ACCOUNTS)
    document["benchmark_accounts_sha256"] = alg.sha(ACCOUNTS)
    document["scene_independence_release"] = {
        "release_version": "v18",
        "decision_core_path": str(Path(base.__file__).resolve()),
        "decision_core_sha256": alg.sha(Path(base.__file__).resolve()),
        "measurement_path": str(Path(independent_measurement.__file__).resolve()),
        "measurement_sha256": alg.sha(Path(independent_measurement.__file__).resolve()),
        "physical_wave_parallelism_unchanged": True,
        "four_scene_progress_lockstep_required": False,
        "workload_contract_unchanged": True,
        "resource_envelope_unchanged": True,
    }
    document["acceptance_aware_selection_release"] = {
        "effective_from_outer": 6,
        "policy": "current_tier_eligible_then_resource_score_v1",
        "historical_outer1_through_outer5_semantics_preserved": True,
        "qps_used_for_resource_prediction_gradient_or_score": False,
        "fixed_real_top_k_unchanged": 5,
    }
    document["tpch_full_suite_plan_release"] = {
        "effective_from_outer": 6,
        "ranker_path": str(Path(plan_ranker.__file__).resolve()),
        "ranker_sha256": alg.sha(Path(plan_ranker.__file__).resolve()),
        "qualification_query_count": 22,
        "plan_rerank_shortlist_size": plan_ranker.PLAN_RERANK_SHORTLIST_SIZE,
        "qps_used_for_resource_prediction_or_score": False,
    }
    document["controller_source_path"] = str(Path(__file__).resolve())
    document["controller_source_sha256"] = alg.sha(Path(__file__).resolve())
    return document


def validate_contract() -> dict[str, Any]:
    if not SCHEDULER_CONTRACT.is_file() or SCHEDULER_CONTRACT.is_symlink():
        raise RuntimeError(f"sealed v18 scheduler contract absent: {SCHEDULER_CONTRACT}")
    contract = alg.load(SCHEDULER_CONTRACT)
    continuation = contract.get("continuation") or {}
    if (
        contract.get("release_version") != "v18"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("candidate_batches") != [[1, 2], [3, 4], [5]]
        or int(contract.get("candidate_parallelism") or 0) != 2
        or continuation.get("resume_boundary")
        != "outer_005_completed_before_outer_006_real_replay"
        or continuation.get("four_scene_algorithmic_progress_independent") is not True
        or continuation.get("historical_state_rewritten") is not False
        or continuation.get("outer6_fresh_replay_required") is not True
    ):
        raise RuntimeError("parallel v18 continuation contract differs")
    return contract


def install() -> None:
    predecessor.install()
    base.FreshBaselineObjective.score = core._score_with_resource_payload
    base.PREPARED = PREPARED
    base.ACCOUNTS = ACCOUNTS
    base.c32.reset_xp = predecessor.predecessor.reset_xp_write_precise
    runtime.alg.reset_xp = predecessor.predecessor.reset_xp_write_precise
    runtime.alg.measure_batch = independent_measurement.measure_batch
    runtime._build_single_candidate_assignments = core._build_assignments_221
    runtime._measure_batch_with_fair_coordination = measure_resume
    plan_ranker.install()
    base.state_document = state_document_v18
    alg.atomic = atomic_with_historical_identity


def main() -> int:
    validate_contract()
    validate_source()
    validate_recovery_and_plan_capture()
    install()
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
