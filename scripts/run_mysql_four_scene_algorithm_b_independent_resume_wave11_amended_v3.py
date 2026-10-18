#!/usr/bin/env python3
"""Resume Algorithm B with a proven-feasible TPC-H calibration amendment."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
import fcntl
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
FRAMEWORK_ROOT = Path("/home/anonymous/program/resource_adaptive")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(FRAMEWORK_ROOT) not in sys.path:
    sys.path.append(str(FRAMEWORK_ROOT))

from common_outer_loop import advance_acceptance_tier, decide_validation_acceptance
from double_loop_grid_optimizer import DoubleLoopGridOptimizer, GridInnerLoopConfig
from double_loop_search_common import OuterLoopConfig, ParameterRange
from double_loop_scenario import (
    FreshBaselineObjective,
    FreshExperienceModel,
    FreshModelQualityThresholds,
    measurement_from_payload,
)
from mysql_algorithm_b_policy import (
    mysql_fresh_model_calibration_points,
    preflight_resource_error_bounds,
    run_mysql_fresh_model_quality_gate,
)
from scripts import mysql_co_resident_pg_isolation_v12 as isolation
from scripts import reset_mysql_xp_data_planes_v7 as reset_v7
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v22 as lease_v22
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v32 as c32


runtime = c32.runtime
alg = runtime.alg
SCENES = tuple(runtime.SCENES)
FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260813_v18"
    / "four_scene_effective_resource_successor.json"
)
PREFLIGHT = ROOT / (
    "preflight/mysql_four_scene_20260809/"
    "v54_recovered_xp_rebind_r0_20260817_attempt012_preflight.json"
)
PREPARED = ROOT / (
    "history/mysql_four_scene_20260809/"
    "v68_outer16_post_baseline_rebind_20260817_attempt006_rebind/"
    "prepared_binding.json"
)
ACCOUNTS = ROOT / (
    "history/mysql_four_scene_20260809/"
    "v68_outer16_post_baseline_rebind_20260817_attempt006_accounts/"
    "benchmark_accounts_binding.json"
)
TARGET_ACCEPTED_SHRINKS = 10
MAXIMUM_OUTER_ATTEMPTS = 100
MAXIMUM_REJECTED_OUTER_ATTEMPTS_PER_SCENE = 20
MAXIMUM_POST_TARGET_CONFIRMATIONS = 3
FIXED_TOP_K = 5
CALIBRATION_REPLAY_BUDGET = 15
GRID_POINTS = 15
INNER_PASSES = 20
CONVERGENCE_PATIENCE = 2
COMBINATION_MIN_SIZE = 2
COMBINATION_MAX_SIZE = 4
COMBINATION_VALUES_PER_PARAMETER = 3
HISTORY = ROOT / "history/mysql_four_scene_20260809"
RESUME_CALIBRATION_WAVES = 10
RESUME_FAILED_WAVE = 11
RESUME_FAILURE = "RuntimeError: Algorithm B calibration wave 11 failed closed"
TPCH_ROW_EVALUATE_REPLACEMENTS = {
    11: 0.117142857142857,
    15: 0.134285714285714,
}


def amend_tpch_calibration_design(
    designs: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Replace only the two high, formally infeasible TPC-H row-cost points."""
    rows = designs["tpch_sf4_memory"]
    audit: list[dict[str, Any]] = []
    for rank, replacement in TPCH_ROW_EVALUATE_REPLACEMENTS.items():
        row = rows[rank - 1]
        original = float(row["params"]["row_evaluate_cost"])
        if (
            row.get("calibration_id") != f"calibration_{rank:03d}"
            or row.get("changed_parameters") != ["row_evaluate_cost"]
            or original <= 0.151428571428571
        ):
            raise RuntimeError("TPC-H calibration amendment source differs")
        row["params"]["row_evaluate_cost"] = replacement
        row["design"] = "mysql_effective_coordinate_formal_feasible_amendment_v1"
        row["phase"] = "coordinate_axis_formal_feasible_interior"
        audit.append({
            "scene": "tpch_sf4_memory",
            "rank": rank,
            "parameter": "row_evaluate_cost",
            "original_value": original,
            "replacement_value": replacement,
            "formal_passing_envelope": [0.1, 0.151428571428571],
        })
    return audit


def configure_runtime() -> dict[str, Any]:
    isolation.validate_release()
    isolation.install()
    alg.FREEZE = FREEZE
    alg.reset = reset_v7
    alg.r0mod.FREEZE = FREEZE
    alg.r0mod.FREEZE_SCHEMA = (
        "mysql_disk2_four_scene_two_worker_effective_resource_successor_v18"
    )
    alg.r0mod.PREFLIGHT = PREFLIGHT
    alg.r0mod.RUNTIME_PREFLIGHT = PREFLIGHT
    alg.r0mod.PREPARED = PREPARED
    alg.r0mod.BENCHMARK_ACCOUNTS = ACCOUNTS
    runtime.WAVE_COORDINATION_CONTRACT = isolation.CONTRACT_PATH
    lease_v22.isolation = isolation
    runtime.alg.reset_xp = c32.reset_xp
    contract = alg.load(isolation.CONTRACT_PATH)
    bindings = dict(contract.get("source_bindings") or {})
    scoped = ROOT / "scripts/mysql_cross_project_wave_coordination_v2.py"
    installer = ROOT / "scripts/mysql_co_resident_pg_isolation_v12.py"
    if (
        contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("release_version") != "v63"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or float(contract.get("per_wave_quiet_seconds") or 0) != 15.0
        or float(contract.get("maximum_quiet_wait_while_held_seconds") or 0)
        != 60.0
        or int(contract.get("maximum_gate_attempts_per_wave") or 0) != 1
        or (contract.get("isolated_external_d_state_policy") or {}).get(
            "unknown_or_unproved_d_state"
        ) != "fail_closed"
        or (contract.get("isolated_external_d_state_policy") or {}).get(
            "proved_cpu_storage_memory_isolated_d_state"
        ) != "record_nonblocking"
        or bindings.get("device_scoped_coordination_path")
        != str(scoped.relative_to(ROOT))
        or bindings.get("device_scoped_coordination_sha256") != alg.sha(scoped)
        or bindings.get("co_resident_profile_installer_v12_path")
        != str(installer.relative_to(ROOT))
        or bindings.get("co_resident_profile_installer_v12_sha256")
        != alg.sha(installer)
    ):
        raise RuntimeError("v63 device-scoped wave authority differs")
    return contract


