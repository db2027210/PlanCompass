#!/usr/bin/env python3
"""Algorithm B v27 core for a completed-outer dynamic-support continuation.

An invalid physical candidate wave may contain valid measurements for scenes
that were not named by the fail-closed attribution.  Preserve those rows and
reject only the affected scene/rank pair.  A scene with zero valid candidates
gets a bounded rejected outer attempt rather than aborting all four scenes.

When a continuation enables ``ONLINE_RESOURCE_MODEL_UPDATE_EFFECTIVE_OUTER``,
every technically valid formal Top-5 replay is added to the current-run model
after the outer decision.  The model's resource targets remain independent of
its audit-only QPS target, and only predicted resources enter candidate score.
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
from scripts import mysql_co_resident_pg_isolation_v12 as isolation
from scripts import mysql_algorithm_b_candidate_diversity_v1 as candidate_diversity
from scripts import reset_mysql_xp_data_planes_v7 as reset_v7
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v22 as lease_v22
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v32 as c32
from scripts import mysql_algorithm_b_pg_outer_policy_v31 as pg_outer
from scripts import mysql_outer_rejection_cycle_v1 as rejection_cycle
from scripts import mysql_async_scene_start_barrier_v1 as scene_start_barrier


runtime = c32.runtime
alg = runtime.alg
FULL_SCENES = tuple(runtime.SCENES)
SCENES = FULL_SCENES
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
PLAN_AUDIT_CANDIDATE_LIMIT = 64
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
SCENE_SCOPE_INSTALLER = None
PRESTART_AUTHORITY_TIMEOUT_SECONDS = 3.0
ONLINE_RESOURCE_MODEL_UPDATE_EFFECTIVE_OUTER: int | None = None
REUSE_VERIFIED_BASELINE = True
REUSE_HISTORICAL_OUTER_EVIDENCE = True
REUSE_COMPLETED_MODEL_GATE = False
RESUME_SOURCE_VALIDATOR = None
ASYNC_COORDINATION_SOURCE_BINDINGS: dict[str, str] | None = None
ACCEPTANCE_AWARE_SELECTION_EFFECTIVE_OUTER = 6
SUBSET_ONLY_SHRINK_EFFECTIVE_OUTER = 7
CANDIDATE_DIVERSITY_EFFECTIVE_OUTER = 7
CONTINUATION_COMPLETED_OUTER_ATTEMPT: int | None = None


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
RESUME_CALIBRATION_WAVES = 15
RESUME_FAILURE = "RuntimeError: Algorithm B outer 1 rank 2 failed closed"
RESUME_OUTER_ATTEMPT = 1
RESUME_VALID_RANKS = (1,)
RESUME_INVALID_RANKS = (2,)
TPCH_ROW_EVALUATE_REPLACEMENTS = {
    11: 0.117142857142857,
    15: 0.134285714285714,
}


def amend_tpch_calibration_design(
    designs: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Replace only the two high, formally infeasible TPC-H row-cost points."""
    if "tpch_sf4_memory" not in designs:
        return []
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
        or contract.get("release_version") not in {"v63", "v64"}
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
        or (
            bindings.get("device_scoped_coordination_sha256") != alg.sha(scoped)
            and (
                ASYNC_COORDINATION_SOURCE_BINDINGS is None
                or ASYNC_COORDINATION_SOURCE_BINDINGS.get(
                    "device_scoped_coordination_sha256"
                ) != alg.sha(scoped)
            )
        )
        or bindings.get("co_resident_profile_installer_v12_path")
        != str(installer.relative_to(ROOT))
        or bindings.get("co_resident_profile_installer_v12_sha256")
        != alg.sha(installer)
    ):
        raise RuntimeError("v63/v64 device-scoped wave authority differs")
    if contract.get("release_version") == "v64":
        policy = contract.get("co_resident_isolated_pg_allowance") or {}
        ports = set(policy.get("allowed_postgres_ports") or [])
        names = {
            str(row.get("name"))
            for row in policy.get("containers") or []
            if isinstance(row, Mapping)
        }
        if (
            not {59513, 59514, 59521, 59522, 59523, 59524, 59525, 59526}
            <= ports
            or not {
                "ra_fullgrid_generic_mem_20260718_w3",
                "ra_fullgrid_generic_mem_20260718_w4",
            } <= names
        ):
            raise RuntimeError("v64 live PG Algorithm A profile differs")
        # Live PostgreSQL controller/configuration files are inventory only.
        # MySQL launch authority comes from current resource-isolation readback,
        # not from pinning a co-resident project's mutable control files.
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


def _scope_mirrored_batch(
    path: Path, rows: Mapping[str, Mapping[str, Any]], *, scene: str,
) -> None:
    """Turn inherited multi-scene evidence into a scene-local reconstruction.

    No workload is rerun.  The successor keeps only the selected scene's rows
    and records the source digest already carried by the continuation manifest.
    """
    document = alg.load(path)
    document["schema_version"] = "mysql_scene_scoped_reconstructed_batch_v1"
    document["measurements"] = copy.deepcopy(dict(rows))
    document["scene"] = scene
    document["physical_measurement_reexecuted"] = False
    document["reconstruction_only"] = True
    document["other_scene_measurements_included"] = False
    document["four_scene_wave_barrier"] = False
    document["other_scene_completion_required"] = False
    alg.atomic(path, document)


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


def technical_candidate_failure_batch(
    batch: Mapping[str, Any], *, batch_path: Path,
) -> dict[str, Any] | None:
    """Classify a PG-style candidate-local technical validation failure.

    The shared C runtime performs the strict artifact-backed classification.
    A derived Algorithm-B rank batch contains only that semantic rank, so its
    invalid workers are excluded while the cached valid sibling rank from the
    same physical [2,2,1] wave remains available to the next rank iteration.
    """
    measurements = batch.get("measurements")
    if not isinstance(measurements, Mapping) or not measurements:
        return None
    assignments = {
        str(worker_id): {
            "rank": int((row or {}).get("rank") or 0),
            "params": copy.deepcopy(dict((row or {}).get("params") or {})),
        }
        for worker_id, row in measurements.items()
        if isinstance(row, Mapping)
    }
    if set(assignments) != set(measurements):
        return None
    technical = runtime.classify_technical_timeout_batch(
        batch, assignments, batch_result_path=batch_path,
    )
    if technical is None:
        return None
    failed = set(technical.get("failed_worker_ids") or [])
    if failed != set(measurements):
        # Rank-split B batches must never mix another rank's valid row into the
        # failed semantic batch.  That sibling is materialized separately.
        return None
    return technical


