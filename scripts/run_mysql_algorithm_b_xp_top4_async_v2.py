#!/usr/bin/env python3
"""Run Algorithm B with PG-compatible outer rejection memory.

An invalid physical candidate wave may contain valid measurements for scenes
that were not named by the fail-closed attribution.  Preserve those rows and
reject only the affected scene/rank pair.  A scene with zero valid candidates
gets a bounded rejected outer attempt rather than aborting all four scenes.

When a continuation enables ``ONLINE_RESOURCE_MODEL_UPDATE_EFFECTIVE_OUTER``,
every technically valid formal Top-5 replay is added to the current-run model
after the outer decision.  The model's resource targets remain independent of
its audit-only QPS target, and only predicted resources enter candidate score.

Unlike the predecessor, the rejected-candidate set persists for the whole
campaign.  A rejected round records only its real small-loop best point;
acceptance and incumbent-centred forced shrink do not clear that memory.
"""

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
from mysql_algorithm_b_policy_v2 import (
    mysql_fresh_model_calibration_points,
    preflight_resource_error_bounds,
    run_mysql_fresh_model_quality_gate,
)
from scripts import mysql_algorithm_b_candidate_diversity_v1 as candidate_diversity
from scripts import mysql_algorithm_b_fresh_parallel_221_scheduler_v25 as scheduler
from scripts import mysql_cross_project_wave_coordination_v4 as dynamic_gate
from scripts import reset_mysql_xp_data_planes_v7 as reset_v7
from scripts import reset_mysql_xp_data_planes_four_worker_v1 as reset4
from scripts import run_mysql_xp_four_worker_authority_v1 as xp4_authority
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v32 as c32
from scripts import mysql_algorithm_b_no_lease_measurement_v1 as no_lease_runtime
from scripts import mysql_outer_rejection_cycle_v1 as rejection_cycle
from scripts import run_mysql_four_scene_algorithm_b_scene_independent_measurement_v4 as single_measurement