def _verified_sidecar(path: Path) -> str:
    digest = alg.sha(path)
    sidecar = path.with_name(path.name + ".sha256")
    if (
        not sidecar.is_file()
        or sidecar.is_symlink()
        or sidecar.read_text(encoding="ascii").split()[0] != digest
    ):
        raise RuntimeError(f"artifact SHA sidecar differs: {path}")
    return digest


def _batch_rows(path: Path, *, expected_count: int) -> dict[str, dict[str, Any]]:
    _verified_sidecar(path)
    batch = alg.load(path)
    raw = batch.get("measurements")
    if not isinstance(raw, Mapping):
        raise RuntimeError(f"batch measurements differ: {path}")
    rows = {str(key): dict(value) for key, value in raw.items()}
    if (
        batch.get("status") != "PASS"
        or batch.get("formal_scope") != "mysql_only"
        or batch.get("cross_engine_comparison_prohibited") is not True
        or batch.get("failures") not in ([], None)
        or len(rows) != expected_count
        or any(
            row.get("hard_gate_passed") is not True
            or row.get("coverage_complete") is not True
            for row in rows.values()
        )
    ):
        raise RuntimeError(f"source batch is not reusable: {path}")
    return rows


def _mirror_batch(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=False, mode=0o700)
    shutil.copy2(source, destination)
    shutil.copy2(
        source.with_name(source.name + ".sha256"),
        destination.with_name(destination.name + ".sha256"),
    )
    if alg.sha(source) != alg.sha(destination):
        raise RuntimeError(f"mirrored batch differs: {source}")


def validate_resume_source(source: Path) -> dict[str, Any]:
    source = source.resolve()
    if source.parent != HISTORY.resolve() or not source.is_dir():
        raise RuntimeError("resume source root differs")
    state_path = source / "run_state.json"
    state_sha = _verified_sidecar(state_path)
    state = alg.load(state_path)
    if (
        state.get("algorithm") != "B"
        or state.get("formal_scope") != "mysql_only"
        or state.get("cross_engine_comparison_prohibited") is not True
        or state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or state.get("error") != RESUME_FAILURE
        or int(state.get("outer_attempt") or 0) != 0
        or any(int(value) != 0 for value in (state.get("accepted_rounds") or {}).values())
        or any(bool(value) for value in (state.get("converged") or {}).values())
        or state.get("active_scenes") != list(SCENES)
    ):
        raise RuntimeError("Algorithm B resume source state differs")

    baseline = source / "formal_independent_baseline/batch_result.json"
    baseline_rows = _batch_rows(baseline, expected_count=8)
    if (state.get("fresh_baseline") or {}).get("sha256") != alg.sha(baseline):
        raise RuntimeError("resume baseline state binding differs")
    design = source / "calibration_design.json"
    design_sha = _verified_sidecar(design)
    design_value = alg.load(design)
    if (
        design_value.get("status") != "PASS"
        or design_value.get("replay_budget_per_scene") != 15
        or design_value.get("algorithm_c_state_read") is not False
    ):
        raise RuntimeError("resume calibration design differs")

    wave_rows: dict[int, dict[str, dict[str, Any]]] = {}
    wave_evidence = []
    for rank in range(1, RESUME_CALIBRATION_WAVES + 1):
        path = source / f"fresh_calibration/wave_{rank:02d}/batch_result.json"
        wave_rows[rank] = _batch_rows(path, expected_count=4)
        wave_evidence.append({"wave": rank, "path": str(path), "sha256": alg.sha(path)})
    failure_evidence = source / "fresh_calibration/wave_11/batch_result.json"
    failure_evidence_sha = _verified_sidecar(failure_evidence)
    failure_value = alg.load(failure_evidence)
    failures = failure_value.get("failures")
    measurements = failure_value.get("measurements")
    failure_errors = [
        str(row.get("error") or "") for row in failures
        if isinstance(row, Mapping)
    ] if isinstance(failures, list) else []
    if (
        failure_value.get("status") != "FAILED_CLOSED"
        or not 900.0 <= float(failure_value.get("elapsed_seconds") or 0.0) <= 910.0
        or not any(
            "tpch_lane_1" in error and "timed out after 900 seconds" in error
            for error in failure_errors
        )
        or not isinstance(measurements, Mapping)
        or set(measurements) != set(workers for workers in (
            "job_w1", "tpch_w1", "xp_cpu_w1", "xp_io_w1"
        ))
        or measurements["job_w1"].get("hard_gate_passed") is not False
        or measurements["tpch_w1"].get("hard_gate_passed") is not False
        or measurements["xp_cpu_w1"].get("hard_gate_passed") is not True
        or measurements["xp_cpu_w1"].get("coverage_complete") is not True
        or measurements["xp_io_w1"].get("hard_gate_passed") is not True
        or measurements["xp_io_w1"].get("coverage_complete") is not True
    ):
        raise RuntimeError("resume source wave-11 timeout evidence differs")
    if (source / "fresh_calibration/wave_12").exists():
        raise RuntimeError("resume source unexpectedly advanced beyond failed wave 11")
    continuation = state.get("technical_continuation") or {}
    prior_failure = Path(str(continuation.get("failure_evidence_path") or ""))
    prior_failure_sha = str(continuation.get("failure_evidence_sha256") or "")
    if (
        continuation.get("kind") != "formal_workload_timeout_whole_wave_retry_v2"
        or continuation.get("partial_failed_wave_results_reused") is not False
        or not prior_failure.is_file()
        or alg.sha(prior_failure) != prior_failure_sha
    ):
        raise RuntimeError("prior wave-11 timeout continuation evidence differs")
    prior_value = alg.load(prior_failure)
    prior_errors = [
        str(row.get("error") or "") for row in (prior_value.get("failures") or [])
        if isinstance(row, Mapping)
    ]
    if (
        prior_value.get("status") != "FAILED_CLOSED"
        or not any(
            "tpch_lane_3" in error and "timed out after 900 seconds" in error
            for error in prior_errors
        )
    ):
        raise RuntimeError("first wave-11 timeout evidence differs")
    for scene in SCENES:
        if (source / f"fresh_calibration/{scene}").exists():
            raise RuntimeError("resume source unexpectedly built a quality model")
    return {
        "source": source,
        "state": state,
        "state_path": state_path,
        "state_sha256": state_sha,
        "baseline_path": baseline,
        "baseline_rows": baseline_rows,
        "baseline_sha256": alg.sha(baseline),
        "design_path": design,
        "design": design_value,
        "design_sha256": design_sha,
        "wave_rows": wave_rows,
        "wave_evidence": wave_evidence,
        "failure_evidence_path": failure_evidence,
        "failure_evidence_sha256": failure_evidence_sha,
        "prior_failure_evidence_path": prior_failure,
        "prior_failure_evidence_sha256": prior_failure_sha,
    }


