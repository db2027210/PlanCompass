#!/usr/bin/env python3
"""Resume after Outer 11 with TPC-H global/local resource-only Top-5."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import (
    run_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v22
    as predecessor,
)
from scripts import mysql_algorithm_b_tpch_full_suite_plan_ranker_v4 as plan_ranker


base = predecessor.base
alg = predecessor.alg
SOURCE = (
    base.HISTORY
    / "v22_formal_online_resource_model_outer11_20260818_attempt022"
)
COMPLETED_OUTERS = 11
EFFECTIVE_OUTER = COMPLETED_OUTERS + 1
CLEAN_BOUNDARY_DRAIN = (
    SOURCE / "stopped_after_outer11_local_trust_v23_drain.json"
)
SCHEDULER_CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_b_parallel_221_20260818_v23"
    / "algorithm_b_parallel_221_contract.json"
)
REUSED_BATCHES = {
    (outer, rank): SOURCE / f"outer_{outer:03d}/top5_batch_{rank}/batch_result.json"
    for outer in range(1, COMPLETED_OUTERS + 1)
    for rank in range(1, base.FIXED_TOP_K + 1)
}


def _bind_predecessor() -> None:
    """Rebind v22 only when v23 is invoked, not merely imported by tests."""
    predecessor.SOURCE = SOURCE
    predecessor.COMPLETED_OUTERS = COMPLETED_OUTERS
    predecessor.EFFECTIVE_OUTER = EFFECTIVE_OUTER
    predecessor.CLEAN_BOUNDARY_DRAIN = CLEAN_BOUNDARY_DRAIN
    predecessor.SCHEDULER_CONTRACT = SCHEDULER_CONTRACT
    predecessor.REUSED_BATCHES = REUSED_BATCHES
    predecessor.plan_ranker = plan_ranker


def validate_source() -> dict[str, Any]:
    _bind_predecessor()
    state = predecessor.validate_source()
    if (
        int(state.get("outer_attempt") or 0) != COMPLETED_OUTERS
        or state.get("accepted_rounds") != {
            "job_memory": 4,
            "tpch_sf4_memory": 2,
            "hybench_xp_cpu": 3,
            "hybench_xp_io": 3,
        }
    ):
        raise RuntimeError("v23 Outer 11 source progress differs")

    partial = SOURCE / "outer_012"
    partial_batches = sorted(partial.glob("top5_batch_*/batch_result.json"))
    if any(partial.glob("*.decision.json")) or partial_batches != [
        partial / "top5_batch_1/batch_result.json"
    ]:
        raise RuntimeError("v23 partial Outer 12 failure evidence differs")
    partial_batch = predecessor._regular(partial_batches[0])
    if (
        state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or partial_batch.get("status") != "FAILED_CLOSED"
        or len(partial_batch.get("measurements") or {}) != len(base.SCENES)
    ):
        raise RuntimeError("v23 partial Outer 12 is not sealed failed evidence")
    drain = predecessor._regular(CLEAN_BOUNDARY_DRAIN)
    workers = {str(row["worker_id"]): row for row in drain.get("workers") or []}
    if (
        drain.get("status") != "PASS"
        or drain.get("all_workers_zero_sessions_after") is not True
        or drain.get("all_tpch_revenue0_views_absent_after") is not True
        or set(workers) != {
            "job_w1", "job_w2", "tpch_w1", "tpch_w2",
            "xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2",
        }
        or any(row.get("sessions_after") for row in workers.values())
    ):
        raise RuntimeError("v23 clean boundary drain differs")
    return state


def state_document_v23(run_id: str) -> dict[str, Any]:
    _bind_predecessor()
    document = predecessor.state_document_v22(run_id)
    continuation = document["technical_continuation"]
    continuation.pop("outer11_fresh_candidate_ranks_required", None)
    continuation.update({
        "kind": "post_outer11_tpch_global_local_trust_resume_v23",
        "source_run_root": str(SOURCE),
        "source_state_sha256": alg.sha(SOURCE / "run_state.json"),
        "completed_outer_decisions_reconstructed": list(
            range(1, COMPLETED_OUTERS + 1)
        ),
        "outer12_fresh_candidate_ranks_required": [1, 2, 3, 4, 5],
        "source_partial_outer12_measurement_credit": False,
        "source_partial_outer12_retained_as_failure_evidence": True,
        "source_partial_outer12_failed_batch_path": str(
            SOURCE / "outer_012/top5_batch_1/batch_result.json"
        ),
        "source_partial_outer12_failed_batch_sha256": alg.sha(
            SOURCE / "outer_012/top5_batch_1/batch_result.json"
        ),
        "clean_boundary_drain_path": str(CLEAN_BOUNDARY_DRAIN),
        "clean_boundary_drain_sha256": alg.sha(CLEAN_BOUNDARY_DRAIN),
        "historical_measurements_or_decisions_rewritten": False,
        "accepted_round_credit_changed": False,
    })
    document["online_resource_model_update_release"].update({
        "release_version": "v23",
        "effective_from_outer": EFFECTIVE_OUTER,
        "historical_valid_top5_bootstrap_outers": list(
            range(1, COMPLETED_OUTERS + 1)
        ),
    })
    document["tpch_full_suite_plan_release"].update({
        "effective_from_outer": EFFECTIVE_OUTER,
        "ranker_path": str(Path(plan_ranker.__file__).resolve()),
        "ranker_sha256": alg.sha(Path(plan_ranker.__file__).resolve()),
        "candidate_pool": "global_resource_grid_plus_incumbent_local_probes",
        "top5_global_quota": base.FIXED_TOP_K - plan_ranker.LOCAL_TOP5_QUOTA,
        "top5_incumbent_local_quota": plan_ranker.LOCAL_TOP5_QUOTA,
        "local_probe_fractions": list(plan_ranker.LOCAL_FRACTIONS),
        "local_probe_one_coordinate_at_a_time": True,
        "qps_used_for_probe_generation_or_ranking": False,
        "inner_candidate_score_modified": False,
        "candidate_hard_pruning": False,
    })
    document["controller_source_path"] = str(Path(__file__).resolve())
    document["controller_source_sha256"] = alg.sha(Path(__file__).resolve())
    return document


def validate_contract() -> dict[str, Any]:
    if not SCHEDULER_CONTRACT.is_file() or SCHEDULER_CONTRACT.is_symlink():
        raise RuntimeError(f"sealed v23 scheduler contract absent: {SCHEDULER_CONTRACT}")
    contract = alg.load(SCHEDULER_CONTRACT)
    continuation = contract.get("continuation") or {}
    plan = contract.get("tpch_plan_ranking") or {}
    update = contract.get("online_resource_model_update") or {}
    if (
        contract.get("release_version") != "v23"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("candidate_batches") != [[1, 2], [3, 4], [5]]
        or int(contract.get("candidate_parallelism") or 0) != 2
        or continuation.get("completed_outer_decisions_reconstructed")
        != list(range(1, COMPLETED_OUTERS + 1))
        or continuation.get("source_partial_outer12_measurement_credit") is not False
        or (contract.get("range_shrink") or {}).get("policy")
        != "accepted_candidate_active_subset_only_v1"
        or plan.get("candidate_pool")
        != "global_resource_grid_plus_incumbent_local_probes"
        or int(plan.get("top5_global_quota") or 0) != 3
        or int(plan.get("top5_incumbent_local_quota") or 0) != 2
        or plan.get("qps_used_for_probe_generation_or_ranking") is not False
        or plan.get("candidate_hard_pruning") is not False
        or update.get("effective_from_outer") != EFFECTIVE_OUTER
        or update.get("qps_used_for_resource_prediction_gradient_or_score") is not False
    ):
        raise RuntimeError("parallel v23 continuation contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(binding["path"])
        if (
            not path.is_file() or path.is_symlink()
            or alg.sha(path) != str(binding["sha256"])
        ):
            raise RuntimeError(f"v23 source binding differs: {path}")
    return contract


def install() -> None:
    _bind_predecessor()
    predecessor.install()
    base.state_document = state_document_v23
    base.ONLINE_RESOURCE_MODEL_UPDATE_EFFECTIVE_OUTER = EFFECTIVE_OUTER


def main() -> int:
    validate_contract()
    validate_source()
    predecessor.v20.validate_source()
    predecessor.v20.predecessor.validate_recovery_and_plan_capture()
    predecessor.v20.validate_batched_plan_capture()
    predecessor.reconstruct_subset_only_ranges()
    install()
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