def minimum_boundary_candidate_rows(
    batch: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Return candidate-local full-suite runs that ended before 300 seconds.

    PostgreSQL's B outer framework records a failed real validation on the
    candidate and continues the remaining fixed cohort.  A complete JOB or
    TPC-H suite that is too short to reach the frozen stability boundary has
    the same semantics: its measurements cannot update the model or outer
    state, but complete coverage is not an infrastructure failure.
    """
    if (
        batch.get("status") != "FAILED_CLOSED"
        or batch.get("failures") not in ([], None)
        or not isinstance(batch.get("measurements"), Mapping)
    ):
        return {}
    recognized: dict[str, dict[str, Any]] = {}
    for worker_id, raw in batch["measurements"].items():
        if not isinstance(raw, Mapping):
            return {}
        row = dict(raw)
        checks = row.get("hard_gate_checks")
        stability = row.get("resource_stability")
        workload = row.get("workload_result")
        coverage = workload.get("query_coverage") if isinstance(workload, Mapping) else None
        other_checks_pass = (
            isinstance(checks, Mapping)
            and checks.get("target_resource_stability_passed") is False
            and all(
                value is True
                for name, value in checks.items()
                if name != "target_resource_stability_passed"
            )
        )
        if not (
            str(row.get("scene") or "") in {"job_memory", "tpch_sf4_memory"}
            and row.get("hard_gate_passed") is False
            and row.get("coverage_complete") is True
            and other_checks_pass
            and isinstance(stability, Mapping)
            and stability.get("stable") is False
            and stability.get("reason")
            == "full_suite_completed_before_minimum_measurement_boundary"
            and float(stability.get("elapsed_seconds") or 0.0) < 300.0
            and isinstance(coverage, Mapping)
            and coverage.get("coverage_complete") is True
            and coverage.get("missing_queries") in ([], None)
        ):
            return {}
        recognized[str(worker_id)] = row
    return recognized


def evaluation_candidate_ranking(
    *, scene: str, row: Mapping[str, Any],
    current_rankings: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return the ranking identity that produced one physical measurement.

    Cached rows were measured in an earlier outer round, so their historical
    rank must never index the newly generated ranking list.  Fresh rows keep
    the current rank mapping.  Both paths verify parameter identity before an
    outer decision can mutate accepted state.
    """
    if row.get("cached_valid_replay_redecision") is True:
        selected = copy.deepcopy(row.get("candidate_ranking") or {})
    else:
        index = int(row.get("rank") or 0) - 1
        if index < 0 or index >= len(current_rankings):
            raise RuntimeError(f"{scene} evaluation ranking index differs")
        selected = copy.deepcopy(current_rankings[index])
    optimized = selected.get("optimized_params")
    if (
        not isinstance(optimized, Mapping)
        or alg.optimizer_params_key(optimized)
        != alg.optimizer_params_key(row.get("params") or {})
    ):
        raise RuntimeError(f"{scene} evaluation candidate identity differs")
    return selected


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
    amendments = design_value.get("formal_feasibility_amendments")
    if (
        design_value.get("schema_version")
        != "mysql_four_scene_algorithm_b_calibration_design_v3"
        or design_value.get("status") != "PASS"
        or design_value.get("replay_budget_per_scene") != 15
        or design_value.get("algorithm_c_state_read") is not False
        or not isinstance(amendments, list)
        or [(row.get("rank"), row.get("replacement_value")) for row in amendments]
        != [(11, 0.117142857142857), (15, 0.134285714285714)]
    ):
        raise RuntimeError("resume calibration design differs")

    wave_rows: dict[int, dict[str, dict[str, Any]]] = {}
    wave_evidence = []
    for rank in range(1, RESUME_CALIBRATION_WAVES + 1):
        path = source / f"fresh_calibration/wave_{rank:02d}/batch_result.json"
        wave_rows[rank] = _batch_rows(path, expected_count=4)
        wave_evidence.append({"wave": rank, "path": str(path), "sha256": alg.sha(path)})
    companion = source / "fresh_calibration/companion_audit.json"
    companion_sha = _verified_sidecar(companion)
    companion_value = alg.load(companion)
    if (
        companion_value.get("status") != "PASS"
        or companion_value.get("restored_verified_wave_count") != 15
        or companion_value.get("full_workload_contract_preserved") is not True
    ):
        raise RuntimeError("resume calibration companion audit differs")
    quality_gate_evidence = []
    state_gates = state.get("fresh_model_quality_gates") or {}
    for scene in SCENES:
        gate_path = source / f"fresh_calibration/{scene}/fresh_model_quality_gate.json"
        gate_sha = _verified_sidecar(gate_path)
        gate = alg.load(gate_path)
        failed_replays = [
            row for row in (gate.get("calibration_replays") or [])
            if isinstance(row, Mapping) and row.get("status") == "failed"
        ]
        state_gate = state_gates.get(scene) or {}
        bounds = state_gate.get("resource_error_bounds") or {}
        if (
            gate.get("passed") is not False
            or gate.get("preflight_soft_start") is not True
            or gate.get("frozen_design_override_consumed") is not True
            or gate.get("calibration_replay_attempt_count") != 15
            or gate.get("usable_sample_count_including_baseline") != 16
            or failed_replays
            or state_gate.get("sha256") != gate_sha
            or state_gate.get("soft_start") is not True
            or set(bounds) != {"cpu", "memory", "io"}
            or any(int((bounds[name] or {}).get("sample_count") or 0) != 15
                   for name in ("cpu", "memory", "io"))
        ):
            raise RuntimeError(f"{scene} completed model-gate evidence differs")
        quality_gate_evidence.append({
            "scene": scene, "path": str(gate_path), "sha256": gate_sha,
        })
    continuation = state.get("technical_continuation") or {}
    if (
        continuation.get("kind")
        != "post_model_gate_outer_plan_log_binding_resume_v5"
        or continuation.get("calibration_design_amended") is not True
        or continuation.get("completed_calibration_waves_reused") != 15
        or continuation.get("all_calibration_wave_results_reused") is not True
        or continuation.get("failed_model_gate_output_reused") is not False
        or continuation.get("partial_failed_wave_results_reused") is not False
    ):
        raise RuntimeError("model-gate continuation evidence differs")
    outer_dirs = sorted(path.name for path in source.glob("outer_[0-9][0-9][0-9]"))
    if outer_dirs != ["outer_001"]:
        raise RuntimeError("resume source outer evidence differs")
    rankings_path = source / "outer_001/predicted_rankings.json"
    rankings_sha = _verified_sidecar(rankings_path)
    rankings = alg.load(rankings_path)
    if (
        rankings.get("status") != "PASS"
        or rankings.get("outer_attempt") != 1
        or rankings.get("active_scenes") != list(SCENES)
        or any(len(rows) != FIXED_TOP_K for rows in
               (rankings.get("selected_top_k") or {}).values())
    ):
        raise RuntimeError("resume source predicted Top-5 differs")
    valid_batch_path = source / "outer_001/top5_batch_1/batch_result.json"
    valid_batch_rows = _batch_rows(valid_batch_path, expected_count=4)
    invalid_batch_path = source / "outer_001/top5_batch_2/batch_result.json"
    invalid_batch_sha = _verified_sidecar(invalid_batch_path)
    invalid_batch = alg.load(invalid_batch_path)
    if not formally_infeasible_candidate_batch(invalid_batch):
        raise RuntimeError("resume source rank-2 failure is not a formal timeout")
    for rank in range(3, FIXED_TOP_K + 1):
        if (source / f"outer_001/top5_batch_{rank}").exists():
            raise RuntimeError("resume source unexpectedly attempted later Top-5 rank")
    if any((source / "outer_001").glob("*.decision.json")):
        raise RuntimeError("resume source unexpectedly committed an outer decision")
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
        "companion_path": companion,
        "companion_sha256": companion_sha,
        "failure_evidence_path": state_path,
        "failure_evidence_sha256": state_sha,
        "quality_gate_evidence": quality_gate_evidence,
        "rankings_path": rankings_path,
        "rankings_sha256": rankings_sha,
        "rankings": rankings,
        "valid_batch_path": valid_batch_path,
        "valid_batch_sha256": alg.sha(valid_batch_path),
        "valid_batch_rows": valid_batch_rows,
        "invalid_batch_path": invalid_batch_path,
        "invalid_batch_sha256": invalid_batch_sha,
        "invalid_batch": invalid_batch,
    }


