#!/usr/bin/env python3
"""Run the isolated coordinate-grid double loop for CPU/Memory/IO/None.

Live mode reuses the project's workload endpoint probe as workload pruning and
the real PostgreSQL validation harness as a measurement transport. Before the
optimizer starts, current-run real calibration rows must pass all-resource model
error gates and shared-Score Top10/Top15 ordering gates. In every outer round,
the small loop scans a 15-point grid one coordinate at a time and then probes
2--4 dimensional combinations. QPS is excluded from both predicted and real
candidate scores.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import copy
import json
import math
import re
import sys
import time
from dataclasses import asdict, is_dataclass, replace
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from binary_plan_resource_tuner import (  # noqa: E402
    COST_PARAMS,
    PlanFeatureProbe,
    endpoint_probe_task,
    load_config,
    param_ranges,
    pg_conn_from_worker,
    psql_query,
    run_real_validation_safe,
    validation_workers,
    wait_for_postgres,
    workload_queries_with_sampling,
)
from double_loop_scenario import (  # noqa: E402
    FreshBaselineObjective,
    FreshExperience,
    FreshExperienceModel,
    FreshModelQualityThresholds,
    ScenarioMeasurement,
    measurement_from_payload,
)
from double_loop_grid_optimizer import (  # noqa: E402
    DoubleLoopGridOptimizer,
    GridInnerLoopConfig,
)
from double_loop_search_common import (  # noqa: E402
    DoubleLoopResult,
    OuterLoopConfig,
    ParameterRange,
)


SCENARIOS = ("cpu", "memory", "io", "none")
HIGH_PRIORITY_SCENARIOS = ("cpu", "memory", "io")
DEFAULT_WORKER_INDEX = {"cpu": 0, "memory": 1, "io": 0, "none": 2}
POSTGRESQL_DEFAULT_COST_PARAMS = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(json_value(value), ensure_ascii=False, sort_keys=True) + "\n")


def json_value(value: Any) -> Any:
    if is_dataclass(value):
        return {key: json_value(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def final_confirmation_is_independent(raw: Mapping[str, Any]) -> bool:
    """Return whether a final measurement is not an exact-vector cache hit."""
    resumed = raw.get("resumed_candidate_replay")
    if isinstance(resumed, Mapping) and resumed.get("used_new_real_replay") is False:
        return False
    return raw.get("used_real_replay") is not False


def subset_audit_record(point: Any) -> Dict[str, Any]:
    """Stable public audit schema for raw and deduplicated subset points."""
    return {
        "subset_id": point.subset_id,
        "active_subset": list(point.active_subset),
        "fixed_params": json_value(point.fixed_params),
        "start_params": json_value(point.start_params),
        "current_bounds": {
            name: [bound.min_value, bound.max_value]
            for name, bound in point.current_bounds.items()
        },
        "iterations": point.iterations,
        "converged": point.converged,
        "final_params": json_value(point.params),
        "estimated_db_qps": point.estimated_db_qps,
        "estimated_cpu": point.estimated_cpu,
        "estimated_memory": point.estimated_memory,
        "estimated_io": point.estimated_io,
        "estimated_objective": (
            point.estimated_score if math.isfinite(point.estimated_score) else None
        ),
        "equal_score_ranking_tiebreak": json_value(
            getattr(point, "ranking_tiebreak_details", {})
        ),
        "iteration_trace": json_value(point.iteration_trace),
        "failure_reason": point.failure_reason,
        "source_subsets": [list(subset) for subset in point.source_subsets],
    }


def real_validation_audit_record(
    point: Any,
    objective: FreshBaselineObjective,
) -> Dict[str, Any]:
    """Add small-score and outer semantic diagnostics to a real replay row."""
    record = json_value(point)
    if point.failure_reason or not point.resource:
        record["gate_status"] = "validation_failure"
        record["score_decision"] = None
        record["outer_acceptance_decision"] = None
        record["initial_baseline_direction_diagnostic"] = None
        record["final_goal_diagnostic"] = None
        return record

    measurement = measurement_from_payload(point.resource, point.raw_result)
    score_decision = objective.decide(measurement)
    initial_baseline_direction = objective.outer_decide(measurement)
    record["gate_status"] = (
        "outer_accepted"
        if point.outer_accepted
        else point.outer_acceptance_reason
    )
    record["score_decision"] = json_value(score_decision)
    record["shared_resource_score"] = json_value(
        objective.resource_score_details(measurement)
    )
    record["outer_acceptance_decision"] = (
        json_value(point.outer_acceptance_details)
        if point.outer_acceptance_details
        else None
    )
    record["initial_baseline_direction_diagnostic"] = json_value(
        initial_baseline_direction
    )
    record["final_goal_diagnostic"] = json_value(
        objective.final_decide(measurement)
    )
    return record


def measurement_invalid_reason(
    measurement: ScenarioMeasurement,
    raw: Mapping[str, Any],
) -> str:
    """Reject finite-looking transport placeholders that are not real samples."""
    returncode = raw.get("returncode")
    if isinstance(returncode, (int, float)) and int(returncode) != 0:
        return f"real workload exited with non-zero returncode {int(returncode)}"
    process_error = str(raw.get("process_error") or "").strip()
    if process_error:
        return f"real workload process error: {process_error}"
    for field in ("runtime_errors", "db_errors"):
        errors = raw.get(field)
        if isinstance(errors, (list, tuple)) and errors:
            return f"real workload {field}: {errors[0]}"
    if measurement.db_qps <= 0.0:
        return "DB QPS must be positive"
    if measurement.cpu <= 0.0:
        return "CPU measurement must be positive"
    if measurement.memory <= 0.0:
        return "Memory measurement must be positive"
    if measurement.io < 0.0:
        return "IO measurement must be non-negative"
    samples = raw.get("resource_samples")
    if isinstance(samples, Mapping):
        required = ("cpu_cores", "memory_bytes", "io_bps")
        missing = [name for name in required if int(samples.get(name) or 0) <= 0]
        if missing:
            return "missing real resource samples: " + ",".join(missing)
    return ""


def config_for_worker(cfg: Dict[str, Any], worker: Mapping[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(cfg)
    postgres = result.setdefault("postgres", {})
    for key in ("host", "port", "user", "password", "dbname"):
        if worker.get(key) is not None:
            postgres[key] = worker[key]
    return result


def config_flag(value: Any, default: bool = False) -> bool:
    """Parse a JSON/config boolean without inheriting legacy gate helpers."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    return default


