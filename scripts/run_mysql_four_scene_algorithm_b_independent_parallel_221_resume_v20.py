#!/usr/bin/env python3
"""Resume at Outer 7 with complete drains and soft TPC-H plan-risk ranking."""

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
    run_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v19
    as predecessor,
)
from scripts import (
    run_mysql_four_scene_algorithm_b_scene_independent_measurement_v4
    as independent_measurement,
)
from scripts import mysql_algorithm_b_tpch_full_suite_plan_ranker_v2 as plan_ranker


v12 = predecessor.v12
base = predecessor.base
core = predecessor.core
alg = predecessor.alg
runtime = predecessor.runtime
SOURCE = (
    base.HISTORY
    / "v19_formal_session_drain_outer6_rank3_resume_20260818_attempt015"
)
PREPARED = predecessor.PREPARED
ACCOUNTS = predecessor.ACCOUNTS
CLEAN_BOUNDARY_DRAIN = SOURCE / "outer_007_interrupted_before_replay_all_worker_drain.json"
BATCHED_PLAN_VALIDATION = (
    ROOT / "preflight/mysql_four_scene_20260809/"
    "tpch_q1_q22_batched_explain_v20_20260818.json"
)
SCHEDULER_CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_b_parallel_221_20260818_v20"
    / "algorithm_b_parallel_221_contract.json"
)
REUSED_BATCHES = {
    (outer, rank): SOURCE / f"outer_{outer:03d}/top5_batch_{rank}/batch_result.json"
    for outer in range(1, 7) for rank in range(1, 6)
}
_ORIGINAL_ATOMIC = predecessor._ORIGINAL_ATOMIC


def _regular(path: Path) -> dict[str, Any]:
    return dict(v12._regular_json(path))


def validate_source() -> dict[str, Any]:
    state = _regular(SOURCE / "run_state.json")
    expected_accepted = {
        "job_memory": 1,
        "tpch_sf4_memory": 0,
        "hybench_xp_cpu": 1,
        "hybench_xp_io": 1,
    }
    expected_rejected = {
        "job_memory": 5,
        "tpch_sf4_memory": 6,
        "hybench_xp_cpu": 5,
        "hybench_xp_io": 5,
    }
    if (
        state.get("algorithm") != "B"
        or state.get("formal_scope") != "mysql_only"
        or state.get("cross_engine_comparison_prohibited") is not True
        or state.get("status") != "RUNNING"
        or state.get("stage") != "ALGORITHM_B_OUTER_007_ESTIMATING"
        or int(state.get("outer_attempt") or 0) != 6
        or state.get("accepted_rounds") != expected_accepted
        or state.get("rejected_outer_attempts") != expected_rejected
        or any(bool(value) for value in (state.get("converged") or {}).values())
    ):
        raise RuntimeError("v20 clean post-Outer-6 source state differs")
    for outer in range(1, 7):
        decisions = sorted((SOURCE / f"outer_{outer:03d}").glob("*.decision.json"))
        if len(decisions) != 4:
            raise RuntimeError(f"v20 source outer {outer} decision cardinality differs")
        for path in decisions:
            _regular(path)
        for rank in range(1, 6):
            batch = _regular(REUSED_BATCHES[(outer, rank)])
            if not (batch.get("measurements") or {}):
                raise RuntimeError(f"v20 source batch empty: outer={outer}, rank={rank}")
    if list((SOURCE / "outer_007").glob("top5_batch_*/batch_result.json")):
        raise RuntimeError("v20 source Outer 7 real replay already started")
    drain = _regular(CLEAN_BOUNDARY_DRAIN)
    if (
        drain.get("status") != "PASS"
        or drain.get("all_workers_zero_sessions_after") is not True
        or drain.get("all_tpch_revenue0_views_absent_after") is not True
        or {row.get("worker_id") for row in drain.get("workers") or []}
        != {"job_w1", "job_w2", "tpch_w1", "tpch_w2"}
    ):
        raise RuntimeError("v20 clean-boundary drain evidence differs")
    return state