def validate_completed_outer_continuation(source: Path) -> dict[str, Any]:
    """Validate an immutable v26 source completed through Outer 2.

    The interrupted Outer-3 wave is deliberately excluded.  Only the fresh
    baseline, frozen calibration release, completed Outer-1/2 decisions, and
    their technically valid real measurements are continuation authority.
    """
    source = source.resolve()
    if source.parent != HISTORY.resolve() or not source.is_dir():
        raise RuntimeError("completed-outer continuation source root differs")
    state_path = source / "run_state.json"
    state_sha = _verified_sidecar(state_path)
    state = alg.load(state_path)
    completed_outer = int(CONTINUATION_COMPLETED_OUTER_ATTEMPT or 0)
    if (
        completed_outer != 2
        or state.get("algorithm") != "B"
        or state.get("formal_scope") != "mysql_only"
        or state.get("cross_engine_comparison_prohibited") is not True
        or state.get("status") != "RUNNING"
        or not str(state.get("stage") or "").startswith(
            "ALGORITHM_B_OUTER_003_"
        )
        or int(state.get("outer_attempt") or 0) != completed_outer
        or state.get("active_scenes") != list(SCENES)
        or state.get("accepted_rounds") != {scene: 1 for scene in SCENES}
        or state.get("rejected_outer_attempts") != {scene: 1 for scene in SCENES}
        or any(bool(value) for value in (state.get("converged") or {}).values())
    ):
        raise RuntimeError("completed-outer continuation state differs")

    baseline = source / "formal_independent_baseline/batch_result.json"
    baseline_rows = _batch_rows(baseline, expected_count=8)
    if (state.get("fresh_baseline") or {}).get("sha256") != alg.sha(baseline):
        raise RuntimeError("completed-outer baseline binding differs")

    design = source / "calibration_design.json"
    design_sha = _verified_sidecar(design)
    design_value = alg.load(design)
    if (
        design_value.get("status") != "PASS"
        or int(design_value.get("replay_budget_per_scene") or 0) != 15
        or design_value.get("algorithm_c_state_read") is not False
    ):
        raise RuntimeError("completed-outer calibration design differs")
    wave_rows: dict[int, dict[str, dict[str, Any]]] = {}
    wave_evidence: list[dict[str, Any]] = []
    for rank in range(1, RESUME_CALIBRATION_WAVES + 1):
        path = source / f"fresh_calibration/wave_{rank:02d}/batch_result.json"
        wave_rows[rank] = _batch_rows(path, expected_count=4)
        wave_evidence.append({
            "wave": rank, "path": str(path), "sha256": alg.sha(path),
        })
    companion = source / "fresh_calibration/companion_audit.json"
    companion_sha = _verified_sidecar(companion)
    if alg.load(companion).get("status") != "PASS":
        raise RuntimeError("completed-outer calibration companion differs")
    quality_gate_evidence: list[dict[str, Any]] = []
    for scene in SCENES:
        gate_path = source / f"fresh_calibration/{scene}/fresh_model_quality_gate.json"
        gate_sha = _verified_sidecar(gate_path)
        gate = alg.load(gate_path)
        if (
            gate.get("preflight_soft_start") is not True
            or gate.get("frozen_design_override_consumed") is not True
            or int(gate.get("calibration_replay_attempt_count") or 0) != 15
            or int(gate.get("usable_sample_count_including_baseline") or 0) != 16
        ):
            raise RuntimeError(f"{scene} completed-outer model gate differs")
        quality_gate_evidence.append({
            "scene": scene, "path": str(gate_path), "sha256": gate_sha,
        })

    completed_evaluations: dict[int, dict[str, list[dict[str, Any]]]] = {}
    completed_outer_evidence: list[dict[str, Any]] = []
    for outer in range(1, completed_outer + 1):
        outer_root = source / f"outer_{outer:03d}"
        scene_rows: dict[str, list[dict[str, Any]]] = {}
        for scene in SCENES:
            decision_path = outer_root / f"{scene}.decision.json"
            decision_sha = _verified_sidecar(decision_path)
            decision = alg.load(decision_path)
            evaluations = [
                copy.deepcopy(dict(row))
                for row in (decision.get("evaluations") or [])
            ]
            if (
                decision.get("scene") != scene
                or int(decision.get("outer_attempt") or 0) != outer
                or not evaluations
                or any(
                    row.get("coverage_complete") is not True
                    or row.get("hard_gate_passed") is not True
                    for row in evaluations
                )
            ):
                raise RuntimeError(
                    f"{scene} completed Outer {outer} decision differs"
                )
            scene_rows[scene] = evaluations
            completed_outer_evidence.append({
                "outer_attempt": outer, "scene": scene,
                "path": str(decision_path), "sha256": decision_sha,
                "valid_evaluation_count": len(evaluations),
                "accepted": decision.get("accepted") is True,
            })
        for first_rank in (1, 3, 5):
            batch_root = outer_root / f"top5_batch_{first_rank}"
            batch_path = batch_root / (
                "physical_joint_wave_batch_result.json"
                if first_rank in (1, 3) else "batch_result.json"
            )
            batch_sha = _verified_sidecar(batch_path)
            batch = alg.load(batch_path)
            if batch.get("status") != "PASS" or batch.get("failures") not in ([], None):
                raise RuntimeError(
                    f"completed Outer {outer} batch {first_rank} differs"
                )
            completed_outer_evidence.append({
                "outer_attempt": outer, "first_rank": first_rank,
                "path": str(batch_path), "sha256": batch_sha,
            })
        completed_evaluations[outer] = scene_rows

    interrupted_outer = source / "outer_003"
    if any(interrupted_outer.glob("top5_batch_*/batch_result.json")) or any(
        interrupted_outer.glob("top5_batch_*/physical_joint_wave_batch_result.json")
    ):
        raise RuntimeError("interrupted Outer 3 unexpectedly has reusable batch result")
    interrupted_rankings = interrupted_outer / "predicted_rankings.json"
    interrupted_rankings_sha = _verified_sidecar(interrupted_rankings)

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
        "companion_path": companion,
        "companion_sha256": companion_sha,
        "failure_evidence_path": state_path,
        "failure_evidence_sha256": state_sha,
        "quality_gate_evidence": quality_gate_evidence,
        "rankings_path": interrupted_rankings,
        "rankings_sha256": interrupted_rankings_sha,
        "rankings": alg.load(interrupted_rankings),
        "valid_batch_path": interrupted_rankings,
        "valid_batch_sha256": interrupted_rankings_sha,
        "valid_batch_rows": {},
        "invalid_batch_path": interrupted_rankings,
        "invalid_batch_sha256": interrupted_rankings_sha,
        "invalid_batch": {},
        "completed_outer_attempt": completed_outer,
        "completed_outer_evaluations": completed_evaluations,
        "completed_outer_evidence": completed_outer_evidence,
        "interrupted_outer_attempt": 3,
        "interrupted_outer_rankings_path": interrupted_rankings,
        "interrupted_outer_rankings_sha256": interrupted_rankings_sha,
        "interrupted_outer_measurement_credit": False,
    }


def validate_intermediate_failure() -> dict[str, Any]:
    """Bind the over-strict v7 comparison failure without inheriting its state."""
    state_path = INTERMEDIATE_FAILURE / "run_state.json"
    state_sha = _verified_sidecar(state_path)
    state = alg.load(state_path)
    continuation = state.get("technical_continuation") or {}
    if (
        state.get("algorithm") != "B"
        or state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or state.get("error")
        != "RuntimeError: recomputed outer-1 Top-5 differs from source"
        or int(state.get("outer_attempt") or 0) != 0
        or any(int(value) != 0 for value in
               (state.get("accepted_rounds") or {}).values())
        or continuation.get("kind")
        != "post_outer_candidate_timeout_isolation_resume_v6"
        or Path(str(continuation.get("source_run_root") or "")).resolve()
        != (HISTORY / "v6_formal_independent_outer_plan_resume_v63_20260818_attempt001").resolve()
        or (INTERMEDIATE_FAILURE / "outer_001/predicted_rankings.json").exists()
        or any((INTERMEDIATE_FAILURE / "outer_001").glob("top5_batch_*"))
    ):
        raise RuntimeError("intermediate v7 identity-comparison failure differs")
    return {
        "state_path": state_path,
        "state_sha256": state_sha,
        "state": state,
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
        "acceptance_tier": {scene: "fallback" for scene in SCENES},
        "consecutive_failed_rounds": {scene: 0 for scene in SCENES},
        "interval_stable_rounds": {scene: 0 for scene in SCENES},
        "stable_intervals": {scene: None for scene in SCENES},
        "converged": {scene: False for scene in SCENES},
        "incumbent_centered_forced_shrinks": {scene: 0 for scene in SCENES},
        "valid_replay_archive_by_scene": {scene: [] for scene in SCENES},
        "previously_accepted_parameter_signatures": {
            scene: [] for scene in SCENES
        },
        "target_accepted_shrinks": TARGET_ACCEPTED_SHRINKS,
        "maximum_outer_attempts": MAXIMUM_OUTER_ATTEMPTS,
        "maximum_rejected_outer_attempts_per_scene": (
            MAXIMUM_REJECTED_OUTER_ATTEMPTS_PER_SCENE
        ),
        "maximum_post_target_confirmations": 0,
        "post_search_stability_confirmation": False,
        "terminal_condition": "all_active_log_domain_widths_reach_1pct",
        "fixed_real_top_k": FIXED_TOP_K,
        "one_time_calibration_replays_per_scene": CALIBRATION_REPLAY_BUDGET,
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
            "accepted_shrinks_required": TARGET_ACCEPTED_SHRINKS,
            "maximum_interval_ratio": None,
            "required_consecutive_stable_accepted_rounds": 0,
            "rejected_outer_round_resets_stability_streak": False,
            "all_active_parameters_must_satisfy_interval": False,
            "accepted_target_is_immediately_terminal": True,
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
    lease = runtime.coordination.acquire_exclusive_blocking(
        timeout_seconds=PRESTART_AUTHORITY_TIMEOUT_SECONDS,
    )
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
    dynamic_support_points: list[Mapping[str, float]] | None = None,
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
    if (
        round_index >= CANDIDATE_DIVERSITY_EFFECTIVE_OUTER
        and len(usable) >= FIXED_TOP_K
    ):
        leading_points = candidate_diversity.select_diverse_points(
            usable,
            ranges=ranges,
            active=active,
            top_k=FIXED_TOP_K,
            negative_count=lambda point: int(soft_negative_counts.get(
                alg.optimizer_params_key(point.params), 0,
            )),
        )
        leading_keys = {
            alg.optimizer_params_key(point.params) for point in leading_points
        }
        selected_points = leading_points + [
            point for point in usable
            if alg.optimizer_params_key(point.params) not in leading_keys
        ]
    else:
        selected_points = usable
    selected_points = selected_points[:PLAN_AUDIT_CANDIDATE_LIMIT]
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
        "all_rankings": selected,
        "selected_top5": selected[:FIXED_TOP_K],
        "resource_error_bounds": copy.deepcopy(dict(error_bounds)),
        "qps_used_for_resource_prediction_gradient_or_score": False,
        "candidate_hard_pruning": False,
        "complete_plan_evidence_captured_before_real_replay": True,
    }