def state_document(run_id: str) -> dict[str, Any]:
    return {
        "schema_version": "mysql_four_scene_algorithm_b_independent_state_v2",
        "algorithm": "B",
        "method": "independent_outer_binary_inner_estimated_subset_search",
        "status": "RUNNING",
        "stage": "VALIDATING_INDEPENDENT_AUTHORITY",
        "started_at": alg.now(),
        "run_id": run_id,
        "pid": os.getpid(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "formal_evidence_eligible": True,
        "accepted_state_credit_prohibited": False,
        "active_scenes": list(SCENES),
        "outer_attempt": 0,
        "accepted_rounds": {scene: 0 for scene in SCENES},
        "rejected_outer_attempts": {scene: 0 for scene in SCENES},
        "post_target_stability_confirmation_counts": {
            scene: 0 for scene in SCENES
        },
        "acceptance_tier": {scene: "strict_three_level" for scene in SCENES},
        "consecutive_failed_rounds": {scene: 0 for scene in SCENES},
        "interval_stable_rounds": {scene: 0 for scene in SCENES},
        "stable_intervals": {scene: None for scene in SCENES},
        "converged": {scene: False for scene in SCENES},
        "target_accepted_shrinks": TARGET_ACCEPTED_SHRINKS,
        "maximum_outer_attempts": MAXIMUM_OUTER_ATTEMPTS,
        "maximum_rejected_outer_attempts_per_scene": (
            MAXIMUM_REJECTED_OUTER_ATTEMPTS_PER_SCENE
        ),
        "maximum_post_target_confirmations": MAXIMUM_POST_TARGET_CONFIRMATIONS,
        "fixed_real_top_k": FIXED_TOP_K,
        "one_time_calibration_replays_per_scene": CALIBRATION_REPLAY_BUDGET,
        "inner_grid_contract": {
            "grid_points_per_coordinate": GRID_POINTS,
            "maximum_passes": INNER_PASSES,
            "no_improvement_patience": CONVERGENCE_PATIENCE,
            "combination_sizes": [COMBINATION_MIN_SIZE, COMBINATION_MAX_SIZE],
            "combination_values_per_parameter": (
                COMBINATION_VALUES_PER_PARAMETER
            ),
            "combination_candidate_cap": 0,
            "global_real_top_k": FIXED_TOP_K,
        },
        "convergence_rule": {
            "accepted_shrinks_required": TARGET_ACCEPTED_SHRINKS,
            "maximum_interval_ratio": 1.01,
            "required_consecutive_stable_accepted_rounds": 2,
            "rejected_outer_round_resets_stability_streak": True,
            "all_active_parameters_must_satisfy_interval": True,
        },
        "independence_contract": {
            "algorithm_c_used_only_as_launch_sequence_gate": True,
            "algorithm_c_checkpoint_read": False,
            "algorithm_c_release_read": False,
            "algorithm_c_incumbent_read": False,
            "algorithm_c_interval_read": False,
            "algorithm_c_measurement_read": False,
            "algorithm_c_candidate_or_pruning_history_read": False,
            "fresh_default_baseline_required": True,
            "full_frozen_parameter_ranges_required": True,
            "own_accepted_rejected_and_convergence_state": True,
            "own_top15_calibration_required": True,
            "own_fresh_resource_model_required": True,
            "algorithm_c_gradient_ranker_used": False,
            "shared_measurement_helpers_are_infrastructure_only": True,
        },
        "dynamic_non_interference_contract": {
            "rolling_window_gate_before_every_baseline_calibration_and_top5_wave": True,
            "co_resident_database_and_benchmark_inventory_required": True,
            "cpu_numa_memory_swap_process_and_storage_isolation_fail_closed": True,
            "external_process_mutation": False,
        },
        "freeze_path": str(FREEZE),
        "freeze_sha256": alg.sha(FREEZE),
        "runtime_preflight_path": str(PREFLIGHT),
        "runtime_preflight_sha256": alg.sha(PREFLIGHT),
        "prepared_binding_path": str(PREPARED),
        "prepared_binding_sha256": alg.sha(PREPARED),
        "benchmark_accounts_path": str(ACCOUNTS),
        "benchmark_accounts_sha256": alg.sha(ACCOUNTS),
        "experience_table_root": str(alg.TABLE_ROOT),
        "controller_source_path": str(Path(__file__).resolve()),
        "controller_source_sha256": alg.sha(Path(__file__).resolve()),
        "post_reset_storage_settle_source_path": str(Path(c32.__file__).resolve()),
        "post_reset_storage_settle_source_sha256": alg.sha(Path(c32.__file__).resolve()),
        "reset_source_path": str(Path(reset_v7.__file__).resolve()),
        "reset_source_sha256": alg.sha(Path(reset_v7.__file__).resolve()),
        "shared_measurement_runtime_path": str(Path(runtime.__file__).resolve()),
        "shared_measurement_runtime_sha256": alg.sha(Path(runtime.__file__).resolve()),
        "shared_measurement_runtime_role": (
            "workload_reset_coverage_and_isolation_helpers_only"
        ),
    }


def fresh_authority() -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any]]:
    lease = runtime.coordination.acquire_exclusive_blocking(timeout_seconds=3.0)
    try:
        alg.r0mod.COORDINATED_LEASE_HOLDER_PID = os.getpid()
        alg.r0mod.COORDINATION_CONTRACT = isolation.CONTRACT_PATH
        return alg.r0mod._validate_authority()
    finally:
        alg.r0mod.COORDINATED_LEASE_HOLDER_PID = None
        alg.r0mod.COORDINATION_CONTRACT = None
        runtime.coordination.release(lease)


def parameter_state() -> tuple[
    dict[str, dict[str, float]], dict[str, dict[str, Any]], dict[str, list[str]],
]:
    params: dict[str, dict[str, float]] = {}
    ranges: dict[str, dict[str, Any]] = {}
    active: dict[str, list[str]] = {}
    for scene in SCENES:
        table = alg.load(alg.TABLE_ROOT / alg.TABLES[scene])
        space = dict(table["parameter_space"])
        active[scene] = [str(name) for name in space["active_params"]]
        ranges[scene] = copy.deepcopy(dict(space["ranges"]))
        if (
            not active[scene]
            or set(ranges[scene]) != set(active[scene])
            or any(
                float(row["lower"]) <= 0
                or float(row["upper"]) / float(row["lower"]) != 100.0
                for row in ranges[scene].values()
            )
        ):
            raise RuntimeError(f"{scene} independent full parameter range differs")
        params[scene] = copy.deepcopy(dict(alg.DEFAULTS))
    return params, ranges, active


