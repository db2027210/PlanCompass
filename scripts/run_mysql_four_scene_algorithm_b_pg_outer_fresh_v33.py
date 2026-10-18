#!/usr/bin/env python3
"""Run fresh MySQL Algorithm B with the PostgreSQL search state machine."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import (
    run_mysql_four_scene_algorithm_b_dynamic_support_resume_core_v27 as base,
)
from scripts import mysql_algorithm_b_fresh_parallel_221_scheduler_v25 as scheduler
from scripts import (
    run_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v20 as v20,
)
from scripts import mysql_algorithm_b_pg_outer_policy_v31 as pg_outer


runtime = base.runtime
alg = base.alg
CALIBRATION_SOURCE = (
    base.HISTORY / "v6_formal_independent_outer_plan_resume_v63_20260818_attempt001"
)
RECOVERY_ROOT = (
    base.HISTORY / "v29_prelaunch_xp_recovery_20260819_attempt001"
)
PREPARED = RECOVERY_ROOT / "prepared_binding.json"
ACCOUNTS = RECOVERY_ROOT / "benchmark_accounts_binding.json"
STOP_DRAIN = (
    base.HISTORY / "v32_pg_outer_prelaunch_20260819/stopped_v29_drain.json"
)
CONTRACT = (
    ROOT / "config/formal/frozen_mysql_pg_outer_20260819_v33"
    / "algorithm_b_fresh_contract.json"
)
_ORIGINAL_STATE_DOCUMENT = base.state_document


def validate_stop_drain() -> dict[str, Any]:
    document = alg.load(STOP_DRAIN)
    workers = document.get("workers") or []
    if (
        document.get("status") != "PASS"
        or document.get("all_workers_zero_sessions_after") is not True
        or document.get("all_tpch_revenue0_views_absent_after") is not True
        or len(workers) != 8
        or any(row.get("sessions_after") for row in workers)
    ):
        raise RuntimeError("v33 predecessor stopped-controller session drain differs")
    return document


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
        or any(bool(value) for value in state["converged"].values())
        or params != expected_params
        or ranges != expected_ranges
    ):
        raise RuntimeError("v33 fresh PG-style Outer 1 state is not initial")
    path = output / "outer_001/pg_outer_fresh_campaign_reset.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    alg.atomic(path, {
        "schema_version": "mysql_algorithm_b_pg_outer_fresh_reset_v1",
        "status": "PASS",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "outer_progress_reset_to_zero": True,
        "historical_outer_state_or_measurement_credit": False,
        "default_params_and_full_ranges": True,
        "fresh_baseline_measured_in_current_campaign": (
            state.get("fresh_baseline", {}).get("measured_in_current_campaign")
            is True
        ),
        "mysql_prediction_calibration_release_reused": True,
        "postgresql_search_outer_contract": pg_outer.policy_contract(),
        "post_search_stability_confirmation": False,
        "stop_drain_path": str(STOP_DRAIN),
        "stop_drain_sha256": alg.sha(STOP_DRAIN),
    })
    state["fresh_outer_campaign_release"] = {
        "status": "PASS",
        "path": str(path),
        "sha256": alg.sha(path),
        "accepted_rounds_inherited": False,
        "outer_measurements_inherited": False,
    }


def state_document_v33(run_id: str) -> dict[str, Any]:
    document = _ORIGINAL_STATE_DOCUMENT(run_id)
    document.update({
        "method": "mysql_algorithm_b_pg_grid_inner_pg_search_outer_fresh_v33",
        "controller_source_path": str(Path(__file__).resolve()),
        "controller_source_sha256": alg.sha(Path(__file__).resolve()),
        "formal_candidate_batches": [[1, 2], [3, 4], [5]],
        "candidate_parallelism": 2,
        "postgresql_search_outer_contract": pg_outer.policy_contract(),
        "post_search_stability_confirmation": False,
    })
    document["fresh_campaign_contract"] = {
        "outer_progress_reset_to_zero": True,
        "default_params_and_full_ranges": True,
        "fresh_baseline_required": True,
        "mysql_prediction_calibration_release_reused": True,
        "historical_b_outer_credit": False,
        "algorithm_c_state_or_result_credit": False,
    }
    document["algorithm_b_inner_contract"] = {
        "database_interface": "mysql",
        "prediction_model": "mysql_fresh_experience_resource_model",
        "grid_points_per_parameter": base.GRID_POINTS,
        "maximum_coordinate_passes": base.INNER_PASSES,
        "no_improvement_patience": base.CONVERGENCE_PATIENCE,
        "exhaustive_interaction_subset_sizes": [
            base.COMBINATION_MIN_SIZE, base.COMBINATION_MAX_SIZE,
        ],
        "representative_values_per_parameter": (
            base.COMBINATION_VALUES_PER_PARAMETER
        ),
        "combination_candidate_cap": 0,
        "qps_used_for_prediction_gradient_or_score": False,
    }
    return document


def validate_contract() -> dict[str, Any]:
    if not CONTRACT.is_file() or CONTRACT.is_symlink():
        raise RuntimeError(f"sealed v33 Algorithm B contract absent: {CONTRACT}")
    contract = alg.load(CONTRACT)
    outer = contract.get("pg_search_outer") or {}
    inner = contract.get("algorithm_b_inner") or {}
    if (
        contract.get("release_version") != "v33"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("candidate_batches") != [[1, 2], [3, 4], [5]]
        or outer.get("accepted_target") != 10
        or outer.get("required_acceptance_tier") != "fallback"
        or outer.get("post_search_stability_confirmation") is not False
        or inner.get("grid_points_per_parameter") != 15
        or inner.get("maximum_coordinate_passes") != 20
        or inner.get("interaction_subset_sizes") != [2, 4]
        or inner.get("database_interface") != "mysql"
        or (contract.get("measurement_stability_contract") or {}).get(
            "minimum_measurement_seconds"
        ) != 300
        or (contract.get("measurement_stability_contract") or {}).get(
            "xp_maximum_measurement_seconds"
        ) != 570
        or (contract.get("measurement_stability_contract") or {}).get(
            "job_tpch_hard_timeout_seconds"
        ) != 900
    ):
        raise RuntimeError("v33 Algorithm B frozen contract differs")
    for name, binding in (contract.get("source_bindings") or {}).items():
        path = ROOT / str(binding["path"])
        if (
            not path.is_file() or path.is_symlink()
            or alg.sha(path) != str(binding["sha256"])
        ):
            raise RuntimeError(f"v33 Algorithm B source binding differs: {name}")
    return contract


def install() -> None:
    base.PREPARED = PREPARED
    base.ACCOUNTS = ACCOUNTS
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
    base.CONTINUATION_COMPLETED_OUTER_ATTEMPT = None
    base.LEGACY_COUPLED_RECONSTRUCTION_MAX_OUTER = 0
    base.ACCEPTANCE_AWARE_SELECTION_EFFECTIVE_OUTER = 1
    base.SUBSET_ONLY_SHRINK_EFFECTIVE_OUTER = 1
    # PG Algorithm B uses the grid's canonical ordering followed by plan
    # representatives; the historical MySQL local-diversity override is off.
    base.CANDIDATE_DIVERSITY_EFFECTIVE_OUTER = base.MAXIMUM_OUTER_ATTEMPTS + 1
    base.ONLINE_RESOURCE_MODEL_UPDATE_EFFECTIVE_OUTER = 1
    base.state_document = state_document_v33
    base.OUTER_BOUNDARY_STATE_TRANSFORM = fresh_outer_boundary


def main() -> int:
    validate_contract()
    validate_stop_drain()
    if not CALIBRATION_SOURCE.is_dir():
        raise RuntimeError("v33 MySQL prediction calibration source absent")
    install()
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
