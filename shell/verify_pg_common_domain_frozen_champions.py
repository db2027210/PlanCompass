#!/usr/bin/env python3
"""Verify a declared shared-domain frozen-champion replay authority."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple


CONTRACT = "pg_declared_scene_initial_baseline_frozen_champion_replay_v5"
BASELINE_NORMALIZATION_CONTRACT = "initial_baseline_same_worker_normalized_v1"
SCENES = ("job_memory", "tpch_memory", "xp_cpu", "xp_io")
METHODS = ("algorithm_a", "algorithm_b", "algorithm_c", "gptuner", "aqetuner")
ACTIVE_PARAMS = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)
JOB_SCHEDULE_SHA256 = "950fde0959033c8da695fae91aedf56481008ddf5de4c2e781fb00b30fb36513"
IO_BASELINE_SSD_WORKER_SCALING_CONTRACT = (
    "io_target_baseline_ssd_minimum_worker_scaling_v1"
)
CONTAINER_START_PREFLIGHT_CONTRACT = "pg_declared_container_start_preflight_v1"


def valid_method_order(methods: Tuple[str, ...]) -> bool:
    """Accept any non-empty, unique frozen-method subset."""
    return bool(
        methods
        and len(methods) == len(set(methods))
        and set(methods).issubset(METHODS)
    )


def expected_baseline_stages(method_count: int) -> List[str]:
    return [f"common_baseline_{index:02d}" for index in range(method_count + 1)]


def load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require(condition: bool, message: str, errors: List[str]) -> None:
    if not condition:
        errors.append(message)


def finite_float(value: Any) -> Any:
    """Return a finite float, or None for malformed audit input."""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def verify_workload(scene: str, row: Mapping[str, Any], errors: List[str]) -> None:
    label = str(row.get("label") or f"{scene}/unknown")
    completeness = row.get("workload_completeness") or {}
    require(completeness.get("passed") is True, f"{label}: workload completeness failed", errors)
    require(completeness.get("errors") == [], f"{label}: workload completeness errors", errors)
    benchmark = (row.get("hybench") or {}).get("benchmark") or {}
    if scene == "job_memory":
        require(completeness.get("workload") == "job", f"{label}: not JOB", errors)
        require(completeness.get("expected_query_count") == 113, f"{label}: JOB expected !=113", errors)
        require(completeness.get("observed_query_count") == 113, f"{label}: JOB observed !=113", errors)
        require(completeness.get("distinct_query_count") == 113, f"{label}: JOB distinct !=113", errors)
        require(completeness.get("non_unit_query_counts") == [], f"{label}: JOB not exactly once", errors)
        require(benchmark.get("exactly_once") is True, f"{label}: JOB benchmark not exactly once", errors)
        require(
            benchmark.get("fixed_schedule_sha256") == JOB_SCHEDULE_SHA256,
            f"{label}: JOB fixed schedule SHA mismatch",
            errors,
        )
    elif scene == "tpch_memory":
        expected = [f"Q{index}" for index in range(1, 23)]
        require(completeness.get("workload") == "tpch", f"{label}: not TPC-H", errors)
        require(completeness.get("expected_query_sequence") == expected, f"{label}: TPC-H Q1-Q22 mismatch", errors)
        require(completeness.get("observed_worker_count") == 1, f"{label}: TPC-H worker count !=1", errors)
        require(completeness.get("non_unit_query_counts") == [], f"{label}: TPC-H not exactly once", errors)
        require(benchmark.get("exactly_once") is True, f"{label}: TPC-H benchmark not exactly once", errors)
        require(benchmark.get("query_count") == 22, f"{label}: TPC-H query count !=22", errors)
    else:
        require(completeness.get("workload") == "hybench_xp", f"{label}: not native HyBench XP", errors)
        require(completeness.get("observed_iq") == [1, 2, 3, 4, 5, 6], f"{label}: XP IQ coverage mismatch", errors)
        require(completeness.get("observed_at") == [1, 2, 3, 4, 5, 6], f"{label}: XP AT coverage mismatch", errors)
        require(completeness.get("forbidden_query_selection_fields_present") == [], f"{label}: forbidden XP selection", errors)


def verify(root: Path) -> Dict[str, Any]:
    root = root.resolve()
    errors: List[str] = []
    authority_path = root / "authority_manifest.json"
    state_path = root / "state.json"
    contract_path = root / "contract.json"
    restore_path = root / "clean_restore_receipt.json"
    for path in (authority_path, state_path, contract_path, restore_path):
        require(path.is_file(), f"missing file: {path}", errors)
    if errors:
        return {"passed": False, "errors": errors, "root": str(root)}

    authority = load_json(authority_path)
    state = load_json(state_path)
    contract = load_json(contract_path)
    restore = load_json(restore_path)
    stages = contract.get("stages") or []
    contract_scenes = contract.get("scenes") or {}
    selected_scenes = tuple(contract_scenes)
    worker_names_by_scene = {
        scene: tuple(
            sorted(
                str(worker.get("name") or "")
                for worker in (scene_contract.get("workers") or [])
            )
        )
        for scene, scene_contract in contract_scenes.items()
    }
    declared_containers = sorted(
        str(worker.get("container") or "")
        for scene_contract in contract_scenes.values()
        for worker in (scene_contract.get("workers") or [])
    )
    declared_container_ports = {
        str(worker.get("container") or ""): int(worker.get("port") or 5432)
        for scene_contract in contract_scenes.values()
        for worker in (scene_contract.get("workers") or [])
    }
    physical_workers_per_stage = sum(
        len(names) for names in worker_names_by_scene.values()
    )
    require(authority.get("contract") == CONTRACT, "authority contract mismatch", errors)
    require(state.get("contract") == CONTRACT, "state contract mismatch", errors)
    require(contract.get("contract") == CONTRACT, "run contract mismatch", errors)
    require(authority.get("phase") == "complete", "authority is not complete", errors)
    require(state.get("phase") == "complete", "state is not complete", errors)
    require(authority.get("state_sha256") == file_sha256(state_path), "state SHA mismatch", errors)
    require(authority.get("contract_sha256") == state.get("contract_sha256"), "contract SHA mismatch", errors)
    require(authority.get("allowed_mutable_gucs") == list(ACTIVE_PARAMS), "mutable GUC set mismatch", errors)
    require(contract.get("allowed_mutable_gucs") == list(ACTIVE_PARAMS), "contract mutable GUC set mismatch", errors)
    require(authority.get("search_performed") is False, "comparison replay performed search", errors)
    require(authority.get("only_frozen_champions_replayed") is True, "champions were not frozen", errors)
    require(authority.get("manual_mutation_after_start") is False, "manual mutation audit failed", errors)
    require(bool(selected_scenes), "selected scene cohort is empty", errors)
    require(
        set(selected_scenes).issubset(SCENES),
        "selected scene cohort contains an unknown scene",
        errors,
    )
    require(
        authority.get("scene_cohort_size") == len(selected_scenes),
        "authority scene cohort size mismatch",
        errors,
    )
    require(
        contract.get("scene_cohort_size") == len(selected_scenes),
        "contract scene cohort size mismatch",
        errors,
    )
    require(
        contract.get("physical_workers_per_stage") == physical_workers_per_stage,
        "physical workers per stage mismatch",
        errors,
    )
    declared_parallelism = int(contract.get("replay_parallelism_per_scene") or 0)
    require(
        authority.get("replay_parallelism_per_scene") == declared_parallelism,
        "authority replay parallelism mismatch",
        errors,
    )
    require(
        all(len(names) == declared_parallelism for names in worker_names_by_scene.values()),
        "per-scene worker count does not match replay parallelism",
        errors,
    )
    container_start_contract = contract.get("container_start_preflight") or {}
    require(
        container_start_contract.get("contract")
        == CONTAINER_START_PREFLIGHT_CONTRACT,
        "container start preflight contract mismatch",
        errors,
    )
    require(
        container_start_contract.get("after_external_quiet") is True,
        "container start preflight is not ordered after external quiet",
        errors,
    )
    require(
        container_start_contract.get("starts_only_declared_stopped_containers")
        is True,
        "container start preflight scope is not fail-closed",
        errors,
    )
    require(
        container_start_contract.get("postgres_readiness_required") is True,
        "container start preflight does not require PostgreSQL readiness",
        errors,
    )
    require(
        container_start_contract.get("resource_fingerprint_after_start") is True,
        "container resource fingerprint is not ordered after startup",
        errors,
    )
    ssd_qualification_contract = contract.get("ssd_qualification_contract") or {}
    io_worker_scaling = (
        ssd_qualification_contract.get(
            "io_target_baseline_minimum_worker_scaling"
        )
        or {}
    )
    require(
        io_worker_scaling.get("contract")
        == IO_BASELINE_SSD_WORKER_SCALING_CONTRACT,
        "IO baseline SSD worker-scaling contract mismatch",
        errors,
    )
    io_worker_scaling_enabled = io_worker_scaling.get("enabled") is True
    effective_io_baseline_minimum = None
    if io_worker_scaling_enabled:
        source_minimum = finite_float(
            io_worker_scaling.get("source_minimum_ssd_avg_pct")
        )
        calibration_workers = io_worker_scaling.get("source_calibration_workers")
        replay_workers = io_worker_scaling.get("replay_workers")
        scale_factor = finite_float(io_worker_scaling.get("scale_factor"))
        effective_io_baseline_minimum = finite_float(
            io_worker_scaling.get("effective_minimum_ssd_avg_pct")
        )
        require(
            set(selected_scenes) == {"xp_io"},
            "worker-scaled IO SSD gate is not isolated to xp_io",
            errors,
        )
        require(
            isinstance(calibration_workers, int) and calibration_workers > 0,
            "IO SSD calibration worker count is invalid",
            errors,
        )
        require(
            replay_workers == declared_parallelism
            and replay_workers == physical_workers_per_stage,
            "IO SSD replay worker count mismatch",
            errors,
        )
        require(
            isinstance(replay_workers, int)
            and isinstance(calibration_workers, int)
            and 0 < replay_workers <= calibration_workers,
            "IO SSD scaling worker relationship is invalid",
            errors,
        )
        expected_scale = (
            replay_workers / calibration_workers
            if isinstance(replay_workers, int)
            and isinstance(calibration_workers, int)
            and calibration_workers > 0
            else None
        )
        require(
            scale_factor is not None
            and expected_scale is not None
            and math.isclose(scale_factor, expected_scale),
            "IO SSD scale factor mismatch",
            errors,
        )
        require(
            source_minimum is not None
            and effective_io_baseline_minimum is not None
            and expected_scale is not None
            and math.isclose(
                effective_io_baseline_minimum,
                source_minimum * expected_scale,
            ),
            "effective IO baseline SSD minimum does not match scaling formula",
            errors,
        )
        require(
            io_worker_scaling.get("source_config_unchanged") is True,
            "source IO config immutability is not declared",
            errors,
        )
        require(
            io_worker_scaling.get("container_io_measurement_retained") is True,
            "container IO measurement retention is not declared",
            errors,
        )
        require(
            io_worker_scaling.get("host_ssd_upper_bounds_retained") is True,
            "host SSD upper-bound retention is not declared",
            errors,
        )
    quiet_contract = contract.get("pre_stage_ssd_quiet") or {}
    require(bool(quiet_contract.get("device")), "pre-stage SSD device is missing", errors)
    require(
        float(quiet_contract.get("maximum_util_pct") or 0.0) > 0.0,
        "pre-stage SSD quiet threshold is invalid",
        errors,
    )
    external_guard_contract = contract.get("external_workload_guard") or {}
    require(
        external_guard_contract.get("contract")
        == "pg_external_workload_contamination_guard_v3",
        "external workload guard contract mismatch",
        errors,
    )
    require(
        external_guard_contract.get("fail_closed") is True,
        "external workload guard is not fail-closed",
        errors,
    )
    require(
        external_guard_contract.get("standalone_backend_min_count") == 2,
        "external workload standalone backend count policy mismatch",
        errors,
    )
    require(
        external_guard_contract.get("standalone_backend_state_persistence_s") == 2.0,
        "external workload standalone backend persistence policy mismatch",
        errors,
    )
    post_restore_quiet_contract = contract.get("post_restore_ssd_quiet") or {}
    require(
        post_restore_quiet_contract.get("contract")
        == "pg_common_domain_post_restore_ssd_quiet_v1",
        "post-restore SSD quiet contract mismatch",
        errors,
    )
    method_order = contract.get("method_order") or []
    methods = tuple(method_order)
    brackets = contract.get("method_baseline_brackets") or {}
    require(
        contract.get("baseline_normalization_contract")
        == BASELINE_NORMALIZATION_CONTRACT,
        "baseline normalization contract mismatch",
        errors,
    )
    require(
        authority.get("baseline_normalization_contract")
        == BASELINE_NORMALIZATION_CONTRACT,
        "authority baseline normalization contract mismatch",
        errors,
    )
    require(valid_method_order(methods), "method order is not a valid randomized cohort", errors)
    require(
        len(stages) == 2 * len(methods) + 1,
        "stage count does not match method count",
        errors,
    )
    require(
        stages[::2] == expected_baseline_stages(len(methods)),
        "bracketed baseline stages mismatch",
        errors,
    )
    require(stages[1::2] == method_order, "interleaved method stage order mismatch", errors)
    require(set(brackets) == set(methods), "method bracket coverage mismatch", errors)
    for index, method in enumerate(method_order):
        require(
            brackets.get(method)
            == [f"common_baseline_{index:02d}", f"common_baseline_{index + 1:02d}"],
            f"{method}: adjacent baseline bracket mismatch",
            errors,
        )
    require(authority.get("method_baseline_brackets") == brackets, "authority bracket map mismatch", errors)
    require(set(state.get("stage_results") or {}) == set(stages), "state stage coverage mismatch", errors)
    runner_path = Path(str(contract.get("runner_path") or ""))
    guard_path = Path(str(contract.get("fixed_guc_guard_path") or ""))
    require(runner_path.is_file(), "runner source is missing", errors)
    require(guard_path.is_file(), "fixed GUC guard source is missing", errors)
    if runner_path.is_file():
        require(contract.get("runner_sha256") == file_sha256(runner_path), "runner SHA mismatch", errors)
    if guard_path.is_file():
        require(contract.get("fixed_guc_guard_sha256") == file_sha256(guard_path), "fixed GUC guard SHA mismatch", errors)
    runtime_sources = contract.get("runtime_source_sha256") or {}
    require(bool(runtime_sources), "runtime source hashes are missing", errors)
    require(
        authority.get("runtime_source_sha256") == runtime_sources,
        "authority runtime source hashes mismatch",
        errors,
    )
    for raw_path, expected_sha256 in runtime_sources.items():
        source_path = Path(str(raw_path))
        require(source_path.is_file(), f"runtime source is missing: {source_path}", errors)
        if source_path.is_file():
            require(
                file_sha256(source_path) == expected_sha256,
                f"runtime source SHA mismatch: {source_path}",
                errors,
            )
    database_cohort_ref = authority.get("database_execution_cohort") or {}
    database_cohort_path = Path(str(database_cohort_ref.get("path") or ""))
    require(database_cohort_path.is_file(), "database execution cohort receipt is missing", errors)
    if database_cohort_path.is_file():
        database_cohort = load_json(database_cohort_path)
        require(database_cohort.get("passed") is True, "database execution cohort failed", errors)
        require(
            database_cohort_ref.get("receipt_sha256")
            == database_cohort.get("receipt_sha256"),
            "database execution cohort receipt SHA mismatch",
            errors,
        )
        database_scenes = database_cohort.get("scenes") or {}
        database_plans = database_cohort.get("plans") or {}
        require(set(database_scenes) == set(selected_scenes), "database cohort scene coverage mismatch", errors)
        require(set(database_plans) == set(selected_scenes), "database plan scene coverage mismatch", errors)
        for scene in selected_scenes:
            rows = database_scenes.get(scene) or {}
            expected_workers = set(worker_names_by_scene.get(scene) or ())
            require(set(rows) == expected_workers, f"{scene}: database worker coverage mismatch", errors)
            semantic_hashes = {
                str((rows.get(worker) or {}).get("semantic_sha256") or "")
                for worker in expected_workers
            }
            require(
                len(semantic_hashes) == 1 and "" not in semantic_hashes,
                f"{scene}: database worker fingerprints differ",
                errors,
            )
            plan_rows = database_plans.get(scene) or {}
            require(set(plan_rows) == expected_workers, f"{scene}: plan worker coverage mismatch", errors)
            expected_points: Mapping[str, Any] = {}
            for worker in sorted(expected_workers):
                points = (plan_rows.get(worker) or {}).get("points") or {}
                if not expected_points:
                    expected_points = points
                    continue
                require(set(points) == set(expected_points), f"{scene}: plan point coverage mismatch", errors)
                for label in set(points).intersection(expected_points):
                    require(
                        (points.get(label) or {}).get("plan_signatures_sha256")
                        == (expected_points.get(label) or {}).get("plan_signatures_sha256"),
                        f"{scene}/{label}: plan worker fingerprints differ",
                        errors,
                    )
    container_start_ref = authority.get("container_start_preflight") or {}
    require(
        container_start_ref == (state.get("container_start_preflight") or {}),
        "container start preflight authority/state mismatch",
        errors,
    )
    container_start_path = Path(str(container_start_ref.get("path") or ""))
    require(
        container_start_path.is_file(),
        "container start preflight receipt is missing",
        errors,
    )
    if container_start_path.is_file():
        container_start = load_json(container_start_path)
        require(
            container_start.get("contract")
            == CONTAINER_START_PREFLIGHT_CONTRACT,
            "container start preflight receipt contract mismatch",
            errors,
        )
        require(
            container_start.get("passed") is True,
            "container start preflight did not pass",
            errors,
        )
        require(
            container_start_ref.get("receipt_sha256")
            == container_start.get("receipt_sha256"),
            "container start preflight receipt SHA mismatch",
            errors,
        )
        require(
            container_start.get("declared_containers") == declared_containers,
            "container start preflight declared set mismatch",
            errors,
        )
        require(
            container_start.get("ready_containers") == declared_containers,
            "container start preflight readiness coverage mismatch",
            errors,
        )
        require(
            container_start.get("postgres_ports") == declared_container_ports,
            "container start preflight PostgreSQL port map mismatch",
            errors,
        )
        started_containers = set(container_start.get("started_containers") or [])
        require(
            started_containers.issubset(set(declared_containers)),
            "container start preflight started an undeclared container",
            errors,
        )
        require(
            container_start.get("starts_only_declared_containers") is True,
            "container start preflight scope receipt mismatch",
            errors,
        )
    physical_count = int(authority.get("physical_workload_invocations") or 0)
    valid_count = int(authority.get("valid_replays") or 0)
    technical_count = int(authority.get("technical_failures") or 0)
    comparison_valid_count = int(authority.get("comparison_valid_replays") or 0)
    discarded_valid_count = int(authority.get("discarded_valid_replays") or 0)
    require(
        comparison_valid_count == len(stages) * physical_workers_per_stage,
        "comparison-valid replay count does not match stage count",
        errors,
    )
    require(
        valid_count >= comparison_valid_count,
        "total valid replay count is below the dynamic comparison requirement",
        errors,
    )
    require(physical_count == valid_count + technical_count, "physical != valid + technical", errors)
    require(discarded_valid_count == valid_count - comparison_valid_count, "discarded-valid count mismatch", errors)
    require(
        authority.get("technical_failures_excluded_from_comparisons") is True,
        "technical-failure comparison isolation is not declared",
        errors,
    )
    require(state.get("comparison_valid_replays") == comparison_valid_count, "state comparison-valid mismatch", errors)
    require(state.get("discarded_valid_replays") == discarded_valid_count, "state discarded-valid mismatch", errors)
    require(state.get("physical_workload_invocations") == physical_count, "state physical mismatch", errors)
    require(state.get("valid_replays") == valid_count, "state valid mismatch", errors)
    require(state.get("technical_failures") == technical_count, "state technical mismatch", errors)
    require(
        authority.get("clean_restore_receipt_sha256") == restore.get("receipt_sha256"),
        "clean restore semantic SHA mismatch",
        errors,
    )
    require(restore.get("physical_workload_invocations") == 0, "restore receipt contains workload", errors)
    require(isinstance(authority.get("fixed_container_resource_sha256"), str), "missing container resource SHA", errors)
    for scene in selected_scenes:
        scene_contract = contract_scenes.get(scene) or {}
        config_path = Path(str(scene_contract.get("config_path") or ""))
        require(config_path.is_file(), f"{scene}: config source missing", errors)
        if config_path.is_file():
            require(scene_contract.get("config_sha256") == file_sha256(config_path), f"{scene}: config SHA mismatch", errors)
            if scene == "xp_io" and io_worker_scaling_enabled:
                source_config = load_json(config_path)
                source_gate = (
                    ((source_config.get("algorithm_c") or {}).get(
                        "ssd_replay_qualification"
                    ))
                    or {}
                )
                source_config_minimum = finite_float(
                    source_gate.get("io_target_baseline_minimum_ssd_avg_pct")
                )
                require(
                    io_worker_scaling.get("source_config_path")
                    == str(config_path),
                    "IO SSD scaling source config path mismatch",
                    errors,
                )
                require(
                    source_config_minimum is not None
                    and math.isclose(
                        source_config_minimum,
                        finite_float(io_worker_scaling.get(
                            "source_minimum_ssd_avg_pct"
                        )) or math.nan,
                    ),
                    "IO SSD scaling source minimum differs from hashed config",
                    errors,
                )
        workers = scene_contract.get("workers") or []
        expected_worker_names = list(worker_names_by_scene.get(scene) or ())
        require(bool(expected_worker_names), f"{scene}: worker set is empty", errors)
        require(
            len(expected_worker_names) == len(set(expected_worker_names))
            and all(expected_worker_names),
            f"{scene}: worker names are invalid",
            errors,
        )
        sources = scene_contract.get("methods") or {}
        require(set(sources) == set(methods), f"{scene}: frozen source coverage mismatch", errors)
        for method in methods:
            source = sources.get(method) or {}
            source_path = Path(str(source.get("authority_path") or ""))
            require(source_path.is_file(), f"{scene}/{method}: source authority missing", errors)
            if source_path.is_file():
                require(source.get("authority_file_sha256") == file_sha256(source_path), f"{scene}/{method}: source authority SHA mismatch", errors)
            require(set(source.get("params") or {}) == set(ACTIVE_PARAMS), f"{scene}/{method}: frozen params mismatch", errors)

    for stage in stages:
        result = (state.get("stage_results") or {}).get(stage) or {}
        require(result.get("all_valid") is True, f"{stage}: not all valid", errors)
        external_guard = result.get("external_workload_guard") or {}
        require(
            external_guard.get("contract")
            == "pg_external_workload_contamination_guard_v3",
            f"{stage}: external workload guard receipt missing",
            errors,
        )
        require(
            external_guard.get("standalone_backend_min_count") == 2,
            f"{stage}: standalone backend count policy mismatch",
            errors,
        )
        require(
            external_guard.get("standalone_backend_state_persistence_s") == 2.0,
            f"{stage}: standalone backend persistence policy mismatch",
            errors,
        )
        require(
            external_guard.get("passed") is True,
            f"{stage}: external workload contamination detected",
            errors,
        )
        require(
            external_guard.get("detections") == [],
            f"{stage}: external workload detections are non-empty",
            errors,
        )
        scenes = result.get("scene_results") or {}
        require(set(scenes) == set(selected_scenes), f"{stage}: scene coverage mismatch", errors)
        for scene in selected_scenes:
            scene_result = scenes.get(scene) or {}
            expected_worker_names = list(worker_names_by_scene.get(scene) or ())
            expected_worker_count = len(expected_worker_names)
            require(scene_result.get("valid") is True, f"{stage}/{scene}: invalid", errors)
            require(
                scene_result.get("physical_invocations") == expected_worker_count,
                f"{stage}/{scene}: physical worker count mismatch",
                errors,
            )
            require(scene_result.get("technical_failures") == 0, f"{stage}/{scene}: technical failure", errors)
            paths = [Path(str(item)) for item in scene_result.get("row_paths") or []]
            require(len(paths) == expected_worker_count, f"{stage}/{scene}: row path count mismatch", errors)
            worker_keys: List[str] = []
            for path in paths:
                require(path.is_file(), f"{stage}/{scene}: missing row {path}", errors)
                if not path.is_file():
                    continue
                row = load_json(path)
                worker_keys.append(str(row.get("worker_key") or ""))
                require(row.get("status") == "ok", f"{path}: status is not ok", errors)
                require(row.get("technical_failure") is False, f"{path}: technical failure", errors)
                require(row.get("valid_replay_count") == 1, f"{path}: valid count !=1", errors)
                require(row.get("physical_workload_invocations") == 1, f"{path}: physical count !=1", errors)
                require(set(row.get("params") or {}) == set(ACTIVE_PARAMS), f"{path}: params are not seven cost GUCs", errors)
                ssd_qualification = row.get("ssd_qualification") or {}
                require(
                    ssd_qualification.get("passed") is True,
                    f"{path}: SSD qualification did not pass",
                    errors,
                )
                if scene == "xp_io" and io_worker_scaling_enabled:
                    thresholds = ssd_qualification.get("thresholds") or {}
                    require(
                        thresholds.get("global_ssd_stable_window_enforced")
                        is True,
                        f"{path}: XP-I/O host SSD window was not enforced",
                        errors,
                    )
                    observed_minimum = finite_float(
                        thresholds.get("minimum_ssd_avg_pct")
                    )
                    if stage.startswith("common_baseline_"):
                        require(
                            observed_minimum is not None
                            and effective_io_baseline_minimum is not None
                            and math.isclose(
                                observed_minimum,
                                effective_io_baseline_minimum,
                            ),
                            f"{path}: effective XP-I/O baseline SSD minimum mismatch",
                            errors,
                        )
                    else:
                        require(
                            thresholds.get("minimum_ssd_avg_pct") is None,
                            f"{path}: candidate unexpectedly has a baseline SSD floor",
                            errors,
                        )
                verify_workload(scene, row, errors)
            require(sorted(worker_keys) == expected_worker_names, f"{stage}/{scene}: worker set mismatch", errors)
        for receipt_group in ("pre_guc_receipts", "post_guc_receipts"):
            receipts = result.get(receipt_group) or {}
            require(set(receipts) == set(selected_scenes), f"{stage}: {receipt_group} scene coverage mismatch", errors)
            for scene in selected_scenes:
                receipt = receipts.get(scene) or {}
                path = Path(str(receipt.get("path") or ""))
                require(path.is_file(), f"{stage}/{scene}: missing {receipt_group}", errors)
                if path.is_file():
                    require(
                        receipt.get("receipt_sha256") == load_json(path).get("receipt_sha256"),
                        f"{stage}/{scene}: {receipt_group} semantic SHA mismatch",
                        errors,
                    )
        for receipt_name in (
            "pre_container_resource_receipt",
            "post_container_resource_receipt",
            "pre_stage_ssd_quiet_receipt",
            "post_restore_ssd_quiet_receipt",
        ):
            receipt = result.get(receipt_name) or {}
            path = Path(str(receipt.get("path") or ""))
            require(path.is_file(), f"{stage}: missing {receipt_name}", errors)
            if path.is_file():
                payload = load_json(path)
                require(
                    receipt.get("receipt_sha256") == payload.get("receipt_sha256"),
                    f"{stage}: {receipt_name} semantic SHA mismatch",
                    errors,
                )
                if receipt_name in {
                    "pre_stage_ssd_quiet_receipt",
                    "post_restore_ssd_quiet_receipt",
                }:
                    require(
                        payload.get("passed") is True,
                        f"{stage}: SSD quiet gate failed for {receipt_name}",
                        errors,
                    )

    stage_attempts = state.get("stage_attempts") or {}
    require(set(stage_attempts) == set(stages), "stage-attempt coverage mismatch", errors)
    attempts_physical = 0
    attempts_technical = 0
    for stage in stages:
        attempts = stage_attempts.get(stage) or []
        require(bool(attempts), f"{stage}: no recorded attempts", errors)
        successful_attempts = [attempt for attempt in attempts if attempt.get("all_valid") is True]
        require(len(successful_attempts) == 1, f"{stage}: successful attempt count !=1", errors)
        require(
            bool(successful_attempts)
            and successful_attempts[0] == (state.get("stage_results") or {}).get(stage),
            f"{stage}: comparison result is not the unique successful attempt",
            errors,
        )
        for attempt in attempts:
            for scene_result in (attempt.get("scene_results") or {}).values():
                attempts_physical += int(scene_result.get("physical_invocations") or 0)
                attempts_technical += int(scene_result.get("technical_failures") or 0)
    require(attempts_physical == physical_count, "attempt physical accounting mismatch", errors)
    require(attempts_technical == technical_count, "attempt technical accounting mismatch", errors)

    comparisons = authority.get("comparisons") or {}
    require(set(comparisons) == set(selected_scenes), "comparison scene coverage mismatch", errors)
    for scene in selected_scenes:
        comparison = comparisons.get(scene) or {}
        require(
            comparison.get("baseline_normalization_contract")
            == BASELINE_NORMALIZATION_CONTRACT,
            f"{scene}: comparison normalization contract mismatch",
            errors,
        )
        baseline_qps_by_stage = comparison.get("baseline_qps_by_stage") or {}
        require(
            set(baseline_qps_by_stage) == set(stages[::2]),
            f"{scene}: baseline stage QPS coverage mismatch",
            errors,
        )
        method_results = comparison.get("methods") or {}
        require(set(method_results) == set(methods), f"{scene}: method comparison coverage mismatch", errors)
        for method in methods:
            method_result = method_results.get(method) or {}
            require(set(method_result.get("params") or {}) == set(ACTIVE_PARAMS), f"{scene}/{method}: params mismatch", errors)
            require(
                [
                    method_result.get("paired_baseline_before_stage"),
                    method_result.get("paired_baseline_after_stage"),
                ]
                == brackets.get(method),
                f"{scene}/{method}: result bracket mismatch",
                errors,
            )
            require(
                method_result.get("qps_reference_stage") == "common_baseline_00",
                f"{scene}/{method}: QPS reference is not the initial baseline",
                errors,
            )
            require(
                method_result.get("qps_reference") == "initial_same_worker_baseline",
                f"{scene}/{method}: same-worker baseline normalization missing",
                errors,
            )

    return {
        "contract": "pg_common_domain_frozen_champion_verification_v1",
        "root": str(root),
        "passed": not errors,
        "errors": errors,
        "authority_sha256": file_sha256(authority_path),
        "state_sha256": file_sha256(state_path),
        "contract_sha256": file_sha256(contract_path),
        "clean_restore_receipt_file_sha256": file_sha256(restore_path),
        "clean_restore_receipt_semantic_sha256": restore.get("receipt_sha256"),
        "physical_workload_invocations": authority.get("physical_workload_invocations"),
        "valid_replays": authority.get("valid_replays"),
        "technical_failures": authority.get("technical_failures"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = verify(args.root)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="", flush=True)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