def transformed_ranges(
    ranges: Mapping[str, Any], active: list[str],
) -> dict[str, ParameterRange]:
    result: dict[str, ParameterRange] = {}
    for priority, name in enumerate(active, 1):
        row = dict(ranges[name])
        result[name] = ParameterRange(
            float(row["lower"]), float(row["upper"]),
            discrete_count=GRID_POINTS, priority=priority, transform="log",
        )
    return result


def measurement_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    resources = {
        name: float(dict(row["resource_means"])[name])
        for name in ("cpu", "memory", "io")
    }
    return {
        "resource": resources,
        "resources": copy.deepcopy(resources),
        "db_qps": float(row["db_qps"]),
        "p95_ms": float(row["p95_ms"]),
        "p99_ms": float(row["p99_ms"]),
        "hard_gate_passed": row.get("hard_gate_passed") is True,
        "coverage_complete": row.get("coverage_complete") is True,
        "source_worker_id": row.get("worker_id"),
        "source_rank": row.get("rank"),
    }


def build_objective(
    scene: str, incumbent: Mapping[str, Any],
) -> FreshBaselineObjective:
    payload = {
        "resource": dict(incumbent["resource_means"]),
        "db_qps": float(incumbent["db_qps"]),
        "p95_ms": float(incumbent["p95_ms"]),
        "p99_ms": float(incumbent["p99_ms"]),
    }
    baseline = measurement_from_payload(payload["resource"], payload)
    target = alg.TARGET[scene]
    return FreshBaselineObjective(
        target, baseline,
        resource_limits={"cpu": 1.0, "memory": 1.0, "io": 1.0},
        target_sweet_upper=max(
            1e-9, float(incumbent["resource_means"][target]) * 0.95,
        ),
    )