def choose_outer_candidate(
    ordered: list[dict[str, Any]], *, outer_attempt: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    """Choose the PG best accepted point from the first accepting batch kind.

    The physical [2,2,1] waves are only a scheduler shape.  PostgreSQL first
    completes the whole base Top-K and then orders all accepted base points;
    fallback expansion is a second semantic batch and is consulted only when
    the complete base batch has no acceptable point.
    """
    if not ordered:
        raise ValueError("at least one real Top-5 evaluation is required")
    if any("outer_decision" not in row for row in ordered):
        raise ValueError("all real Top-5 evaluations require an outer decision")
    semantic_batches = (
        (
            "cached_valid_replay",
            lambda row: row.get("cached_valid_replay_redecision") is True,
        ),
        (
            "base_top5",
            lambda row: row.get("cached_valid_replay_redecision") is not True
            and int(row.get("rank") or 0) <= FIXED_TOP_K,
        ),
        (
            "fallback_plan_change",
            lambda row: int(row.get("rank") or 0) > FIXED_TOP_K,
        ),
    )
    for batch_kind, member in semantic_batches:
        acceptance_eligible = [
            row for row in ordered
            if member(row) and row["outer_decision"].get("accepted") is True
        ]
        acceptance_eligible.sort(
            key=lambda row: pg_outer.accepted_selection_key({
                "decision": row["outer_decision"],
                "row": row,
                "reference": {},
            }),
            reverse=True,
        )
        if acceptance_eligible:
            return (
                acceptance_eligible[0], acceptance_eligible,
                f"pg_{batch_kind}_tier_target_qps_io_v1",
            )
    return (
        ordered[0], acceptance_eligible,
        "pg_no_accepted_candidate_resource_score_diagnostic_only_v1",
    )


def _scope_resume_to_scene(
    resume: Mapping[str, Any], scene: str,
) -> dict[str, Any]:
    """Project verified four-scene calibration authority onto one scene lane."""

    scoped = copy.deepcopy(dict(resume))
    scene_fields = {
        "accepted_rounds", "rejected_outer_attempts", "acceptance_tier",
        "consecutive_failed_rounds", "interval_stable_rounds",
        "stable_intervals", "converged", "valid_replay_archive_by_scene",
        "previously_accepted_parameter_signatures", "params_by_scene",
        "ranges_by_scene", "active_by_scene", "incumbents",
        "replayed_param_keys_by_scene", "soft_negative_candidate_counts_by_scene",
        "post_target_stability_confirmation_counts",
    }
    state = copy.deepcopy(dict(scoped["state"]))
    state["active_scenes"] = [scene]
    for field in scene_fields:
        value = state.get(field)
        if isinstance(value, Mapping) and scene in value:
            state[field] = {scene: copy.deepcopy(value[scene])}
    baseline = state.get("fresh_baseline")
    if isinstance(baseline, dict):
        incumbents = baseline.get("incumbents")
        if isinstance(incumbents, Mapping) and scene in incumbents:
            baseline["incumbents"] = {scene: copy.deepcopy(incumbents[scene])}
    scoped["state"] = state
    scoped["baseline_rows"] = {
        worker_id: copy.deepcopy(row)
        for worker_id, row in dict(scoped["baseline_rows"]).items()
        if str(row.get("scene")) == scene
    }
    scoped["wave_rows"] = {
        int(rank): {
            worker_id: copy.deepcopy(row)
            for worker_id, row in dict(rows).items()
            if str(row.get("scene")) == scene
        }
        for rank, rows in dict(scoped["wave_rows"]).items()
    }
    design = copy.deepcopy(dict(scoped["design"]))
    scene_designs = design.get("scene_designs")
    if isinstance(scene_designs, Mapping):
        design["scene_designs"] = {
            scene: copy.deepcopy(scene_designs[scene])
        }
    amendments = design.get("formal_feasibility_amendments")
    if isinstance(amendments, list):
        design["formal_feasibility_amendments"] = [
            copy.deepcopy(row) for row in amendments
            if str(row.get("scene")) == scene
        ]
    scoped["design"] = design
    scoped["quality_gate_evidence"] = [
        copy.deepcopy(row) for row in scoped.get("quality_gate_evidence", [])
        if str(row.get("scene")) == scene
    ]
    rankings = copy.deepcopy(dict(scoped.get("rankings") or {}))
    rankings["active_scenes"] = [scene]
    for field in (
        "selected_top_k", "scene_audit", "fallback_plan_change_admission",
        "full_range_plan_basin_escape_admission",
    ):
        value = rankings.get(field)
        if isinstance(value, Mapping) and scene in value:
            rankings[field] = {scene: copy.deepcopy(value[scene])}
    scoped["rankings"] = rankings
    scoped["valid_batch_rows"] = {
        worker_id: copy.deepcopy(row)
        for worker_id, row in dict(scoped.get("valid_batch_rows") or {}).items()
        if str(row.get("scene")) == scene
    }
    completed = scoped.get("completed_outer_evaluations")
    if isinstance(completed, Mapping):
        scoped["completed_outer_evaluations"] = {
            int(outer): {scene: copy.deepcopy(rows[scene])}
            for outer, rows in completed.items()
            if isinstance(rows, Mapping) and scene in rows
        }
    return scoped


def main() -> int:
    global SCENES
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--resume-from", type=Path, required=True)
    parser.add_argument("--scene", choices=FULL_SCENES)
    parser.add_argument("--scene-start-barrier", type=Path)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.run_id) is None:
        parser.error("unsafe run id")
    if (args.scene is None) != (args.scene_start_barrier is None):
        parser.error("scene and scene-start-barrier must be supplied together")
    resume = (
        RESUME_SOURCE_VALIDATOR(args.resume_from)
        if RESUME_SOURCE_VALIDATOR is not None
        else validate_completed_outer_continuation(args.resume_from)
        if CONTINUATION_COMPLETED_OUTER_ATTEMPT is not None
        else validate_resume_source(args.resume_from)
    )
    intermediate = validate_intermediate_failure()
    if args.scene is not None:
        SCENES = (args.scene,)
        resume = _scope_resume_to_scene(resume, args.scene)
        if SCENE_SCOPE_INSTALLER is not None:
            SCENE_SCOPE_INSTALLER(args.scene)
    output = HISTORY / args.run_id
    if output.resolve() == resume["source"]:
        raise RuntimeError("resume output must differ from immutable source")
    if output.exists():
        raise RuntimeError(f"fresh Algorithm B output exists: {output}")
    output.mkdir(parents=True, mode=0o700)
    lock_name = (
        "mysql_four_scene_algorithm_b_independent_v1"
        if args.scene is None else
        f"mysql_algorithm_b_independent_{args.scene}_v1"
    )
    lock = Path(f"/tmp/{lock_name}.lock").open("w")
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
        "kind": (
            "post_top5_candidate_identity_audit_resume_v7"
            if REUSE_HISTORICAL_OUTER_EVIDENCE
            else "calibration_release_only_fresh_outer_campaign_v1"
        ),
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
        "all_calibration_wave_results_reused": True,
        "failure_evidence_path": str(resume["failure_evidence_path"]),
        "failure_evidence_sha256": resume["failure_evidence_sha256"],
        "failed_model_gate_output_reused": False,
        "valid_outer_batches_reused": list(RESUME_VALID_RANKS),
        "invalid_outer_batches_reused_as_measurement": False,
        "invalid_outer_attempts_preserved": list(RESUME_INVALID_RANKS),
        "intermediate_failure_state_path": str(intermediate["state_path"]),
        "intermediate_failure_state_sha256": intermediate["state_sha256"],
        "intermediate_failure_state_inherited": False,
        "calibration_design_amended": True,
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
    if CONTINUATION_COMPLETED_OUTER_ATTEMPT is not None:
        state["technical_continuation"] = {
            "kind": "completed_outer_dynamic_support_boundary_resume_v1",
            "source_run_root": str(resume["source"]),
            "source_state_path": str(resume["state_path"]),
            "source_state_sha256": resume["state_sha256"],
            "completed_calibration_waves_reused": 15,
            "all_calibration_wave_results_reused": True,
            "fresh_baseline_reused": True,
            "completed_outer_attempt": resume["completed_outer_attempt"],
            "completed_outer_evidence": resume["completed_outer_evidence"],
            "accepted_and_rejected_state_inherited": True,
            "params_ranges_incumbents_inherited": True,
            "valid_formal_measurements_rebuilt_into_resource_model": True,
            "interrupted_outer_attempt": resume["interrupted_outer_attempt"],
            "interrupted_outer_rankings_path": str(
                resume["interrupted_outer_rankings_path"]
            ),
            "interrupted_outer_rankings_sha256": (
                resume["interrupted_outer_rankings_sha256"]
            ),
            "interrupted_outer_measurement_credit": False,
            "dynamic_support_effective_from_outer": (
                resume["completed_outer_attempt"] + 1
            ),
            "algorithm_c_state_read": False,
        }
    alg.atomic(state_path, state)
    alg.atomic(output / "technical_continuation_manifest.json", {
        "schema_version": (
            "mysql_algorithm_b_independent_technical_resume_v7"
            if REUSE_HISTORICAL_OUTER_EVIDENCE
            else "mysql_algorithm_b_fresh_outer_calibration_reuse_v1"
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
        "source_baseline_path": str(resume["baseline_path"]),
        "source_baseline_sha256": resume["baseline_sha256"],
        "source_calibration_design_path": str(resume["design_path"]),
        "source_calibration_design_sha256": resume["design_sha256"],
        "source_completed_calibration_waves": resume["wave_evidence"],
        "source_companion_audit_path": str(resume["companion_path"]),
        "source_companion_audit_sha256": resume["companion_sha256"],
        "source_failure_evidence_path": str(resume["failure_evidence_path"]),
        "source_failure_evidence_sha256": resume["failure_evidence_sha256"],
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
        "calibration_design_amended": True,
        "failure_stage": (
            "outer_001_top5_identity_audit"
            if REUSE_HISTORICAL_OUTER_EVIDENCE else None
        ),
        "original_failure_stage": (
            "outer_001_rank_2_formal_timeout"
            if REUSE_HISTORICAL_OUTER_EVIDENCE else None
        ),
        "partial_failed_wave_results_reused": False,
        "all_calibration_wave_results_reused": True,
        "completed_model_gate_output_reused_as_model": False,
        "model_gate_rebuilt_from_verified_calibration": True,
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
        "coordination_contract_path": str(isolation.CONTRACT_PATH),
    })
    if CONTINUATION_COMPLETED_OUTER_ATTEMPT is not None:
        manifest_path = output / "technical_continuation_manifest.json"
        manifest = alg.load(manifest_path)
        manifest.update({
            "schema_version": (
                "mysql_algorithm_b_completed_outer_dynamic_support_resume_v1"
            ),
            "status": "VERIFIED_BEFORE_CONTINUATION",
            "source_completed_outer_attempt": resume["completed_outer_attempt"],
            "source_completed_outer_evidence": resume[
                "completed_outer_evidence"
            ],
            "accepted_or_rejected_outer_state_reused": True,
            "valid_outer_measurements_rebuilt_into_resource_model": True,
            "interrupted_outer_attempt": resume["interrupted_outer_attempt"],
            "interrupted_outer_rankings_path": str(
                resume["interrupted_outer_rankings_path"]
            ),
            "interrupted_outer_rankings_sha256": (
                resume["interrupted_outer_rankings_sha256"]
            ),
            "interrupted_outer_measurement_credit": False,
            "partial_failed_wave_results_reused": False,
            "dynamic_support_effective_from_outer": (
                resume["completed_outer_attempt"] + 1
            ),
        })
        alg.atomic(manifest_path, manifest)
    try:
        coordination_contract = configure_runtime()
        freeze, workers, point_gate = fresh_authority()
        if args.scene is not None:
            workers = {
                worker_id: worker for worker_id, worker in workers.items()
                if str(worker["scene"]) == args.scene
            }
            if len(workers) != 2:
                raise RuntimeError(
                    f"{args.scene} frozen two-worker authority differs"
                )
            state["scene_start_authority_barrier"] = scene_start_barrier.wait_ready(
                barrier_root=args.scene_start_barrier,
                scene=args.scene,
                expected_scenes=FULL_SCENES,
                authority={
                    "point_gate_passed": point_gate.get("passed") is True,
                    "worker_ids": sorted(workers),
                    "fresh_baseline_reused": REUSE_VERIFIED_BASELINE,
                    "model_gate_reused": REUSE_COMPLETED_MODEL_GATE,
                },
            )
            alg.atomic(state_path, state)
        params_by_scene, ranges_by_scene, active_by_scene = parameter_state()
        _initial_params, full_ranges_by_scene, _initial_active = parameter_state()
        source_state = resume["state"]
        if source_state.get("active_by_scene") != active_by_scene:
            raise RuntimeError("resume source frozen active parameter state differs")
        if CONTINUATION_COMPLETED_OUTER_ATTEMPT is None and (
            source_state.get("params_by_scene") != params_by_scene
            or source_state.get("ranges_by_scene") != ranges_by_scene
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
            if args.scene is not None:
                _scope_mirrored_batch(
                    baseline_path, baseline_rows, scene=args.scene,
                )
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
        shutil.copy2(resume["design_path"], destination_design)
        shutil.copy2(
            resume["design_path"].with_name(resume["design_path"].name + ".sha256"),
            destination_design.with_name(destination_design.name + ".sha256"),
        )
        if alg.sha(destination_design) != resume["design_sha256"]:
            raise RuntimeError("mirrored amended calibration design differs")

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
                if args.scene is not None:
                    _scope_mirrored_batch(
                        batch_path, measured, scene=args.scene,
                    )
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
            if REUSE_COMPLETED_MODEL_GATE:
                for design_row in calibration_designs[scene]:
                    raw = premeasured_validator(
                        {"scene": scene}, dict(design_row["params"]),
                    )
                    measurement = measurement_from_payload(
                        raw.get("resources") or raw.get("resource") or {}, raw,
                    )
                    models[scene].add(
                        design_row["params"], measurement.as_resource(), raw,
                    )
                gate_evidence = next(
                    row for row in resume["quality_gate_evidence"]
                    if str(row["scene"]) == scene
                )
                source_gate = Path(str(gate_evidence["path"]))
                destination_gate = scene_root / "fresh_model_quality_gate.json"
                shutil.copy2(source_gate, destination_gate)
                source_sidecar = source_gate.with_name(source_gate.name + ".sha256")
                if source_sidecar.is_file():
                    shutil.copy2(
                        source_sidecar,
                        destination_gate.with_name(destination_gate.name + ".sha256"),
                    )
                quality_gates[scene] = alg.load(destination_gate)
                source_jsonl = Path(
                    str(quality_gates[scene].get("calibration_jsonl") or "")
                )
                if source_jsonl.is_file():
                    shutil.copy2(
                        source_jsonl, scene_root / "fresh_model_calibration.jsonl",
                    )
                source_gate_state = (
                    resume["state"].get("fresh_model_quality_gates") or {}
                ).get(scene) or {}
                error_bounds_by_scene[scene] = copy.deepcopy(
                    dict(source_gate_state.get("resource_error_bounds") or {})
                )
                if (
                    quality_gates[scene].get("optimizer_started") is not True
                    or quality_gates[scene].get("preflight_soft_start") is not True
                    or set(error_bounds_by_scene[scene]) != {"cpu", "memory", "io"}
                ):
                    raise RuntimeError(
                        f"{scene} reused completed model gate differs"
                    )
                alg.atomic(scene_root / "model_gate_reuse.json", {
                    "schema_version": "mysql_algorithm_b_model_gate_reuse_v1",
                    "status": "PASS",
                    "scene": scene,
                    "source_path": str(source_gate),
                    "source_sha256": alg.sha(source_gate),
                    "physical_calibration_replayed": False,
                    "quality_search_recomputed": False,
                    "operational_soft_start_mode": "default_idw_with_reused_top15_rows",
                })
            else:
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
            if not REUSE_COMPLETED_MODEL_GATE:
                error_bounds_by_scene[scene] = preflight_resource_error_bounds(
                    quality_gates[scene]
                )
                quality_gates[scene]["optimizer_started"] = True
                alg.atomic(
                    scene_root / "fresh_model_quality_gate.json",
                    quality_gates[scene],
                )
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
        outer_attempt = 0
        if CONTINUATION_COMPLETED_OUTER_ATTEMPT is not None:
            completed_outer = int(resume["completed_outer_attempt"])
            if completed_outer != CONTINUATION_COMPLETED_OUTER_ATTEMPT:
                raise RuntimeError("completed-outer continuation boundary differs")
            for completed in range(1, completed_outer + 1):
                for scene in SCENES:
                    evaluations = resume["completed_outer_evaluations"][completed][scene]
                    update_resource_model_from_formal_evaluations(
                        scene=scene, model=models[scene],
                        evaluations=evaluations, outer_attempt=completed,
                    )
            inherited = resume["state"]
            for key in (
                "accepted_rounds",
                "rejected_outer_attempts",
                "interval_stable_rounds",
                "stable_intervals",
                "converged",
                "acceptance_tier",
                "consecutive_failed_rounds",
                "post_target_stability_confirmation_counts",
                "valid_replay_archive_by_scene",
                "previously_accepted_parameter_signatures",
            ):
                state[key] = copy.deepcopy(inherited[key])
            params_by_scene = copy.deepcopy(inherited["params_by_scene"])
            ranges_by_scene = copy.deepcopy(inherited["ranges_by_scene"])
            incumbents = copy.deepcopy(inherited["incumbents"])
            replayed = {
                scene: set(
                    (inherited.get("replayed_param_keys_by_scene") or {}).get(
                        scene, []
                    )
                )
                for scene in SCENES
            }
            soft_negative_counts = {
                scene: {
                    str(key): int(value)
                    for key, value in dict(
                        (inherited.get(
                            "soft_negative_candidate_counts_by_scene"
                        ) or {}).get(scene, {})
                    ).items()
                }
                for scene in SCENES
            }
            state["params_by_scene"] = copy.deepcopy(params_by_scene)
            state["ranges_by_scene"] = copy.deepcopy(ranges_by_scene)
            state["incumbents"] = copy.deepcopy(incumbents)
            state["replayed_param_keys_by_scene"] = {
                scene: sorted(replayed[scene]) for scene in SCENES
            }
            state["soft_negative_candidate_counts_by_scene"] = copy.deepcopy(
                soft_negative_counts
            )
            state["outer_attempt"] = completed_outer
            state["completed_outer_dynamic_support_restore"] = {
                "status": "PASS",
                "source_run_root": str(resume["source"]),
                "source_state_path": str(resume["state_path"]),
                "source_state_sha256": resume["state_sha256"],
                "completed_outer_attempt": completed_outer,
                "accepted_rounds": copy.deepcopy(state["accepted_rounds"]),
                "rejected_outer_attempts": copy.deepcopy(
                    state["rejected_outer_attempts"]
                ),
                "resource_model_rebuilt_from_valid_formal_measurements": True,
                "interrupted_outer_measurement_credit": False,
                "dynamic_support_sources": [
                    "frozen_calibration", "valid_formal_replay",
                    "current_real_incumbent",
                ],
                "qps_used_for_prediction_gradient_or_score": False,
            }
            state["stage"] = (
                f"ALGORITHM_B_OUTER_{completed_outer + 1:03d}_"
                "DYNAMIC_SUPPORT_BOUNDARY_RESTORED"
            )
            alg.atomic(state_path, state)
            outer_attempt = completed_outer
        initial_ranges_by_scene = {
            scene: copy.deepcopy(
                alg.load(alg.TABLE_ROOT / alg.TABLES[scene])[
                    "parameter_space"
                ]["ranges"]
            )
            for scene in SCENES
        }
        while not all(bool(state["converged"][scene]) for scene in SCENES):
            exhausted = sorted(
                scene for scene, value in
                (state.get("candidate_space_exhausted") or {}).items()
                if value is True
            )
            if exhausted or outer_attempt >= MAXIMUM_OUTER_ATTEMPTS:
                state["termination_audit"] = {
                    "rejected_retry_budget_exhausted": [],
                    "candidate_space_exhausted": exhausted,
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
            active_scenes = [
                scene for scene in SCENES
                if not bool(state["converged"][scene])
            ]
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
                    dynamic_support_by_key = {
                        alg.optimizer_params_key(row.params): copy.deepcopy(
                            dict(row.params)
                        )
                        for row in models[scene].snapshot()
                    }
                    dynamic_support_by_key[
                        alg.optimizer_params_key(params_by_scene[scene])
                    ] = copy.deepcopy(params_by_scene[scene])
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
                        dynamic_support_points=[
                            dynamic_support_by_key[key]
                            for key in sorted(dynamic_support_by_key)
                        ],
                        plan_client_log=(
                            output / f"outer_{outer_attempt:03d}/plan_capture"
                            / scene / "client_processes.jsonl"
                        ),
                        soft_negative_counts=soft_negative_counts[scene],
                    )] = scene
                for future in as_completed(futures):
                    ranking_rows[futures[future]] = future.result()
            for scene in active_scenes:
                if (
                    ranking_rows[scene].get("all_rankings")
                    or ranges_by_scene[scene] == full_ranges_by_scene[scene]
                ):
                    continue
                representative = sorted(
                    worker_id for worker_id, worker in workers.items()
                    if worker["scene"] == scene
                )[0]
                dynamic_support_by_key = {
                    alg.optimizer_params_key(row.params): copy.deepcopy(
                        dict(row.params)
                    )
                    for row in models[scene].snapshot()
                }
                dynamic_support_by_key[
                    alg.optimizer_params_key(params_by_scene[scene])
                ] = copy.deepcopy(params_by_scene[scene])
                ranking_rows[scene] = rank_scene(
                    scene=scene,
                    params=params_by_scene[scene],
                    ranges=full_ranges_by_scene[scene],
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
                    dynamic_support_points=[
                        dynamic_support_by_key[key]
                        for key in sorted(dynamic_support_by_key)
                    ],
                    plan_client_log=(
                        output / f"outer_{outer_attempt:03d}/plan_capture"
                        / scene / "full_range_basin_escape.jsonl"
                    ),
                    soft_negative_counts=soft_negative_counts[scene],
                )
                ranking_rows[scene]["ranking_scope"] = (
                    "full_range_plan_basin_escape"
                )
            rankings: dict[str, list[dict[str, Any]]] = {}
            for scene in active_scenes:
                all_scene_rankings = (
                    ranking_rows[scene].get("all_rankings")
                    or ranking_rows[scene]["selected_top5"]
                )
                admitted, admission = pg_outer.admit_partial_topk(
                    all_scene_rankings,
                    incumbent_params=params_by_scene[scene],
                    previously_accepted_signatures=set(),
                    hard_pruned_plan_fingerprints=set(),
                )
                rankings[scene] = []
                for rank, row in enumerate(admitted, 1):
                    row["source_rank"] = row.get("rank")
                    row["rank"] = rank
                    rankings[scene].append(row)
                ranking_rows[scene]["pg_outer_admission"] = admission
                base_signatures = {
                    alg.optimizer_params_key(row["optimized_params"])
                    for row in admitted
                }
                base_plans = {
                    pg_outer.full_plan_fingerprint(row) for row in admitted
                }
                raw_expansion = [
                    row for row in all_scene_rankings
                    if alg.optimizer_params_key(row["optimized_params"])
                    not in base_signatures
                    and pg_outer.full_plan_fingerprint(row) not in base_plans
                ]
                expansion, expansion_admission = pg_outer.admit_partial_topk(
                    raw_expansion,
                    incumbent_params=params_by_scene[scene],
                )
                if len(admitted) == FIXED_TOP_K:
                    for rank, row in enumerate(expansion, FIXED_TOP_K + 1):
                        row["source_rank"] = row.get("rank")
                        row["rank"] = rank
                        rankings[scene].append(row)
                ranking_rows[scene]["pg_fallback_plan_change_admission"] = (
                    expansion_admission
                )
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
                "real_top_k_limit": FIXED_TOP_K,
                "top_k_is_upper_bound": True,
                "partial_top_k_allowed": True,
                "duplicate_plan_padding_allowed": False,
                "baseline_padding_allowed": False,
                "candidate_hard_pruning": False,
                "qps_used_for_resource_prediction_gradient_or_score": False,
                "algorithm_c_state_read": False,
            })

            evaluations = {scene: [] for scene in active_scenes}
            companion_audit: list[dict[str, Any]] = []
            invalid_candidate_attempts: list[dict[str, Any]] = []
            unresolved_scenes = set(active_scenes)
            first_accepting_wave_by_scene: dict[str, dict[str, Any]] = {}

            def finish_semantic_batch(rank: int) -> None:
                if rank not in {FIXED_TOP_K, maximum_admitted_rank}:
                    return
                lower = 1 if rank <= FIXED_TOP_K else FIXED_TOP_K + 1
                upper = FIXED_TOP_K if rank <= FIXED_TOP_K else rank
                for semantic_scene in sorted(unresolved_scenes):
                    semantic_batch_rows = [
                        row for row in evaluations[semantic_scene]
                        if lower <= int(row.get("rank") or 0) <= upper
                    ]
                    accepted_evaluation, _valid = pg_outer.evaluate_batch(
                        semantic_batch_rows,
                        incumbents[semantic_scene],
                        canonical_baseline=state["fresh_baseline"][
                            "incumbents"
                        ][semantic_scene],
                        target=alg.TARGET[semantic_scene],
                    )
                    if accepted_evaluation is not None:
                        first_accepting_wave_by_scene[semantic_scene] = {
                            "batch_kind": (
                                "base_top5" if lower == 1
                                else "fallback_plan_change"
                            ),
                            "wave_ranks": list(range(lower, upper + 1)),
                            "selected_rank": int(
                                accepted_evaluation["row"]["rank"]
                            ),
                            "selected_param_signature": (
                                alg.optimizer_params_key(
                                    accepted_evaluation["row"]["params"]
                                )
                            ),
                            "semantic_batch_evaluated_after_all_physical_waves": True,
                            "invalid_boundary_candidate_does_not_skip_batch_selection": True,
                        }
                unresolved_scenes.difference_update(
                    first_accepting_wave_by_scene
                )
            for scene in sorted(active_scenes):
                accepted_signatures = set(
                    state["previously_accepted_parameter_signatures"][scene]
                )
                cached_rows = []
                for raw in state["valid_replay_archive_by_scene"][scene]:
                    row = copy.deepcopy(raw)
                    candidate = row.get("candidate_ranking") or {}
                    params = candidate.get("optimized_params") or row.get("params")
                    if not isinstance(params, Mapping):
                        continue
                    signature = alg.optimizer_params_key(params)
                    if (
                        signature in accepted_signatures
                        or signature == alg.optimizer_params_key(
                            params_by_scene[scene]
                        )
                        or any(
                            float(params[name])
                            < float(ranges_by_scene[scene][name]["lower"])
                            or float(params[name])
                            > float(ranges_by_scene[scene][name]["upper"])
                            for name in active_by_scene[scene]
                        )
                    ):
                        continue
                    row["cached_valid_replay_redecision"] = True
                    cached_rows.append(row)
                cached_accepted, _cached_valid = pg_outer.evaluate_batch(
                    cached_rows,
                    incumbents[scene],
                    canonical_baseline=state["fresh_baseline"][
                        "incumbents"
                    ][scene],
                    target=alg.TARGET[scene],
                )
                if cached_accepted is not None:
                    row = cached_accepted["row"]
                    evaluations[scene].append(row)
                    first_accepting_wave_by_scene[scene] = {
                        "wave_ranks": [],
                        "selected_rank": int(row.get("rank") or 0),
                        "selected_param_signature": alg.optimizer_params_key(
                            row["params"]
                        ),
                    }
            unresolved_scenes.difference_update(
                first_accepting_wave_by_scene
            )
            maximum_admitted_rank = max(
                (
                    int(row.get("rank") or 0)
                    for rows in rankings.values() for row in rows
                ),
                default=0,
            )
            for rank in range(1, maximum_admitted_rank + 1):
                rankings_for_rank = {
                    scene: rows
                    for scene, rows in rankings.items()
                    if scene in unresolved_scenes and len(rows) >= rank
                }
                if not rankings_for_rank:
                    continue
                assignments, companions = runtime._build_single_candidate_assignments(
                    rankings=rankings_for_rank, params_by_scene=params_by_scene,
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
                        scene not in rankings_for_rank
                        or scene in seen_scenes
                        or alg.optimizer_params_key(row.get("params") or {})
                        != alg.optimizer_params_key(
                            rankings_for_rank[scene][rank - 1]["optimized_params"]
                        )
                    ):
                        raise RuntimeError(
                            f"Algorithm B outer {outer_attempt} rank {rank} "
                            "measurement identity differs"
                        )
                    seen_scenes.add(scene)
                if seen_scenes != set(rankings_for_rank):
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
                    minimum_boundary_rows = minimum_boundary_candidate_rows(batch)
                    if minimum_boundary_rows:
                        if set(minimum_boundary_rows) != set(measured):
                            raise RuntimeError(
                                f"Algorithm B outer {outer_attempt} rank {rank} "
                                "minimum-boundary candidate cardinality differs"
                            )
                        for worker_id, failed_row in minimum_boundary_rows.items():
                            failed_scene = str(failed_row["scene"])
                            failed_rank = int(failed_row.get("rank") or rank)
                            failed_key = alg.optimizer_params_key(
                                failed_row.get("params") or {}
                            )
                            soft_negative_counts[failed_scene][failed_key] = (
                                soft_negative_counts[failed_scene].get(failed_key, 0)
                                + 1
                            )
                            stability = dict(failed_row["resource_stability"])
                            invalid_candidate_attempts.append({
                                "outer_attempt": outer_attempt,
                                "rank": failed_rank,
                                "worker_id": worker_id,
                                "batch_path": str(batch_path),
                                "batch_sha256": alg.sha(batch_path),
                                "elapsed_seconds": float(
                                    failed_row.get("workload_result", {}).get(
                                        "independent_wall_time_s"
                                    ) or stability.get("elapsed_seconds") or 0.0
                                ),
                                "failures": [],
                                "source_reused": source_reused,
                                "affected_scene_measurement_data_used": False,
                                "unaffected_valid_scene_measurement_data_used": False,
                                "unaffected_scenes_used": [],
                                "historical_legacy_reconstruction": False,
                                "direct_failure_scenes": [failed_scene],
                                "derived_unavailable_scenes": [],
                                "model_update_used_for_affected_scenes": False,
                                "accepted_state_credit": False,
                                "classification": (
                                    "candidate_local_full_suite_completed_before_"
                                    "minimum_measurement_boundary"
                                ),
                                "affected_scenes": [failed_scene],
                                "candidate_param_signature": failed_key,
                                "coverage_complete": True,
                                "minimum_measurement_seconds": 300,
                                "hard_pruned_across_future_outer_rounds": False,
                                "candidate_ranking_soft_downweight_count_incremented": True,
                                "candidate_ranking_soft_downweight_scenes": [failed_scene],
                                "pg_real_validation_failure_reason_semantics": True,
                                "semantic_state_unchanged": True,
                            })
                        finish_semantic_batch(rank)
                        continue
                    technical_failure = technical_candidate_failure_batch(
                        batch, batch_path=batch_path,
                    )
                    if technical_failure is not None:
                        failed_worker_ids = set(
                            technical_failure["failed_worker_ids"]
                        )
                        affected_scenes = sorted({
                            str(measured[worker_id].get("scene") or "")
                            for worker_id in failed_worker_ids
                        })
                        if (
                            not affected_scenes
                            or "" in affected_scenes
                            or any(
                                scene not in rankings_for_rank
                                for scene in affected_scenes
                            )
                        ):
                            raise RuntimeError(
                                f"Algorithm B outer {outer_attempt} rank {rank} "
                                "technical failure scene attribution differs"
                            )
                        invalid_candidate_attempts.append({
                            "outer_attempt": outer_attempt,
                            "rank": rank,
                            "batch_path": str(batch_path),
                            "batch_sha256": alg.sha(batch_path),
                            "elapsed_seconds": float(
                                batch.get("elapsed_seconds") or 0.0
                            ),
                            "failures": copy.deepcopy(
                                batch.get("failures") or []
                            ),
                            "source_reused": source_reused,
                            "affected_scene_measurement_data_used": False,
                            "unaffected_valid_scene_measurement_data_used": False,
                            "unaffected_scenes_used": [],
                            "historical_legacy_reconstruction": False,
                            "direct_failure_scenes": affected_scenes,
                            "derived_unavailable_scenes": [],
                            "model_update_used_for_affected_scenes": False,
                            "accepted_state_credit": False,
                            "classification": technical_failure[
                                "classification"
                            ],
                            "affected_scenes": affected_scenes,
                            "technical_failure": copy.deepcopy(
                                technical_failure
                            ),
                            "hard_pruned_across_future_outer_rounds": False,
                            "candidate_ranking_soft_downweight_count_incremented": False,
                            "candidate_ranking_soft_downweight_scenes": [],
                            "pg_candidate_technical_failure_semantics": True,
                            "semantic_state_unchanged": True,
                            "parallel_valid_sibling_rank_remains_reusable": True,
                        })
                        finish_semantic_batch(rank)
                        continue
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
                        if failed_scene not in rankings_for_rank:
                            continue
                        failed_key = alg.optimizer_params_key(
                            rankings_for_rank[failed_scene][rank - 1]["optimized_params"]
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
                    finish_semantic_batch(rank)
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
                        row["candidate_ranking"] = copy.deepcopy(
                            rankings[scene][int(row["rank"]) - 1]
                        )
                        evaluations[scene].append(row)
                # A [2,2,1] wave is not a semantic early-stop boundary.  The
                # complete base Top-K (ranks 1..5), then the complete fallback
                # batch (ranks 6..10), is evaluated exactly once.  Boundary
                # evaluation also runs when the last physical candidate is
                # locally invalid, so prior valid rows remain selectable.
                finish_semantic_batch(rank)
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
                    top_k=(
                        len(evaluations[scene])
                        + sum(
                            scene in (row.get("affected_scenes") or [])
                            for row in invalid_candidate_attempts
                        )
                    ),
                )
                if expected_valid == 0:
                    if rankings[scene]:
                        raise pg_outer.TechnicalRoundFailure(
                            f"{scene} round has no valid replay; semantic state unchanged"
                        )
                    state.setdefault("candidate_space_exhausted", {})[scene] = True
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
                            "attempted_real_top_k": 0,
                            "valid_real_evaluation_count": 0,
                            "invalid_candidate_attempts": scene_invalid_attempts,
                            "invalid_measurement_data_used": False,
                            "evaluations": [],
                            "converged": False,
                            "required_acceptance_tier": "fallback",
                            "algorithm_c_state_read": False,
                            "candidate_space_exhausted_clean": True,
                            "semantic_state_unchanged": True,
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
                    selected_ranking = evaluation_candidate_ranking(
                        scene=scene,
                        row=row,
                        current_rankings=rankings[scene],
                    )
                    row["candidate_ranking"] = copy.deepcopy(selected_ranking)
                    decision = pg_outer.outer_semantic_decision(
                        row,
                        incumbents[scene],
                        canonical_baseline=state["fresh_baseline"][
                            "incumbents"
                        ][scene],
                        target=alg.TARGET[scene],
                    )
                    row["latency_gate_passed"] = None
                    row["non_target_bottleneck_gate_passed"] = not bool(
                        decision["candidate_non_target_bottlenecks"]
                    )
                    row["non_target_bottlenecks"] = copy.deepcopy(
                        decision["candidate_non_target_bottlenecks"]
                    )
                    row["outer_decision"] = decision
                archive = state["valid_replay_archive_by_scene"][scene]
                archived_signatures = {
                    alg.optimizer_params_key(
                        (item.get("candidate_ranking") or {}).get(
                            "optimized_params"
                        ) or item.get("params") or {}
                    )
                    for item in archive
                }
                for row in evaluations[scene]:
                    if row.get("cached_valid_replay_redecision") is True:
                        continue
                    signature = alg.optimizer_params_key(row["params"])
                    if signature not in archived_signatures:
                        archive.append(copy.deepcopy(row))
                        archived_signatures.add(signature)
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
                early_wave = first_accepting_wave_by_scene.get(scene)
                if (
                    early_wave is not None
                    and alg.optimizer_params_key(chosen["params"])
                    != early_wave["selected_param_signature"]
                ):
                    raise RuntimeError(
                        f"{scene} PG wave-time and commit-time selections differ"
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
                    incumbents[scene] = {
                        key: copy.deepcopy(chosen[key])
                        for key in ("db_qps", "p95_ms", "p99_ms", "resource_means")
                    }
                    selected_ranking = copy.deepcopy(
                        chosen.get("candidate_ranking") or {}
                    )
                    if (
                        alg.optimizer_params_key(selected_ranking["optimized_params"])
                        != alg.optimizer_params_key(chosen["params"])
                    ):
                        raise RuntimeError(
                            f"{scene} selected ranking/measurement identity differs"
                        )
                    shrink_active_parameters = pg_outer.moved_shrink_subset(
                        incumbent_params=params_by_scene[scene],
                        candidate_ranking=selected_ranking,
                        active_params=active_by_scene[scene],
                    )
                    # Derive the shrink subset before replacing the incumbent.
                    params_by_scene[scene] = copy.deepcopy(dict(chosen["params"]))
                    ranges_by_scene[scene] = alg.shrink_ranges(
                        ranges_by_scene[scene], params_by_scene[scene],
                        shrink_active_parameters,
                    )
                    state["accepted_rounds"][scene] += 1
                    accepted_credit = 1
                    signature = alg.optimizer_params_key(
                        params_by_scene[scene]
                    )
                    if signature not in state[
                        "previously_accepted_parameter_signatures"
                    ][scene]:
                        state[
                            "previously_accepted_parameter_signatures"
                        ][scene].append(signature)
                    replayed[scene].clear()
                else:
                    state["rejected_outer_attempts"][scene] += 1
                    replayed[scene].update(
                        alg.optimizer_params_key(row["optimized_params"])
                        for row in rankings[scene]
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
                    replayed[scene].clear()
                    state.setdefault(
                        "incumbent_centered_forced_shrinks", {}
                    ).setdefault(scene, 0)
                    state["incumbent_centered_forced_shrinks"][scene] += 1
                convergence = rejection_cycle.convergence_document(
                    ranges=ranges_by_scene[scene],
                    initial_ranges=initial_ranges_by_scene[scene],
                    active_params=active_by_scene[scene],
                )
                state["interval_stable_rounds"][scene] = 0
                state["stable_intervals"][scene] = None
                state["converged"][scene] = bool(convergence["converged"])
                state["acceptance_tier"][scene] = "fallback"
                tier = {
                    "acceptance_tier": "fallback",
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
                    "rejection_cycle": rejection_progress,
                    "converged": state["converged"][scene],
                    "tier_progress": tier,
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

        state["status"] = (
            "COMPLETE" if all(state["converged"].values()) else "NON_CONVERGED"
        )
        state["completion_reason"] = (
            "all_active_log_domain_widths_reached_1pct"
            if state["status"] == "COMPLETE"
            else "candidate_space_or_max_rounds_exhausted"
        )
        state["post_search_stability_confirmation"] = False
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