runtime = c32.runtime
alg = runtime.alg
SCENES: tuple[str, ...] = ()
FREEZE = (
    ROOT / "config/formal/frozen_mysql_algorithm_c_xp4_handoff_20260826_v2"
    / "xp_four_worker_resource_successor.json"
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
LEGACY_COUPLED_RECONSTRUCTION_MAX_OUTER = 4
CALIBRATION_REPLAY_BUDGET = 15
GRID_POINTS = 15
INNER_PASSES = 20
CONVERGENCE_PATIENCE = 2
COMBINATION_MIN_SIZE = 2
COMBINATION_MAX_SIZE = 4
COMBINATION_VALUES_PER_PARAMETER = 3
HISTORY = ROOT / "history/mysql_four_scene_20260809"
OUTER_BOUNDARY_STATE_TRANSFORM = None
POST_CALIBRATION_STATE_TRANSFORM = None
INITIAL_OUTER_ATTEMPT = 0
ONLINE_RESOURCE_MODEL_UPDATE_EFFECTIVE_OUTER: int | None = None
REUSE_VERIFIED_BASELINE = False
REUSE_HISTORICAL_OUTER_EVIDENCE = False
ACCEPTANCE_AWARE_SELECTION_EFFECTIVE_OUTER = 6
SUBSET_ONLY_SHRINK_EFFECTIVE_OUTER = 7
CANDIDATE_DIVERSITY_EFFECTIVE_OUTER = 7


def update_resource_model_from_formal_evaluations(
    *, scene: str, model: FreshExperienceModel,
    evaluations: list[Mapping[str, Any]], outer_attempt: int,
) -> dict[str, Any]:
    """Add valid formal replay resources without coupling QPS into scoring."""
    added: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for raw in evaluations:
        row = dict(raw)
        rank = int(row.get("rank") or 0)
        if (
            row.get("hard_gate_passed") is not True
            or row.get("coverage_complete") is not True
            or not isinstance(row.get("params"), Mapping)
            or not isinstance(row.get("resource_means"), Mapping)
        ):
            skipped.append({
                "rank": rank,
                "reason": "invalid_or_incomplete_formal_measurement",
            })
            continue
        params = {
            str(name): float(value)
            for name, value in dict(row["params"]).items()
        }
        resources = {
            name: float(dict(row["resource_means"])[name])
            for name in ("cpu", "memory", "io")
        }
        # FreshExperienceModel fits every output independently.  QPS is kept
        # solely for complete replay audit; it is not an input feature, a
        # resource target, a gradient, or an estimated/real resource score.
        model.add(params, resources, {
            "db_qps": float(row["db_qps"]),
            "p95_ms": float(row["p95_ms"]),
            "p99_ms": float(row["p99_ms"]),
            "phase": "formal_top5_online_resource_correction",
            "scene": scene,
            "outer_attempt": outer_attempt,
            "rank": rank,
        })
        added.append({
            "rank": rank,
            "parameter_key": alg.optimizer_params_key(params),
            "resources": resources,
            "db_qps_audit_only": float(row["db_qps"]),
        })
    return {
        "scene": scene,
        "outer_attempt": outer_attempt,
        "added_count": len(added),
        "skipped_count": len(skipped),
        "added": added,
        "skipped": skipped,
        "qps_used_as_model_input_feature": False,
        "qps_used_for_resource_target": False,
        "qps_used_for_resource_prediction_gradient_or_score": False,
        "parameter_distance_and_resource_targets_only": True,
        "model_row_count_after": len(model.snapshot()),
    }


INTERMEDIATE_FAILURE = (
    HISTORY / "v7_formal_independent_outer_rank3_resume_v63_20260818_attempt001"
)
RESUME_CALIBRATION_WAVES = 0
RESUME_FAILURE = "RuntimeError: Algorithm B outer 1 rank 2 failed closed"
RESUME_OUTER_ATTEMPT = 0
RESUME_VALID_RANKS: tuple[int, ...] = ()
RESUME_INVALID_RANKS: tuple[int, ...] = ()
TPCH_ROW_EVALUATE_REPLACEMENTS = {
    11: 0.117142857142857,
    15: 0.134285714285714,
}

TOP4_ROOT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_c_20260825_v1"
TOP4_TABLES = {
    "hybench_xp_cpu": "T.hybench_xp_cpu.top4.operator_physics_v1.json",
    "hybench_xp_io": "T.hybench_xp_io.top4.operator_physics_v1.json",
}
CONTRACT = (
    ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_20260826_v10"
    / "algorithm_b_top4_contract.json"
)
COORDINATION_CONTRACT = (
    ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_20260826_v10"
    / "coordination_contract.json"
)
AUTHORITY_PROFILE = (
    ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260819_v65"
    / "coordination_contract.json"
)


def amend_tpch_calibration_design(
    designs: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Top-4 XP calibration uses its frozen design without TPC-H amendments."""
    if set(designs) != set(SCENES):
        raise RuntimeError("Top-4 XP calibration scene set differs")
    return []


def configure_runtime() -> dict[str, Any]:
    scene = SCENES[0]
    authority_root = (
        HISTORY / "v164_to_xp4_live_authority_20260826" / scene / "authority"
    )
    reset4.install()
    alg.FREEZE = FREEZE
    alg.reset = reset4
    alg.r0mod = xp4_authority
    alg.WORKER_IDS = (
        "xp_cpu_w1", "xp_cpu_w2", "xp_cpu_w3", "xp_cpu_w4",
        "xp_io_w1", "xp_io_w2", "xp_io_w3", "xp_io_w4",
    )
    alg.XP_IDS = alg.WORKER_IDS
    alg.XP_RESET_MAX_WORKERS = 4
    alg.r0mod.FREEZE = FREEZE
    alg.r0mod.FREEZE_SCHEMA = (
        "mysql_algorithm_c_xp_four_worker_after_job_resource_successor_v1"
    )
    alg.r0mod.PREFLIGHT = authority_root / "transition_preflight.json"
    alg.r0mod.RUNTIME_PREFLIGHT = alg.r0mod.PREFLIGHT
    alg.r0mod.PREPARED = authority_root / "prepared_binding.json"
    alg.r0mod.BENCHMARK_ACCOUNTS = authority_root / "benchmark_accounts_binding.json"
    alg.r0mod.AUTHORITY_SCENE_SCOPE = scene
    runtime.WAVE_COORDINATION_CONTRACT = COORDINATION_CONTRACT
    runtime.alg.reset_xp = c32.reset_xp
    contract = alg.load(COORDINATION_CONTRACT)
    if (
        contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("schema_version")
        != "mysql_algorithm_b_top4_no_lease_coordination_v1"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or float(contract.get("per_wave_quiet_seconds") or 0) != 15.0
        or float(contract.get("maximum_quiet_wait_seconds") or 0) != 60.0
        or contract.get("lease_mode") != "none"
        or contract.get("four_scene_barrier") is not False
        or contract.get("sibling_scene_wait") is not False
    ):
        raise RuntimeError("Top-4 B no-lease wave authority differs")
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


def formally_infeasible_candidate_batch(batch: Mapping[str, Any]) -> bool:
    """Recognize a completed formal attempt invalid only at the 900s workload cap."""
    failures = batch.get("failures")
    if (
        batch.get("status") != "FAILED_CLOSED"
        or not isinstance(failures, list)
        or len(failures) < 1
        or float(batch.get("elapsed_seconds") or 0.0) < 900.0
    ):
        return False
    timeout_rows = [
        row for row in failures
        if isinstance(row, Mapping)
        and "timed out after 900 seconds" in str(row.get("error") or "")
    ]
    allowed = all(
        isinstance(row, Mapping)
        and (
            "timed out after 900 seconds" in str(row.get("error") or "")
            or row.get("error") == "skipped_after_invalid_workload"
        )
        for row in failures
    )
    measurements = batch.get("measurements")
    return bool(timeout_rows) and allowed and isinstance(measurements, Mapping)


def infeasible_batch_scenes(batch: Mapping[str, Any]) -> list[str]:
    """Return only scenes directly named by a formal workload timeout."""
    text = "\n".join(
        str(row.get("error") or "")
        for row in (batch.get("failures") or [])
        if isinstance(row, Mapping)
    )
    scenes = []
    if "benchbase.jar', '-b', 'tpch'" in text or "benchbase.jar -b tpch" in text:
        scenes.append("tpch_sf4_memory")
    if "com.hybench.HyBench" in text and "xp_cpu" in text:
        scenes.append("hybench_xp_cpu")
    if "com.hybench.HyBench" in text and "xp_io" in text:
        scenes.append("hybench_xp_io")
    if "imdb_job" in text or "/job/" in text:
        scenes.append("job_memory")
    return scenes


def scene_candidate_accounting(
    scene: str,
    invalid_candidate_attempts: list[dict[str, Any]],
    evaluations: list[dict[str, Any]],
    *,
    top_k: int = FIXED_TOP_K,
) -> tuple[list[dict[str, Any]], int]:
    """Verify valid-row cardinality using only failures attributed to a scene."""
    scene_invalid = [
        row for row in invalid_candidate_attempts
        if scene in (row.get("affected_scenes") or [])
    ]
    expected_valid = top_k - len(scene_invalid)
    if expected_valid < 0 or len(evaluations) != expected_valid:
        raise RuntimeError(
            f"{scene} Algorithm B valid real evaluation cardinality differs"
        )
    return scene_invalid, expected_valid


def candidate_accounting_for_outer(
    outer_attempt: int,
    scene: str,
    invalid_candidate_attempts: list[dict[str, Any]],
    evaluations: list[dict[str, Any]],
    *,
    top_k: int = FIXED_TOP_K,
) -> tuple[list[dict[str, Any]], int]:
    """Reconstruct old decisions exactly, then use scene-local accounting.

    Outer 1--4 were measured by the predecessor's coupled JOB/TPC-H batch
    implementation.  A timeout discarded the complete rank for every scene,
    so those committed decisions must retain global cardinality.  Fresh
    measurements from outer 5 onward have independent scene result windows and
    therefore use scene-local failure cardinality.
    """
    if outer_attempt <= LEGACY_COUPLED_RECONSTRUCTION_MAX_OUTER:
        expected_valid = top_k - len(invalid_candidate_attempts)
        if expected_valid < 1 or len(evaluations) != expected_valid:
            raise RuntimeError(
                f"{scene} Algorithm B legacy valid real evaluation cardinality differs"
            )
        return list(invalid_candidate_attempts), expected_valid
    return scene_candidate_accounting(
        scene, invalid_candidate_attempts, evaluations, top_k=top_k,
    )


def validate_resume_source(source: Path) -> dict[str, Any]:
    source = source.resolve()
    manifest_path = source / "bootstrap_manifest.json"
    manifest_sha = _verified_sidecar(manifest_path)
    manifest = alg.load(manifest_path)
    params, ranges, active = parameter_state()
    scene = SCENES[0]
    expected_active = list(alg.load(TOP4_ROOT / TOP4_TABLES[scene])[
        "parameter_space"
    ]["active_params"])
    if (
        source.parent != HISTORY.resolve()
        or manifest.get("schema_version")
        != "mysql_algorithm_b_xp_top4_fresh_bootstrap_v1"
        or manifest.get("status") != "FROZEN"
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("scene") != scene
        or manifest.get("active_parameters") != expected_active
        or manifest.get("algorithm_c_state_or_result_credit") is not False
        or manifest.get("fresh_baseline_required") is not True
        or manifest.get("fresh_calibration_replays_required") != 15
    ):
        raise RuntimeError("Top-4 B fresh bootstrap differs")
    design_rows = mysql_fresh_model_calibration_points(
        params[scene], transformed_ranges(ranges[scene], active[scene]),
        active[scene], replay_budget=CALIBRATION_REPLAY_BUDGET,
    )
    design_value = {
        "schema_version": "mysql_algorithm_b_xp_top4_calibration_design_v1",
        "status": "PASS", "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "replay_budget_per_scene": 15,
        "scene_designs": {scene: design_rows},
        "formal_feasibility_amendments": [],
        "algorithm_c_state_read": False,
    }
    placeholder = manifest_path
    return {
        "source": source,
        "state": {"params_by_scene": params, "ranges_by_scene": ranges,
                  "active_by_scene": active},
        "state_path": manifest_path,
        "state_sha256": manifest_sha,
        "baseline_path": placeholder,
        "baseline_rows": {},
        "baseline_sha256": manifest_sha,
        "design_path": placeholder,
        "design": design_value,
        "design_sha256": manifest_sha,
        "wave_rows": {}, "wave_evidence": [],
        "companion_path": placeholder, "companion_sha256": manifest_sha,
        "failure_evidence_path": placeholder,
        "failure_evidence_sha256": manifest_sha,
        "quality_gate_evidence": [],
        "rankings_path": placeholder, "rankings_sha256": manifest_sha,
        "rankings": {}, "valid_batch_path": placeholder,
        "valid_batch_sha256": manifest_sha, "valid_batch_rows": {},
        "invalid_batch_path": placeholder,
        "invalid_batch_sha256": manifest_sha, "invalid_batch": {},
    }


def validate_intermediate_failure() -> dict[str, Any]:
    """Fresh Top-4 B has no inherited technical or outer-search state."""
    path = CONTRACT
    return {"state_path": path, "state_sha256": alg.sha(path), "state": {}}


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
        "async_scene": SCENES[0],
        "four_scene_wave_barrier": False,
        "other_scene_completion_required": False,
        "scene_may_advance_immediately_after_own_round": True,
        "formal_candidate_batches": [[1, 2, 3, 4], [5]],
        "candidate_parallelism": 4,
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
        "incumbent_centered_forced_shrinks": {scene: 0 for scene in SCENES},
        "target_accepted_shrinks": TARGET_ACCEPTED_SHRINKS,
        "maximum_outer_attempts": MAXIMUM_OUTER_ATTEMPTS,
        "maximum_rejected_outer_attempts_per_scene": (
            MAXIMUM_REJECTED_OUTER_ATTEMPTS_PER_SCENE
        ),
        "maximum_post_target_confirmations": MAXIMUM_POST_TARGET_CONFIRMATIONS,
        "fixed_real_top_k": FIXED_TOP_K,
        "one_time_calibration_replays_per_scene": CALIBRATION_REPLAY_BUDGET,
        "xp_top4_parameter_release": {
            "contract_path": str(CONTRACT),
            "active_parameters": list(
                alg.load(TOP4_ROOT / TOP4_TABLES[SCENES[0]])[
                    "parameter_space"
                ]["active_params"]
            ),
            "candidate_subset_count": 15,
            "fresh_search_required": True,
            "predecessor_seven_parameter_evidence_reused": False,
        },
        "online_resource_model_update_release": {
            "enabled": ONLINE_RESOURCE_MODEL_UPDATE_EFFECTIVE_OUTER is not None,
            "effective_from_outer": ONLINE_RESOURCE_MODEL_UPDATE_EFFECTIVE_OUTER,
            "valid_formal_top5_resources_added_after_each_outer": True,
            "accepted_and_rejected_valid_measurements_used": True,
            "qps_used_as_model_input_feature": False,
            "qps_used_for_resource_target": False,
            "qps_used_for_resource_prediction_gradient_or_score": False,
            "inner_candidate_score_modified": False,
        },
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
            "accepted_shrinks_required": None,
            "maximum_interval_ratio": 0.01,
            "required_consecutive_stable_accepted_rounds": 0,
            "rejected_outer_round_resets_stability_streak": False,
            "all_active_parameters_must_satisfy_interval": True,
            "two_consecutive_rejections_shrink_around_incumbent": True,
            "forced_shrink_grants_accepted_credit": False,
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
    if len(SCENES) != 1:
        raise RuntimeError("Top-4 B authority requires exactly one XP scene")
    alg.r0mod.COORDINATED_LEASE_HOLDER_PID = None
    alg.r0mod.COORDINATION_CONTRACT = AUTHORITY_PROFILE
    alg.r0mod.AUTHORITY_SCENE_SCOPE = SCENES[0]
    try:
        return alg.r0mod._validate_authority()
    finally:
        alg.r0mod.COORDINATED_LEASE_HOLDER_PID = None
        alg.r0mod.COORDINATION_CONTRACT = None
        alg.r0mod.AUTHORITY_SCENE_SCOPE = None


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


def worker_with_plan_log(
    worker: Mapping[str, Any], plan_client_log: Path,
) -> dict[str, Any]:
    """Return an output-scoped worker authority for read-only plan capture."""
    result = copy.deepcopy(dict(worker))
    plan_client_log.parent.mkdir(parents=True, exist_ok=True)
    result["formal_client_log"] = str(plan_client_log)
    return result


def ranking_identity(rows_by_scene: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Compare candidate identity, not newly captured runtime evidence."""
    result: dict[str, list[dict[str, Any]]] = {}
    for scene, rows in rows_by_scene.items():
        result[str(scene)] = []
        for raw in rows:
            row = dict(raw)
            result[str(scene)].append({
                "rank": int(row["rank"]),
                "subset_id": str(row["subset_id"]),
                "active_subset": list(row["active_subset"]),
                "optimized_params": copy.deepcopy(dict(row["optimized_params"])),
            })
    return result


def rank_scene(
    *, scene: str, params: Mapping[str, float], ranges: Mapping[str, Any],
    active: list[str], model: FreshExperienceModel,
    objective: FreshBaselineObjective, error_bounds: Mapping[str, Any],
    incumbent_resources: Mapping[str, float],
    worker: Mapping[str, Any], replayed: set[str], round_index: int,
    calibration_seed_points: list[Mapping[str, float]],
    plan_client_log: Path,
    soft_negative_counts: Mapping[str, int],
) -> dict[str, Any]:
    """Run B's estimated coordinate/interaction loop without real replay."""
    started = time.monotonic()
    worker = worker_with_plan_log(worker, plan_client_log)
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
    usable = [
        point for _, point in sorted(
            enumerate(usable),
            key=lambda pair: (
                int(soft_negative_counts.get(
                    alg.optimizer_params_key(pair[1].params), 0,
                )),
                pair[0],
            ),
        )
    ]
    if len(usable) < FIXED_TOP_K:
        raise RuntimeError(f"{scene} independent B grid lacks fixed Top-5")
    if round_index >= CANDIDATE_DIVERSITY_EFFECTIVE_OUTER:
        selected_points = candidate_diversity.select_diverse_points(
            usable,
            ranges=ranges,
            active=active,
            top_k=FIXED_TOP_K,
            negative_count=lambda point: int(soft_negative_counts.get(
                alg.optimizer_params_key(point.params), 0,
            )),
        )
    else:
        selected_points = usable[:FIXED_TOP_K]
    selected: list[dict[str, Any]] = []
    for rank, point in enumerate(selected_points, 1):
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
            "soft_negative_evidence_count": int(
                soft_negative_counts.get(alg.optimizer_params_key(point.params), 0)
            ),
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
        "plan_client_log": str(plan_client_log),
        "elapsed_seconds": time.monotonic() - started,
        "grid_audit": copy.deepcopy(grid_audit),
        "candidate_count_before_top5": len(usable),
        "selected_top5": selected,
        "resource_error_bounds": copy.deepcopy(dict(error_bounds)),
        "qps_used_for_resource_prediction_gradient_or_score": False,
        "candidate_hard_pruning": False,
        "complete_plan_evidence_captured_before_real_replay": True,
    }


def choose_outer_candidate(
    ordered: list[dict[str, Any]], *, outer_attempt: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    """Choose only among current-tier-eligible Top-5 rows from Outer 6."""
    if not ordered:
        raise ValueError("at least one real Top-5 evaluation is required")
    if any("outer_decision" not in row for row in ordered):
        raise ValueError("all real Top-5 evaluations require an outer decision")
    acceptance_eligible = [
        row for row in ordered
        if row["outer_decision"].get("accepted") is True
    ]
    if (
        outer_attempt >= ACCEPTANCE_AWARE_SELECTION_EFFECTIVE_OUTER
        and acceptance_eligible
    ):
        return (
            acceptance_eligible[0], acceptance_eligible,
            "current_tier_eligible_then_resource_score_v1",
        )
    return (
        ordered[0], acceptance_eligible,
        (
            "historical_resource_score_before_outer_gate_v1"
            if outer_attempt < 6
            else "resource_score_best_no_acceptance_eligible_v1"
        ),
    )


def install_scene(scene: str) -> None:
    """Bind the fresh B core to one independent two-worker Top-4 XP scene."""
    global SCENES
    SCENES = (scene,)
    alg.TABLE_ROOT = TOP4_ROOT
    alg.TABLES = copy.deepcopy(TOP4_TABLES)
    runtime.SCENES = SCENES
    if hasattr(runtime, "predecessor"):
        runtime.predecessor.SCENES = SCENES
    no_lease_runtime.alg = alg
    scheduler.SCENES = SCENES
    scheduler._pending.clear()
    scheduler._rank_cache.clear()
    scheduler._ORIGINAL_ASSIGNMENTS = runtime._build_single_candidate_assignments
    scheduler._ORIGINAL_MEASURE = no_lease_runtime.measure_batch
    alg.measure_batch = single_measurement.measure_batch
    runtime._build_single_candidate_assignments = scheduler._build_assignments_221
    runtime._measure_batch_with_fair_coordination = scheduler._measure_221


def validate_release(scene: str) -> dict[str, Any]:
    contract = alg.load(CONTRACT)
    expected = list(alg.load(TOP4_ROOT / TOP4_TABLES[scene])[
        "parameter_space"
    ]["active_params"])
    if (
        contract.get("schema_version")
        != "mysql_algorithm_b_xp_top4_async_contract_v1"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or (contract.get("active_parameters_by_scene") or {}).get(scene)
        != expected
        or contract.get("fresh_baseline_required") is not True
        or contract.get("fresh_calibration_replays_per_scene") != 15
        or contract.get("candidate_batches") != [[1, 2, 3, 4], [5]]
        or contract.get("physical_replay_parallelism") != 4
        or contract.get("lease_mode") != "none"
        or contract.get("four_scene_barrier") is not False
        or contract.get("sibling_scene_wait") is not False
    ):
        raise RuntimeError("Top-4 B release contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(binding.get("path") or "")
        if not path.is_file() or path.is_symlink() or alg.sha(path) != binding.get("sha256"):
            raise RuntimeError(f"Top-4 B release binding differs: {path}")
    return contract


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--resume-from", type=Path, required=True)
    parser.add_argument("--scene", choices=sorted(TOP4_TABLES), required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.run_id) is None:
        parser.error("unsafe run id")
    install_scene(args.scene)
    validate_release(args.scene)
    resume = validate_resume_source(args.resume_from)
    intermediate = validate_intermediate_failure()
    output = HISTORY / args.run_id
    if output.resolve() == resume["source"]:
        raise RuntimeError("resume output must differ from immutable source")
    if output.exists():
        raise RuntimeError(f"fresh Algorithm B output exists: {output}")
    output.mkdir(parents=True, mode=0o700)
    lock = Path(f"/tmp/mysql_algorithm_b_top4_{args.scene}_v1.lock").open("w")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        raise RuntimeError("another independent Algorithm B controller owns the lock") from error

    state_path = output / "run_state.json"
    state = state_document(args.run_id)
    state["method"] = (
        "independent_outer_binary_inner_estimated_subset_search_resumed"
        if REUSE_HISTORICAL_OUTER_EVIDENCE
        else "independent_outer_binary_inner_estimated_subset_search_fresh"
    )
    state["technical_continuation"] = {
        "kind": "fresh_top4_after_algorithm_c_sequence_gate_v1",
        "source_run_root": str(resume["source"]),
        "source_state_path": str(resume["state_path"]),
        "source_state_sha256": resume["state_sha256"],
        "completed_calibration_waves_reused": RESUME_CALIBRATION_WAVES,
        "failure_stage": (
            "outer_001_top5_identity_audit"
            if REUSE_HISTORICAL_OUTER_EVIDENCE else None
        ),
        "original_failure_stage": (
            "outer_001_rank_2_formal_timeout"
            if REUSE_HISTORICAL_OUTER_EVIDENCE else None
        ),
        "partial_failed_wave_results_reused": False,
        "all_calibration_wave_results_reused": False,
        "failure_evidence_path": None,
        "failure_evidence_sha256": None,
        "failed_model_gate_output_reused": False,
        "valid_outer_batches_reused": list(RESUME_VALID_RANKS),
        "invalid_outer_batches_reused_as_measurement": False,
        "invalid_outer_attempts_preserved": list(RESUME_INVALID_RANKS),
        "intermediate_failure_state_path": str(intermediate["state_path"]),
        "intermediate_failure_state_sha256": intermediate["state_sha256"],
        "intermediate_failure_state_inherited": False,
        "calibration_design_amended": False,
        "accepted_rounds_inherited": False,
        "outer_state_inherited": False,
        "historical_outer_rankings_or_batches_reused": (
            REUSE_HISTORICAL_OUTER_EVIDENCE
        ),
        "historical_outer_evidence_role": (
            "continuation"
            if REUSE_HISTORICAL_OUTER_EVIDENCE
            else "diagnostic_provenance_only_no_state_or_measurement_credit"
        ),
        "algorithm_c_state_read": False,
    }
    alg.atomic(state_path, state)
    alg.atomic(output / "technical_continuation_manifest.json", {
        "schema_version": (
            "mysql_algorithm_b_independent_technical_resume_v7"
            if REUSE_HISTORICAL_OUTER_EVIDENCE
            else "mysql_algorithm_b_xp_top4_fresh_campaign_v1"
        ),
        "status": (
            "VERIFIED_BEFORE_CONTINUATION"
            if REUSE_HISTORICAL_OUTER_EVIDENCE
            else "VERIFIED_BEFORE_FRESH_OUTER_CAMPAIGN"
        ),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "source_run_root": str(resume["source"]),
        "source_state_path": str(resume["state_path"]),
        "source_state_sha256": resume["state_sha256"],
        "source_baseline_path": None,
        "source_baseline_sha256": None,
        "source_calibration_design_path": None,
        "source_calibration_design_sha256": None,
        "fresh_bootstrap_manifest_path": str(resume["state_path"]),
        "fresh_bootstrap_manifest_sha256": resume["state_sha256"],
        "source_completed_calibration_waves": resume["wave_evidence"],
        "source_companion_audit_path": None,
        "source_companion_audit_sha256": None,
        "source_failure_evidence_path": None,
        "source_failure_evidence_sha256": None,
        "source_completed_quality_gates": resume["quality_gate_evidence"],
        "source_outer_rankings_path": (
            str(resume["rankings_path"])
            if REUSE_HISTORICAL_OUTER_EVIDENCE else None
        ),
        "source_outer_rankings_sha256": (
            resume["rankings_sha256"]
            if REUSE_HISTORICAL_OUTER_EVIDENCE else None
        ),
        "source_valid_batch_path": (
            str(resume["valid_batch_path"])
            if REUSE_HISTORICAL_OUTER_EVIDENCE else None
        ),
        "source_valid_batch_sha256": (
            resume["valid_batch_sha256"]
            if REUSE_HISTORICAL_OUTER_EVIDENCE else None
        ),
        "source_invalid_batch_path": (
            str(resume["invalid_batch_path"])
            if REUSE_HISTORICAL_OUTER_EVIDENCE else None
        ),
        "source_invalid_batch_sha256": (
            resume["invalid_batch_sha256"]
            if REUSE_HISTORICAL_OUTER_EVIDENCE else None
        ),
        "intermediate_failure_state_path": str(intermediate["state_path"]),
        "intermediate_failure_state_sha256": intermediate["state_sha256"],
        "intermediate_failure_state_inherited": False,
        "calibration_design_amended": False,
        "failure_stage": (
            "outer_001_top5_identity_audit"
            if REUSE_HISTORICAL_OUTER_EVIDENCE else None
        ),
        "original_failure_stage": (
            "outer_001_rank_2_formal_timeout"
            if REUSE_HISTORICAL_OUTER_EVIDENCE else None
        ),
        "partial_failed_wave_results_reused": False,
        "all_calibration_wave_results_reused": False,
        "completed_model_gate_output_reused_as_model": False,
        "model_gate_rebuilt_from_fresh_current_campaign_calibration": True,
        "failed_outer_real_replay_started": REUSE_HISTORICAL_OUTER_EVIDENCE,
        "valid_outer_batches_reused": list(RESUME_VALID_RANKS),
        "invalid_outer_batches_reused_as_measurement": False,
        "invalid_outer_attempts_preserved": list(RESUME_INVALID_RANKS),
        "accepted_or_rejected_outer_state_reused": False,
        "historical_outer_rankings_or_batches_reused": (
            REUSE_HISTORICAL_OUTER_EVIDENCE
        ),
        "historical_outer_evidence_role": (
            "continuation"
            if REUSE_HISTORICAL_OUTER_EVIDENCE
            else "diagnostic_provenance_only_no_state_or_measurement_credit"
        ),
        "algorithm_c_state_or_result_read": False,
        "controller_source_path": str(Path(__file__).resolve()),
        "controller_source_sha256": alg.sha(Path(__file__).resolve()),
        "coordination_contract_path": str(COORDINATION_CONTRACT),
    })
    try:
        coordination_contract = configure_runtime()
        freeze, workers, point_gate = fresh_authority()
        params_by_scene, ranges_by_scene, active_by_scene = parameter_state()
        initial_ranges_by_scene = copy.deepcopy(ranges_by_scene)
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
        baseline_path = output / "formal_independent_baseline/batch_result.json"
        if REUSE_VERIFIED_BASELINE:
            state["stage"] = "RESTORING_VERIFIED_INDEPENDENT_FRESH_BASELINE"
            alg.atomic(state_path, state)
            _mirror_batch(resume["baseline_path"], baseline_path)
            baseline_rows = copy.deepcopy(resume["baseline_rows"])
        else:
            state["stage"] = "INDEPENDENT_FRESH_BASELINE"
            alg.atomic(state_path, state)
            assignments = {
                worker_id: {
                    "rank": 0,
                    "params": copy.deepcopy(
                        params_by_scene[str(worker["scene"])]
                    ),
                }
                for worker_id, worker in workers.items()
            }
            baseline_rows, state = runtime._measure_batch_with_fair_coordination(
                freeze=freeze, workers=workers, assignments=assignments,
                output=output / "formal_independent_baseline",
                reset_token="algorithm_b_independent_fresh_baseline",
                state_path=state_path, state=state,
                stage_prefix="ALGORITHM_B_INDEPENDENT_BASELINE",
                run_id=args.run_id, contract=coordination_contract,
            )
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
            "original_path": (
                str(resume["baseline_path"])
                if REUSE_VERIFIED_BASELINE else None
            ),
            "original_sha256": (
                resume["baseline_sha256"] if REUSE_VERIFIED_BASELINE else None
            ),
            "bit_identical_technical_continuation": REUSE_VERIFIED_BASELINE,
            "measured_in_current_campaign": not REUSE_VERIFIED_BASELINE,
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
        calibration_amendments = amend_tpch_calibration_design(calibration_designs)
        if resume["design"].get("scene_designs") != calibration_designs:
            raise RuntimeError("recomputed amended calibration design differs")
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
        if resume["design"].get("formal_feasibility_amendments") != calibration_amendments:
            raise RuntimeError("calibration amendment audit differs")
        destination_design = output / "calibration_design.json"
        alg.atomic(destination_design, {
            "schema_version": "mysql_algorithm_b_xp_top4_calibration_design_v1",
            "status": "PASS", "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "replay_budget_per_scene": CALIBRATION_REPLAY_BUDGET,
            "scene_designs": copy.deepcopy(calibration_designs),
            "formal_feasibility_amendments": copy.deepcopy(calibration_amendments),
            "algorithm_c_state_read": False,
            "fresh_physical_calibration_required": True,
            "historical_calibration_measurement_reused": False,
        })

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
                design_override=calibration_designs[scene],
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
        soft_negative_counts: dict[str, dict[str, int]] = {
            scene: {} for scene in SCENES
        }
        if POST_CALIBRATION_STATE_TRANSFORM is not None:
            POST_CALIBRATION_STATE_TRANSFORM(
                state=state,
                state_path=state_path,
                source_resume=resume,
                params_by_scene=params_by_scene,
                ranges_by_scene=ranges_by_scene,
                initial_ranges_by_scene=initial_ranges_by_scene,
                active_by_scene=active_by_scene,
                incumbents=incumbents,
                objectives=objectives,
                models=models,
                replayed=replayed,
                soft_negative_counts=soft_negative_counts,
                output=output,
            )
        outer_attempt = int(INITIAL_OUTER_ATTEMPT)
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
            if OUTER_BOUNDARY_STATE_TRANSFORM is not None:
                OUTER_BOUNDARY_STATE_TRANSFORM(
                    outer_attempt=outer_attempt,
                    state=state,
                    params_by_scene=params_by_scene,
                    ranges_by_scene=ranges_by_scene,
                    active_by_scene=active_by_scene,
                    models=models,
                    output=output,
                )
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
                        plan_client_log=(
                            output / f"outer_{outer_attempt:03d}/plan_capture"
                            / scene / "client_processes.jsonl"
                        ),
                        soft_negative_counts=soft_negative_counts[scene],
                    )] = scene
                for future in as_completed(futures):
                    ranking_rows[futures[future]] = future.result()
            rankings = {
                scene: copy.deepcopy(ranking_rows[scene]["selected_top5"])
                for scene in active_scenes
            }
            if (
                outer_attempt == RESUME_OUTER_ATTEMPT
                and ranking_identity(rankings)
                != ranking_identity(resume["rankings"].get("selected_top_k") or {})
            ):
                raise RuntimeError("recomputed outer-1 Top-5 differs from source")
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
            invalid_candidate_attempts: list[dict[str, Any]] = []
            for rank in range(1, FIXED_TOP_K + 1):
                assignments, companions = runtime._build_single_candidate_assignments(
                    rankings=rankings, params_by_scene=params_by_scene,
                    workers=workers, rank=rank,
                )
                batch_path = output / f"outer_{outer_attempt:03d}/top5_batch_{rank}/batch_result.json"
                source_reused = False
                if outer_attempt == RESUME_OUTER_ATTEMPT and rank == 1:
                    _mirror_batch(resume["valid_batch_path"], batch_path)
                    measured = copy.deepcopy(resume["valid_batch_rows"])
                    source_reused = True
                    state["stage"] = "ALGORITHM_B_OUTER_001_RANK_1_RESTORED_VERIFIED"
                    alg.atomic(state_path, state)
                elif outer_attempt == RESUME_OUTER_ATTEMPT and rank == 2:
                    _mirror_batch(resume["invalid_batch_path"], batch_path)
                    measured = copy.deepcopy(
                        dict(resume["invalid_batch"].get("measurements") or {})
                    )
                    source_reused = True
                    state["stage"] = "ALGORITHM_B_OUTER_001_RANK_2_INVALID_PRESERVED"
                    alg.atomic(state_path, state)
                else:
                    measured, state = runtime._measure_batch_with_fair_coordination(
                        freeze=freeze, workers=workers, assignments=assignments,
                        output=output / f"outer_{outer_attempt:03d}/top5_batch_{rank}",
                        reset_token=f"algorithm_b_outer_{outer_attempt:03d}_rank_{rank}",
                        state_path=state_path, state=state,
                        stage_prefix=f"ALGORITHM_B_OUTER_{outer_attempt:03d}_RANK_{rank}",
                        run_id=args.run_id, contract=coordination_contract,
                    )
                batch = alg.load(batch_path)
                seen_scenes: set[str] = set()
                for row in measured.values():
                    scene = str(row.get("scene") or "")
                    if (
                        scene not in rankings
                        or scene in seen_scenes
                        or alg.optimizer_params_key(row.get("params") or {})
                        != alg.optimizer_params_key(
                            rankings[scene][rank - 1]["optimized_params"]
                        )
                    ):
                        raise RuntimeError(
                            f"Algorithm B outer {outer_attempt} rank {rank} "
                            "measurement identity differs"
                        )
                    seen_scenes.add(scene)
                if seen_scenes != set(active_scenes):
                    raise RuntimeError(
                        f"Algorithm B outer {outer_attempt} rank {rank} "
                        "scene cardinality differs"
                    )
                if (
                    batch.get("status") != "PASS"
                    or batch.get("failures") not in ([], None)
                    or any(
                        row.get("hard_gate_passed") is not True
                        or row.get("coverage_complete") is not True
                        for row in measured.values()
                    )
                ):
                    if not formally_infeasible_candidate_batch(batch):
                        raise RuntimeError(
                            f"Algorithm B outer {outer_attempt} rank {rank} failed closed"
                        )
                    direct_failure_scenes = infeasible_batch_scenes(batch)
                    if not direct_failure_scenes:
                        raise RuntimeError(
                            f"Algorithm B outer {outer_attempt} rank {rank} "
                            "timeout scene attribution differs"
                        )
                    for failed_scene in direct_failure_scenes:
                        failed_key = alg.optimizer_params_key(
                            rankings[failed_scene][rank - 1]["optimized_params"]
                        )
                        soft_negative_counts[failed_scene][failed_key] = (
                            soft_negative_counts[failed_scene].get(failed_key, 0) + 1
                        )
                    historical_legacy_reconstruction = (
                        outer_attempt <= LEGACY_COUPLED_RECONSTRUCTION_MAX_OUTER
                    )
                    derived_unavailable_scenes = []
                    if not historical_legacy_reconstruction:
                        derived_unavailable_scenes = sorted({
                            str(row.get("scene") or "")
                            for row in measured.values()
                            if (
                                row.get("hard_gate_passed") is not True
                                or row.get("coverage_complete") is not True
                            )
                        } - set(direct_failure_scenes))
                    affected_scenes = sorted(
                        set(direct_failure_scenes) | set(derived_unavailable_scenes)
                    )
                    unaffected_scenes_used: list[str] = []
                    if not historical_legacy_reconstruction:
                        for worker_id, row in measured.items():
                            scene = str(row["scene"])
                            if scene in affected_scenes:
                                continue
                            unaffected_scenes_used.append(scene)
                            if worker_id in companions:
                                companion_audit.append({
                                    "rank": rank, "worker_id": worker_id,
                                    "scene": scene, "hard_gate_passed": True,
                                    "coverage_complete": True,
                                })
                            else:
                                evaluations[scene].append(row)
                    invalid_candidate_attempts.append({
                        "outer_attempt": outer_attempt,
                        "rank": rank,
                        "batch_path": str(batch_path),
                        "batch_sha256": alg.sha(batch_path),
                        "elapsed_seconds": float(batch.get("elapsed_seconds") or 0.0),
                        "failures": copy.deepcopy(batch.get("failures") or []),
                        "source_reused": source_reused,
                        "affected_scene_measurement_data_used": False,
                        "unaffected_valid_scene_measurement_data_used": bool(
                            unaffected_scenes_used
                        ),
                        "unaffected_scenes_used": sorted(unaffected_scenes_used),
                        "historical_legacy_reconstruction": (
                            historical_legacy_reconstruction
                        ),
                        "direct_failure_scenes": sorted(direct_failure_scenes),
                        "derived_unavailable_scenes": derived_unavailable_scenes,
                        "model_update_used_for_affected_scenes": False,
                        "accepted_state_credit": False,
                        "classification": "formal_workload_timeout_candidate_infeasible",
                        "affected_scenes": affected_scenes,
                        "hard_pruned_across_future_outer_rounds": False,
                        "candidate_ranking_soft_downweight_count_incremented": True,
                        "candidate_ranking_soft_downweight_scenes": sorted(
                            direct_failure_scenes
                        ),
                    })
                    continue
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
                "attempted_real_top_k": FIXED_TOP_K,
                "invalid_candidate_attempts": invalid_candidate_attempts,
                "invalid_measurement_data_used": False,
            })

            for scene in active_scenes:
                scene_invalid_attempts, expected_valid = candidate_accounting_for_outer(
                    outer_attempt, scene, invalid_candidate_attempts,
                    evaluations[scene],
                )
                if expected_valid == 0:
                    state["rejected_outer_attempts"][scene] += 1
                    ranges_by_scene[scene], rejection_progress = (
                        rejection_cycle.advance_rejection_cycle(
                            accepted=False,
                            consecutive_rejections=int(
                                state["consecutive_failed_rounds"][scene]
                            ),
                            ranges=ranges_by_scene[scene],
                            incumbent_params=params_by_scene[scene],
                            active_params=active_by_scene[scene],
                        )
                    )
                    state["consecutive_failed_rounds"][scene] = int(
                        rejection_progress["consecutive_rejections_after"]
                    )
                    if rejection_progress["forced_incumbent_shrink"]:
                        state["incumbent_centered_forced_shrinks"][scene] += 1
                    convergence = rejection_cycle.convergence_document(
                        ranges=ranges_by_scene[scene],
                        initial_ranges=initial_ranges_by_scene[scene],
                        active_params=active_by_scene[scene],
                    )
                    state["interval_stable_rounds"][scene] = 0
                    state["stable_intervals"][scene] = None
                    state["converged"][scene] = bool(convergence["converged"])
                    tier = {
                        "acceptance_tier": state["acceptance_tier"][scene],
                        "consecutive_failed_rounds": state[
                            "consecutive_failed_rounds"
                        ][scene],
                        "acceptance_tier_downgrade_active": False,
                    }
                    alg.atomic(
                        output / f"outer_{outer_attempt:03d}/{scene}.decision.json",
                        {
                            "schema_version": (
                                "mysql_four_scene_algorithm_b_independent_decision_v1"
                            ),
                            "algorithm": "B", "scene": scene,
                            "outer_attempt": outer_attempt,
                            "accepted": False,
                            "accepted_state_credit_increment": 0,
                            "confirmation_ordinal": None,
                            "accepted_rounds": state["accepted_rounds"][scene],
                            "rejected_outer_attempts": state[
                                "rejected_outer_attempts"
                            ][scene],
                            "unique_real_resource_score_best_rank": None,
                            "unique_real_resource_score_best": None,
                            "score_tie_count_before_rank_signature_tiebreak": 0,
                            "small_loop_best_selected_before_outer_gate": False,
                            "attempted_real_top_k": FIXED_TOP_K,
                            "valid_real_evaluation_count": 0,
                            "invalid_candidate_attempts": scene_invalid_attempts,
                            "invalid_measurement_data_used": False,
                            "evaluations": [],
                            "interval_convergence": convergence,
                            "converged": state["converged"][scene],
                            "tier_progress": tier,
                            "rejection_cycle": rejection_progress,
                            "algorithm_c_state_read": False,
                            "zero_valid_candidates_rejected_without_global_abort": True,
                        },
                    )
                    continue
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
                resource_score_best = ordered[0]
                resource_score_best_value = float(
                    resource_score_best["real_resource_score"]
                )
                score_tie_count = sum(
                    math.isclose(
                        float(row["real_resource_score"]),
                        resource_score_best_value,
                        rel_tol=0.0, abs_tol=1e-12,
                    )
                    for row in ordered
                )
                for row in evaluations[scene]:
                    row["unique_small_loop_best"] = row is resource_score_best
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
                # Historical continuations default to score-first semantics
                # before the configured boundary.  Fresh corrected campaigns
                # set the boundary to Outer 1.  At and after the boundary, the
                # outer acceptance gate establishes eligibility and the
                # unchanged resource-only score orders the eligible set.  QPS
                # therefore remains absent from prediction, gradients, and
                # candidate score while a lower-ranked valid Top-5 replay can
                # no longer be discarded merely because the global resource
                # score winner failed a hard/latency/current-tier gate.
                chosen, acceptance_eligible, selection_policy = (
                    choose_outer_candidate(ordered, outer_attempt=outer_attempt)
                )
                chosen_score = float(chosen["real_resource_score"])
                if outer_attempt >= ACCEPTANCE_AWARE_SELECTION_EFFECTIVE_OUTER:
                    for row in evaluations[scene]:
                        row["selected_for_outer_decision"] = row is chosen
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
                    selected_ranking = rankings[scene][int(chosen["rank"]) - 1]
                    if (
                        alg.optimizer_params_key(selected_ranking["optimized_params"])
                        != alg.optimizer_params_key(chosen["params"])
                    ):
                        raise RuntimeError(
                            f"{scene} selected ranking/measurement identity differs"
                        )
                    shrink_active_parameters = (
                        list(selected_ranking["active_subset"])
                        if outer_attempt >= SUBSET_ONLY_SHRINK_EFFECTIVE_OUTER
                        else list(active_by_scene[scene])
                    )
                    ranges_by_scene[scene] = alg.shrink_ranges(
                        ranges_by_scene[scene], params_by_scene[scene],
                        shrink_active_parameters,
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
                else:
                    state["rejected_outer_attempts"][scene] += 1
                    replayed[scene].add(
                        alg.optimizer_params_key(resource_score_best["params"])
                    )
                ranges_by_scene[scene], rejection_progress = (
                    rejection_cycle.advance_rejection_cycle(
                        accepted=accepted,
                        consecutive_rejections=int(
                            state["consecutive_failed_rounds"][scene]
                        ),
                        ranges=ranges_by_scene[scene],
                        incumbent_params=params_by_scene[scene],
                        active_params=active_by_scene[scene],
                    )
                )
                state["consecutive_failed_rounds"][scene] = int(
                    rejection_progress["consecutive_rejections_after"]
                )
                if rejection_progress["forced_incumbent_shrink"]:
                    state["incumbent_centered_forced_shrinks"][scene] += 1
                convergence = rejection_cycle.convergence_document(
                    ranges=ranges_by_scene[scene],
                    initial_ranges=initial_ranges_by_scene[scene],
                    active_params=active_by_scene[scene],
                )
                state["interval_stable_rounds"][scene] = 0
                state["stable_intervals"][scene] = None
                state["converged"][scene] = bool(convergence["converged"])
                tier = {
                    "acceptance_tier": state["acceptance_tier"][scene],
                    "consecutive_failed_rounds": state[
                        "consecutive_failed_rounds"
                    ][scene],
                    "acceptance_tier_downgrade_active": False,
                }
                decision_document = {
                    "schema_version": "mysql_four_scene_algorithm_b_independent_decision_v1",
                    "algorithm": "B", "scene": scene,
                    "outer_attempt": outer_attempt,
                    "accepted": accepted,
                    "accepted_state_credit_increment": accepted_credit,
                    "confirmation_ordinal": confirmation_ordinal,
                    "accepted_rounds": state["accepted_rounds"][scene],
                    "rejected_outer_attempts": state["rejected_outer_attempts"][scene],
                    "unique_real_resource_score_best_rank": resource_score_best["rank"],
                    "unique_real_resource_score_best": resource_score_best_value,
                    "score_tie_count_before_rank_signature_tiebreak": score_tie_count,
                    "small_loop_best_selected_before_outer_gate": True,
                    "attempted_real_top_k": FIXED_TOP_K,
                    "valid_real_evaluation_count": len(evaluations[scene]),
                    "invalid_candidate_attempts": scene_invalid_attempts,
                    "invalid_measurement_data_used": False,
                    "evaluations": evaluations[scene],
                    "interval_convergence": convergence,
                    "converged": state["converged"][scene],
                    "tier_progress": tier,
                    "rejection_cycle": rejection_progress,
                    "algorithm_c_state_read": False,
                }
                if (
                    accepted
                    and outer_attempt >= SUBSET_ONLY_SHRINK_EFFECTIVE_OUTER
                ):
                    decision_document.update({
                        "accepted_candidate_active_subset": (
                            shrink_active_parameters
                        ),
                        "range_shrink_policy": (
                            "accepted_candidate_active_subset_only_v1"
                        ),
                        "untested_active_parameter_ranges_shrunk": False,
                    })
                if outer_attempt >= ACCEPTANCE_AWARE_SELECTION_EFFECTIVE_OUTER:
                    boundary_audit = (
                        {
                            "outer_1_through_5": (
                                "resource_score_best_before_outer_gate_v1"
                            ),
                            "outer_6_and_later": (
                                "current_tier_eligible_then_resource_score_v1"
                            ),
                            "historical_state_rewritten": False,
                        }
                        if ACCEPTANCE_AWARE_SELECTION_EFFECTIVE_OUTER == 6
                        else {
                            "selection_effective_from_outer": (
                                ACCEPTANCE_AWARE_SELECTION_EFFECTIVE_OUTER
                            ),
                            "prior_outer_semantics": None,
                            "fresh_campaign": True,
                            "historical_state_rewritten": False,
                        }
                    )
                    decision_document.update({
                        "schema_version": (
                            "mysql_four_scene_algorithm_b_independent_decision_v2"
                        ),
                        "outer_candidate_selection_policy": selection_policy,
                        "acceptance_eligible_ranks": [
                            int(row["rank"]) for row in acceptance_eligible
                        ],
                        "selected_outer_candidate_rank": int(chosen["rank"]),
                        "selected_outer_candidate_resource_score": chosen_score,
                        "small_loop_best_selected_before_outer_gate": False,
                        "qps_used_for_resource_prediction_gradient_or_score": False,
                        "historical_decision_semantics_boundary": boundary_audit,
                    })
                alg.atomic(
                    output / f"outer_{outer_attempt:03d}/{scene}.decision.json",
                    decision_document,
                )
            if (
                ONLINE_RESOURCE_MODEL_UPDATE_EFFECTIVE_OUTER is not None
                and outer_attempt >= ONLINE_RESOURCE_MODEL_UPDATE_EFFECTIVE_OUTER
            ):
                update_rows = {
                    scene: update_resource_model_from_formal_evaluations(
                        scene=scene,
                        model=models[scene],
                        evaluations=evaluations[scene],
                        outer_attempt=outer_attempt,
                    )
                    for scene in active_scenes
                }
                update_path = (
                    output / f"outer_{outer_attempt:03d}"
                    / "online_resource_model_update.json"
                )
                alg.atomic(update_path, {
                    "schema_version": (
                        "mysql_algorithm_b_online_resource_model_update_v1"
                    ),
                    "status": "PASS",
                    "formal_scope": "mysql_only",
                    "cross_engine_comparison_prohibited": True,
                    "outer_attempt": outer_attempt,
                    "scene_updates": update_rows,
                    "qps_used_as_model_input_feature": False,
                    "qps_used_for_resource_target": False,
                    "qps_used_for_resource_prediction_gradient_or_score": False,
                    "inner_candidate_score_modified": False,
                })
                state["last_online_resource_model_update"] = {
                    "outer_attempt": outer_attempt,
                    "path": str(update_path),
                    "sha256": alg.sha(update_path),
                    "added_count_by_scene": {
                        scene: int(row["added_count"])
                        for scene, row in update_rows.items()
                    },
                }
            state["outer_attempt"] = outer_attempt
            state["params_by_scene"] = copy.deepcopy(params_by_scene)
            state["ranges_by_scene"] = copy.deepcopy(ranges_by_scene)
            state["incumbents"] = copy.deepcopy(incumbents)
            state["replayed_param_keys_by_scene"] = {
                scene: sorted(replayed[scene]) for scene in SCENES
            }
            state["soft_negative_candidate_counts_by_scene"] = copy.deepcopy(
                soft_negative_counts
            )
            state["stage"] = "ALGORITHM_B_OUTER_RUNNING"
            alg.atomic(state_path, state)

        # This controller owns exactly one installed scene.  Do not let any
        # dormant sibling keys inherited from legacy document schemas decide
        # the terminal label.
        state["status"] = (
            "COMPLETE"
            if all(state["converged"][scene] for scene in SCENES)
            else "NON_CONVERGED"
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