def rank_scene(
    *, scene: str, params: Mapping[str, float], ranges: Mapping[str, Any],
    active: list[str], model: FreshExperienceModel,
    objective: FreshBaselineObjective, error_bounds: Mapping[str, Any],
    incumbent_resources: Mapping[str, float],
    worker: Mapping[str, Any], replayed: set[str], round_index: int,
    calibration_seed_points: list[Mapping[str, float]],
) -> dict[str, Any]:
    """Run B's estimated coordinate/interaction loop without real replay."""
    started = time.monotonic()
    parameter_ranges = transformed_ranges(ranges, active)

    def no_real_replay(_sql_set: Any, _params: dict[str, float]) -> dict[str, Any]:
        raise RuntimeError("Algorithm B estimated ranking cannot perform real replay")

    optimizer = DoubleLoopGridOptimizer(
        model.predict, no_real_replay,
        {"cpu": 1.0, "memory": 1.0, "io": 1.0},
        candidate_seed_points=calibration_seed_points,
        grid_config=GridInnerLoopConfig(
            max_workers=8,
            max_passes=INNER_PASSES,
            no_improvement_patience=CONVERGENCE_PATIENCE,
            combination_probe_enabled=True,
            combination_min_size=COMBINATION_MIN_SIZE,
            combination_max_size=COMBINATION_MAX_SIZE,
            combination_values_per_param=COMBINATION_VALUES_PER_PARAMETER,
            combination_max_candidates=0,
        ),
        outer_config=OuterLoopConfig(
            max_rounds=TARGET_ACCEPTED_SHRINKS,
            real_validation_top_k=FIXED_TOP_K,
            real_validation_top_fraction=1.0,
            topk_controller="fixed",
            adaptive_topk_min_k=FIXED_TOP_K,
            adaptive_topk_batch_size=1,
            no_improvement_patience=0,
            epsilon=1e-4,
            target_resource=alg.TARGET[scene],
        ),
        estimated_score_func=objective.score,
        real_score_func=objective.score,
        resource_box_upper_bound_func=objective.optimistic_score_in_resource_box,
        initial_resource_error_bounds=dict(error_bounds),
        verbose=False,
    )
    points, grid_audit = optimizer._grid_small_loop(
        {"scene": scene}, dict(params), active, parameter_ranges,
        round_index=round_index, iteration_writer=None,
    )
    ranked = optimizer._deduplicate_and_rank(points)
    incumbent_key = alg.optimizer_params_key(params)
    usable = [
        point for point in ranked
        if alg.optimizer_params_key(point.params) != incumbent_key
        and alg.optimizer_params_key(point.params) not in replayed
    ]
    if len(usable) < FIXED_TOP_K:
        raise RuntimeError(f"{scene} independent B grid lacks fixed Top-5")
    selected: list[dict[str, Any]] = []
    for rank, point in enumerate(usable[:FIXED_TOP_K], 1):
        selected.append({
            "rank": rank,
            "subset_id": point.subset_id,
            "active_subset": list(point.active_subset),
            "optimized_params": {
                name: float(value) for name, value in point.params.items()
            },
            "estimated_resources": {
                name: float(point.estimated_resource[name])
                for name in ("cpu", "memory", "io")
            },
            "estimated_score": float(point.estimated_score),
            "qps_used_for_resource_prediction_or_score": False,
            "hard_pruned": False,
            "full_plan_evidence_required_before_real_replay": True,
        })
    # Capture complete EXPLAIN evidence for exactly the selected global Top-5.
    alg.apply_costs(worker, params)
    baseline_plans = alg.capture_scene_plans(alg.load(FREEZE), scene, worker)
    relation_stats = alg.relation_stats(worker)
    executor_parameters = alg.executor_parameters(worker)
    try:
        for row in selected:
            readback = alg.apply_costs(worker, row["optimized_params"])
            candidate_plans = alg.capture_scene_plans(
                alg.load(FREEZE), scene, worker,
            )
            row["effective_optimizer_cost_readback"] = readback
            row["resource_model"] = {
                **alg.enhance_resource_prediction(
                    incumbent_resources=incumbent_resources,
                    experience_prediction=row["estimated_resources"],
                    baseline_plans=baseline_plans,
                    candidate_plans=candidate_plans,
                    relation_stats=relation_stats,
                    executor_parameters=executor_parameters,
                    concurrency=alg.SCENE_CONCURRENCY[scene],
                    cache_profile=alg.PLAN_CACHE_PROFILE,
                ),
                "kind": "algorithm_b_fresh_model_grid_with_full_plan_v2",
                "qps_used_for_resource_prediction_or_score": False,
                "candidate_hard_pruning": False,
                "complete_full_plan_evidence": True,
            }
    finally:
        alg.apply_costs(worker, params)
    return {
        "scene": scene,
        "representative_worker_id": worker["worker_id"],
        "elapsed_seconds": time.monotonic() - started,
        "grid_audit": copy.deepcopy(grid_audit),
        "candidate_count_before_top5": len(usable),
        "selected_top5": selected,
        "resource_error_bounds": copy.deepcopy(dict(error_bounds)),
        "qps_used_for_resource_prediction_gradient_or_score": False,
        "candidate_hard_pruning": False,
        "complete_plan_evidence_captured_before_real_replay": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--resume-from", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.run_id) is None:
        parser.error("unsafe run id")
    resume = validate_resume_source(args.resume_from)
    output = HISTORY / args.run_id
    if output.resolve() == resume["source"]:
        raise RuntimeError("resume output must differ from immutable source")
    if output.exists():
        raise RuntimeError(f"fresh Algorithm B output exists: {output}")
    output.mkdir(parents=True, mode=0o700)
    lock = Path("/tmp/mysql_four_scene_algorithm_b_independent_v1.lock").open("w")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        raise RuntimeError("another independent Algorithm B controller owns the lock") from error

    state_path = output / "run_state.json"
    state = state_document(args.run_id)
    state["method"] = "independent_outer_binary_inner_estimated_subset_search_resumed"
    state["technical_continuation"] = {
        "kind": "formal_infeasible_calibration_point_amendment_v3",
        "source_run_root": str(resume["source"]),
        "source_state_path": str(resume["state_path"]),
        "source_state_sha256": resume["state_sha256"],
        "completed_calibration_waves_reused": RESUME_CALIBRATION_WAVES,
        "failed_wave": RESUME_FAILED_WAVE,
        "failed_wave_measurement_started": True,
        "partial_failed_wave_results_reused": False,
        "failure_evidence_path": str(resume["failure_evidence_path"]),
        "failure_evidence_sha256": resume["failure_evidence_sha256"],
        "prior_failure_evidence_path": str(resume["prior_failure_evidence_path"]),
        "prior_failure_evidence_sha256": resume["prior_failure_evidence_sha256"],
        "calibration_design_amended": True,
        "accepted_rounds_inherited": False,
        "outer_state_inherited": False,
        "algorithm_c_state_read": False,
    }
    alg.atomic(state_path, state)
    alg.atomic(output / "technical_continuation_manifest.json", {
        "schema_version": "mysql_algorithm_b_independent_technical_resume_v3",
        "status": "VERIFIED_BEFORE_CONTINUATION",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "source_run_root": str(resume["source"]),
        "source_state_path": str(resume["state_path"]),
        "source_state_sha256": resume["state_sha256"],
        "source_baseline_path": str(resume["baseline_path"]),
        "source_baseline_sha256": resume["baseline_sha256"],
        "source_calibration_design_path": str(resume["design_path"]),
        "source_calibration_design_sha256": resume["design_sha256"],
        "source_completed_calibration_waves": resume["wave_evidence"],
        "source_failure_evidence_path": str(resume["failure_evidence_path"]),
        "source_failure_evidence_sha256": resume["failure_evidence_sha256"],
        "prior_failure_evidence_path": str(resume["prior_failure_evidence_path"]),
        "prior_failure_evidence_sha256": resume["prior_failure_evidence_sha256"],
        "calibration_design_amended": True,
        "failed_wave": RESUME_FAILED_WAVE,
        "failed_wave_measurement_started": True,
        "partial_failed_wave_results_reused": False,
        "accepted_or_rejected_outer_state_reused": False,
        "algorithm_c_state_or_result_read": False,
        "controller_source_path": str(Path(__file__).resolve()),
        "controller_source_sha256": alg.sha(Path(__file__).resolve()),
        "coordination_contract_path": str(isolation.CONTRACT_PATH),
    })
    try:
        coordination_contract = configure_runtime()
        freeze, workers, point_gate = fresh_authority()
        params_by_scene, ranges_by_scene, active_by_scene = parameter_state()
        source_state = resume["state"]
        if (
            source_state.get("params_by_scene") != params_by_scene
            or source_state.get("ranges_by_scene") != ranges_by_scene
            or source_state.get("active_by_scene") != active_by_scene
        ):
            raise RuntimeError("resume source frozen parameter state differs")
        state["prestart_point_gate"] = point_gate
        state["params_by_scene"] = copy.deepcopy(params_by_scene)
        state["ranges_by_scene"] = copy.deepcopy(ranges_by_scene)
        state["active_by_scene"] = copy.deepcopy(active_by_scene)
        state["stage"] = "RESTORING_VERIFIED_INDEPENDENT_FRESH_BASELINE"
        alg.atomic(state_path, state)
        baseline_path = output / "formal_independent_baseline/batch_result.json"
        _mirror_batch(resume["baseline_path"], baseline_path)
        baseline_rows = copy.deepcopy(resume["baseline_rows"])
        baseline = alg.load(baseline_path)
        if (
            baseline.get("status") != "PASS"
            or baseline.get("failures") not in ([], None)
            or set(baseline_rows) != set(workers)
            or any(
                row.get("hard_gate_passed") is not True
                or row.get("coverage_complete") is not True
                for row in baseline_rows.values()
            )
        ):
            raise RuntimeError("Algorithm B independent fresh baseline failed closed")
        incumbents: dict[str, dict[str, Any]] = {}
        for scene in SCENES:
            rows = [row for row in baseline_rows.values() if row["scene"] == scene]
            if len(rows) != 2:
                raise RuntimeError(f"{scene} independent baseline lane count differs")
            incumbents[scene] = {
                "db_qps": sum(float(row["db_qps"]) for row in rows) / 2.0,
                "p95_ms": sum(float(row["p95_ms"]) for row in rows) / 2.0,
                "p99_ms": sum(float(row["p99_ms"]) for row in rows) / 2.0,
                "resource_means": {
                    name: sum(float(row["resource_means"][name]) for row in rows) / 2.0
                    for name in ("cpu", "memory", "io")
                },
            }
        state["fresh_baseline"] = {
            "status": "PASS", "path": str(baseline_path),
            "sha256": alg.sha(baseline_path),
            "original_path": str(resume["baseline_path"]),
            "original_sha256": resume["baseline_sha256"],
            "bit_identical_technical_continuation": True,
            "defaults": copy.deepcopy(alg.DEFAULTS),
            "incumbents": copy.deepcopy(incumbents),
            "algorithm_c_state_read": False,
        }
        state["stage"] = "INDEPENDENT_BASELINE_COMPLETE"
        alg.atomic(state_path, state)

        objectives: dict[str, FreshBaselineObjective] = {}
        models: dict[str, FreshExperienceModel] = {}
        parameter_ranges_by_scene: dict[str, dict[str, ParameterRange]] = {}
        calibration_designs: dict[str, list[dict[str, Any]]] = {}
        calibration_rankings: dict[str, list[dict[str, Any]]] = {}
        calibration_payloads: dict[str, dict[str, dict[str, Any]]] = {
            scene: {} for scene in SCENES
        }
        for scene in SCENES:
            objectives[scene] = build_objective(scene, incumbents[scene])
            parameter_ranges_by_scene[scene] = transformed_ranges(
                ranges_by_scene[scene], active_by_scene[scene],
            )
            models[scene] = FreshExperienceModel(
                objectives[scene],
                {
                    name: (bound.min_value, bound.max_value)
                    for name, bound in parameter_ranges_by_scene[scene].items()
                },
                max_neighbors=2,
            )
            models[scene].add(
                params_by_scene[scene], incumbents[scene]["resource_means"],
                {
                    "db_qps": incumbents[scene]["db_qps"],
                    "p95_ms": incumbents[scene]["p95_ms"],
                    "p99_ms": incumbents[scene]["p99_ms"],
                    "phase": "independent_fresh_baseline",
                },
            )
            calibration_designs[scene] = mysql_fresh_model_calibration_points(
                params_by_scene[scene], parameter_ranges_by_scene[scene],
                active_by_scene[scene],
                replay_budget=CALIBRATION_REPLAY_BUDGET,
            )
        if resume["design"].get("scene_designs") != calibration_designs:
            raise RuntimeError("recomputed source calibration design differs")
        calibration_amendments = amend_tpch_calibration_design(calibration_designs)
        for scene in SCENES:
            calibration_rankings[scene] = [
                {
                    "rank": index,
                    "optimized_params": copy.deepcopy(row["params"]),
                    "calibration_id": row["calibration_id"],
                    "calibration_phase": row["phase"],
                    "changed_parameters": copy.deepcopy(row["changed_parameters"]),
                }
                for index, row in enumerate(calibration_designs[scene], 1)
            ]
        calibration_design_document = {
            "schema_version": "mysql_four_scene_algorithm_b_calibration_design_v3",
            "status": "PASS",
            "algorithm": "B",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "fresh_baseline_excluded_from_budget": True,
            "replay_budget_per_scene": CALIBRATION_REPLAY_BUDGET,
            "scene_designs": calibration_designs,
            "source_design_path": str(resume["design_path"]),
            "source_design_sha256": resume["design_sha256"],
            "formal_feasibility_amendments": calibration_amendments,
            "amendment_basis": {
                "same_original_point_failed_full_suite_twice": True,
                "first_failure_path": str(resume["prior_failure_evidence_path"]),
                "first_failure_sha256": resume["prior_failure_evidence_sha256"],
                "second_failure_path": str(resume["failure_evidence_path"]),
                "second_failure_sha256": resume["failure_evidence_sha256"],
                "outer_parameter_ranges_changed": False,
                "formal_wall_timeout_changed": False,
            },
            "algorithm_c_state_read": False,
        }
        alg.atomic(output / "calibration_design.json", calibration_design_document)

        calibration_companions: list[dict[str, Any]] = []
        for rank in range(1, CALIBRATION_REPLAY_BUDGET + 1):
            state["stage"] = f"ALGORITHM_B_CALIBRATION_{rank:02d}_OF_15"
            alg.atomic(state_path, state)
            assignments, companions = runtime._build_single_candidate_assignments(
                rankings=calibration_rankings,
                params_by_scene=params_by_scene,
                workers=workers,
                rank=rank,
            )
            batch_path = output / f"fresh_calibration/wave_{rank:02d}/batch_result.json"
            if rank <= RESUME_CALIBRATION_WAVES:
                source_batch = (
                    resume["source"]
                    / f"fresh_calibration/wave_{rank:02d}/batch_result.json"
                )
                _mirror_batch(source_batch, batch_path)
                measured = copy.deepcopy(resume["wave_rows"][rank])
                state["stage"] = (
                    f"ALGORITHM_B_CALIBRATION_{rank:02d}_RESTORED_VERIFIED"
                )
                alg.atomic(state_path, state)
            else:
                measured, state = runtime._measure_batch_with_fair_coordination(
                    freeze=freeze, workers=workers, assignments=assignments,
                    output=output / f"fresh_calibration/wave_{rank:02d}",
                    reset_token=f"algorithm_b_calibration_{rank:02d}",
                    state_path=state_path, state=state,
                    stage_prefix=f"ALGORITHM_B_CALIBRATION_{rank:02d}",
                    run_id=args.run_id, contract=coordination_contract,
                )
            batch = alg.load(batch_path)
            if (
                batch.get("status") != "PASS"
                or batch.get("failures") not in ([], None)
                or any(
                    row.get("hard_gate_passed") is not True
                    or row.get("coverage_complete") is not True
                    for row in measured.values()
                )
            ):
                raise RuntimeError(
                    f"Algorithm B calibration wave {rank} failed closed"
                )
            for worker_id, row in measured.items():
                scene = str(row["scene"])
                if worker_id in companions:
                    calibration_companions.append({
                        "wave": rank, "worker_id": worker_id,
                        "scene": scene, "hard_gate_passed": True,
                        "coverage_complete": True,
                    })
                    continue
                key = alg.optimizer_params_key(row["params"])
                expected_key = alg.optimizer_params_key(
                    calibration_rankings[scene][rank - 1]["optimized_params"]
                )
                if key != expected_key or key in calibration_payloads[scene]:
                    raise RuntimeError(
                        f"{scene} calibration effective parameter identity differs"
                    )
                calibration_payloads[scene][key] = measurement_payload(row)

        alg.atomic(output / "fresh_calibration/companion_audit.json", {
            "schema_version": "mysql_algorithm_b_calibration_companion_audit_v2",
            "status": "PASS", "rows": calibration_companions,
            "full_workload_contract_preserved": True,
            "restored_verified_wave_count": RESUME_CALIBRATION_WAVES,
            "source_run_root": str(resume["source"]),
            "source_wave_evidence": resume["wave_evidence"],
        })
        quality_gates: dict[str, dict[str, Any]] = {}
        error_bounds_by_scene: dict[str, dict[str, dict[str, float]]] = {}
        for scene in SCENES:
            pending = dict(calibration_payloads[scene])

            def premeasured_validator(
                _sql_set: Any, point: dict[str, float], *, _scene: str = scene,
                _pending: dict[str, dict[str, Any]] = pending,
            ) -> dict[str, Any]:
                key = alg.optimizer_params_key(point)
                if key not in _pending:
                    raise RuntimeError(
                        f"{_scene} calibration payload lookup differs"
                    )
                return _pending.pop(key)

            scene_root = output / f"fresh_calibration/{scene}"
            scene_root.mkdir(parents=True, exist_ok=False)
            quality_gates[scene] = run_mysql_fresh_model_quality_gate(
                models[scene], premeasured_validator,
                {"scene": scene}, params_by_scene[scene],
                parameter_ranges_by_scene[scene], active_by_scene[scene],
                scenario_dir=scene_root,
                thresholds=FreshModelQualityThresholds(),
            )
            if pending:
                raise RuntimeError(f"{scene} unused calibration payloads remain")
            error_bounds_by_scene[scene] = preflight_resource_error_bounds(
                quality_gates[scene]
            )
            quality_gates[scene]["optimizer_started"] = True
            alg.atomic(scene_root / "fresh_model_quality_gate.json", quality_gates[scene])
        state["fresh_model_quality_gates"] = {
            scene: {
                "path": str(
                    output / f"fresh_calibration/{scene}/fresh_model_quality_gate.json"
                ),
                "sha256": alg.sha(
                    output / f"fresh_calibration/{scene}/fresh_model_quality_gate.json"
                ),
                "passed": quality_gates[scene].get("passed") is True,
                "soft_start": quality_gates[scene].get("preflight_soft_start") is True,
                "resource_error_bounds": error_bounds_by_scene[scene],
            }
            for scene in SCENES
        }
        state["stage"] = "INDEPENDENT_TOP15_CALIBRATION_COMPLETE"
        alg.atomic(state_path, state)

        replayed = {scene: set() for scene in SCENES}
        outer_attempt = 0
        while not all(state["converged"].values()):
            exhausted = [
                scene for scene in SCENES
                if not state["converged"][scene]
                and state["rejected_outer_attempts"][scene]
                >= MAXIMUM_REJECTED_OUTER_ATTEMPTS_PER_SCENE
            ]
            if exhausted or outer_attempt >= MAXIMUM_OUTER_ATTEMPTS:
                state["termination_audit"] = {
                    "rejected_retry_budget_exhausted": exhausted,
                    "maximum_outer_attempts_exhausted": (
                        outer_attempt >= MAXIMUM_OUTER_ATTEMPTS
                    ),
                }
                break
            outer_attempt += 1
            active_scenes = [scene for scene in SCENES if not state["converged"][scene]]
            state["stage"] = f"ALGORITHM_B_OUTER_{outer_attempt:03d}_ESTIMATING"
            alg.atomic(state_path, state)
            ranking_rows: dict[str, dict[str, Any]] = {}
            with ThreadPoolExecutor(
                max_workers=min(4, len(active_scenes)),
                thread_name_prefix="algorithm-b-independent-ranking",
            ) as pool:
                futures = {}
                for scene in active_scenes:
                    representative = sorted(
                        worker_id for worker_id, worker in workers.items()
                        if worker["scene"] == scene
                    )[0]
                    futures[pool.submit(
                        rank_scene,
                        scene=scene,
                        params=params_by_scene[scene],
                        ranges=ranges_by_scene[scene],
                        active=active_by_scene[scene],
                        model=models[scene],
                        objective=objectives[scene],
                        error_bounds=error_bounds_by_scene[scene],
                        incumbent_resources=incumbents[scene]["resource_means"],
                        worker=workers[representative],
                        replayed=replayed[scene],
                        round_index=outer_attempt,
                        calibration_seed_points=[
                            dict(row["params"])
                            for row in calibration_designs[scene]
                        ],
                    )] = scene
                for future in as_completed(futures):
                    ranking_rows[futures[future]] = future.result()
            rankings = {
                scene: copy.deepcopy(ranking_rows[scene]["selected_top5"])
                for scene in active_scenes
            }
            alg.atomic(output / f"outer_{outer_attempt:03d}/predicted_rankings.json", {
                "schema_version": "mysql_four_scene_algorithm_b_independent_rankings_v1",
                "status": "PASS",
                "algorithm": "B",
                "outer_attempt": outer_attempt,
                "formal_scope": "mysql_only",
                "cross_engine_comparison_prohibited": True,
                "active_scenes": active_scenes,
                "selected_top_k": rankings,
                "scene_audit": ranking_rows,
                "fixed_real_top_k": FIXED_TOP_K,
                "candidate_hard_pruning": False,
                "qps_used_for_resource_prediction_gradient_or_score": False,
                "algorithm_c_state_read": False,
            })

            evaluations = {scene: [] for scene in active_scenes}
            companion_audit: list[dict[str, Any]] = []
            for rank in range(1, FIXED_TOP_K + 1):
                assignments, companions = runtime._build_single_candidate_assignments(
                    rankings=rankings, params_by_scene=params_by_scene,
                    workers=workers, rank=rank,
                )
                measured, state = runtime._measure_batch_with_fair_coordination(
                    freeze=freeze, workers=workers, assignments=assignments,
                    output=output / f"outer_{outer_attempt:03d}/top5_batch_{rank}",
                    reset_token=f"algorithm_b_outer_{outer_attempt:03d}_rank_{rank}",
                    state_path=state_path, state=state,
                    stage_prefix=f"ALGORITHM_B_OUTER_{outer_attempt:03d}_RANK_{rank}",
                    run_id=args.run_id, contract=coordination_contract,
                )
                batch_path = output / f"outer_{outer_attempt:03d}/top5_batch_{rank}/batch_result.json"
                batch = alg.load(batch_path)
                if (
                    batch.get("status") != "PASS"
                    or batch.get("failures") not in ([], None)
                    or any(
                        row.get("hard_gate_passed") is not True
                        or row.get("coverage_complete") is not True
                        for row in measured.values()
                    )
                ):
                    raise RuntimeError(
                        f"Algorithm B outer {outer_attempt} rank {rank} failed closed"
                    )
                for worker_id, row in measured.items():
                    scene = str(row["scene"])
                    if worker_id in companions:
                        companion_audit.append({
                            "rank": rank, "worker_id": worker_id,
                            "scene": scene, "hard_gate_passed": True,
                            "coverage_complete": True,
                        })
                    else:
                        evaluations[scene].append(row)
            alg.atomic(output / f"outer_{outer_attempt:03d}/companion_audit.json", {
                "schema_version": "mysql_algorithm_b_independent_companion_audit_v1",
                "status": "PASS", "rows": companion_audit,
                "full_workload_contract_preserved": True,
            })

            for scene in active_scenes:
                if len(evaluations[scene]) != FIXED_TOP_K:
                    raise RuntimeError(
                        f"{scene} Algorithm B real Top-5 cardinality differs"
                    )
                for row in sorted(evaluations[scene], key=lambda item: item["rank"]):
                    payload = measurement_payload(row)
                    measurement = measurement_from_payload(
                        payload["resource"], payload,
                    )
                    score = float(objectives[scene].score(measurement.as_resource()))
                    if not math.isfinite(score):
                        raise RuntimeError(f"{scene} non-finite real Resource Score")
                    row["real_resource_score"] = score
                    row["qps_used_for_real_resource_score"] = False
                    models[scene].add(
                        row["params"], payload["resource"], payload,
                    )
                ordered = sorted(
                    evaluations[scene],
                    key=lambda row: (
                        -float(row["real_resource_score"]),
                        int(row["rank"]),
                        alg.optimizer_params_key(row["params"]),
                    ),
                )
                chosen = ordered[0]
                chosen_score = float(chosen["real_resource_score"])
                score_tie_count = sum(
                    math.isclose(
                        float(row["real_resource_score"]), chosen_score,
                        rel_tol=0.0, abs_tol=1e-12,
                    )
                    for row in ordered
                )
                for row in evaluations[scene]:
                    row["unique_small_loop_best"] = row is chosen
                    latency_gate = (
                        row["p95_ms"] <= incumbents[scene]["p95_ms"] * 1.10
                        and row["p99_ms"] <= incumbents[scene]["p99_ms"] * 1.10
                    )
                    non_target_gate, bottlenecks = (
                        runtime.candidate_non_target_bottleneck_gate(
                            row, target_resource=alg.TARGET[scene],
                        )
                    )
                    decision = decide_validation_acceptance(
                        incumbents[scene], row,
                        target_resource=alg.TARGET[scene],
                        hard_gate_passed=(
                            row["hard_gate_passed"]
                            and latency_gate and non_target_gate
                        ),
                        utilization_key="resource_means",
                        acceptance_tier=state["acceptance_tier"][scene],
                    )
                    row["latency_gate_passed"] = latency_gate
                    row["non_target_bottleneck_gate_passed"] = non_target_gate
                    row["non_target_bottlenecks"] = bottlenecks
                    row["outer_decision"] = decision.to_dict()
                chosen_decision = dict(chosen["outer_decision"])
                accepted = bool(chosen_decision.get("accepted"))
                accepted_credit = 0
                confirmation_ordinal = None
                if accepted:
                    params_by_scene[scene] = copy.deepcopy(dict(chosen["params"]))
                    incumbents[scene] = {
                        key: copy.deepcopy(chosen[key])
                        for key in ("db_qps", "p95_ms", "p99_ms", "resource_means")
                    }
                    ranges_by_scene[scene] = alg.shrink_ranges(
                        ranges_by_scene[scene], params_by_scene[scene],
                        active_by_scene[scene],
                    )
                    if state["accepted_rounds"][scene] < TARGET_ACCEPTED_SHRINKS:
                        state["accepted_rounds"][scene] += 1
                        accepted_credit = 1
                    else:
                        state["post_target_stability_confirmation_counts"][scene] += 1
                        confirmation_ordinal = state[
                            "post_target_stability_confirmation_counts"
                        ][scene]
                        if confirmation_ordinal > MAXIMUM_POST_TARGET_CONFIRMATIONS:
                            raise RuntimeError(
                                f"{scene} exceeded bounded post-target confirmations"
                            )
                    replayed[scene].clear()
                else:
                    state["rejected_outer_attempts"][scene] += 1
                    replayed[scene].update(
                        alg.optimizer_params_key(row["optimized_params"])
                        for row in rankings[scene]
                    )
                convergence = alg.advance_interval_convergence(
                    ranges=ranges_by_scene[scene],
                    active=active_by_scene[scene],
                    candidate_params=(chosen["params"] if accepted else None),
                    accepted=accepted,
                    previous_stable_rounds=state["interval_stable_rounds"][scene],
                    previous_interval=state["stable_intervals"][scene],
                )
                state["interval_stable_rounds"][scene] = convergence["stable_rounds"]
                state["stable_intervals"][scene] = convergence["stable_interval"]
                state["converged"][scene] = bool(
                    state["accepted_rounds"][scene] == TARGET_ACCEPTED_SHRINKS
                    and convergence["converged"]
                )
                tier = advance_acceptance_tier(
                    acceptance_tier=state["acceptance_tier"][scene],
                    consecutive_failed_rounds=state["consecutive_failed_rounds"][scene],
                    accepted=accepted, downgrade_after_rounds=2,
                )
                state["acceptance_tier"][scene] = tier.acceptance_tier
                state["consecutive_failed_rounds"][scene] = tier.consecutive_failed_rounds
                alg.atomic(output / f"outer_{outer_attempt:03d}/{scene}.decision.json", {
                    "schema_version": "mysql_four_scene_algorithm_b_independent_decision_v1",
                    "algorithm": "B", "scene": scene,
                    "outer_attempt": outer_attempt,
                    "accepted": accepted,
                    "accepted_state_credit_increment": accepted_credit,
                    "confirmation_ordinal": confirmation_ordinal,
                    "accepted_rounds": state["accepted_rounds"][scene],
                    "rejected_outer_attempts": state["rejected_outer_attempts"][scene],
                    "unique_real_resource_score_best_rank": chosen["rank"],
                    "unique_real_resource_score_best": chosen_score,
                    "score_tie_count_before_rank_signature_tiebreak": score_tie_count,
                    "small_loop_best_selected_before_outer_gate": True,
                    "evaluations": evaluations[scene],
                    "interval_convergence": convergence,
                    "converged": state["converged"][scene],
                    "tier_progress": tier.to_dict(),
                    "algorithm_c_state_read": False,
                })
            state["outer_attempt"] = outer_attempt
            state["params_by_scene"] = copy.deepcopy(params_by_scene)
            state["ranges_by_scene"] = copy.deepcopy(ranges_by_scene)
            state["incumbents"] = copy.deepcopy(incumbents)
            state["replayed_param_keys_by_scene"] = {
                scene: sorted(replayed[scene]) for scene in SCENES
            }
            state["stage"] = "ALGORITHM_B_OUTER_RUNNING"
            alg.atomic(state_path, state)

        state["status"] = (
            "COMPLETE" if all(state["converged"].values()) else "NON_CONVERGED"
        )
        state["stage"] = "FINISHED"
        state["completed_at"] = alg.now()
        alg.atomic(state_path, state)
        return 0 if state["status"] == "COMPLETE" else 2
    except BaseException as error:
        state["status"] = "FAILED_CLOSED"
        state["stage"] = "FAILED"
        state["failed_at"] = alg.now()
        state["error"] = f"{type(error).__name__}: {error}"
        alg.atomic(state_path, state)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