def validate_batched_plan_capture() -> dict[str, Any]:
    validation = _regular(BATCHED_PLAN_VALIDATION)
    if (
        validation.get("status") != "PASS"
        or validation.get("formal_scope") != "mysql_only"
        or validation.get("cross_engine_comparison_prohibited") is not True
        or validation.get("query_ids") != list(range(1, 23))
        or validation.get("query_count") != 22
        or validation.get("read_only_explain_only") is not True
        or validation.get("workload_executed") is not False
        or validation.get("byte_identical_plan_payloads") is not True
        or validation.get("legacy_client_process_count") != 22
        or validation.get("batched_client_process_count") != 1
        or float(validation.get("speedup") or 0) <= 1.0
        or validation.get("batched_ranker_sha256")
        != alg.sha(Path(plan_ranker.__file__).resolve())
    ):
        raise RuntimeError("v20 batched Q1-Q22 plan validation differs")
    return validation


def measure_resume(**kwargs: Any):
    output = Path(kwargs["output"])
    match = re.search(r"outer_(\d+)/top5_batch_(\d+)$", str(output))
    if match is not None:
        key = (int(match.group(1)), int(match.group(2)))
        source = REUSED_BATCHES.get(key)
        if source is not None:
            destination = output / "batch_result.json"
            predecessor.predecessor.base._mirror_batch(source, destination)
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
    historical_outers = {f"outer_{outer:03d}" for outer in range(1, 7)}
    if path.name.endswith(".decision.json") and path.parent.name in historical_outers:
        source = _regular(SOURCE / path.parent.name / path.name)
        if v12._decision_semantics(value) != v12._decision_semantics(source):
            raise RuntimeError(
                f"{path.parent.name} reconstructed decision differs: {path.name}"
            )
    if path.name == "predicted_rankings.json" and path.parent.name in historical_outers:
        source = _regular(SOURCE / path.parent.name / path.name)
        if (
            base.ranking_identity(value.get("selected_top_k") or {})
            != base.ranking_identity(source.get("selected_top_k") or {})
        ):
            raise RuntimeError(
                f"{path.parent.name} recomputed Top-5 differs from source"
            )
    _ORIGINAL_ATOMIC(path, value)


def state_document_v20(run_id: str) -> dict[str, Any]:
    original_contract = predecessor.SCHEDULER_CONTRACT
    try:
        predecessor.SCHEDULER_CONTRACT = SCHEDULER_CONTRACT
        document = predecessor.state_document_v19(run_id)
    finally:
        predecessor.SCHEDULER_CONTRACT = original_contract
    document["technical_continuation"].update({
        "kind": "outer7_complete_drain_and_tpch_soft_plan_risk_resume_v20",
        "source_run_root": str(SOURCE),
        "source_state_sha256": alg.sha(SOURCE / "run_state.json"),
        "completed_outer_decisions_reconstructed": list(range(1, 7)),
        "outer1_through_outer6_candidate_ranks_reused": [1, 2, 3, 4, 5],
        "outer7_real_replay_started_in_source": False,
        "outer7_fresh_candidate_ranks_required": [1, 2, 3, 4, 5],
        "historical_state_rewritten": False,
        "four_scene_algorithmic_progress_independent": True,
        "clean_boundary_drain_path": str(CLEAN_BOUNDARY_DRAIN),
        "clean_boundary_drain_sha256": alg.sha(CLEAN_BOUNDARY_DRAIN),
    })
    document["scene_independence_release"] = {
        "release_version": "v20",
        "decision_core_path": str(Path(base.__file__).resolve()),
        "decision_core_sha256": alg.sha(Path(base.__file__).resolve()),
        "measurement_path": str(Path(independent_measurement.__file__).resolve()),
        "measurement_sha256": alg.sha(Path(independent_measurement.__file__).resolve()),
        "physical_wave_parallelism_unchanged": True,
        "four_scene_progress_lockstep_required": False,
        "workload_contract_unchanged": True,
        "resource_envelope_unchanged": True,
    }
    document["benchmark_session_drain_release"] = {
        "effective_from_outer": 7,
        "measurement_path": str(Path(independent_measurement.__file__).resolve()),
        "measurement_sha256": alg.sha(Path(independent_measurement.__file__).resolve()),
        "drain_path": str(ROOT / "scripts/mysql_algorithm_b_benchmark_session_drain_v1.py"),
        "drain_sha256": alg.sha(
            ROOT / "scripts/mysql_algorithm_b_benchmark_session_drain_v1.py"
        ),
        "scope": "all_formal_job_and_tpch_ra_benchmark_sessions",
        "pre_and_post_measurement": True,
        "tpch_q15_revenue0_cleanup": True,
        "zero_session_readback_required": True,
        "external_process_or_container_mutation": False,
    }
    document["tpch_full_suite_plan_release"] = {
        "effective_from_outer": 7,
        "ranker_path": str(Path(plan_ranker.__file__).resolve()),
        "ranker_sha256": alg.sha(Path(plan_ranker.__file__).resolve()),
        "qualification_query_count": 22,
        "plan_rerank_shortlist_size": plan_ranker.PLAN_RERANK_SHORTLIST_SIZE,
        "extreme_plan_handling": "uncapped_raw_plan_ratio_soft_discount",
        "candidate_hard_pruning": False,
        "qps_used_for_resource_prediction_or_score": False,
        "explain_client_processes_per_candidate": 1,
        "batched_plan_validation_path": str(BATCHED_PLAN_VALIDATION),
        "batched_plan_validation_sha256": alg.sha(BATCHED_PLAN_VALIDATION),
        "byte_identical_to_legacy_q1_q22_capture": True,
    }
    document["controller_source_path"] = str(Path(__file__).resolve())
    document["controller_source_sha256"] = alg.sha(Path(__file__).resolve())
    return document