def isolated_fresh_runtime_config(
    scenario_cfg: Mapping[str, Any],
    runtime_cfg: Mapping[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Build the effective config without exposing legacy optimization state.

    The scenario file remains a workload/physical-environment description.  The
    new optimizer receives PostgreSQL defaults, legal bounds, a plan-only
    workload-pruning probe, and the VM validation transport.  Teacher points,
    sensitivity values, learned resource models, historical ranking labels and
    all legacy candidate gates are removed before any new-algorithm component is
    constructed.
    """
    result = copy.deepcopy(dict(scenario_cfg))
    removed: List[str] = []
    for key in (
        "_teacher_params",
        "_teacher_source",
        "sensitivity_table",
        "candidate_gate",
        "optimizer",
        "bottleneck_calibration",
        "resource_iteration_reports",
        "final_evaluation",
    ):
        if key in result:
            result.pop(key, None)
            removed.append(key)

    result["initial_params"] = dict(POSTGRESQL_DEFAULT_COST_PARAMS)

    raw_scoring = result.get("scoring")
    raw_scoring = raw_scoring if isinstance(raw_scoring, Mapping) else {}
    result["scoring"] = {
        "overload_threshold": float(raw_scoring.get("overload_threshold", 0.8)),
        "resource_target_ranges": copy.deepcopy(
            raw_scoring.get("resource_target_ranges") or {}
        ),
    }

    metrics = copy.deepcopy(result.get("metrics") or {})
    estimator = metrics.get("plan_estimator")
    estimator = estimator if isinstance(estimator, dict) else {}
    if estimator.pop("model_retraining", None) is not None:
        removed.append("metrics.plan_estimator.model_retraining")
    for resource_name in ("cpu", "memory", "io"):
        resource_cfg = estimator.get(resource_name)
        if not isinstance(resource_cfg, dict):
            continue
        if resource_cfg.pop("learned_model", None) is not None:
            removed.append(f"metrics.plan_estimator.{resource_name}.learned_model")
    metrics["plan_estimator"] = estimator
    result["metrics"] = metrics

    runtime_bpt = runtime_cfg.get("binary_plan_tuning")
    runtime_bpt = runtime_bpt if isinstance(runtime_bpt, Mapping) else {}
    runtime_validation = runtime_bpt.get("validation")
    runtime_validation = (
        runtime_validation if isinstance(runtime_validation, Mapping) else {}
    )
    result["binary_plan_tuning"] = {
        key: copy.deepcopy(runtime_bpt[key])
        for key in (
            "workload_name",
            "workload_context",
            "workload",
            "endpoint_screening",
        )
        if key in runtime_bpt
    }
    result["binary_plan_tuning"]["validation"] = {
        key: copy.deepcopy(runtime_validation[key])
        for key in ("hybench_xp", "vm_replay", "docker_runtime", "docker_workers")
        if key in runtime_validation
    }
    audit = {
        "event": "isolated_fresh_runtime_config",
        "postgresql_default_start_params": dict(POSTGRESQL_DEFAULT_COST_PARAMS),
        "removed_forbidden_fields": sorted(removed),
        "historical_teacher_loaded": False,
        "historical_best_params_loaded": False,
        "historical_sensitivity_loaded": False,
        "historical_resource_model_loaded": False,
        "legacy_candidate_gate_loaded": False,
        "legacy_accepted_rejected_gate_loaded": False,
        "transport_sections_copied": sorted(
            result["binary_plan_tuning"]["validation"]
        ),
    }
    return result, audit


def vm_replay_probe_worker(
    cfg: Mapping[str, Any],
    scenario: str,
) -> Tuple[Dict[str, Any], Path]:
    """Build the direct-PG worker used only by fresh workload pruning.

    Real candidate validation remains owned by ``run_vm_replay_validation``.
    The small-loop workload probe only needs the same VM's PostgreSQL endpoint,
    so it must not masquerade as a Docker worker or pass a container gate.
    """
    validation = (((cfg.get("binary_plan_tuning") or {}).get("validation") or {}))
    replay = validation.get("vm_replay") if isinstance(validation, Mapping) else {}
    replay = replay if isinstance(replay, Mapping) else {}
    worker_paths = replay.get("worker_configs")
    worker_paths = worker_paths if isinstance(worker_paths, Mapping) else {}
    raw_path = worker_paths.get(scenario)
    if not raw_path:
        raise RuntimeError(f"vm_replay worker config is missing for {scenario}")
    worker_path = Path(str(raw_path))
    if not worker_path.is_absolute():
        worker_path = ROOT / worker_path
    worker_path = worker_path.resolve()
    worker_cfg = load_config(str(worker_path))
    override = worker_cfg.get("config_override") or {}
    postgres = override.get("postgres") if isinstance(override, Mapping) else {}
    postgres = dict(postgres) if isinstance(postgres, Mapping) else {}
    base_pg = cfg.get("postgres") or {}
    for key in ("host", "port", "user", "password", "dbname"):
        if postgres.get(key) is None and isinstance(base_pg, Mapping) and base_pg.get(key) is not None:
            postgres[key] = base_pg[key]
    name = str(worker_cfg.get("worker_id") or worker_cfg.get("vm_name") or scenario)
    vm_resource = worker_cfg.get("vm_resource")
    vm_resource = vm_resource if isinstance(vm_resource, Mapping) else {}
    explicit_capacity = override.get("resource_capacity")
    explicit_capacity = (
        dict(explicit_capacity) if isinstance(explicit_capacity, Mapping) else {}
    )
    if not explicit_capacity:
        read_mib_s = float(vm_resource.get("read_mb") or 0.0)
        write_mib_s = float(vm_resource.get("write_mb") or 0.0)
        derived_capacity = {
            "cpu": float(vm_resource.get("cpu") or 0.0),
            "memory": float(vm_resource.get("memory_gb") or 0.0),
            "io": read_mib_s + write_mib_s,
        }
        explicit_capacity = {
            resource: value
            for resource, value in derived_capacity.items()
            if math.isfinite(value) and value > 0.0
        }
    postgres.update(
        {
            "name": name,
            "vm_name": str(worker_cfg.get("vm_name") or name),
            "worker_config": str(worker_path),
            "transport": "vm_replay",
            "capability": copy.deepcopy(override.get("capability") or {}),
            "resource_capability": copy.deepcopy(
                override.get("resource_capability") or {}
            ),
            "resource_capacity": copy.deepcopy(explicit_capacity),
            "resource_capacity_source": (
                "config_override.resource_capacity"
                if override.get("resource_capacity")
                else "worker_config.vm_resource"
            ),
        }
    )
    if not postgres.get("host") or not postgres.get("port"):
        raise RuntimeError(f"vm_replay worker {name!r} has no PostgreSQL endpoint")
    return postgres, worker_path


def wait_for_primary_postgres(
    pg: Any,
    *,
    timeout_s: float,
    query_func: Callable[..., str] = psql_query,
    sleep_func: Callable[[float], None] = time.sleep,
    clock_func: Callable[[], float] = time.time,
) -> None:
    """Wait until PostgreSQL accepts writes after a replay snapshot restore.

    ``SELECT 1`` succeeds on a hot standby, but a restored worker can still be
    replaying WAL and rejects the benchmark's session setup with "database
    system is in recovery mode".  Formal replays must therefore wait for a
    primary-ready response rather than mere connection readiness.
    """
    deadline = clock_func() + max(1.0, float(timeout_s))
    last_status = ""
    while clock_func() < deadline:
        try:
            value = query_func(
                pg,
                "SELECT pg_is_in_recovery()::text;",
                timeout_s=min(10.0, max(1.0, deadline - clock_func())),
            ).strip().lower()
            if value in {"f", "false", "0", "off"}:
                return
            last_status = f"pg_is_in_recovery()={value or '<empty>'}"
        except Exception as exc:
            last_status = str(exc)
        sleep_func(2.0)
    raise RuntimeError(f"PostgreSQL did not become primary-ready: {last_status}")


def run_generic_vm_replay_validation(
    worker_path: Path,
    scenario: str,
    params: Mapping[str, float],
    *,
    output_dir: Path,
    label: str,
    restore_timeout_s: float,
    drop_cache_timeout_s: float,
    workload_timeout_s: float,
    postgres_ready_timeout_s: float,
    force_new_replay: bool,
) -> Dict[str, Any]:
    """Run an arbitrary benchmark command on a target-owned replay VM."""
    shell_dir = ROOT / "shell"
    if str(shell_dir) not in sys.path:
        sys.path.insert(0, str(shell_dir))
    from bonect_parallel_vm_replay import (  # type: ignore
        drop_worker_cache,
        live_disk_throttle,
        resolve_restore_io_boost,
        restore_worker_database,
        rootless_qmp_disk_throttle,
    )
    from bonect_real_validate_candidates import (  # type: ignore
        load_worker_config,
        scenario_context,
        validate_candidate,
    )

    worker_cfg = load_worker_config(str(worker_path))
    context = scenario_context(scenario, worker_cfg)
    recovery: Dict[str, Any] = {"worker_config": str(worker_path)}
    vm_resource = dict(worker_cfg.get("vm_resource") or {})
    boost = resolve_restore_io_boost(worker_cfg, scenario)

    def apply_throttle(read_mb: float, write_mb: float) -> Dict[str, Any]:
        qmp_socket = str(vm_resource.get("qmp_socket") or "")
        if qmp_socket:
            return rootless_qmp_disk_throttle(
                qmp_socket,
                str(vm_resource.get("qmp_disk_device") or "drive0"),
                read_mb=read_mb,
                write_mb=write_mb,
                verify=True,
            )
        return live_disk_throttle(
            str(
                vm_resource.get("vm_name")
                or worker_cfg.get("vm_name")
                or worker_cfg.get("worker_id")
            ),
            str(vm_resource.get("disk_dev") or "vda"),
            read_mb=int(read_mb),
            write_mb=int(write_mb),
            verify=True,
        )

    if boost is not None:
        recovery["restore_io_boost"] = apply_throttle(
            float(boost["read_mb"]),
            float(boost["write_mb"]),
        )
    try:
        recovery["snapshot_restore"] = restore_worker_database(
            worker_cfg,
            scenario,
            restore_timeout_s,
        )
    finally:
        if boost is not None:
            recovery["measurement_io_throttle"] = apply_throttle(
                float(vm_resource.get("read_mb") or 0.0),
                float(vm_resource.get("write_mb") or 0.0),
            )
    recovery["cache_drop"] = drop_worker_cache(
        worker_cfg,
        scenario,
        drop_cache_timeout_s,
    )
    ready_started = time.time()
    wait_for_primary_postgres(
        pg_conn_from_worker(dict(context.get("config") or {}), context.get("pg")),
        timeout_s=max(1.0, postgres_ready_timeout_s),
    )
    recovery["postgres_ready_after_recovery"] = {
        "status": "ok",
        "elapsed_s": time.time() - ready_started,
        "checked_from": "controller_to_formal_postgres_endpoint",
    }
    raw = validate_candidate(
        {
            "candidate_id": label,
            "scenario": scenario,
            "source": "double_loop_generic_vm_replay",
            "params": dict(params),
        },
        context,
        run_mins=None,
        workload_timeout_s=workload_timeout_s,
    )
    resources = dict(raw.get("actual_resource") or {})
    delta = raw.get("db_delta") or {}
    raw_stats = raw.get("raw_resource_stats") or {}
    # Fixed-horizon HyBench is deliberately terminated after the comparable
    # measurement window, so the legacy validator may leave its top-level
    # benchmark fields empty even though the monitor parsed complete latency
    # statistics.  Preserve those real values for outer-loop and final logs.
    benchmark = raw.get("benchmark") or raw_stats.get("benchmark") or {}
    latency = raw.get("benchmark_latency") or raw_stats.get("benchmark_latency") or {}
    workload_output = str(raw_stats.get("workload_output") or "")
    workload_error_patterns = (
        "org.postgresql.util.PSQLException: ERROR:",
        "java.lang.OutOfMemoryError",
        "Exception in thread",
        "No space left on device",
        "canceling statement due to statement timeout",
    )
    workload_output_error = next(
        (pattern for pattern in workload_error_patterns if pattern in workload_output),
        None,
    )
    raw_stats["workload_output_error"] = workload_output_error
    if raw.get("status") == "ok" and workload_output_error:
        raw["status"] = "failed"
        raw["error"] = (
            "benchmark workload reported a serious runtime error: "
            f"{workload_output_error}"
        )
    stable = raw_stats.get("stable_sample_count") or {}
    result = {
        "event": "real_validation",
        "workload": str((context.get("config") or {}).get("benchmark_name") or "generic"),
        "validation_backend": "generic_vm_replay",
        "label": label,
        "target": scenario,
        "status": raw.get("status"),
        "error": raw.get("error"),
        "window": [raw.get("started_at"), time.time()],
        "elapsed_s": raw.get("elapsed_s"),
        "params": dict(params),
        "resources": resources,
        "actual_utilization_pct": raw.get("actual_utilization_pct") or {},
        "runtime_capability": context.get("capability") or {},
        "resource_stats": raw_stats,
        "resource_samples": {
            "cpu_cores": int(float(stable.get("cpu") or 0)),
            "memory_bytes": int(float(stable.get("memory") or 0)),
            "io_bps": int(float(stable.get("io") or 0)),
        },
        "db_qps": float(delta.get("db_qps") or 0.0),
        "db_xact_tps": float(delta.get("db_xact_tps") or 0.0),
        "benchmark": benchmark,
        "benchmark_latency": latency,
        "p90_ms": finite_or_none(latency.get("all_p90_avg")),
        "p95_ms": finite_or_none(latency.get("all_p95_avg")),
        "p99_ms": finite_or_none(latency.get("all_p99_avg")),
        "worker": {
            "name": worker_cfg.get("worker_id"),
            "host": (context.get("pg") or {}).get("host"),
            "port": (context.get("pg") or {}).get("port"),
            "vm_name": worker_cfg.get("vm_name"),
        },
        "vm_replay": {
            "worker_config": str(worker_path),
            "recovery": recovery,
        },
        "used_real_replay": True,
        "forced_new_real_replay": bool(force_new_replay),
    }
    append_jsonl(output_dir / f"{scenario}_generic_vm_replay_raw.jsonl", raw)
    return result


def stop_owned_seeded_vm(config_path: Optional[Path]) -> Dict[str, Any]:
    """Stop only the disposable direct-QEMU guest declared by this run."""
    if config_path is None:
        return {"attempted": False, "reason": "no_scenario_config"}
    cfg = load_config(config_path)
    reset = (
        (((cfg.get("pre_benchmark_recovery") or {}).get("snapshot_restore") or {}).get(
            "seeded_qemu_reset"
        ))
        or {}
    )
    if reset.get("enabled") is not True:
        return {"attempted": False, "reason": "seeded_qemu_reset_disabled"}
    shell_dir = ROOT / "shell"
    if str(shell_dir) not in sys.path:
        sys.path.insert(0, str(shell_dir))
    from reset_seeded_qemu_worker import (  # type: ignore
        spec_from_reset,
        stop_work_guest,
    )

    spec = spec_from_reset(reset)
    stop_work_guest(spec, timeout_s=15.0)
    return {
        "attempted": True,
        "stopped": True,
        "vm_name": spec.name,
        "ownership_source": str(config_path),
    }


def finite_or_none(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def fatal_replay_workload_error(payload: Mapping[str, Any]) -> Optional[str]:
    """Return only an explicit benchmark/runtime fatal marker.

    A legacy transport status can be failed for harmless sample-count warnings,
    so status alone is deliberately insufficient.  The generic adapter writes
    this marker only after matching a PostgreSQL error, OOM, process exception,
    disk-full condition, or statement timeout in the benchmark output.
    """
    stats = payload.get("resource_stats")
    if isinstance(stats, Mapping):
        value = stats.get("workload_output_error")
        text = str(value or "").strip()
        if text:
            return text

    # Some fatal failures (for example a guest OOM killing PostgreSQL) abort
    # the monitor before it can return samples or retain workload output.  A
    # legacy ``status=failed`` remains non-fatal when complete measurements are
    # present, but it must not fall through to a misleading ``cpu=None`` score
    # error when the transport also returned an explicit process exception and
    # no resource vector.
    resources = payload.get("resources")
    complete_resources = isinstance(resources, Mapping) and all(
        finite_or_none(resources.get(name)) is not None
        for name in ("cpu", "memory", "io")
    )
    error = str(payload.get("error") or "").strip()
    if payload.get("status") != "ok" and error and not complete_resources:
        return f"{payload.get('status')}: {error}"
    return None


def deferred_dynamic_sweet_score_error(
    scenario: str,
    payload: Mapping[str, Any],
) -> bool:
    """Identify the legacy pre-baseline score error used by dynamic IO bands.

    IO deliberately derives its 90%--95% sweet band from the fresh real
    baseline.  The legacy validation transport tries to score the measurement
    before that band exists and reports a failed status even though the replay,
    QPS, and resource measurements all succeeded.  The double-loop scorer runs
    after the band is derived, so this transport-only status is safely deferred.
    """
    error = str(payload.get("error") or "")
    return (
        scenario == "io"
        and payload.get("status") != "ok"
        and "target scenario requires a finite sweet upper value" in error
    )


def probe_change_strength(result: Mapping[str, Any]) -> float:
    changes = result.get("changes") if isinstance(result.get("changes"), Mapping) else {}
    plan = changes.get("plan") if isinstance(changes.get("plan"), Mapping) else {}
    score = 1000.0 * float(plan.get("changed_count") or 0)
    if plan.get("changed"):
        score += 100.0
    features = changes.get("features") if isinstance(changes.get("features"), Mapping) else {}
    for detail in features.values():
        if not isinstance(detail, Mapping):
            continue
        score += float(detail.get("changed_count") or 0)
        for item in detail.get("top_changes") or []:
            if not isinstance(item, Mapping):
                continue
            baseline = abs(float(item.get("baseline") or 0.0))
            delta = abs(float(item.get("delta") or 0.0))
            score += min(10.0, delta / max(1.0, baseline))
    return score


def fresh_workload_pruning(
    _scenario: str,
    cfg: Dict[str, Any],
    worker: Mapping[str, Any],
    candidates: Sequence[str],
    ranges: Mapping[str, Tuple[float, float]],
    base_params: Mapping[str, float],
    *,
    active_k: int,
    max_workers: int,
    output_path: Path,
) -> List[str]:
    """Select active parameters now; never export resource directions to the model."""
    worker_cfg = config_for_worker(cfg, worker)
    probe = PlanFeatureProbe(worker_cfg)
    queries, sampling = workload_queries_with_sampling(worker_cfg, probe)
    baseline = probe.probe(queries, dict(base_params))
    tasks = [
        (name, endpoint, ranges[name][0 if endpoint == "min" else 1])
        for name in candidates
        for endpoint in ("min", "max")
    ]
    endpoint_results: Dict[str, List[Dict[str, Any]]] = {name: [] for name in candidates}
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(max_workers, len(tasks))) as executor:
        future_map = {
            executor.submit(
                endpoint_probe_task,
                worker_cfg,
                queries,
                baseline,
                dict(base_params),
                name,
                endpoint,
                value,
            ): name
            for name, endpoint, value in tasks
        }
        for future in concurrent.futures.as_completed(future_map):
            name = future_map[future]
            try:
                endpoint_results[name].append(future.result())
            except Exception as exc:
                endpoint_results[name].append(
                    {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "changes": {}}
                )

    scores: Dict[str, float] = {}
    for name, rows in endpoint_results.items():
        # A probe failure is conservatively retained by giving it the highest
        # workload-pruning priority; it is not silently dropped.
        if any(row.get("status") != "ok" for row in rows):
            scores[name] = float("inf")
        else:
            scores[name] = max((probe_change_strength(row) for row in rows), default=0.0)
    ordered = sorted(candidates, key=lambda name: (-scores[name], candidates.index(name)))
    # Delete a parameter only when both fresh endpoints produced no plan or
    # resource-input feature change. Probe failures remain active (fail-open),
    # because missing evidence must never masquerade as inactivity.
    workload_active = [
        name for name in ordered if not math.isfinite(scores[name]) or scores[name] > 0.0
    ]
    # ``active_k`` is retained only for CLI/backward-compatible audit output.
    # It must not become a second, arbitrary parameter-count pruning rule:
    # every parameter proven active by this invocation's fresh endpoint probes
    # participates in the complete coordinate grid and interaction probes.
    active = workload_active
    prune_reasons = {
        name: (
            "inactive_at_both_fresh_endpoints"
            if scores[name] == 0.0
            else "retained"
        )
        for name in candidates
    }
    write_json(
        output_path,
        {
            "event": "fresh_workload_pruning",
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "source": "current_invocation_endpoint_probes",
            "historical_artifacts_loaded": False,
            "candidate_params": list(candidates),
            "pruning_rule": (
                "remove_only_when_both_fresh_lower_upper_endpoints_have_no_plan_or_input_feature_change"
            ),
            "probe_failure_policy": "retain_parameter",
            "requested_active_k_legacy": int(active_k),
            "active_k_cap_applied": False,
            "active_k": len(active),
            "active_params": active,
            "pruned_params": [name for name in candidates if name not in active],
            "prune_reasons": prune_reasons,
            "scores": {name: (None if not math.isfinite(value) else value) for name, value in scores.items()},
            "sampling": sampling,
            "query_count": len(queries),
            "endpoint_results": endpoint_results,
        },
    )
    return active


def fresh_model_calibration_points(
    base_params: Mapping[str, float],
    ranges: Mapping[str, Tuple[float, float]],
    candidates: Sequence[str],
    *,
    replay_budget: int,
    grid_points: int,
) -> List[Dict[str, Any]]:
    """Deterministic current-run calibration design, excluding the baseline.

    The small loop is a coordinate grid.  Its preflight must therefore include
    real one-coordinate observations: an all-dimension Latin hypercube makes
    every calibration point an interaction, so neither of the coordinate
    prediction modes has evidence for a single planner parameter's response.
    This was especially harmful around plan-switch thresholds, where a linear
    model has no way to separate which parameter caused the resource change.

    Keep the fixed budget at exactly 15 real replays, but concentrate the first
    two observations of every active coordinate on the grid values immediately
    below and above the fresh default.  A single held-out global endpoint has
    no same-axis neighbour in a 15-point, seven-axis design, so strict
    leave-one-out resource/error validation can only extrapolate across a
    planner discontinuity.  The two local, bracketing observations let the
    predictor validate the actual local response used to rank the next grid
    pass.  Any spare slots expand outward deterministically by coordinate
    distance from the default.

    The complete interaction grid is still enumerated by the inner loop and
    still subject to Top-K real validation.  This changes only the legal
    pre-optimizer resource-model training design; it neither changes Score nor
    accepts an unvalidated candidate.

    The fresh baseline is already measured by the caller and is deliberately
    not counted as a calibration replay.  No historical candidate coordinates
    are read.
    """
    result: List[Dict[str, Any]] = []
    seen = {
        tuple((name, round(float(value), 12)) for name, value in sorted(base_params.items()))
    }

    def add(phase: str, params: Mapping[str, float], **metadata: Any) -> None:
        signature = tuple(
            (name, round(float(value), 12)) for name, value in sorted(params.items())
        )
        cap_reached = len(result) >= max(0, int(replay_budget))
        if signature in seen or cap_reached:
            return
        seen.add(signature)
        result.append(
            {
                "calibration_id": f"calibration_{len(result) + 1:03d}",
                "phase": phase,
                "params": {name: float(value) for name, value in params.items()},
                **metadata,
            }
        )

    if not candidates:
        return result

    count = max(2, int(grid_points))
    grid_values: Dict[str, List[float]] = {}
    for name in candidates:
        low, high = ranges[name]
        # Calibration uses the exact same coordinate values as the complete
        # candidate grid, including logarithmic spacing for wide positive
        # planner-cost ranges.
        grid_values[name] = DoubleLoopGridOptimizer.coordinate_grid_values(
            float(low),
            float(high),
            count,
        )

    def add_coordinate(
        name: str,
        rank: int,
        *,
        phase: str,
    ) -> None:
        params = dict(base_params)
        params[name] = grid_values[name][rank]
        add(
            phase,
            params,
            design="deterministic_coordinate_axis_grid",
            parameter=name,
            grid_rank=int(rank),
            changed_parameters=[name],
        )

    def local_rank_order(name: str) -> List[int]:
        """Return grid ranks in deterministic outward order around default."""
        base_value = float(base_params[name])
        values = grid_values[name]
        lower = [rank for rank, value in enumerate(values) if value < base_value]
        upper = [rank for rank, value in enumerate(values) if value > base_value]
        lower.sort(key=lambda rank: (base_value - values[rank], -rank))
        upper.sort(key=lambda rank: (values[rank] - base_value, rank))
        order: List[int] = []
        while lower or upper:
            if lower:
                order.append(lower.pop(0))
            if upper:
                order.append(upper.pop(0))
        return order

    local_ranks = {name: local_rank_order(name) for name in candidates}
    depth = 0
    while len(result) < max(0, int(replay_budget)):
        added_at_depth = False
        for name in candidates:
            ranks = local_ranks[name]
            if depth >= len(ranks):
                continue
            before = len(result)
            add_coordinate(
                name,
                ranks[depth],
                phase=(
                    "coordinate_axis_local_bracket"
                    if depth < 2
                    else "coordinate_axis_local_expand"
                ),
            )
            added_at_depth = added_at_depth or len(result) > before
            if len(result) >= max(0, int(replay_budget)):
                break
        if len(result) >= max(0, int(replay_budget)):
            break
        if not added_at_depth and all(
            depth + 1 >= len(local_ranks[name]) for name in candidates
        ):
            break
        depth += 1
    if len(result) < max(0, int(replay_budget)):
        raise RuntimeError("unable to construct the requested calibration design")
    return result


def load_anchored_resource_training_seed(
    model: FreshExperienceModel,
    seed_dir: Path,
    *,
    include_real_candidates: bool = False,
) -> Tuple[List[FreshExperience], Dict[str, Any]]:
    """Load only parameter/resource vectors and anchor them to this baseline.

    Prior real candidate replays are optional support rows.  They are limited
    to parameter/resource measurements: old Scores, ranks, decisions and best
    points are never read.
    """
    baseline_path = seed_dir / "fresh_baseline.json"
    calibration_path = seed_dir / "fresh_model_calibration.jsonl"
    if not baseline_path.is_file() or not calibration_path.is_file():
        raise FileNotFoundError(
            f"training seed requires {baseline_path} and {calibration_path}"
        )
    with baseline_path.open("r", encoding="utf-8") as handle:
        baseline_record = json.load(handle)
    seed_baseline = baseline_record.get("measurement") or {}
    current_baseline = model.objective.baseline.as_resource()
    resource_names = ("db_qps", "cpu", "memory", "io")
    scale = {
        name: float(current_baseline[name]) / float(seed_baseline[name])
        for name in resource_names
    }
    by_signature: Dict[Tuple[Tuple[str, float], ...], FreshExperience] = {}

    def add(params: Mapping[str, Any], measurement: Mapping[str, Any]) -> None:
        anchored = {
            name: float(measurement[name]) * scale[name]
            for name in resource_names
        }
        row = model.build_experience(params, anchored)
        by_signature[model._signature(row.params)] = row

    add(baseline_record["params"], seed_baseline)
    source_usable_replays = 0
    with calibration_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            if (
                record.get("event") != "fresh_model_calibration_replay"
                or record.get("status") != "usable"
            ):
                continue
            source_usable_replays += 1
            add(record["params"], record["measurement"])
    source_real_candidate_row_count = 0
    source_real_candidate_files: List[str] = []
    if include_real_candidates:
        validation_dir = seed_dir / "real_validations"
        for candidate_path in sorted(
            validation_dir.glob("*_generic_vm_replay_raw.jsonl")
        ):
            source_real_candidate_files.append(str(candidate_path))
            with candidate_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    record = json.loads(line)
                    if record.get("status") != "ok":
                        continue
                    params = record.get("params")
                    resources = record.get("actual_resource")
                    db_qps = (record.get("db_delta") or {}).get("db_qps")
                    if (
                        not isinstance(params, Mapping)
                        or not isinstance(resources, Mapping)
                        or db_qps is None
                    ):
                        continue
                    add(
                        params,
                        {
                            "db_qps": float(db_qps),
                            "cpu": float(resources["cpu"]),
                            "memory": float(resources["memory"]),
                            "io": float(resources["io"]),
                        },
                    )
                    source_real_candidate_row_count += 1
    rows = list(by_signature.values())
    return rows, {
        "enabled": True,
        "source": str(seed_dir),
        "source_baseline": seed_baseline,
        "current_fresh_baseline": current_baseline,
        "baseline_anchor_scale": scale,
        "source_usable_replay_count": source_usable_replays,
        "real_candidate_rows_enabled": bool(include_real_candidates),
        "source_real_candidate_row_count": source_real_candidate_row_count,
        "source_real_candidate_files": source_real_candidate_files,
        "deduplicated_training_row_count": len(rows),
        "loaded_fields": ["params", "measurement"],
        "old_scores_loaded": False,
        "old_ranks_loaded": False,
        "old_decisions_loaded": False,
        "old_intervals_loaded": False,
        "old_best_params_loaded": False,
    }


def run_fresh_model_quality_gate(
    model: FreshExperienceModel,
    validator: Any,
    sql_set: Any,
    base_params: Mapping[str, float],
    ranges: Mapping[str, Tuple[float, float]],
    candidates: Sequence[str],
    *,
    thresholds: FreshModelQualityThresholds,
    replay_budget: int,
    grid_points: int,
    scenario_dir: Path,
    fixed_training_rows: Optional[Sequence[FreshExperience]] = None,
    training_seed_audit: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Replay a fixed fresh prefix, then return quality gate result and soft-start if needed."""
    if replay_budget != 15:
        raise ValueError("fresh model calibration replay budget must be exactly 15")
    calibration_log = scenario_dir / "fresh_model_calibration.jsonl"
    design = fresh_model_calibration_points(
        base_params,
        ranges,
        candidates,
        replay_budget=replay_budget,
        grid_points=grid_points,
    )
    attempts: List[Dict[str, Any]] = []
    final_quality: Dict[str, Any] = {
        "passed": False,
        "reason": "not_evaluated",
    }
    validation_rows: List[FreshExperience] = []
    for design_row in design:
        replay: Dict[str, Any] = {
            "event": "fresh_model_calibration_replay",
            **copy.deepcopy(design_row),
            "current_run_only": True,
            "used_for_optimizer_candidate_selection": False,
            "parameter_vector_eligible_for_grid_seed": True,
            "calibration_real_result_reused_as_candidate_replay": False,
        }
        try:
            raw = validator(sql_set, dict(design_row["params"]))
            measurement = measurement_from_payload(
                raw.get("resources") or raw.get("resource") or {},
                raw,
            )
            row = model.build_experience(
                design_row["params"],
                measurement.as_resource(),
                raw,
            )
            validation_rows.append(row)
            if fixed_training_rows is None:
                model.add(
                    design_row["params"],
                    measurement.as_resource(),
                    raw,
                )
            replay.update(
                {
                    "status": "usable",
                    "measurement": measurement.as_resource(),
                    "raw_result": json_value(raw),
                    "real_resource_score": model.objective.resource_score_details(
                        measurement
                    )["total"],
                    "usable_sample_count_including_baseline": (
                        len(model.snapshot())
                        if fixed_training_rows is None
                        else 1 + len(validation_rows)
                    ),
                }
            )
        except Exception as exc:
            replay.update(
                {
                    "status": "failed",
                    "failure_reason": f"{type(exc).__name__}: {exc}",
                    "usable_sample_count_including_baseline": len(model.snapshot()),
                }
            )
        attempts.append(copy.deepcopy(replay))
        append_jsonl(calibration_log, replay)

    # Model selection/retraining is allowed to use only this fixed 15-replay
    # prefix plus the fresh baseline.  A failed gate changes the fresh model;
    # it must never silently buy more real calibration replays.
    fresh_baseline_training_row = model.build_experience(
        base_params,
        model.objective.baseline.as_resource(),
    )
    final_quality = model.select_quality_mode(
        thresholds,
        validation_rows=validation_rows,
        fixed_training_rows=fixed_training_rows,
        always_training_rows=[fresh_baseline_training_row],
    )
    final_quality["event"] = "fresh_model_quality_gate_attempt"
    final_quality["after_calibration_id"] = design[-1]["calibration_id"] if design else None
    final_quality["real_replay_attempt_count"] = len(attempts)
    append_jsonl(calibration_log, final_quality)
    summary = {
        "event": "fresh_model_quality_gate",
        "passed": bool(final_quality.get("passed")),
        "reason": final_quality.get("reason"),
        "current_run_only": bool(final_quality.get("current_run_only")),
        "historical_training_rows_used": bool(
            final_quality.get("historical_training_rows_used")
        ),
        "historical_training_rows_evaluated": bool(
            final_quality.get("historical_training_rows_evaluated")
        ),
        "historical_seed_discarded_after_quality_failure": bool(
            final_quality.get("historical_seed_discarded_after_quality_failure")
        ),
        "historical_model_artifacts_used": False,
        "old_model_labels_used": False,
        "optimizer_started": False,
        "calibration_design": design,
        "calibration_replay_attempt_count": len(attempts),
        "calibration_candidate_count": len(design),
        "calibration_replay_budget": replay_budget,
        "calibration_budget_excludes_fresh_baseline": True,
        "fresh_baseline_sample_count": 1,
        "usable_sample_count_including_baseline": 1 + len(validation_rows),
        "thresholds": json_value(asdict(thresholds)),
        "quality_attempt_count": 1,
        "selected_quality": json_value(final_quality),
        "calibration_replays": json_value(attempts),
        "calibration_jsonl": str(calibration_log),
        "score_formula": "sigmoid_linear_sweet_band_weighted_mean_v2",
        "estimated_and_real_score_formula_identical": True,
        "training_seed": json_value(dict(training_seed_audit or {})),
        "preflight_soft_start": False,
        "preflight_soft_start_reason": None,
    }
    write_json(scenario_dir / "fresh_model_quality_gate.json", json_value(summary))
    if not summary["passed"]:
        summary["preflight_soft_start"] = True
        summary["preflight_soft_start_reason"] = summary["reason"]
        write_json(
            scenario_dir / "fresh_model_quality_gate.json",
            json_value(summary),
        )
    if (
        fixed_training_rows is not None
        and final_quality.get("historical_training_rows_used") is True
    ):
        # Holdout rows become ordinary online experience only after the strict
        # pre-optimizer gate has passed; fresh exact points override seed rows.
        for row in fixed_training_rows:
            model.add(row.params, row.measurement.as_resource())
    if fixed_training_rows is not None:
        for row in validation_rows:
            model.add(row.params, row.measurement.as_resource())
    return summary


def grid_parameter_ranges(
    cfg: Mapping[str, Any],
    raw_ranges: Mapping[str, Tuple[float, float]],
    candidates: Sequence[str],
    *,
    default_discrete_count: int,
) -> Dict[str, ParameterRange]:
    """Build the discrete parameter ranges used by coordinate-grid scans."""
    configured = cfg.get("param_ranges")
    configured = configured if isinstance(configured, Mapping) else {}
    result: Dict[str, ParameterRange] = {}
    for priority, name in enumerate(candidates, start=1):
        low, high = raw_ranges[name]
        item = configured.get(name)
        item = item if isinstance(item, Mapping) else {}
        raw_values = item.get("values")
        values = (
            tuple(float(value) for value in raw_values)
            if isinstance(raw_values, list) and len(raw_values) >= 2
            else None
        )
        step_raw = item.get("step")
        result[name] = ParameterRange(
            min_value=float(low),
            max_value=float(high),
            step=(
                float(step_raw)
                if step_raw not in (None, "")
                else None
            ),
            discrete_count=max(
                2,
                int(item.get("discrete_count", default_discrete_count)),
            ),
            priority=int(item.get("priority", priority)),
            values=values,
            transform=str(item.get("transform", "linear")),
        )
    return result


def preflight_resource_error_bounds(
    model_quality_gate: Mapping[str, Any],
) -> Dict[str, Dict[str, float]]:
    """Extract signed LOO residual boxes used by replay pruning."""
    selected_quality = model_quality_gate.get("selected_quality")
    selected_quality = (
        selected_quality if isinstance(selected_quality, Mapping) else {}
    )
    evaluation = selected_quality.get("selected_uncertainty_evaluation")
    if not isinstance(evaluation, Mapping):
        evaluation = selected_quality.get("selected_evaluation")
    evaluation = evaluation if isinstance(evaluation, Mapping) else {}
    metrics = evaluation.get("resource_metrics")
    metrics = metrics if isinstance(metrics, Mapping) else {}
    result: Dict[str, Dict[str, float]] = {}
    for name in ("cpu", "memory", "io"):
        item = metrics.get(name)
        if not isinstance(item, Mapping):
            if not selected_quality.get("passed", False):


                result[name] = {
                    "lower_delta": 0.0,
                    "upper_delta": 0.0,
                    "sample_count": 0,
                }
                continue
            raise RuntimeError(
                f"passed model quality gate lacks {name} residual metrics"
            )
        result[name] = {
            "lower_delta": float(item.get("signed_residual_lower", 0.0)),
            "upper_delta": float(item.get("signed_residual_upper", 0.0)),
            "sample_count": float(item.get("sample_count", 0)),
        }
    return result


class SimulatedScenario:
    """Deterministic DB-shaped test fixture used only by --mode simulated."""

    def __init__(self, scenario: str) -> None:
        self.scenario = scenario

    def validate(self, _sql_set: Any, params: Dict[str, float]) -> Dict[str, Any]:
        a, b, c = params["a"], params["b"], params["c"]
        movement = {"cpu": a - 0.2, "memory": b - 0.2, "io": c - 0.2}
        qps_gain = 0.32 * sum(movement.values()) - 0.04 * sum(value * value for value in movement.values())
        cpu = 8.0 * (1.0 - 0.34 * movement["cpu"] + 0.04 * movement["memory"])
        memory = 4.0 * (1.0 - 0.30 * movement["memory"] + 0.05 * movement["io"])
        io = 20.0 * (1.0 - 0.28 * movement["io"] + 0.05 * movement["cpu"])
        if self.scenario == "none":
            cpu = 8.0 * (1.0 + 0.25 * movement["cpu"])
            memory = 4.0 * (1.0 + 0.18 * movement["memory"])
            io = 20.0 * (1.0 + 0.15 * movement["io"])
        return {
            "status": "ok",
            "db_qps": 100.0 * (1.0 + qps_gain),
            "resources": {"cpu": cpu, "memory": memory, "io": io},
            "params": dict(params),
            "validation_backend": "deterministic_simulation",
        }


def double_loop_validation_label(scenario: str, validation_index: int) -> str:
    """Build a transport label whose prefix activates only this scenario's workload override."""
    normalized = str(scenario).strip().lower()
    if normalized not in SCENARIOS:
        raise ValueError(f"unsupported double-loop scenario: {scenario!r}")
    return f"{normalized}_double_loop_{int(validation_index):04d}"


def effective_outer_round_budget(
    ranges: Mapping[str, Tuple[float, float]],
    active_params: Sequence[str],
    epsilon: float,
    requested_rounds: int,
) -> int:
    """Return an explicit limit or a convergence-capable automatic budget.

    The limit is only a safety ceiling.  Each accepted grid round shrinks all
    active dimensions around the accepted full-vector p*, while rejected rounds
    change no bounds.  Doubling the slowest dimension's required halvings leaves
    room for rejected rounds without truncating convergence.
    """
    if requested_rounds > 0:
        return int(requested_rounds)
    if not math.isfinite(epsilon) or epsilon <= 0.0:
        raise ValueError("epsilon must be finite and positive")
    required_halvings = 0
    for name in active_params:
        low, high = ranges[name]
        width = max(0.0, float(high) - float(low))
        if width <= epsilon:
            continue
        required_halvings = max(
            required_halvings,
            int(math.ceil(math.log(width / epsilon, 2))),
        )
    return max(1, 2 * max(1, required_halvings))


def _resource_capacities(
    cfg: Mapping[str, Any],
    worker: Mapping[str, Any],
) -> Tuple[Dict[str, float], Dict[str, str]]:
    """Resolve current VM capacities, preferring the actual worker override."""
    sources: List[Tuple[str, Mapping[str, Any]]] = []
    for label, raw in (
        ("worker.resource_capacity", worker.get("resource_capacity")),
        ("worker.resource_capability", worker.get("resource_capability")),
        ("worker.capability", worker.get("capability")),
        ("scenario.resource_capacity", cfg.get("resource_capacity")),
        ("scenario.resource_capability", cfg.get("resource_capability")),
        ("scenario.capability", cfg.get("capability")),
    ):
        if isinstance(raw, Mapping):
            sources.append((label, raw))
    aliases = {
        "cpu": ("cpu", "cpu_cores"),
        "memory": ("memory", "memory_gib"),
        "io": ("io", "io_mib_s"),
    }
    capacities: Dict[str, float] = {}
    provenance: Dict[str, str] = {}
    for name, keys in aliases.items():
        for label, raw in sources:
            value = next((raw.get(key) for key in keys if raw.get(key) is not None), None)
            if value is None:
                continue
            numeric = float(value)
            if math.isfinite(numeric) and numeric > 0.0:
                capacities[name] = numeric
                provenance[name] = label
                break
        if name not in capacities:
            raise ValueError(f"cannot resolve current {name} capacity for {worker.get('name')}")
    return capacities, provenance


def _resource_sweet_definitions(cfg: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
    scoring = cfg.get("scoring")
    scoring = scoring if isinstance(scoring, Mapping) else {}
    ranges = scoring.get("resource_target_ranges")
    ranges = ranges if isinstance(ranges, Mapping) else {}
    if ranges:
        return {
            name: (ranges.get(name) if isinstance(ranges.get(name), Mapping) else {})
            for name in ("cpu", "memory", "io")
        }
    tuning = cfg.get("binary_plan_tuning")
    tuning = tuning if isinstance(tuning, Mapping) else {}
    policy = tuning.get("acceptance_policy")
    policy = policy if isinstance(policy, Mapping) else {}
    sweet = policy.get("target_sweet_spot")
    sweet = sweet if isinstance(sweet, Mapping) else {}
    return {
        name: (sweet.get(name) if isinstance(sweet.get(name), Mapping) else {})
        for name in ("cpu", "memory", "io")
    }


def _physical_sweet_bounds(
    name: str,
    capacity: float,
    definition: Mapping[str, Any],
    default_max_pct: float,
) -> Tuple[Optional[float], float]:
    if definition.get("max_mib_s") is not None:
        upper = float(definition["max_mib_s"])
        lower = (
            float(definition["min_mib_s"])
            if definition.get("min_mib_s") is not None
            else None
        )
    else:
        upper = capacity * float(definition.get("max_pct", default_max_pct)) / 100.0
        lower = (
            capacity * float(definition["min_pct"]) / 100.0
            if definition.get("min_pct") is not None
            else None
        )
    if not math.isfinite(upper) or upper <= 0.0:
        raise ValueError(f"invalid {name} physical sweet upper: {upper!r}")
    if lower is not None and (
        not math.isfinite(lower) or lower < 0.0 or lower > upper
    ):
        raise ValueError(f"invalid {name} physical sweet lower: {lower!r}")
    return lower, upper


def sigmoid_linear_resource_limits(
    scenario: str,
    cfg: Mapping[str, Any],
    worker: Mapping[str, Any],
    *,
    explicit_target_upper: Optional[float] = None,
) -> Dict[str, float]:
    """Resolve all three current-VM ``u=1`` physical limits."""
    capacities, _ = _resource_capacities(cfg, worker)
    definitions = _resource_sweet_definitions(cfg)
    scoring = cfg.get("scoring")
    scoring = scoring if isinstance(scoring, Mapping) else {}
    default_max_pct = 100.0 * float(scoring.get("overload_threshold", 0.8))
    limits: Dict[str, float] = {}
    for name in ("cpu", "memory", "io"):
        _, upper = _physical_sweet_bounds(
            name,
            capacities[name],
            definitions[name],
            default_max_pct,
        )
        limits[name] = (
            float(explicit_target_upper)
            if name == scenario and explicit_target_upper is not None
            else upper
        )
    return limits


def physical_target_sweet_upper(
    scenario: str,
    cfg: Mapping[str, Any],
    baseline: ScenarioMeasurement,
    *,
    worker: Optional[Mapping[str, Any]] = None,
    explicit_upper: Optional[float] = None,
    min_target_drop_ratio: float = 0.05,
    max_target_drop_ratio: float = 0.10,
    simulated: bool = False,
) -> Tuple[Optional[float], Dict[str, Any]]:
    """Resolve the target sweet band from this run's VM and workload."""
    if scenario == "none":
        return None, {
            "scenario": scenario,
            "used": False,
            "reason": "None has no target bottleneck sweet zone",
        }
    if simulated:
        capacity: Dict[str, float] = {}
        provenance: Dict[str, str] = {}
        # Keep the deterministic fixture compatible with the production
        # requirement that final target reduction is between 5% and 10%.
        upper = 0.95 * float(getattr(baseline, scenario))
        lower: Optional[float] = 0.90 * float(getattr(baseline, scenario))
        definition: Mapping[str, Any] = {
            "min_fraction_of_fresh_baseline": 0.90,
            "max_fraction_of_fresh_baseline": 0.95,
        }
        source = "deterministic_simulation_fixture"
    else:
        if worker is None:
            raise ValueError("live physical sweet resolution requires the current worker")
        capacity, provenance = _resource_capacities(cfg, worker)
        definitions = _resource_sweet_definitions(cfg)
        definition = definitions[scenario]
        baseline_target = float(getattr(baseline, scenario))
        has_physical_band = any(
            definition.get(key) is not None
            for key in ("min_pct", "max_pct", "min_mib_s", "max_mib_s")
        )
        if not has_physical_band and explicit_upper is None:
            # The requested target semantics are a 5%--10% bottleneck decrease.
            # When a workload has no independently configured physical sweet
            # band, derive exactly that band from this invocation's fresh real
            # baseline instead of silently using an unrelated 80%-capacity point.
            lower = 0.90 * baseline_target
            upper = 0.95 * baseline_target
            definition = {
                "min_fraction_of_fresh_baseline": 0.90,
                "max_fraction_of_fresh_baseline": 0.95,
            }
            source = "fresh_baseline_required_5_10_target_band"
        else:
            scoring = cfg.get("scoring")
            scoring = scoring if isinstance(scoring, Mapping) else {}
            default_max_pct = 100.0 * float(scoring.get("overload_threshold", 0.8))
            lower, configured_upper = _physical_sweet_bounds(
                scenario,
                capacity[scenario],
                definition,
                default_max_pct,
            )
            upper = float(explicit_upper) if explicit_upper is not None else configured_upper
            source = (
                "explicit_double_loop_cli_with_current_vm_lower"
                if explicit_upper is not None
                else "current_vm_capacity_and_current_workload_sweet_definition"
            )
        if lower is not None and lower > upper:
            raise ValueError(
                f"explicit {scenario} sweet upper {upper} is below configured lower {lower}"
            )
    baseline_target = float(getattr(baseline, scenario))
    configured_or_derived_lower = lower
    configured_or_derived_upper = upper
    if baseline_target <= configured_or_derived_upper:
        raise ValueError(
            f"fresh {scenario} baseline {baseline_target} is not above sweet upper "
            f"{configured_or_derived_upper}; "
            "the workload is not a target-bottleneck baseline"
        )
    if not (
        0.0 <= float(min_target_drop_ratio) <= float(max_target_drop_ratio) < 1.0
    ):
        raise ValueError("target drop ratios must satisfy 0 <= min <= max < 1")
    lowest_allowed_target = baseline_target * (1.0 - float(max_target_drop_ratio))
    highest_allowed_target = baseline_target * (1.0 - float(min_target_drop_ratio))
    # The resource Score and the outer physical acceptance must describe the
    # same feasible target.  A configured non-bottleneck band alone may award
    # full score to a point that drops less than 5%, while a very low configured
    # lower bound may reward a point that drops more than 10%.  Intersect both
    # constraints before constructing the shared estimated/real scorer.
    lower = max(
        lowest_allowed_target,
        (
            float(configured_or_derived_lower)
            if configured_or_derived_lower is not None
            else lowest_allowed_target
        ),
    )
    upper = min(configured_or_derived_upper, highest_allowed_target)
    if lower > upper + 1e-12:
        raise ValueError(
            f"fresh {scenario} baseline {baseline_target}, physical sweet band "
            f"[{configured_or_derived_lower}, {configured_or_derived_upper}], and "
            f"required target drop [{min_target_drop_ratio:.6g}, "
            f"{max_target_drop_ratio:.6g}] have no feasible intersection"
        )
    return upper, {
        "scenario": scenario,
        "used": True,
        "source": source,
        "measurement_unit": {"cpu": "cores", "memory": "GiB", "io": "MiB/s"}[scenario],
        "fresh_baseline_target": baseline_target,
        "target_sweet_lower": lower,
        "target_sweet_upper": upper,
        "configured_or_derived_target_sweet_lower": configured_or_derived_lower,
        "configured_or_derived_target_sweet_upper": configured_or_derived_upper,
        "lowest_target_allowed_by_max_drop": lowest_allowed_target,
        "highest_target_allowed_by_min_drop": highest_allowed_target,
        "min_target_drop_ratio": float(min_target_drop_ratio),
        "max_target_drop_ratio": float(max_target_drop_ratio),
        "raw_sweet_definition": json_value(dict(definition)),
        "resource_capacity": json_value(capacity),
        "resource_capacity_provenance": json_value(provenance),
        "historical_candidate_data_used": False,
        "legacy_accept_reject_result_used": False,
    }


def compact_result(
    scenario: str,
    baseline_raw: Mapping[str, Any],
    objective: FreshBaselineObjective,
    result: DoubleLoopResult,
    *,
    active_params: Sequence[str],
    run_dir: Path,
    selection_policy: str,
    physical_semantics: Mapping[str, Any],
    model_quality_gate: Mapping[str, Any],
    final_confirmation_raw: Optional[Mapping[str, Any]] = None,
    final_confirmation_error: str = "",
) -> Dict[str, Any]:
    baseline = measurement_from_payload(result.initial_real_resource, baseline_raw)
    selected_final = measurement_from_payload(result.final_real_resource, None)
    selected_score_decision = objective.decide(selected_final)
    selected_outer_decision = objective.outer_decide(selected_final)
    confirmation_record: Optional[Dict[str, Any]] = None
    confirmation_decision = None
    confirmation_independent = False
    if final_confirmation_raw is not None:
        confirmation_independent = final_confirmation_is_independent(
            final_confirmation_raw
        )
        confirmed = measurement_from_payload(
            final_confirmation_raw.get("resources") or {}, final_confirmation_raw
        )
        confirmation_decision = objective.final_decide(confirmed)
        hybench = final_confirmation_raw.get("hybench") or {}
        benchmark = hybench.get("benchmark") or {}
        latency = hybench.get("benchmark_latency") or {}
        db_monitor = final_confirmation_raw.get("db_view_monitor") or {}
        active_sessions = db_monitor.get("active_sessions") or {}
        confirmation_record = {
            "confirmation_kind": (
                "post_convergence"
                if result.stop_reason == "epsilon_converged"
                else "nonconverged_diagnostic"
            ),
            "eligible_to_mark_algorithm_success": (
                result.stop_reason == "epsilon_converged"
            ),
            "independent_real_replay": confirmation_independent,
            "search_stop_reason": result.stop_reason,
            "label": final_confirmation_raw.get("label"),
            "params": json_value(final_confirmation_raw.get("params") or result.optimized_params),
            "measurement": confirmed.as_resource(),
            "resource_score_decision": json_value(objective.decide(confirmed)),
            "outer_semantic_decision": json_value(objective.outer_decide(confirmed)),
            "final_goal_decision": json_value(confirmation_decision),
            "db_qps": confirmed.db_qps,
            "db_xact_tps": db_monitor.get("db_xact_tps"),
            "active_sessions_avg": active_sessions.get("avg"),
            "active_sessions_p99": active_sessions.get("p99"),
            "p90_ms": latency.get("all_p90_avg", final_confirmation_raw.get("p90_ms")),
            "p95_ms": final_confirmation_raw.get(
                "p95_ms", latency.get("all_p95_avg")
            ),
            "p99_ms": final_confirmation_raw.get(
                "p99_ms", latency.get("all_p99_avg")
            ),
            "xp_qps": hybench.get("xp_qps", benchmark.get("xp_qps")),
            "xp_tps": hybench.get("xp_tps", benchmark.get("xp_tps")),
            "ap_qps": benchmark.get("ap_qps"),
            "tp_tps": benchmark.get("tp_tps"),
            "resources": json_value(final_confirmation_raw.get("resources") or {}),
            "resource_samples": json_value(
                final_confirmation_raw.get("resource_samples") or {}
            ),
            "raw_validation_path": (
                str(
                    run_dir
                    / "real_validations"
                    / f"{final_confirmation_raw.get('label')}.json"
                )
                if final_confirmation_raw.get("label")
                else None
            ),
        }
    final = (
        measurement_from_payload(final_confirmation_raw.get("resources") or {}, final_confirmation_raw)
        if final_confirmation_raw is not None
        else selected_final
    )
    final_decision = confirmation_decision
    rounds = []
    for outer_round in result.rounds:
        validated_records = []
        for point in outer_round.validated_points:
            record = real_validation_audit_record(point, objective)
            record["is_small_loop_best"] = point is outer_round.small_loop_best
            validated_records.append(record)
        gate_status_counts: Dict[str, int] = {}
        for record in validated_records:
            status = str(record["gate_status"])
            gate_status_counts[status] = gate_status_counts.get(status, 0) + 1
        round_p_star = outer_round.small_loop_best
        rounds.append(
            {
                "round": outer_round.round_index,
                "small_loop_algorithm": "binary_plan_coordinate_grid_plus_resource_estimation",
                "estimated_grid_candidate_count": outer_round.subset_count,
                "estimated_grid_candidates": [
                    subset_audit_record(point) for point in outer_round.subset_runs
                ],
                "unique_converged_point_count": len(outer_round.converged_points),
                "converged_points": [
                    subset_audit_record(point) for point in outer_round.converged_points
                ],
                "real_validation_count": len(outer_round.validated_points),
                "adaptive_topk_selected_k": len(outer_round.validated_points),
                "adaptive_topk_decisions": json_value(
                    outer_round.adaptive_topk_decisions
                ),
                "resource_bound_pruning_decisions": json_value(
                    outer_round.resource_bound_pruning_decisions
                ),
                "resource_bound_pruned_candidate_count": sum(
                    1
                    for decision in outer_round.resource_bound_pruning_decisions
                    if decision.get("action") == "prune_without_real_replay"
                ),
                "outer_accepted_validation_count": 1 if outer_round.outer_accepted else 0,
                "validated_points": validated_records,
                "selection_policy": selection_policy,
                "estimated_recall_ranks": [
                    point.estimated_rank for point in outer_round.validated_points
                ],
                "common_start_params": json_value(outer_round.common_start_params),
                "accepted_anchor_after": json_value(outer_round.accepted_anchor_after),
                "small_loop_best_candidate": (
                    {
                        "params": json_value(round_p_star.params),
                        "real_resource_score": round_p_star.score,
                        "real_db_qps": round_p_star.resource.get("db_qps"),
                        "real_resource": json_value(round_p_star.resource),
                        "estimated_score": round_p_star.estimated_score,
                        "estimated_rank": round_p_star.estimated_rank,
                        "active_subset": list(round_p_star.active_subset),
                        "source_subsets": [
                            list(subset) for subset in round_p_star.source_subsets
                        ],
                        "outer_accepted": outer_round.outer_accepted,
                        "outer_acceptance_reason": outer_round.outer_acceptance_reason,
                        "outer_acceptance_details": json_value(
                            outer_round.outer_acceptance_details
                        ),
                    }
                    if round_p_star is not None
                    else None
                ),
                "gate_status_counts": gate_status_counts,
                "binary_shrink_subset": list(outer_round.binary_shrink_subset),
                "shrink_applied": bool(outer_round.binary_shrink_subset),
                "no_shrink_reason": (
                    None
                    if outer_round.binary_shrink_subset
                    else outer_round.outer_acceptance_reason
                ),
                "ranges_before": json_value(outer_round.ranges_before),
                "ranges_after": json_value(outer_round.ranges_after),
                "outer_accepted": outer_round.outer_accepted,
                "rejected_state_unchanged": (
                    outer_round.outer_accepted
                    or (
                        outer_round.ranges_before == outer_round.ranges_after
                        and outer_round.common_start_params
                        == outer_round.accepted_anchor_after
                    )
                ),
            }
        )
    return {
        "scenario": scenario,
        "status": "completed",
        "successful": (
            result.stop_reason == "epsilon_converged"
            and result.successful
            and not final_confirmation_error
            and confirmation_independent
            and confirmation_decision is not None
            and confirmation_decision.successful
        ),
        "selection_policy": selection_policy,
        "score_semantics": "sigmoid_linear_sweet_band_weighted_mean_v2_shared_by_estimated_and_real",
        "score_policy": objective.score_policy,
        "score_formula": {
            "non_target_u": "resource_value / resource_limit",
            "target_distance": "0 inside [sweet_lower,sweet_upper]; (sweet_lower-r)/sweet_lower below; (r-sweet_upper)/sweet_upper above",
            "target_u": "1 + target_distance",
            "u_le_1": "(sigmoid(5*u)-0.5)/(sigmoid(5)-0.5)",
            "u_gt_1": "1-10*(u-1)-30*(u-1)^2",
            "aggregation": "sum(reward_weight_i*max(score_i,0) + penalty_weight_i*min(score_i,0)) / sum(reward_weight_i)",
            "reward_weights": objective.resource_score_details(selected_final)["reward_weights"],
            "penalty_weights": objective.resource_score_details(selected_final)["penalty_weights"],
            "resource_limits": dict(objective.resource_limits),
            "qps_in_score": False,
        },
        "physical_semantics": json_value(dict(physical_semantics)),
        "fresh_model_quality_gate": json_value(dict(model_quality_gate)),
        "mandatory_min_qps_gain_ratio": objective.min_qps_gain_ratio,
        "score_shape_min_target_drop_ratio": (
            objective.min_target_drop_ratio if scenario in HIGH_PRIORITY_SCENARIOS else None
        ),
        "mandatory_max_target_drop_ratio": (
            objective.max_target_drop_ratio if scenario in HIGH_PRIORITY_SCENARIOS else None
        ),
        "io_reward_weight": objective.io_reward_weight,
        "priority": "high" if scenario in HIGH_PRIORITY_SCENARIOS else "low",
        "fresh_baseline": baseline.as_resource(),
        "selected_real": selected_final.as_resource(),
        "selected_score_decision": json_value(selected_score_decision),
        "selected_outer_decision": json_value(selected_outer_decision),
        "final_real": final.as_resource(),
        "final_decision": json_value(final_decision),
        "final_confirmation_required": True,
        "final_confirmation": confirmation_record,
        "final_confirmation_independent_real_replay": confirmation_independent,
        "final_confirmation_error": final_confirmation_error or None,
        "final_confirmation_validation_count": 1 if final_confirmation_raw is not None else 0,
        "search_produced_outer_accepted_candidate": result.successful,
        "initial_params": result.initial_params,
        "optimized_params": result.optimized_params,
        "active_params": list(active_params),
        "pruned_params": result.pruned_params,
        "small_loop_algorithm": "binary_plan_coordinate_grid_plus_resource_estimation",
        "estimated_grid_candidates_by_round": [
            item.subset_count for item in result.rounds
        ],
        "parameter_subsets_by_round": [
            item.subset_count for item in result.rounds
        ],
        "outer_round_count": len(result.rounds),
        "real_candidate_validation_count": result.real_validation_count,
        "preoptimizer_calibration_validation_count": int(
            model_quality_gate.get("calibration_replay_attempt_count", 0)
        ),
        "stop_reason": result.stop_reason,
        "optimizer_log": result.log_path,
        "inner_iteration_log": result.inner_iteration_log_path,
        "run_dir": str(run_dir),
        "rounds": rounds,
        "isolation": {
            "fresh_baseline_only": True,
            "current_invocation_experience_only": not bool(
                model_quality_gate.get("historical_training_rows_used")
            ),
            "anchored_parameter_resource_seed_loaded": bool(
                model_quality_gate.get("historical_training_rows_used")
            ),
            "historical_teacher_loaded": False,
            "historical_best_params_loaded": False,
            "historical_sensitivity_loaded": False,
            "legacy_model_labels_loaded": False,
            "legacy_decision_gate_called": False,
            "reused_pruning": ["workload_pruning", "candidate_replay_pruning"],
        },
    }


def run_scenario(
    scenario: str,
    args: argparse.Namespace,
    config_path: Optional[Path],
    output_root: Path,
) -> Dict[str, Any]:
    scenario_dir = output_root / scenario
    scenario_dir.mkdir(parents=True, exist_ok=True)
    cfg: Dict[str, Any] = {}
    worker: Dict[str, Any] = {}
    outer_round_evidence_func = None
    if args.mode == "simulated":
        candidates = ["a", "b", "c", "unused"]
        base_params = {"a": 0.2, "b": 0.2, "c": 0.2, "unused": 0.5}
        raw_ranges = {name: (0.0, 1.0) for name in candidates}
        active = ["a", "b", "c"]
        write_json(
            scenario_dir / "fresh_workload_pruning.json",
            {
                "event": "fresh_workload_pruning",
                "source": "current_simulated_workload",
                "active_params": active,
                "pruned_params": [name for name in candidates if name not in active],
                "requested_active_k_legacy": int(args.active_k),
                "active_k_cap_applied": False,
                "historical_artifacts_loaded": False,
            },
        )
        simulator = SimulatedScenario(scenario)
        validator = simulator.validate
        sql_set: Any = {"scenario": scenario, "source": "current_invocation"}
    else:
        if config_path is None:
            raise ValueError(f"live scenario {scenario} has no config path")
        scenario_cfg = load_config(str(config_path))
        runtime_cfg = (
            load_config(str(Path(args.worker_config).resolve()))
            if args.worker_config
            else scenario_cfg
        )
        cfg, config_isolation_audit = isolated_fresh_runtime_config(
            scenario_cfg,
            runtime_cfg,
        )
        write_json(scenario_dir / "config_isolation.json", config_isolation_audit)
        validation_cfg = cfg.setdefault("binary_plan_tuning", {}).setdefault("validation", {})
        replay_cfg = validation_cfg.get("vm_replay")
        replay_cfg = replay_cfg if isinstance(replay_cfg, dict) else {}
        vm_replay_enabled = config_flag(replay_cfg.get("enabled"), False)
        if args.live_run_mins > 0:
            validation_cfg.setdefault("hybench_xp", {})["xp_run_mins"] = int(args.live_run_mins)
            if vm_replay_enabled:
                replay_cfg["run_mins"] = int(args.live_run_mins)
                replay_cfg.setdefault("run_mins_by_target", {})[scenario] = int(args.live_run_mins)
                validation_cfg["vm_replay"] = replay_cfg
        hybench_xp = validation_cfg.setdefault("hybench_xp", {})
        workload_override = hybench_xp.setdefault("target_overrides", {}).setdefault(
            scenario, {}
        )
        if scenario in args.workload_xtpclients:
            workload_override["xtpclient"] = args.workload_xtpclients[scenario]
        if scenario in args.workload_xapclients:
            workload_override["xapclient"] = args.workload_xapclients[scenario]
        for field_name, values in (
            ("ap_query_ids", args.workload_ap_query_ids),
            ("ap_query_weights", args.workload_ap_query_weights),
            ("ap_query_sequence", args.workload_ap_query_sequences),
        ):
            if scenario in values:
                workload_override[field_name] = values[scenario]
        if scenario in args.workload_statement_sql_files:
            workload_override["statement_sql_files"] = copy.deepcopy(
                args.workload_statement_sql_files[scenario]
            )
        raw_ranges = param_ranges(cfg)
        candidates = [name for name in COST_PARAMS if name in raw_ranges]
        base_params = {
            name: float(POSTGRESQL_DEFAULT_COST_PARAMS[name]) for name in candidates
        }
        requested_worker = args.worker_names.get(scenario)
        real_validation_worker: Optional[Dict[str, Any]]
        if vm_replay_enabled:
            worker, replay_worker_path = vm_replay_probe_worker(cfg, scenario)
            if requested_worker and requested_worker not in {
                str(worker.get("name") or ""),
                str(worker.get("vm_name") or ""),
            }:
                raise RuntimeError(
                    f"configured VM replay worker {requested_worker!r} not found for {scenario}; "
                    f"effective worker is {worker.get('name')!r}"
                )
            real_validation_worker = None
        elif requested_worker:
            configured_workers = (
                (((cfg.get("binary_plan_tuning") or {}).get("validation") or {}).get("docker_workers") or {}).get(
                    "workers"
                )
                or []
            )
            workers = [
                dict(item)
                for item in configured_workers
                if isinstance(item, Mapping) and str(item.get("name") or "") == requested_worker
            ]
            if not workers:
                raise RuntimeError(f"configured worker {requested_worker!r} not found for {scenario}")
            worker_index = min(DEFAULT_WORKER_INDEX[scenario], len(workers) - 1)
            worker = workers[worker_index]
            real_validation_worker = worker
        else:
            workers = validation_workers(cfg, scenario)
            if not workers:
                raise RuntimeError(f"no live validation worker for {scenario}")
            worker_index = min(DEFAULT_WORKER_INDEX[scenario], len(workers) - 1)
            worker = workers[worker_index]
            real_validation_worker = worker
        worker_pg = pg_conn_from_worker(cfg, worker)
        worker_admin_pg = replace(worker_pg, dbname="postgres")
        validation_index = 0

        def validator(
            _sql_set: Any,
            params: Dict[str, float],
            *,
            force_new_replay: bool = False,
        ) -> Dict[str, Any]:
            nonlocal validation_index
            # A resource-stressing candidate can legitimately OOM a PostgreSQL
            # backend.  Do not let the following Top-K candidate inherit the
            # server's crash-recovery window and become a false validation
            # failure.  This is transport readiness only; it neither retries
            # the bad candidate nor changes score, rank, or acceptance.
            # A generic seeded replay owns the complete VM lifecycle.  Its
            # reset callback starts the disposable guest and waits for the
            # database, so waiting here first deadlocks whenever the previous
            # failed attempt correctly left that guest stopped.  Docker/live
            # workers do not have that reset step and still need this guard.
            if not args.generic_vm_replay:
                wait_for_postgres(
                    worker_admin_pg,
                    timeout_s=args.postgres_ready_timeout,
                )
            # binary_plan_resource_tuner intentionally selects target-specific
            # HyBench workload overrides from the label prefix (cpu_, memory_,
            # io_, or none_). Keep that transport convention while all ranking,
            # scoring, and gates remain isolated in this new implementation.
            label = double_loop_validation_label(scenario, validation_index)
            validation_index += 1
            if args.generic_vm_replay:
                if not vm_replay_enabled:
                    raise RuntimeError("--generic-vm-replay requires validation.vm_replay")
                raw = run_generic_vm_replay_validation(
                    replay_worker_path,
                    scenario,
                    params,
                    output_dir=scenario_dir / "real_validations",
                    label=label,
                    restore_timeout_s=max(
                        1.0,
                        float(replay_cfg.get("snapshot_restore_timeout_s") or 600.0),
                    ),
                    drop_cache_timeout_s=max(
                        1.0,
                        float(replay_cfg.get("drop_cache_timeout_s") or 120.0),
                    ),
                    workload_timeout_s=max(
                        1.0,
                        float(replay_cfg.get("workload_timeout_s") or 900.0),
                    ),
                    postgres_ready_timeout_s=args.postgres_ready_timeout,
                    force_new_replay=force_new_replay,
                )
            else:
                raw = run_real_validation_safe(
                    cfg,
                    params,
                    output_dir=scenario_dir / "real_validations",
                    label=label,
                    worker=real_validation_worker,
                    force_new_replay=force_new_replay,
                )
            fatal_workload_error = fatal_replay_workload_error(raw)
            if fatal_workload_error:
                raise RuntimeError(
                    "real replay rejected by fail-closed workload gate: "
                    f"{fatal_workload_error}"
                )
            # The legacy transport may label a row failed because of one of its
            # monitor/sample-count gates. Those gates are explicitly outside this
            # algorithm. A row is usable exactly when the four real objective
            # measurements exist and are finite; transport status remains audit
            # metadata and never becomes a candidate decision condition.
            measurement = measurement_from_payload(raw.get("resources") or {}, raw)
            invalid_reason = measurement_invalid_reason(measurement, raw)
            raw["double_loop_measurement_usable"] = not invalid_reason
            raw["double_loop_measurement_invalid_reason"] = invalid_reason
            raw["double_loop_transport_warning"] = (
                str(raw.get("error") or "") if raw.get("status") != "ok" else ""
            )
            if not invalid_reason and deferred_dynamic_sweet_score_error(scenario, raw):
                raw["double_loop_transport_status_original"] = raw.get("status")
                raw["double_loop_transport_status_normalized"] = True
                raw["double_loop_score_deferred_until_fresh_baseline"] = True
                raw["status"] = "ok"
                raw["error"] = None
            if invalid_reason:
                raw["double_loop_recovery_wait_attempted"] = True
                try:
                    wait_for_primary_postgres(
                        worker_admin_pg,
                        timeout_s=args.postgres_ready_timeout,
                    )
                    raw["double_loop_recovery_wait_succeeded"] = True
                    raw["double_loop_recovery_wait_error"] = ""
                except Exception as recovery_exc:
                    raw["double_loop_recovery_wait_succeeded"] = False
                    raw["double_loop_recovery_wait_error"] = str(recovery_exc)
                write_json(scenario_dir / "real_validations" / f"{label}.json", raw)
                raise RuntimeError(f"real validation measurement unusable: {invalid_reason}")
            raw["double_loop_recovery_wait_attempted"] = False
            write_json(scenario_dir / "real_validations" / f"{label}.json", raw)
            return raw

        # The fresh default-parameter validation happens before workload pruning so
        # pg_stat_statements reflects this scenario's just-executed workload.
        sql_set = {
            "scenario": scenario,
            "config_path": str(config_path),
            "worker": worker.get("name"),
            "validation_transport": "vm_replay" if vm_replay_enabled else "docker_worker",
            "vm_replay_worker_config": (
                str(replay_worker_path) if vm_replay_enabled else None
            ),
        }

        def capture_outer_round_evidence(
            _sql_set: Any,
            round_index: int,
            params: Dict[str, float],
            _raw: Any,
        ) -> Dict[str, Any]:
            worker_cfg = config_for_worker(cfg, worker)
            probe = PlanFeatureProbe(worker_cfg)
            queries, sampling = workload_queries_with_sampling(worker_cfg, probe)
            evidence = probe.probe(queries, params)
            signatures = evidence.get("plan_signatures") or {}
            samples = [
                json.dumps(
                    {"query": query, "plan_signature": signature},
                    ensure_ascii=False,
                    sort_keys=True,
                )
                for query, signature in list(signatures.items())[:20]
            ]
            return {
                "explain_samples": samples,
                "explain_capture": {
                    "round": round_index,
                    "mode": "EXPLAIN_FORMAT_JSON_plan_signature",
                    "query_sampling": sampling,
                    "query_count": evidence.get("query_count"),
                    "explained_query_count": evidence.get("explained_query_count"),
                    "error_count": len(evidence.get("errors") or []),
                    "errors": evidence.get("errors") or [],
                },
            }

        outer_round_evidence_func = capture_outer_round_evidence

    # A generic VM replay restores PostgreSQL and drops the guest cache before
    # every measurement, but the rootless QEMU block path can still populate
    # its host-side cache during the very first workload.  If that first run is
    # used as the formal baseline, later candidates are compared with a
    # different storage state and the fresh parameter->resource model receives
    # an unlearnable time-order discontinuity.  Run one discarded default
    # replay first so that the scored baseline and every subsequent replay use
    # the same warmed VM storage state.  This is explicitly not a calibration
    # sample and is never added to the model, score, Top-K, or outer loop.
    if args.mode == "live" and args.pre_baseline_warmup:
        warmup_raw = validator(sql_set, dict(base_params))
        write_json(
            scenario_dir / "pre_baseline_warmup.json",
            {
                "event": "pre_baseline_warmup",
                "scenario": scenario,
                "params": dict(base_params),
                "used_for_baseline": False,
                "used_for_model_calibration": False,
                "used_for_optimizer_candidate_selection": False,
                "used_for_outer_loop": False,
                "raw": warmup_raw,
            },
        )
    baseline_raw = validator(sql_set, dict(base_params))
    baseline_measurement = measurement_from_payload(
        baseline_raw.get("resources") or baseline_raw.get("resource") or {}, baseline_raw
    )
    target_sweet_upper, physical_semantics = physical_target_sweet_upper(
        scenario,
        cfg,
        baseline_measurement,
        worker=worker if args.mode == "live" else None,
        explicit_upper=args.target_sweet_uppers.get(scenario),
        min_target_drop_ratio=args.min_target_drop_ratio,
        max_target_drop_ratio=args.max_target_drop_ratio,
        simulated=args.mode == "simulated",
    )
    scoring_resource_limits = (
        {
            name: (
                float(target_sweet_upper)
                if name == scenario and target_sweet_upper is not None
                else float(getattr(baseline_measurement, name))
            )
            for name in ("cpu", "memory", "io")
        }
        if args.mode == "simulated"
        else sigmoid_linear_resource_limits(
            scenario,
            cfg,
            worker,
            explicit_target_upper=target_sweet_upper,
        )
    )
    physical_semantics["scoring_formula"] = "sigmoid_linear_sweet_band_weighted_mean_v2"
    physical_semantics["scoring_resource_limits"] = dict(scoring_resource_limits)
    write_json(
        scenario_dir / "fresh_baseline.json",
        {
            "event": "fresh_baseline",
            "scenario": scenario,
            "params": base_params,
            "measurement": baseline_measurement.as_resource(),
            "physical_semantics": physical_semantics,
            "raw": baseline_raw,
        },
    )

    if args.mode == "live":
        active = fresh_workload_pruning(
            scenario,
            cfg,
            worker,
            candidates,
            raw_ranges,
            base_params,
            active_k=args.active_k,
            max_workers=args.pruning_workers,
            output_path=scenario_dir / "fresh_workload_pruning.json",
        )
    objective = FreshBaselineObjective(
        scenario,
        baseline_measurement,
        score_policy=args.score_policy,
        resource_limits=scoring_resource_limits,
        min_qps_gain_ratio=args.min_qps_gain_ratio,
        min_target_drop_ratio=args.min_target_drop_ratio,
        max_target_drop_ratio=args.max_target_drop_ratio,
        target_sweet_lower=physical_semantics.get("target_sweet_lower"),
        target_sweet_upper=target_sweet_upper,
        target_sweet_weight=args.target_sweet_weight,
        resource_bonus_saturation_ratio=args.resource_bonus_saturation_ratio,
        non_target_bonus_weight=args.non_target_bonus_weight,
        none_resource_bonus_weights={
            "cpu": args.none_cpu_bonus_weight,
            "memory": args.none_memory_bonus_weight,
            "io": args.none_io_bonus_weight,
        },
        io_reward_weight=args.io_reward_weight,
        none_cpu_memory_balance_weight=args.none_cpu_memory_balance_weight,
        none_io_usage_weight=args.none_io_usage_weight,
        constraint_violation_weight=args.constraint_violation_weight,
    )
    model = FreshExperienceModel(
        objective,
        {name: raw_ranges[name] for name in active},
        exploration_weight=args.exploration_weight,
        max_neighbors=args.model_max_neighbors,
    )
    model.add(base_params, baseline_measurement.as_resource(), baseline_raw)
    fixed_training_rows: Optional[List[FreshExperience]] = None
    training_seed_audit: Dict[str, Any]
    if args.model_training_seed_dir:
        seed_dir = Path(args.model_training_seed_dir).resolve()
        if seed_dir.name != scenario:
            seed_dir = seed_dir / scenario
        fixed_training_rows, training_seed_audit = load_anchored_resource_training_seed(
            model,
            seed_dir,
            include_real_candidates=bool(args.model_training_seed_include_candidates),
        )
    else:
        training_seed_audit = {
            "enabled": False,
            "reason": "no_model_training_seed_dir",
            "current_run_rows_before_calibration": len(model.snapshot()),
        }
    write_json(
        scenario_dir / "fresh_model_training_seed.json",
        json_value(training_seed_audit),
    )

    # The initial 15-point calibration is a deliberately broader admission
    # gate.  It prevents wasting a full grid search on a model that is clearly
    # unusable.  Even if this gate does not fully pass, we keep soft start
    # to preserve iteration momentum.
    quality_thresholds = FreshModelQualityThresholds(
        min_samples=args.model_quality_min_samples,
        max_relative_error_p95=args.model_quality_max_relative_error_p95,
        max_relative_error_max=args.model_quality_max_relative_error_max,
        min_r2=args.model_quality_min_r2,
        max_pairwise_inversion_rate=(
            args.model_quality_ranking_inversion_threshold
        ),
        score_tie_margin=args.model_quality_score_tie_margin,
    )
    online_quality_thresholds = FreshModelQualityThresholds(
        min_samples=args.model_quality_min_samples,
        max_relative_error_p95=args.model_quality_online_max_relative_error_p95,
        max_relative_error_max=args.model_quality_online_max_relative_error_max,
        min_r2=args.model_quality_online_min_r2,
        max_pairwise_inversion_rate=(
            args.model_quality_online_ranking_inversion_threshold
        ),
        score_tie_margin=args.model_quality_online_score_tie_margin,
    )
    model_quality_gate = run_fresh_model_quality_gate(
        model,
        validator,
        sql_set,
        base_params,
        raw_ranges,
        active,
        thresholds=quality_thresholds,
        replay_budget=args.model_quality_calibration_replays,
        grid_points=args.grid_points,
        scenario_dir=scenario_dir,
        fixed_training_rows=fixed_training_rows,
        training_seed_audit=training_seed_audit,
    )
    initial_error_bounds = preflight_resource_error_bounds(model_quality_gate)

    # Candidate interactions can expose extrapolation error that was absent
    # from the fixed coordinate Top15.  Re-run the exact same held-out resource
    # and score-ranking gates after each new batch of 15 real candidate rows.

    # otherwise we keep going and accumulate more evidence.
    online_batch_size = max(15, int(args.model_quality_min_samples))
    online_validation_rows: List[FreshExperience] = []
    online_prior_by_signature: Dict[
        Tuple[Tuple[str, float], ...], FreshExperience
    ] = {}

    def add_online_prior(row: FreshExperience) -> None:
        online_prior_by_signature[model._signature(row.params)] = row

    online_baseline_training_row = model.build_experience(
        base_params,
        baseline_measurement.as_resource(),
        baseline_raw,
    )
    # The online held-out gate must train from the exact resource-vector rows
    # that passed the pre-optimizer gate.  In resumed pooled designs those rows
    # are installed directly into ``model`` by the gate wrapper and are not the
    # same object as the runner's optional ``fixed_training_rows`` argument.
    # Reconstructing the prior only from calibration JSON would silently drop
    # the qualified pool (94 rows in the None run) and make the first online
    # Top15 look like a 15-on-15 cold start.  Snapshotting the qualified model
    # preserves parameter->resource evidence only; old ranks, scores, intervals,
    # decisions, and best-point labels still never enter the gate.
    preflight_model_rows = model.snapshot()
    for row in preflight_model_rows:
        add_online_prior(row)
    add_online_prior(online_baseline_training_row)
    for replay in model_quality_gate.get("calibration_replays") or []:
        if not isinstance(replay, Mapping):
            continue
        replay_params = replay.get("params")
        replay_measurement = replay.get("measurement")
        if isinstance(replay_params, Mapping) and isinstance(
            replay_measurement, Mapping
        ):
            add_online_prior(
                model.build_experience(replay_params, replay_measurement)
            )
    if (
        fixed_training_rows is not None
        and model_quality_gate.get("historical_training_rows_used") is True
    ):
        for row in fixed_training_rows:
            add_online_prior(row)

    online_gate_state: Dict[str, Any] = {
        "enabled": True,
        "batch_size": online_batch_size,
        "same_thresholds_as_preoptimizer": False,
        "preflight_thresholds": json_value(asdict(quality_thresholds)),
        "online_thresholds": json_value(asdict(online_quality_thresholds)),
        "preflight_model_prior_sample_count": len(online_prior_by_signature),
        "preflight_prior_source": "qualified_model_parameter_to_resource_snapshot",
        "attempt_count": 0,
        "passed_attempt_count": 0,
        "failed_attempt_count": 0,
        "consecutive_failed_batches": 0,
        "hard_failures_allowed": max(
            0, int(args.model_quality_online_hard_failures_allowed)
        ),
        "blocked": False,
        "last_attempt": None,
        "jsonl": str(scenario_dir / "fresh_model_online_quality.jsonl"),
    }
    model_quality_gate["online_retraining"] = online_gate_state

    def guarded_model_predict(
        current_sql_set: Any,
        params: Dict[str, float],
    ) -> Dict[str, Any]:
        if online_gate_state["blocked"]:
            last = online_gate_state.get("last_attempt") or {}
            raise RuntimeError(
                "online resource model quality gate blocks the next small loop: "
                f"{last.get('reason') or 'no mode passed'}"
            )
        return model.predict(current_sql_set, params)

    def real_validate(current_sql_set: Any, params: Dict[str, float]) -> Dict[str, Any]:
        raw = validator(current_sql_set, params)
        resource = raw.get("resources") or raw.get("resource") or {}
        experience = model.build_experience(params, resource, raw)
        model.add(params, resource, raw)
        online_validation_rows.append(experience)
        if len(online_validation_rows) >= online_batch_size:
            quality = model.select_quality_mode(
                online_quality_thresholds,
                validation_rows=online_validation_rows,
                fixed_training_rows=list(online_prior_by_signature.values()),
                always_training_rows=[online_baseline_training_row],
            )
            selected = quality.get("selected_evaluation") or {}
            attempt = {
                "event": "fresh_model_online_quality_gate",
                "scenario": scenario,
                "attempt": int(online_gate_state["attempt_count"]) + 1,
                "validation_sample_count": len(online_validation_rows),
                "fixed_training_sample_count": len(online_prior_by_signature),
                "passed": bool(quality.get("passed")),
                "reason": quality.get("reason"),
                "selected_mode": json_value(quality.get("selected_mode")),
                "selected_training_strategy": quality.get(
                    "selected_training_strategy"
                ),
                "selected_resource_metrics": json_value(
                    selected.get("resource_metrics") or {}
                ),
                "selected_pairwise_inversion_rate": selected.get(
                    "pairwise_inversion_rate"
                ),
                "selected_ranking_prefixes": json_value(
                    selected.get("ranking_prefixes") or {}
                ),
                "candidate_mode_count": len(quality.get("mode_evaluations") or []),
                "candidate_mode_evaluation_error_count": sum(
                    1
                    for item in quality.get("mode_evaluations") or []
                    if item.get("evaluation_error")
                ),
                "thresholds": json_value(quality.get("thresholds") or {}),
                "score_formula": "sigmoid_linear_sweet_band_weighted_mean_v2",
                "qps_used_in_score": False,
            }
            append_jsonl(
                scenario_dir / "fresh_model_online_quality.jsonl",
                attempt,
            )
            online_gate_state["attempt_count"] += 1
            online_gate_state["last_attempt"] = attempt
            if attempt["passed"]:
                online_gate_state["passed_attempt_count"] += 1
                online_gate_state["consecutive_failed_batches"] = 0
                online_gate_state["blocked"] = False
                for row in online_validation_rows:
                    add_online_prior(row)
                online_validation_rows.clear()
            else:
                online_gate_state["failed_attempt_count"] += 1
                online_gate_state["consecutive_failed_batches"] += 1
                # Failures are first treated as soft signals.  Only repeated
                # failures trigger hard block to avoid losing momentum due to one-shot
                # noise in quality estimation.
                hard_failures_allowed = int(online_gate_state["hard_failures_allowed"])
                online_gate_state["blocked"] = (
                    hard_failures_allowed > 0
                    and online_gate_state["consecutive_failed_batches"]
                    >= hard_failures_allowed
                )
            write_json(
                scenario_dir / "fresh_model_quality_gate.json",
                json_value(model_quality_gate),
            )
        return raw

    optimizer = DoubleLoopGridOptimizer(
        guarded_model_predict,
        real_validate,
        {"cpu": 1.0, "memory": 1.0, "io": 1.0},
        candidate_seed_points=[
            dict(replay["params"])
            for replay in model_quality_gate.get("calibration_replays") or []
            if replay.get("status") == "usable"
            and isinstance(replay.get("params"), Mapping)
        ],
        outer_round_evidence_func=outer_round_evidence_func,
        workload_pruner=lambda _sql_set, names: [name for name in active if name in names],
        real_score_func=objective.score,
        resource_box_upper_bound_func=objective.optimistic_score_in_resource_box,
        initial_resource_error_bounds=initial_error_bounds,
        preflight_quality_gate=model_quality_gate,
        replay_continuation_required_func=lambda: {
            "required": bool(online_gate_state["blocked"]),
            "attempt_count": int(online_gate_state["attempt_count"]),
            "failed_attempt_count": int(online_gate_state["failed_attempt_count"]),
            "last_reason": (
                (online_gate_state.get("last_attempt") or {}).get("reason")
            ),
            "next_action": "expand_real_replay_k_and_retry_same_quality_gate",
        },
        grid_config=GridInnerLoopConfig(
            max_workers=args.inner_workers,
            max_passes=args.max_iter,
            no_improvement_patience=args.convergence_patience,
            combination_probe_enabled=True,
            combination_min_size=2,
            combination_max_size=args.combination_max_size,
            combination_values_per_param=args.combination_values_per_param,
            combination_max_candidates=args.combination_max_candidates,
        ),
        outer_config=OuterLoopConfig(
            max_rounds=effective_outer_round_budget(
                raw_ranges,
                active,
                args.epsilon,
                args.outer_rounds,
            ),
            real_validation_top_k=args.real_top_k,
            real_validation_top_fraction=1.0,
            # The shared core ranks the deduplicated union of coordinate and
            # interaction candidates. Replay uses a fixed prefix bounded by
            # the global Top-5 safety cap; formal runs use the complete Top-5.
            topk_controller="fixed",
            adaptive_topk_min_k=args.adaptive_topk_min_k,
            adaptive_topk_batch_size=args.adaptive_topk_batch_size,
            adaptive_topk_inversion_threshold=args.adaptive_topk_inversion_threshold,
            adaptive_topk_score_tie_margin=args.model_quality_score_tie_margin,
            selection_policy=args.selection_policy,
            no_improvement_patience=args.no_improvement_patience,
            epsilon=args.epsilon,
            target_resource=scenario if scenario in {"cpu", "memory", "io"} else None,
        ),
        verbose=True,
    )
    # Record optimizer readiness after preflight, residual-box extraction,
    # and optimizer construction have completed.
    model_quality_gate["optimizer_started"] = True
    write_json(
        scenario_dir / "fresh_model_quality_gate.json",
        json_value(model_quality_gate),
    )
    ranges = grid_parameter_ranges(
        cfg,
        raw_ranges,
        candidates,
        default_discrete_count=args.grid_points,
    )
    result = optimizer.optimize(
        base_params,
        ranges,
        sql_set,
        candidate_params=candidates,
        initial_real_resource=baseline_raw,
        log_path=str(scenario_dir / "optimizer.jsonl"),
        inner_iteration_log_path=str(scenario_dir / "inner_iterations.jsonl"),
    )
    final_confirmation_raw: Optional[Mapping[str, Any]] = None
    final_confirmation_error = ""
    # Search termination and final-goal validation are separate concerns.  Max
    # rounds or rejection patience still leave a well-defined last accepted
    # anchor, and that algorithm output must receive one independent final replay.
    if result.successful:
        try:
            # Independent post-search replay. It never feeds back into the
            # small-loop score, p* selection, outer acceptance, or range shrink.
            final_confirmation_raw = (
                validator(
                    sql_set,
                    result.optimized_params,
                    force_new_replay=True,
                )
                if args.mode == "live"
                else validator(sql_set, result.optimized_params)
            )
            if not final_confirmation_is_independent(final_confirmation_raw):
                raise RuntimeError(
                    "post-convergence confirmation reused a candidate measurement"
                )
            confirmed_measurement = measurement_from_payload(
                final_confirmation_raw.get("resources") or {}, final_confirmation_raw
            )
            write_json(
                scenario_dir / "final_confirmation.json",
                {
                    "event": "double_loop_final_confirmation",
                    "scenario": scenario,
                    "confirmation_kind": (
                        "post_convergence"
                        if result.stop_reason == "epsilon_converged"
                        else "nonconverged_diagnostic"
                    ),
                    "eligible_to_mark_algorithm_success": (
                        result.stop_reason == "epsilon_converged"
                    ),
                    "search_stop_reason": result.stop_reason,
                    "score_policy": args.score_policy,
                    "params": result.optimized_params,
                    "measurement": confirmed_measurement.as_resource(),
                    "resource_score_decision": json_value(
                        objective.decide(confirmed_measurement)
                    ),
                    "outer_semantic_decision": json_value(
                        objective.outer_decide(confirmed_measurement)
                    ),
                    "final_goal_decision": json_value(
                        objective.final_decide(confirmed_measurement)
                    ),
                    "raw": final_confirmation_raw,
                    "independent_real_replay": True,
                    "used_for_candidate_selection": False,
                },
            )
        except Exception as exc:
            final_confirmation_error = f"{type(exc).__name__}: {exc}"
            write_json(
                scenario_dir / "final_confirmation.json",
                {
                    "event": "double_loop_final_confirmation",
                    "scenario": scenario,
                    "score_policy": args.score_policy,
                    "params": result.optimized_params,
                    "error": final_confirmation_error,
                    "used_for_candidate_selection": False,
                },
            )
    else:
        write_json(
            scenario_dir / "final_confirmation.json",
            {
                "event": "double_loop_final_confirmation",
                "scenario": scenario,
                "status": "skipped",
                "reason": "no_outer_accepted_candidate",
                "stop_reason": result.stop_reason,
                "used_for_candidate_selection": False,
            },
        )
    summary = compact_result(
        scenario,
        baseline_raw,
        objective,
        result,
        active_params=active,
        run_dir=scenario_dir,
        selection_policy=args.selection_policy,
        physical_semantics=physical_semantics,
        model_quality_gate=model_quality_gate,
        final_confirmation_raw=final_confirmation_raw,
        final_confirmation_error=final_confirmation_error,
    )
    write_json(scenario_dir / "scenario_summary.json", summary)
    return summary


def parse_scenario_configs(values: Sequence[str], fallback: str) -> Dict[str, Path]:
    result = {scenario: Path(fallback).resolve() for scenario in SCENARIOS} if fallback else {}
    for value in values:
        scenario, separator, raw_path = value.partition("=")
        scenario = scenario.strip().lower()
        if not separator or scenario not in SCENARIOS or not raw_path.strip():
            raise ValueError(f"invalid --scenario-config: {value}")
        result[scenario] = Path(raw_path.strip()).resolve()
    return result


def parse_scenario_values(values: Sequence[str], *, option: str) -> Dict[str, str]:
    """Parse repeatable SCENARIO=VALUE options without inventing defaults."""
    result: Dict[str, str] = {}
    for value in values:
        scenario, separator, raw_value = value.partition("=")
        scenario = scenario.strip().lower()
        if not separator or scenario not in SCENARIOS or not raw_value.strip():
            raise ValueError(f"invalid {option}: {value}")
        result[scenario] = raw_value.strip()
    return result


def write_markdown(output_root: Path, summaries: Sequence[Mapping[str, Any]]) -> None:
    lines = [
        "# Double-loop Coordinate-grid Four-scenario Run",
        "",
        "| Scenario | Priority | Success | Fresh QPS | Final QPS | Parameter subsets/round | Rounds | Real candidates |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    by_name = {str(row.get("scenario")): row for row in summaries}
    for scenario in SCENARIOS:
        row = by_name.get(scenario, {"scenario": scenario, "status": "missing"})
        baseline = row.get("fresh_baseline") or {}
        final = row.get("final_real") or {}
        lines.append(
            f"| {scenario} | {row.get('priority', '')} | {row.get('successful', False)} | "
            f"{baseline.get('db_qps', '')} | {final.get('db_qps', '')} | "
            f"{row.get('parameter_subsets_by_round', '')} | {row.get('outer_round_count', '')} | "
            f"{row.get('real_candidate_validation_count', '')} |"
        )
        if row.get("status") == "failed":
            lines.append(f"\n- `{scenario}` failed: `{row.get('error')}`")
    (output_root / "four_scenario_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--mode", choices=("live", "simulated"), default="live")
    parser.add_argument("--config", default="config/binary_plan_tuning_hybenchxp.json")
    parser.add_argument("--scenario-config", action="append", default=[], metavar="SCENARIO=PATH")
    parser.add_argument("--worker-config", default="")
    parser.add_argument(
        "--generic-vm-replay",
        action="store_true",
        help=(
            "execute benchmark.workload_cmd from the target VM worker config; "
            "used for JOB, TPC-H, TPC-C and explicit HyBench AP/TP profiles"
        ),
    )
    parser.add_argument(
        "--model-training-seed-dir",
        default="",
        help=(
            "optional root/scenario directory containing only baseline-anchored "
            "parameter-to-resource training rows; current Top15 remains a strict holdout"
        ),
    )
    parser.add_argument(
        "--model-training-seed-include-candidates",
        action="store_true",
        help=(
            "also load prior real candidate parameter/resource rows as training "
            "support; old Scores, ranks and decisions remain excluded"
        ),
    )
    parser.add_argument(
        "--worker-name",
        action="append",
        default=[],
        metavar="SCENARIO=NAME",
        help="explicitly select a configured worker; useful when a target's default worker is busy",
    )
    parser.add_argument(
        "--force-active-param",
        action="append",
        default=[],
        metavar="SCENARIO=PARAM[,PARAM...]",
        help=(
            "disabled for the isolated double-loop algorithm: active dimensions "
            "must come only from this invocation's fresh workload pruning"
        ),
    )
    parser.add_argument("--live-run-mins", type=int, default=0)
    parser.add_argument(
        "--postgres-ready-timeout",
        type=float,
        default=300.0,
        help="seconds to wait for PostgreSQL recovery between real Top-K replays",
    )
    parser.add_argument(
        "--workload-xtpclient",
        action="append",
        default=[],
        metavar="SCENARIO=COUNT",
        help="current-run HyBench XP transactional-client override per scenario",
    )
    parser.add_argument(
        "--workload-xapclient",
        action="append",
        default=[],
        metavar="SCENARIO=COUNT",
        help="current-run HyBench XP analytical-client override per scenario",
    )
    parser.add_argument(
        "--workload-ap-query-ids",
        action="append",
        default=[],
        metavar="SCENARIO=CSV",
        help="current-run HyBench XP AP query-id mix override per scenario",
    )
    parser.add_argument(
        "--workload-ap-query-weights",
        action="append",
        default=[],
        metavar="SCENARIO=CSV",
        help="current-run HyBench XP AP query weight override per scenario",
    )
    parser.add_argument(
        "--workload-ap-query-sequence",
        action="append",
        default=[],
        metavar="SCENARIO=CSV",
        help="current-run fixed AP query sequence override per scenario",
    )
    parser.add_argument(
        "--workload-statement-sql-file",
        action="append",
        default=[],
        metavar="SCENARIO=AP-N:PATH[,AP-N:PATH...]",
        help="current-run HyBench statement SQL-file overrides per scenario",
    )
    parser.add_argument(
        "--min-qps-gain-ratio",
        type=float,
        default=0.15,
        help="post-convergence final replay DB QPS gain threshold; outer acceptance only requires QPS > baseline",
    )
    parser.add_argument(
        "--min-target-drop-ratio",
        type=float,
        default=0.05,
        help="small-loop score shaping threshold; outer acceptance only requires target < baseline",
    )
    parser.add_argument(
        "--max-target-drop-ratio",
        type=float,
        default=0.10,
        help="post-convergence target bottleneck drop must not exceed 0.10 ratio",
    )
    parser.add_argument(
        "--selection-policy",
        choices=("estimated_top1", "real_score_topk"),
        default="real_score_topk",
        help="A: replay estimated Top1 only; B: replay TopK and select p* by real physical score",
    )
    parser.add_argument(
        "--score-policy",
        choices=("sigmoid_linear_resource",),
        default="sigmoid_linear_resource",
        help=(
            "one sigmoid-linear formula for IDW-estimated and real replay resources; "
            "DB QPS is excluded from candidate scoring"
        ),
    )
    parser.add_argument(
        "--io-reward-weight",
        type=float,
        default=FreshBaselineObjective.DEFAULT_IO_REWARD_WEIGHT,
        help=(
            "IO weight when IO is non-target (and in None); IO-target sweet-band "
            "score always keeps unit positive and negative weight"
        ),
    )
    parser.add_argument(
        "--target-sweet-upper",
        action="append",
        default=[],
        metavar="SCENARIO=VALUE",
        help="optional physical sweet upper override in cores/GiB/MiB/s",
    )
    parser.add_argument(
        "--target-sweet-weight",
        type=float,
        default=FreshBaselineObjective.DEFAULT_TARGET_SWEET_WEIGHT,
        help="compatibility option; must remain 1 because target sweet score is unit weighted",
    )
    parser.add_argument(
        "--resource-bonus-saturation-ratio",
        type=float,
        default=FreshBaselineObjective.DEFAULT_RESOURCE_BONUS_SATURATION_RATIO,
        help="positive non-target resource gain at which its optional bonus saturates",
    )
    parser.add_argument(
        "--non-target-bonus-weight",
        type=float,
        default=FreshBaselineObjective.DEFAULT_NON_TARGET_BONUS_WEIGHT,
    )
    parser.add_argument("--none-cpu-bonus-weight", type=float, default=0.05)
    parser.add_argument("--none-memory-bonus-weight", type=float, default=0.10)
    parser.add_argument("--none-io-bonus-weight", type=float, default=0.02)
    parser.add_argument(
        "--none-cpu-memory-balance-weight",
        type=float,
        default=FreshBaselineObjective.DEFAULT_NONE_CPU_MEMORY_BALANCE_WEIGHT,
        help="soft penalty per unit of positive CPU/Memory gain imbalance versus fresh baseline",
    )
    parser.add_argument(
        "--none-io-usage-weight",
        type=float,
        default=FreshBaselineObjective.DEFAULT_NONE_IO_USAGE_WEIGHT,
        help="soft cost per unit of IO gain; a real IO decrease becomes a saving reward",
    )
    parser.add_argument(
        "--constraint-violation-weight",
        type=float,
        default=FreshBaselineObjective.DEFAULT_CONSTRAINT_VIOLATION_WEIGHT,
        help="score-only training penalty for unmet fresh success constraints",
    )
    parser.add_argument("--scenarios", default=",".join(SCENARIOS))
    parser.add_argument("--output-dir", default="")
    parser.add_argument(
        "--active-k",
        type=int,
        default=7,
        help="legacy audit field only; fresh endpoint feature pruning determines every active parameter",
    )
    parser.add_argument("--pruning-workers", type=int, default=4)
    parser.add_argument("--inner-workers", type=int, default=8)
    parser.add_argument("--max-parallel-scenarios", type=int, default=4)
    parser.add_argument(
        "--max-iter",
        type=int,
        default=20,
        help="maximum coordinate-grid passes in each small loop",
    )
    parser.add_argument(
        "--grid-points",
        type=int,
        default=15,
        help="points per active coordinate and resolution of the fixed Top15 calibration design",
    )
    parser.add_argument(
        "--combination-max-size",
        type=int,
        default=4,
        help="largest interaction subset probed after coordinate scans (minimum is fixed at 2)",
    )
    parser.add_argument(
        "--combination-values-per-param",
        type=int,
        default=3,
        help="representative grid values per dimension for 2--4D interaction probes",
    )
    parser.add_argument(
        "--combination-max-candidates",
        type=int,
        default=0,
        help="interaction candidate cap; 0 means no artificial truncation",
    )
    parser.add_argument(
        "--outer-rounds",
        type=int,
        default=0,
        help="outer safety limit; 0 derives a convergence-capable budget from ranges and epsilon",
    )
    parser.add_argument(
        "--real-top-k",
        type=int,
        default=5,
        help="real replays per scenario/round; fixed formal budget is Top-5",
    )
    parser.add_argument(
        "--real-top-fraction",
        type=float,
        default=1.0,
        help=(
            "legacy fixed-controller prefix cap; ignored by decision_tree, whose "
            "candidate universe is bounded by --real-top-k"
        ),
    )
    parser.add_argument(
        "--no-improvement-patience",
        type=int,
        default=0,
        help="optional consecutive outer rejection stop; 0 keeps returning to the small loop",
    )
    parser.add_argument("--convergence-patience", type=int, default=2)
    parser.add_argument(
        "--exploration-weight",
        type=float,
        default=0.25,
        help=(
            "round-0 candidate-generation acquisition only; excluded from both "
            "estimated Top-K score and real replay score"
        ),
    )
    parser.add_argument(
        "--model-max-neighbors",
        type=int,
        default=2,
        help="nearest current-run measured resource vectors used by the local IDW predictor",
    )
    parser.add_argument("--model-quality-min-samples", type=int, default=15)
    parser.add_argument(
        "--model-quality-calibration-replays",
        type=int,
        default=15,
        help=(
            "fixed current-run calibration replay count; the fresh baseline is "
            "separate from these replays"
        ),
    )
    parser.add_argument(
        "--model-quality-max-relative-error-p95",
        type=float,
        default=1.0,
    )
    parser.add_argument(
        "--model-quality-max-relative-error-max",
        type=float,
        default=5.0,
    )
    parser.add_argument("--model-quality-min-r2", type=float, default=0.0)
    parser.add_argument(
        "--model-quality-ranking-inversion-threshold",
        type=float,
        default=0.10,
    )
    parser.add_argument(
        "--model-quality-score-tie-margin",
        type=float,
        default=0.01,
        help="shared-Score differences at or below this margin are ranking ties",
    )
    parser.add_argument(
        "--model-quality-online-max-relative-error-p95",
        type=float,
        default=1.0,
        help="strict post-TopK replay resource-error P95 gate",
    )
    parser.add_argument(
        "--model-quality-online-max-relative-error-max",
        type=float,
        default=5.0,
        help="strict post-TopK replay resource-error maximum gate",
    )
    parser.add_argument(
        "--model-quality-online-min-r2",
        type=float,
        default=0.0,
        help="strict post-TopK replay per-resource R2 lower bound",
    )
    parser.add_argument(
        "--model-quality-online-ranking-inversion-threshold",
        type=float,
        default=0.10,
        help="strict post-TopK replay shared-Score inversion-rate gate",
    )
    parser.add_argument(
        "--model-quality-online-score-tie-margin",
        type=float,
        default=0.01,
        help="strict post-TopK replay shared-Score tie margin",
    )
    parser.add_argument(
        "--model-quality-online-hard-failures-allowed",
        type=int,
        default=0,
        help=(
            "online quality gate 0 "
            ""
        ),
    )
    parser.add_argument(
        "--skip-pre-baseline-warmup",
        dest="pre_baseline_warmup",
        action="store_false",
        help=(
            "skip the discarded default replay that equalizes the VM storage "
            "state before the scored fresh baseline"
        ),
    )
    parser.set_defaults(pre_baseline_warmup=True)
    parser.add_argument(
        "--topk-controller",
        choices=("decision_tree", "fixed"),
        default="fixed",
        help="decision tree controls replay K only; it never predicts candidate score",
    )
    parser.add_argument("--adaptive-topk-min-k", type=int, default=5)
    parser.add_argument("--adaptive-topk-batch-size", type=int, default=1)
    parser.add_argument(
        "--adaptive-topk-inversion-threshold",
        type=float,
        default=0.10,
    )
    parser.add_argument("--epsilon", type=float, default=1e-4)
    args = parser.parse_args()

    scenarios = [value.strip().lower() for value in args.scenarios.split(",") if value.strip()]
    if not scenarios or any(value not in SCENARIOS for value in scenarios):
        raise ValueError("--scenarios must contain cpu,memory,io,none values")
    if args.force_active_param:
        raise ValueError(
            "--force-active-param is forbidden: isolated double-loop runs must use "
            "this invocation's fresh workload-pruning result without manual replacement"
        )
    if args.active_k < 1:
        raise ValueError("--active-k must be positive")
    if args.outer_rounds < 0:
        raise ValueError("--outer-rounds cannot be negative")
    if args.model_max_neighbors < 1:
        raise ValueError("--model-max-neighbors must be positive")
    if args.model_quality_min_samples < 15:
        raise ValueError("--model-quality-min-samples must be at least 15 for Top15 ordering validation")
    if args.model_quality_min_samples > 16:
        raise ValueError(
            "--model-quality-min-samples cannot exceed 16 (fresh baseline + 15 calibration replays)"
        )
    if args.model_quality_calibration_replays != 15:
        raise ValueError(
            "--model-quality-calibration-replays is fixed at 15; optimizer TopK "
            "uses the independent --adaptive-topk-min-k/--real-top-k budget"
        )
    if args.grid_points < 2:
        raise ValueError("--grid-points must be at least 2")
    if args.max_iter < 1 or args.convergence_patience < 1:
        raise ValueError("--max-iter and --convergence-patience must be positive")
    if not 2 <= args.combination_max_size <= 4:
        raise ValueError("--combination-max-size must be in [2,4]")
    if not 2 <= args.combination_values_per_param <= 5:
        raise ValueError("--combination-values-per-param must be in [2,5]")
    if args.combination_max_candidates < 0:
        raise ValueError("--combination-max-candidates cannot be negative")
    if args.real_top_k != 5:
        raise ValueError("--real-top-k is fixed at 5 for formal replay")
    if args.topk_controller != "fixed":
        raise ValueError("--topk-controller must be fixed for bounded real replay")
    for name in (
        "model_quality_max_relative_error_p95",
        "model_quality_max_relative_error_max",
        "model_quality_online_max_relative_error_p95",
        "model_quality_online_max_relative_error_max",
    ):
        value = float(getattr(args, name))
        if not math.isfinite(value) or value < 0.0:
            raise ValueError(f"--{name.replace('_', '-')} must be finite and non-negative")
    for name in ("model_quality_min_r2", "model_quality_online_min_r2"):
        if not math.isfinite(getattr(args, name)):
            raise ValueError(f"--{name.replace('_', '-')} must be finite")
    for name in (
        "model_quality_ranking_inversion_threshold",
        "model_quality_online_ranking_inversion_threshold",
    ):
        value = float(getattr(args, name))
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"--{name.replace('_', '-')} must be in [0,1]")
    for name in (
        "model_quality_score_tie_margin",
        "model_quality_online_score_tie_margin",
    ):
        value = float(getattr(args, name))
        if not math.isfinite(value) or value < 0.0:
            raise ValueError(
                f"--{name.replace('_', '-')} must be finite and non-negative"
            )
    if args.adaptive_topk_min_k != 5:
        raise ValueError("--adaptive-topk-min-k is fixed at 5")
    if args.adaptive_topk_batch_size < 1:
        raise ValueError("--adaptive-topk-batch-size must be positive")
    if not 0.0 <= args.adaptive_topk_inversion_threshold <= 1.0:
        raise ValueError("--adaptive-topk-inversion-threshold must be in [0,1]")
    if args.model_quality_online_hard_failures_allowed < 0:
        raise ValueError(
            "--model-quality-online-hard-failures-allowed cannot be negative"
        )
    if not math.isfinite(args.postgres_ready_timeout) or args.postgres_ready_timeout <= 0.0:
        raise ValueError("--postgres-ready-timeout must be finite and positive")
    if args.min_qps_gain_ratio < FreshBaselineObjective.MIN_REQUIRED_QPS_GAIN_RATIO:
        raise ValueError("--min-qps-gain-ratio cannot be lower than 0.15")
    if args.min_target_drop_ratio < FreshBaselineObjective.MIN_REQUIRED_TARGET_DROP_RATIO:
        raise ValueError("--min-target-drop-ratio cannot be lower than 0.05")
    if args.max_target_drop_ratio > FreshBaselineObjective.MAX_REQUIRED_TARGET_DROP_RATIO:
        raise ValueError("--max-target-drop-ratio cannot exceed 0.10")
    if args.max_target_drop_ratio < args.min_target_drop_ratio:
        raise ValueError("--max-target-drop-ratio cannot be below --min-target-drop-ratio")
    if not math.isclose(args.target_sweet_weight, 1.0, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError("--target-sweet-weight must remain 1 for the unified formula")
    if (
        not math.isfinite(args.resource_bonus_saturation_ratio)
        or args.resource_bonus_saturation_ratio <= 0.0
    ):
        raise ValueError("--resource-bonus-saturation-ratio must be finite and positive")
    for name in (
        "target_sweet_weight",
        "non_target_bonus_weight",
        "none_cpu_bonus_weight",
        "none_memory_bonus_weight",
        "none_io_bonus_weight",
        "none_cpu_memory_balance_weight",
        "none_io_usage_weight",
        "constraint_violation_weight",
        "io_reward_weight",
    ):
        value = float(getattr(args, name))
        if not math.isfinite(value) or value < 0.0:
            raise ValueError(f"--{name.replace('_', '-')} must be finite and non-negative")
    args.worker_names = parse_scenario_values(args.worker_name, option="--worker-name")
    args.forced_active_params = {}
    raw_xtpclients = parse_scenario_values(
        args.workload_xtpclient,
        option="--workload-xtpclient",
    )
    raw_xapclients = parse_scenario_values(
        args.workload_xapclient,
        option="--workload-xapclient",
    )
    args.workload_xtpclients = {
        scenario: int(value) for scenario, value in raw_xtpclients.items()
    }
    args.workload_xapclients = {
        scenario: int(value) for scenario, value in raw_xapclients.items()
    }
    args.workload_ap_query_ids = parse_scenario_values(
        args.workload_ap_query_ids,
        option="--workload-ap-query-ids",
    )
    args.workload_ap_query_weights = parse_scenario_values(
        args.workload_ap_query_weights,
        option="--workload-ap-query-weights",
    )
    args.workload_ap_query_sequences = parse_scenario_values(
        args.workload_ap_query_sequence,
        option="--workload-ap-query-sequence",
    )
    raw_statement_files = parse_scenario_values(
        args.workload_statement_sql_file,
        option="--workload-statement-sql-file",
    )
    args.workload_statement_sql_files = {}
    for scenario, raw_items in raw_statement_files.items():
        statement_files: Dict[str, str] = {}
        for raw_item in raw_items.split(","):
            statement_name, separator, raw_path = raw_item.partition(":")
            statement_name = statement_name.strip().upper()
            sql_path = Path(raw_path.strip()).resolve() if separator else Path()
            if (
                not separator
                or not re.fullmatch(r"AP-\d+", statement_name)
                or not raw_path.strip()
                or not sql_path.is_file()
            ):
                raise ValueError(
                    "--workload-statement-sql-file requires existing "
                    f"AP-N:PATH entries: {raw_item!r}"
                )
            statement_files[statement_name] = str(sql_path)
        args.workload_statement_sql_files[scenario] = statement_files
    for option, values in (
        ("--workload-xtpclient", args.workload_xtpclients),
        ("--workload-xapclient", args.workload_xapclients),
    ):
        invalid = {scenario: value for scenario, value in values.items() if value < 1}
        if invalid:
            raise ValueError(f"{option} counts must be positive: {invalid}")
    raw_sweet_uppers = parse_scenario_values(
        args.target_sweet_upper,
        option="--target-sweet-upper",
    )
    args.target_sweet_uppers = {
        scenario: float(value) for scenario, value in raw_sweet_uppers.items()
    }
    if args.model_training_seed_dir:
        seed_root = Path(args.model_training_seed_dir).resolve()
        if not seed_root.is_dir():
            raise ValueError(f"--model-training-seed-dir is not a directory: {seed_root}")
        args.model_training_seed_dir = str(seed_root)
    configs = parse_scenario_configs(args.scenario_config, args.config)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    output_root = Path(args.output_dir or f"report/double_loop_sgd_{args.mode}_{stamp}").resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    write_json(
        output_root / "run_manifest.json",
        {
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "mode": args.mode,
            "scenarios": scenarios,
            "scenario_priority_order": list(HIGH_PRIORITY_SCENARIOS) + ["none"],
            "configs": {name: str(path) for name, path in configs.items()},
            "args": vars(args),
            "small_loop_algorithm": "binary_plan_coordinate_grid_plus_resource_estimation",
            "generic_vm_replay": bool(args.generic_vm_replay),
            "grid_search": {
                "points_per_coordinate": args.grid_points,
                "coordinate_dimensions_serial": True,
                "points_within_coordinate_parallel_workers": args.inner_workers,
                "combination_sizes": [2, args.combination_max_size],
                "combination_values_per_param": args.combination_values_per_param,
                "combination_max_candidates": args.combination_max_candidates,
                "candidate_cache_and_exact_vector_dedup": True,
                "qps_used_in_score": False,
            },
            "isolation_contract": {
                "historical_inputs_allowed": (
                    ["baseline_anchored_parameter_to_resource_training_rows"]
                    if args.model_training_seed_dir
                    else []
                ),
                "current_run_inputs": [
                    "fresh_baseline",
                    "fresh_workload_pruning",
                    "fresh_model_calibration_replays",
                    "fresh_model_quality_gate",
                    "new_real_experience",
                    "current_scenario_physical_capacity",
                    "current_scenario_target_sweet_definition",
                ],
                "pruning_reuse_only": ["workload_pruning", "candidate_replay_pruning"],
                "score_semantics": (
                    "estimated_and_real_resources_share_sigmoid_linear_sweet_band_weighted_mean_v2"
                ),
                "legacy_accepted_rejected_results_used": False,
                "optimizer_start_requires_model_quality_gate_pass": False,
                "model_quality_online_hard_failures_allowed": max(
                    0, int(args.model_quality_online_hard_failures_allowed)
                ),
                "model_quality_gate_policy": "soft_start_until_consecutive_hard_failures_exceeded",
            },
        },
    )

    # Submission order gives CPU/Memory/IO priority whenever scenario concurrency
    # is below four. Each scenario keeps all dependent outer rounds in one worker.
    ordered = [name for name in HIGH_PRIORITY_SCENARIOS if name in scenarios]
    ordered += [name for name in scenarios if name not in ordered]
    summaries: List[Dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(len(ordered), max(1, args.max_parallel_scenarios))
    ) as executor:
        futures = {
            executor.submit(run_scenario, name, args, configs.get(name), output_root): name
            for name in ordered
        }
        for future in concurrent.futures.as_completed(futures):
            scenario = futures[future]
            try:
                summary = future.result()
            except Exception as exc:
                summary = {
                    "scenario": scenario,
                    "status": "failed",
                    "successful": False,
                    "priority": "high" if scenario in HIGH_PRIORITY_SCENARIOS else "low",
                    "error": f"{type(exc).__name__}: {exc}",
                }
                write_json(output_root / scenario / "scenario_summary.json", summary)
            finally:
                if args.generic_vm_replay:
                    try:
                        cleanup = stop_owned_seeded_vm(configs.get(scenario))
                    except Exception as cleanup_exc:
                        cleanup = {
                            "attempted": True,
                            "stopped": False,
                            "error": f"{type(cleanup_exc).__name__}: {cleanup_exc}",
                        }
                    write_json(
                        output_root / scenario / "owned_vm_cleanup.json",
                        cleanup,
                    )
            summaries.append(summary)
            print(
                f"[{scenario}] status={summary.get('status')} successful={summary.get('successful')}",
                flush=True,
            )
    summaries.sort(key=lambda row: SCENARIOS.index(str(row["scenario"])))
    write_json(output_root / "four_scenario_summary.json", {"scenarios": summaries})
    write_markdown(output_root, summaries)
    print(f"wrote {output_root / 'four_scenario_summary.json'}", flush=True)
    return 0 if all(row.get("status") == "completed" for row in summaries) else 1


if __name__ == "__main__":
    raise SystemExit(main())
