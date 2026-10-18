#!/usr/bin/env python3
"""Run Algorithm B fresh from Outer 1 with common all-scene plan scoring."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_four_scene_algorithm_b_fresh_core_v25 as base
from scripts import mysql_algorithm_b_fresh_parallel_221_scheduler_v25 as scheduler
from scripts import (
    run_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v20 as v20,
)
from scripts import mysql_algorithm_b_all_scene_incumbent_plan_reranker_v5 as plan_ranker


alg = base.alg
runtime = base.runtime
CALIBRATION_SOURCE = (
    base.HISTORY / "v6_formal_independent_outer_plan_resume_v63_20260818_attempt001"
)
STOP_DRAIN = (
    base.HISTORY
    / "v23_formal_tpch_global_local_outer12_20260818_attempt023"
    / "stopped_mid_outer12_for_fresh_v25_drain.json"
)
SCHEDULER_CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_b_fresh_common_plan_20260818_v25"
    / "algorithm_b_fresh_contract.json"
)
_ORIGINAL_STATE_DOCUMENT = base.state_document


def validate_stop_drain() -> dict[str, Any]:
    drain = alg.load(STOP_DRAIN)
    workers = {str(row["worker_id"]): row for row in drain.get("workers") or []}
    if (
        drain.get("status") != "PASS"
        or drain.get("all_workers_zero_sessions_after") is not True
        or drain.get("all_tpch_revenue0_views_absent_after") is not True
        or len(workers) != 8
        or any(row.get("sessions_after") for row in workers.values())
    ):
        raise RuntimeError("v25 pre-fresh-campaign drain differs")
    return drain


def fresh_outer_boundary(**kwargs: Any) -> None:
    if int(kwargs["outer_attempt"]) != 1:
        return
    state = kwargs["state"]
    output = Path(kwargs["output"])
    ranges = kwargs["ranges_by_scene"]
    params = kwargs["params_by_scene"]
    expected_params, expected_ranges, _ = base.parameter_state()
    if (
        int(state.get("outer_attempt") or 0) != 0
        or any(int(value) != 0 for value in state["accepted_rounds"].values())
        or any(int(value) != 0 for value in state["rejected_outer_attempts"].values())
        or any(int(value) != 0 for value in state["interval_stable_rounds"].values())
        or params != expected_params
        or ranges != expected_ranges
    ):
        raise RuntimeError("v25 fresh Outer 1 state is not initial")
    reset_path = output / "outer_001/fresh_campaign_reset.json"
    reset_path.parent.mkdir(parents=True, exist_ok=True)
    alg.atomic(reset_path, {
        "schema_version": "mysql_algorithm_b_fresh_outer_campaign_reset_v1",
        "status": "PASS",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "effective_from_outer": 1,
        "accepted_rounds": copy.deepcopy(state["accepted_rounds"]),
        "rejected_outer_attempts": copy.deepcopy(state["rejected_outer_attempts"]),
        "interval_stable_rounds": copy.deepcopy(state["interval_stable_rounds"]),
        "params_by_scene": copy.deepcopy(params),
        "ranges_by_scene": copy.deepcopy(ranges),
        "historical_algorithm_b_outer_state_or_measurement_credit": False,
        "historical_algorithm_b_outer_evidence_role": "diagnostic_only",
        "fresh_baseline_measured_in_current_campaign": (
            state.get("fresh_baseline", {}).get("measured_in_current_campaign")
            is True
        ),
        "calibration_release_reused": True,
        "calibration_replay_count_in_current_campaign": 0,
        "stop_drain_path": str(STOP_DRAIN),
        "stop_drain_sha256": alg.sha(STOP_DRAIN),
    })
    state["fresh_outer_campaign_release"] = {
        "status": "PASS",
        "effective_from_outer": 1,
        "path": str(reset_path),
        "sha256": alg.sha(reset_path),
        "accepted_rounds_inherited": False,
        "outer_measurements_inherited": False,
    }


def state_document_v25(run_id: str) -> dict[str, Any]:
    document = _ORIGINAL_STATE_DOCUMENT(run_id)
    document.update({
        "method": (
            "independent_fresh_outer_binary_inner_estimated_subset_search_"
            "common_plan_score_v25"
        ),
        "formal_candidate_batches": [[1, 2], [3, 4], [5]],
        "candidate_parallelism": 2,
        "controller_source_path": str(Path(__file__).resolve()),
        "controller_source_sha256": alg.sha(Path(__file__).resolve()),
    })
    document["fresh_campaign_contract"] = {
        "outer_progress_reset_to_zero": True,
        "default_params_and_full_ranges": True,
        "fresh_baseline_required": True,
        "calibration_release_reused": True,
        "historical_b_outer_credit": False,
        "algorithm_c_state_or_result_read": False,
    }
    document["all_scene_incumbent_plan_rerank_release"] = {
        "release_version": "v25",
        "effective_from_outer": 1,
        "active_scenes": list(base.SCENES),
        "plan_resource_estimator_shared_with_algorithm_c": True,
        "score_formula": (
            "algorithm_c_target_drop_plus_0.25_non_target_delta_"
            "confidence_adjusted_v1"
        ),
        "score_anchor": "current_scene_incumbent",
        "inner_candidate_score_modified": False,
        "qps_used_for_prediction_gradient_or_score": False,
        "candidate_hard_pruning": False,
        "top5_global_quota": plan_ranker.GLOBAL_TOP5_QUOTA,
        "top5_incumbent_local_quota": plan_ranker.LOCAL_TOP5_QUOTA,
        "ranker_path": str(Path(plan_ranker.__file__).resolve()),
        "ranker_sha256": alg.sha(Path(plan_ranker.__file__).resolve()),
    }
    return document


def validate_contract() -> dict[str, Any]:
    if not SCHEDULER_CONTRACT.is_file() or SCHEDULER_CONTRACT.is_symlink():
        raise RuntimeError(f"sealed v25 fresh contract absent: {SCHEDULER_CONTRACT}")
    contract = alg.load(SCHEDULER_CONTRACT)
    fresh = contract.get("fresh_campaign") or {}
    rerank = contract.get("all_scene_incumbent_plan_rerank") or {}
    if (
        contract.get("release_version") != "v25"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("candidate_batches") != [[1, 2], [3, 4], [5]]
        or int(contract.get("candidate_parallelism") or 0) != 2
        or fresh.get("outer_progress_reset_to_zero") is not True
        or fresh.get("fresh_baseline_required") is not True
        or fresh.get("calibration_release_reused") is not True
        or fresh.get("historical_b_outer_credit") is not False
        or rerank.get("active_scenes") != list(base.SCENES)
        or rerank.get("plan_resource_estimator_shared_with_algorithm_c") is not True
        or rerank.get("score_anchor") != "current_scene_incumbent"
        or rerank.get("inner_candidate_score_modified") is not False
        or rerank.get("qps_used_for_prediction_gradient_or_score") is not False
        or rerank.get("candidate_hard_pruning") is not False
        or int(rerank.get("top5_global_quota") or 0) != 3
        or int(rerank.get("top5_incumbent_local_quota") or 0) != 2
    ):
        raise RuntimeError("v25 fresh Algorithm B contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(binding["path"])
        if (
            not path.is_file() or path.is_symlink()
            or alg.sha(path) != str(binding["sha256"])
        ):
            raise RuntimeError(f"v25 source binding differs: {path}")
    return contract


def install() -> None:
    base.PREPARED = v20.PREPARED
    base.ACCOUNTS = v20.ACCOUNTS
    base.FreshBaselineObjective.score = scheduler._score_with_resource_payload
    runtime.alg.measure_batch = v20.independent_measurement.measure_batch
    runtime._build_single_candidate_assignments = scheduler._build_assignments_221
    runtime._measure_batch_with_fair_coordination = scheduler._measure_221
    scheduler._pending.clear()
    scheduler._rank_cache.clear()

    base.REUSE_VERIFIED_BASELINE = False
    base.REUSE_HISTORICAL_OUTER_EVIDENCE = False
    base.RESUME_OUTER_ATTEMPT = 0
    base.RESUME_VALID_RANKS = ()
    base.RESUME_INVALID_RANKS = ()
    base.LEGACY_COUPLED_RECONSTRUCTION_MAX_OUTER = 0
    base.ACCEPTANCE_AWARE_SELECTION_EFFECTIVE_OUTER = 1
    base.SUBSET_ONLY_SHRINK_EFFECTIVE_OUTER = 1
    base.CANDIDATE_DIVERSITY_EFFECTIVE_OUTER = 1
    base.ONLINE_RESOURCE_MODEL_UPDATE_EFFECTIVE_OUTER = 1

    plan_ranker.EFFECTIVE_FROM_OUTER = 1
    plan_ranker.BASE_RANK_SCENE_OVERRIDE = base.rank_scene
    plan_ranker.install()
    base.rank_scene = plan_ranker.rank_scene
    base.state_document = state_document_v25
    base.OUTER_BOUNDARY_STATE_TRANSFORM = fresh_outer_boundary


def main() -> int:
    validate_contract()
    validate_stop_drain()
    if not CALIBRATION_SOURCE.is_dir():
        raise RuntimeError("v25 calibration release source absent")
    install()
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