def validate_contract() -> dict[str, Any]:
    if not SCHEDULER_CONTRACT.is_file() or SCHEDULER_CONTRACT.is_symlink():
        raise RuntimeError(f"sealed v20 scheduler contract absent: {SCHEDULER_CONTRACT}")
    contract = alg.load(SCHEDULER_CONTRACT)
    continuation = contract.get("continuation") or {}
    plan_policy = contract.get("tpch_plan_ranking") or {}
    drain_policy = contract.get("benchmark_session_drain") or {}
    if (
        contract.get("release_version") != "v20"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("candidate_batches") != [[1, 2], [3, 4], [5]]
        or int(contract.get("candidate_parallelism") or 0) != 2
        or continuation.get("resume_boundary")
        != "outer_006_complete_outer_007_before_real_replay"
        or continuation.get("completed_outer_decisions_reconstructed")
        != list(range(1, 7))
        or continuation.get("outer7_fresh_ranks_required") != [1, 2, 3, 4, 5]
        or continuation.get("historical_state_rewritten") is not False
        or plan_policy.get("shortlist_size") != 64
        or plan_policy.get("candidate_hard_pruning") is not False
        or plan_policy.get("qps_used_for_resource_prediction_or_score") is not False
        or drain_policy.get("all_relational_workers_pre_and_post_wave") is not True
    ):
        raise RuntimeError("parallel v20 continuation contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(binding["path"])
        if (
            not path.is_file() or path.is_symlink()
            or alg.sha(path) != str(binding["sha256"])
        ):
            raise RuntimeError(f"v20 source binding differs: {path}")
    return contract


def install() -> None:
    predecessor.install()
    base.PREPARED = PREPARED
    base.ACCOUNTS = ACCOUNTS
    runtime.alg.measure_batch = independent_measurement.measure_batch
    runtime._build_single_candidate_assignments = core._build_assignments_221
    runtime._measure_batch_with_fair_coordination = measure_resume
    plan_ranker.install()
    base.state_document = state_document_v20
    alg.atomic = atomic_with_historical_identity


def main() -> int:
    validate_contract()
    validate_source()
    predecessor.validate_recovery_and_plan_capture()
    validate_batched_plan_capture()
    install()
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
