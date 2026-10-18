#!/usr/bin/env python3
"""Run one real MySQL outer-binary / inner-estimated-grid experiment."""

from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
import json
import math
import os
from pathlib import Path
import sys
import time
import traceback
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
FRAMEWORK_ROOT = Path("/home/anonymous/program/resource_adaptive")
if str(FRAMEWORK_ROOT) in sys.path:
    sys.path.remove(str(FRAMEWORK_ROOT))
sys.path.insert(0, str(FRAMEWORK_ROOT))

from double_loop_grid_optimizer import DoubleLoopGridOptimizer, GridInnerLoopConfig  # noqa: E402
from double_loop_search_common import OuterLoopConfig, ParameterRange  # noqa: E402
from double_loop_scenario import (  # noqa: E402
    FreshBaselineObjective,
    FreshExperienceModel,
    FreshModelQualityThresholds,
    measurement_from_payload,
)

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))
from mysql_algorithm_b_policy import (  # noqa: E402
    effective_outer_round_budget,
    preflight_resource_error_bounds,
    run_mysql_fresh_model_quality_gate,
)
from mysql_cost_adapter import (  # noqa: E402
    MySQLCLIClient,
    MySQLCostAdapter,
    SYSTEM_VARIABLE_KEYS,
    ZERO_ALLOWED_SYSTEM_VARIABLE_KEYS,
)
from mysql_live_measurement import (  # noqa: E402
    measure_benchbase,
    measure_benchbase_multiprocess,
    measure_hybench,
    measure_job,
)
from mysql_workload_adapter import MySQLEndpoint  # noqa: E402


def json_value(value: Any) -> Any:
    if is_dataclass(value):
        return json_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(json_value(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def normalized_resource(resource: Mapping[str, Any], capability: Mapping[str, float]) -> dict[str, float]:
    memory = float(resource["memory"]) / float(capability["memory"])
    return {
        "cpu": float(resource["cpu"]) / float(capability["cpu"]),
        "memory": memory,
        "mem": memory,
        "io": float(resource["io"]) / float(capability["io"]),
    }


def mysql_effective_grid_values(
    name: str,
    low: float,
    high: float,
    discrete_count: int,
) -> tuple[float, ...] | None:
    """Return grid values after MySQL's integer system-variable coercion.

    Cost-table parameters are genuinely continuous, but every allowlisted
    ``SET GLOBAL`` parameter is rounded to an integer by ``MySQLCostAdapter``.
    Ranking fractional aliases such as AHI=0.9 and AHI=1.0 as distinct points
    wastes real Top-K slots on the same effective database configuration.
    Canonicalising only the grid metadata keeps the coordinate-grid and outer
    binary algorithms unchanged while making each ranked point executable as
    recorded.
    """
    if name not in SYSTEM_VARIABLE_KEYS:
        return None
    low_value = float(low)
    high_value = float(high)
    if math.isclose(low_value, high_value, rel_tol=0.0, abs_tol=1e-15):
        return None
    count = max(2, int(discrete_count))
    step = (high_value - low_value) / (count - 1)
    raw_values = [low_value + index * step for index in range(count)]
    raw_values.extend((low_value, high_value))
    values = tuple(sorted({float(int(round(value))) for value in raw_values}))
    return values if len(values) >= 2 else None


def _raw_payload(validation: Any) -> dict[str, Any]:
    raw = getattr(validation, "raw_result", None)
    return dict(raw) if isinstance(raw, Mapping) else {}


def select_strict_final_topk(result: Any, objective: FreshBaselineObjective) -> tuple[dict[str, float] | None, dict[str, Any]]:
    """Select the most robust strict-pass point from all physically replayed Top-K rows.

    The outer binary search and inner estimated grid remain untouched.  This
    only prevents a later direction-only outer anchor from hiding a candidate
    which already passed every final gate during real Top-K validation.
    """
    eligible: list[tuple[tuple[float, float, float], Any, Any]] = []
    seen: set[tuple[tuple[str, float], ...]] = set()
    inspected = 0
    for round_row in result.rounds:
        for validation in round_row.validated_points:
            if validation.failure_reason or not validation.resource:
                continue
            signature = tuple(sorted((str(k), float(v)) for k, v in validation.params.items()))
            if signature in seen:
                continue
            seen.add(signature)
            inspected += 1
            measurement = measurement_from_payload(validation.resource, _raw_payload(validation))
            decision = objective.final_decide(measurement)
            if not decision.successful:
                continue
            qps_margin = float(decision.qps_gain_ratio) - float(decision.min_qps_gain_ratio)
            if objective.scenario == "none":
                resource_margin = max(float(value) for value in decision.resource_gain_ratios.values())
                constraint_margin = min(qps_margin, resource_margin)
            else:
                assert decision.target_drop_ratio is not None
                assert decision.min_target_drop_ratio is not None
                assert decision.max_target_drop_ratio is not None
                assert decision.target_value is not None
                assert decision.target_sweet_upper is not None
                drop_margin = min(
                    float(decision.target_drop_ratio) - float(decision.min_target_drop_ratio),
                    float(decision.max_target_drop_ratio) - float(decision.target_drop_ratio),
                )
                sweet_margin = (
                    float(decision.target_sweet_upper) - float(decision.target_value)
                ) / max(abs(float(getattr(objective.baseline, objective.scenario))), 1e-12)
                constraint_margin = min(qps_margin, drop_margin, sweet_margin)
            eligible.append((
                (constraint_margin, qps_margin, float(validation.score)),
                validation,
                decision,
            ))
    if not eligible:
        return None, {
            "policy": "strict_final_gate_then_maximin_margin",
            "inspected_real_topk": inspected,
            "strict_eligible": 0,
            "selected": False,
        }
    key, validation, decision = max(eligible, key=lambda item: item[0])
    return dict(validation.params), {
        "policy": "strict_final_gate_then_maximin_margin",
        "inspected_real_topk": inspected,
        "strict_eligible": len(eligible),
        "selected": True,
        "selection_key": {
            "min_constraint_margin": key[0],
            "qps_margin_above_15pct": key[1],
            "real_resource_score": key[2],
        },
        "measured_decision": json_value(decision),
        "selected_params": dict(validation.params),
    }


def build_standard_log(
    args: argparse.Namespace,
    result: Any,
    baseline: Mapping[str, Any],
    final: Mapping[str, Any],
    final_decision: Any,
    capability: Mapping[str, float],
    total_wall_time_s: float,
    inner_rows: list[Mapping[str, Any]],
) -> dict[str, Any]:
    baseline_physical = dict(baseline["resource"])
    final_physical = dict(final["resource"])
    baseline_normalized = normalized_resource(baseline_physical, capability)
    final_normalized = normalized_resource(final_physical, capability)
    target_before = (
        float(baseline_physical[args.scenario]) if args.scenario != "none" else None
    )
    target_after = (
        float(final_physical[args.scenario]) if args.scenario != "none" else None
    )
    # Δu is the before/after bottleneck-utilization difference in percentage
    # points relative to the observed baseline utilization.  This is the
    # acceptance quantity used by the B4/B5/B6 protocols (for example, a
    # 7% reduction is reported as 7 pp); it is intentionally not the legacy
    # optimizer score delta stored in result.delta_u_pct.  The fixed-machine
    # capacity-normalized difference is also retained for reproducibility.
    delta_bottleneck_util_pp = (
        (target_before - target_after) / max(abs(target_before), 1e-12) * 100.0
        if args.scenario != "none" and target_before is not None and target_after is not None
        else None
    )
    delta_bottleneck_machine_util_pp = (
        (target_before - target_after) / float(capability[args.scenario]) * 100.0
        if args.scenario != "none" and target_before is not None and target_after is not None
        else None
    )
    final_cpu_change_pp = (
        (float(final_physical["cpu"]) - float(baseline_physical["cpu"]))
        / float(capability["cpu"]) * 100.0
    )
    cpu_policy_enabled = args.max_non_target_cpu_increase_pp is not None
    cpu_policy_met = (
        None
        if not cpu_policy_enabled
        else final_cpu_change_pp <= float(args.max_non_target_cpu_increase_pp) + 1e-12
        or float(final_normalized["cpu"]) <= 1.0 + 1e-12
    )
    final_acceptance_met = bool(final_decision.successful) and (
        True if cpu_policy_met is None else bool(cpu_policy_met)
    )
    outer_rows: list[dict[str, Any]] = [{
        "iter": 0,
        "params": dict(result.initial_params),
        "qps": float(baseline["db_qps"]),
        "latency_p99_ms": float(baseline["p99_ms"]),
        "latency_p95_ms": float(baseline["p95_ms"]),
        "resource": baseline_normalized,
        "utility": float(result.initial_real_score),
        "explain_samples": list(baseline.get("explain_samples") or []),
        "phase": "baseline",
        "outer_accepted": True,
        "outer_acceptance_reason": "baseline",
    }]
    for round_row in result.rounds:
        best = round_row.small_loop_best
        if best is None or not best.resource:
            continue
        raw = _raw_payload(best)
        outer_rows.append({
            "iter": int(round_row.round_index) + 1,
            "params": dict(best.params),
            "qps": float(best.resource.get("db_qps", raw.get("db_qps", 0.0))),
            "latency_p99_ms": float(raw.get("p99_ms", 0.0)),
            "latency_p95_ms": float(raw.get("p95_ms", 0.0)),
            "resource": normalized_resource(best.resource, capability),
            "utility": float(best.score),
            "explain_samples": list(raw.get("explain_samples") or []),
            "outer_accepted": bool(round_row.outer_accepted),
            "outer_acceptance_reason": str(round_row.outer_acceptance_reason),
            "phase": "outer_candidate",
        })
    outer_rows.append({
        "iter": len(result.rounds) + 1,
        "params": dict(result.optimized_params),
        "qps": float(final["db_qps"]),
        "latency_p99_ms": float(final["p99_ms"]),
        "latency_p95_ms": float(final["p95_ms"]),
        "resource": final_normalized,
        "utility": float(result.final_real_score),
        "explain_samples": list(final.get("explain_samples") or []),
        "phase": "independent_final",
        "outer_accepted": final_acceptance_met,
        "outer_acceptance_reason": (
            str(getattr(final_decision, "reason", "final_goal_decision"))
            if cpu_policy_met is not False else "non_target_cpu_policy_not_met"
        ),
    })
    best_estimated = max(
        (float(row.get("estimated_resource_score")) for row in inner_rows
         if isinstance(row.get("estimated_resource_score"), (int, float))),
        default=float(result.initial_real_score),
    )
    baseline_score = float(result.initial_real_score)
    final_score = float(result.final_real_score)
    # Retained for compatibility with the required historical JSON schema.  It
    # is a *relative optimizer-score* difference, not a bottleneck-utilization
    # delta.  The latter is recorded explicitly in audit.acceptance_metrics.
    legacy_relative_score_delta_pct = (
        (final_score - baseline_score) / max(abs(baseline_score), 1e-12) * 100.0
    )
    return {
        "meta": {
            "exp_id": args.exp_id,
            "benchmark": args.benchmark,
            "db": "mysql8",
            "method": "outer_binary_inner_estimated_grid",
            "bottleneck": args.scenario,
            "bottleneck_target": (
                args.target_sweet_upper / capability[args.scenario]
                if args.scenario != "none" else None
            ),
            "repeat_id": args.repeat_id,
            "machine_spec": {
                "cpu_cores": args.cpu_cores,
                "mem_gb": args.mem_gb,
                "disk_bw_mb": args.disk_bw_mb,
            },
        },
        "outer_loop": outer_rows,
        "inner_loop_summary": {
            "total_iterations": len(inner_rows),
            # The shared framework records every inner evaluation with a
            # timestamp, but OuterRound intentionally has no wall-time field.
            "wall_time_ms": round(total_wall_time_s * 1000.0),
            "best_estimated_utility": best_estimated,
            "best_params": dict(result.optimized_params),
        },
        "result": {
            "final_params": dict(result.optimized_params),
            "delta_u_pct": legacy_relative_score_delta_pct,
            "qps_before": float(baseline["db_qps"]),
            "qps_after": float(final["db_qps"]),
            "p99_before_ms": float(baseline["p99_ms"]),
            "p99_after_ms": float(final["p99_ms"]),
            "bad_config": not final_acceptance_met,
            "converge_outer_iters": len(result.rounds),
            "total_wall_time_s": total_wall_time_s,
        },
        "audit": {
            "framework": "DoubleLoopGridOptimizer",
            "outer_search": "binary_interval_shrink_after_real_topk_acceptance",
            "inner_search": "estimated_coordinate_grid_plus_combinations",
            "active_params": list(result.active_params),
            "pruned_params": list(result.pruned_params),
            "real_validation_count": int(result.real_validation_count),
            "total_physical_measurement_count": int(args.total_physical_measurement_count),
            "stop_reason": str(result.stop_reason),
            "final_goal_decision": json_value(final_decision),
            "baseline_resource_physical": baseline_physical,
            "baseline_resource_normalized": baseline_normalized,
            "final_resource_physical": final_physical,
            "final_resource_normalized": final_normalized,
            "acceptance_metrics": {
                "bottleneck": args.scenario,
                "bottleneck_util_before": (
                    baseline_normalized[args.scenario] if args.scenario != "none" else None
                ),
                "bottleneck_util_after": (
                    final_normalized[args.scenario] if args.scenario != "none" else None
                ),
                "delta_bottleneck_util_pp": delta_bottleneck_util_pp,
                "delta_bottleneck_machine_util_pp": delta_bottleneck_machine_util_pp,
                "target_drop_ratio": (
                    (target_before - target_after) / max(abs(target_before), 1e-12)
                    if target_before is not None and target_after is not None else None
                ),
                "qps_gain_ratio": (
                    (float(final["db_qps"]) - float(baseline["db_qps"]))
                    / max(abs(float(baseline["db_qps"])), 1e-12)
                ),
                "non_target_cpu_util_change_pp": final_cpu_change_pp,
                "non_target_cpu_policy_max_increase_pp": args.max_non_target_cpu_increase_pp,
                "non_target_cpu_policy_met": cpu_policy_met,
                "any_non_target_resource_higher": getattr(
                    final_decision, "any_non_target_resource_higher", None
                ),
                "final_acceptance_met": final_acceptance_met,
                "result_delta_u_pct_semantics": "legacy_relative_optimizer_score_pct_not_util_pp",
            },
            "final_topk_selection": json_value(getattr(args, "final_topk_selection_audit", {})),
            "independent_final_real_replay": True,
            "initial_param_values": json.loads(args.initial_param_values),
            "fixed_param_values": json.loads(args.fixed_param_values),
            "parameter_ranges": json.loads(args.parameter_ranges),
            "mysql_effective_grid_values": json_value(
                getattr(args, "effective_grid_values", {})
            ),
            "tpch_query_ids": args.tpch_query_ids or None,
            "tpch_native_manifest": (
                str(args.tpch_native_manifest.resolve())
                if args.tpch_native_manifest is not None else None
            ),
            "tpch_native_repetitions": (
                args.tpch_native_repetitions
                if args.tpch_native_manifest is not None else None
            ),
            "tpcc_weights": args.tpcc_weights or None,
            "benchbase_rate_limit": (
                getattr(args, "benchbase_rate_limit", 0.0)
                if getattr(args, "benchbase_rate_limit", 0.0) > 0.0 else None
            ),
            "benchbase_client_processes": args.benchbase_client_processes,
            "benchbase_terminals_per_process": (
                args.terminals if args.benchbase_client_processes > 1 else None
            ),
            "hybench_extra_properties": json.loads(args.hybench_extra_properties),
            "score_resource_limits": dict(capability),
            "bottleneck_score_cap_auto_from_baseline": (
                args.scenario != "none" and {
                    "cpu": args.score_cpu_cap,
                    "memory": args.score_memory_cap,
                    "io": args.score_io_cap,
                }[args.scenario] <= 0.0
            ),
            "memory_measurement_mode": args.memory_mode,
            "target_sweet_upper_policy": {
                "physical_value": args.target_sweet_upper if args.scenario != "none" else None,
                "baseline_ratio": (
                    args.target_sweet_upper_ratio if args.scenario != "none" else None
                ),
            },
            "protocol": {
                "protocol_id": args.protocol_id or None,
                "attempt_index": args.attempt_index,
                "attempt_limit": args.attempt_limit,
                "workload_seed": args.workload_seed,
            },
            "post_apply_settle_s": args.post_apply_settle_s,
            "per_point_warmup_runs": args.per_point_warmup_runs,
            "final_rss_memory_gib": final.get("rss_memory_gib"),
            "baseline_throughput_source": baseline.get("throughput_source"),
            "final_throughput_source": final.get("throughput_source"),
            "baseline_latency_source": baseline.get("latency_source"),
            "final_latency_source": final.get("latency_source"),
        },
    }


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
    return rows


def tpch_weights(query_ids: str) -> list[float] | None:
    if not query_ids.strip():
        return None
    selected = {int(value.strip()) for value in query_ids.split(",") if value.strip()}
    if not selected or min(selected) < 1 or max(selected) > 22:
        raise ValueError("--tpch-query-ids must contain values in 1..22")
    return [1.0 if index in selected else 0.0 for index in range(1, 23)]


def tpcc_weights(spec: str) -> list[float] | None:
    if not spec.strip():
        return None
    values = [float(value.strip()) for value in spec.split(",")]
    if (
        len(values) != 5
        or any(not math.isfinite(value) or value < 0.0 for value in values)
        or not any(values)
    ):
        raise ValueError("--tpcc-weights needs five non-negative, not-all-zero values")
    return values


def run(args: argparse.Namespace) -> Path:
    if args.warmup_runs < 0:
        raise ValueError("--warmup-runs cannot be negative")
    if args.post_apply_settle_s < 0.0:
        raise ValueError("--post-apply-settle-s cannot be negative")
    if args.per_point_warmup_runs < 0:
        raise ValueError("--per-point-warmup-runs cannot be negative")
    if args.tpch_native_manifest is not None and args.benchmark != "tpch":
        raise ValueError("--tpch-native-manifest is only valid for TPC-H")
    if args.tpch_native_repetitions < 1:
        raise ValueError("--tpch-native-repetitions must be positive")
    if args.benchbase_client_processes < 1:
        raise ValueError("--benchbase-client-processes must be positive")
    if args.benchbase_client_processes > 1 and args.benchmark != "tpcc":
        raise ValueError("multiprocess BenchBase is currently limited to TPC-C")
    if args.attempt_index < 1:
        raise ValueError("--attempt-index must be positive")
    if args.attempt_limit < 1:
        raise ValueError("--attempt-limit must be positive")
    if args.attempt_index > args.attempt_limit:
        raise ValueError("--attempt-index cannot exceed --attempt-limit")
    if args.target_sweet_upper > 0.0 and args.target_sweet_upper_ratio > 0.0:
        raise ValueError("set at most one of --target-sweet-upper and --target-sweet-upper-ratio")
    if args.target_sweet_upper_ratio > 0.0 and not 0.90 <= args.target_sweet_upper_ratio < 1.0:
        raise ValueError("--target-sweet-upper-ratio must be in [0.90, 1.0)")
    if args.grid_points < 2:
        raise ValueError("--grid-points must be at least 2")
    if args.inner_passes < 1 or args.convergence_patience < 1:
        raise ValueError("--inner-passes and --convergence-patience must be positive")
    if not 2 <= args.combination_max_size <= 4:
        raise ValueError("--combination-max-size must be in [2,4]")
    if not 2 <= args.combination_values_per_param <= 5:
        raise ValueError("--combination-values-per-param must be in [2,5]")
    if args.combination_max_candidates < 0:
        raise ValueError("--combination-max-candidates cannot be negative")
    if not 15 <= args.real_top_k <= 30:
        raise ValueError("--real-top-k must be in [15,30]")
    if not 15 <= args.adaptive_topk_min_k <= args.real_top_k:
        raise ValueError("--adaptive-topk-min-k must be in [15,--real-top-k]")
    if args.adaptive_topk_batch_size < 1:
        raise ValueError("--adaptive-topk-batch-size must be positive")
    if not 0.0 <= args.adaptive_topk_inversion_threshold <= 1.0:
        raise ValueError("--adaptive-topk-inversion-threshold must be in [0,1]")
    if args.outer_rounds < 0:
        raise ValueError("--outer-rounds cannot be negative")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    endpoint = MySQLEndpoint(args.host, args.port, args.database, args.user, args.password)
    hybench_extra_properties = json.loads(args.hybench_extra_properties)
    if not isinstance(hybench_extra_properties, dict):
        raise ValueError("--hybench-extra-properties must be a JSON object")
    client_cfg = {
        "host": args.host, "port": args.port, "user": args.user,
        "password": args.password, "database": args.database,
        "query_timeout_sec": 30,
    }
    adapter = MySQLCostAdapter(MySQLCLIClient(client_cfg))
    snapshot = adapter.snapshot()
    adapter.save_snapshot(output / "mysql_cost_snapshot.json", snapshot)
    initial = {item.name: item.effective_value for item in snapshot}
    active = [item.strip() for item in args.active_params.split(",") if item.strip()]
    unknown = sorted(set(active) - set(initial))
    if unknown:
        raise ValueError(f"unknown active MySQL costs: {unknown}")
    fixed_values = json.loads(args.fixed_param_values)
    if not isinstance(fixed_values, dict):
        raise ValueError("--fixed-param-values must be a JSON object")
    unknown_fixed = sorted(set(fixed_values) - set(initial))
    if unknown_fixed:
        raise ValueError(f"unknown fixed MySQL costs: {unknown_fixed}")
    fixed_active_overlap = sorted(set(fixed_values) & set(active))
    if fixed_active_overlap:
        raise ValueError(f"fixed parameters cannot also be active: {fixed_active_overlap}")
    fixed_values = {str(name): float(value) for name, value in fixed_values.items()}
    if any(
        not math.isfinite(value)
        or value < 0.0
        or (value == 0.0 and name not in ZERO_ALLOWED_SYSTEM_VARIABLE_KEYS)
        for name, value in fixed_values.items()
    ):
        raise ValueError(
            "fixed parameter values must be finite and positive, except supported zero-valued system variables"
        )
    if fixed_values:
        adapter.apply(fixed_values)
        initial.update(fixed_values)
    seed_values = json.loads(args.initial_param_values)
    if not isinstance(seed_values, dict):
        raise ValueError("--initial-param-values must be a JSON object")
    bad_seed_names = sorted(set(seed_values) - set(active))
    if bad_seed_names:
        raise ValueError(f"seed values must be active parameters: {bad_seed_names}")
    seed_values = {str(name): float(value) for name, value in seed_values.items()}
    if any(
        not math.isfinite(value)
        or value < 0.0
        or (value == 0.0 and name not in ZERO_ALLOWED_SYSTEM_VARIABLE_KEYS)
        for name, value in seed_values.items()
    ):
        raise ValueError(
            "initial parameter values must be finite and positive, except supported zero-valued system variables"
        )
    if seed_values:
        adapter.apply(seed_values)
        initial.update(seed_values)
    explicit_ranges = json.loads(args.parameter_ranges)
    if not isinstance(explicit_ranges, dict):
        raise ValueError("--parameter-ranges must be a JSON object")
    unknown_range_names = sorted(set(explicit_ranges) - set(active))
    if unknown_range_names:
        raise ValueError(f"parameter ranges must be active parameters: {unknown_range_names}")
    for name, values in explicit_ranges.items():
        if (not isinstance(values, list) or len(values) != 2
                or any(
                    not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                    or float(value) < 0
                    or (float(value) == 0.0 and name not in ZERO_ALLOWED_SYSTEM_VARIABLE_KEYS)
                    for value in values
                )
                or float(values[0]) > float(values[1])):
            raise ValueError(f"invalid explicit parameter range for {name}: {values!r}")
    ranges: dict[str, ParameterRange] = {}
    effective_grid_values: dict[str, list[float]] = {}
    for priority, name in enumerate(active, start=1):
        low = (
            float(explicit_ranges[name][0])
            if name in explicit_ranges else initial[name] * args.min_multiplier
        )
        high = (
            float(explicit_ranges[name][1])
            if name in explicit_ranges else initial[name] * args.max_multiplier
        )
        values = mysql_effective_grid_values(name, low, high, args.grid_points)
        ranges[name] = ParameterRange(
            low,
            high,
            discrete_count=args.grid_points,
            priority=priority,
            values=values,
            transform=("log" if low > 0.0 else "linear"),
        )
        if values is not None:
            effective_grid_values[name] = list(values)
    args.effective_grid_values = effective_grid_values
    capability = {
        "cpu": args.score_cpu_cap or args.cpu_cores,
        "memory": args.score_memory_cap or args.mem_gb,
        "io": args.score_io_cap or args.disk_bw_mb,
    }
    counter = 0
    physical_counter = 0
    model: FreshExperienceModel | None = None

    def measure_current(path: Path) -> dict[str, Any]:
        if args.benchmark == "tpch" and args.tpch_native_manifest is not None:
            payload = measure_job(
                endpoint, path, mysqld_pid=args.mysqld_pid,
                manifest_path=args.tpch_native_manifest,
                concurrency=args.terminals,
                repetitions=args.tpch_native_repetitions,
                interval_s=args.sample_interval,
                timeout_s=args.validation_timeout,
                memory_mode=args.memory_mode,
            )
            payload["throughput_source"] = "native_mysql_official_tpch_sql"
        elif args.benchmark in {"tpch", "tpcc"}:
            measure = (
                measure_benchbase_multiprocess
                if args.benchmark == "tpcc" and args.benchbase_client_processes > 1
                else measure_benchbase
            )
            concurrency = (
                {
                    "processes": args.benchbase_client_processes,
                    "terminals_per_process": args.terminals,
                }
                if args.benchmark == "tpcc" and args.benchbase_client_processes > 1
                else {"terminals": args.terminals}
            )
            payload = measure(
                args.benchmark, endpoint, path,
                mysqld_pid=args.mysqld_pid, jar_path=args.benchbase_jar,
                duration_s=args.duration_s, **concurrency,
                scale_factor=args.scale_factor, interval_s=args.sample_interval,
                timeout_s=args.validation_timeout,
                transaction_weights=(
                    tpch_weights(args.tpch_query_ids)
                    if args.benchmark == "tpch"
                    else tpcc_weights(args.tpcc_weights)
                ),
                rate_limit=(
                    getattr(args, "benchbase_rate_limit", 0.0)
                    if getattr(args, "benchbase_rate_limit", 0.0) > 0.0 else None
                ),
                memory_mode=args.memory_mode,
            )
        elif args.benchmark.startswith("hybench_"):
            payload = measure_hybench(
                args.benchmark.split("_", 1)[1], endpoint, path,
                mysqld_pid=args.mysqld_pid, hybench_home=args.hybench_home,
                duration_s=args.duration_s, clients=args.terminals,
                scale_factor=args.hybench_scale_factor,
                interval_s=args.sample_interval, timeout_s=args.validation_timeout,
                extra_properties=hybench_extra_properties,
                memory_mode=args.memory_mode,
            )
        else:
            if args.job_manifest is None:
                raise ValueError("--job-manifest is required for JOB")
            payload = measure_job(
                endpoint, path, mysqld_pid=args.mysqld_pid,
                manifest_path=args.job_manifest, concurrency=args.terminals,
                repetitions=args.job_repetitions, interval_s=args.sample_interval,
                timeout_s=args.validation_timeout,
                memory_mode=args.memory_mode,
            )
        return payload

    def real_measure(
        params: Mapping[str, float],
        label: str,
        *,
        update_model: bool = True,
    ) -> dict[str, Any]:
        nonlocal counter, physical_counter
        adapter.apply(params)
        if args.post_apply_settle_s > 0.0:
            time.sleep(args.post_apply_settle_s)
        next_index = counter + 1
        for warmup_index in range(args.per_point_warmup_runs):
            physical_counter += 1
            warmup_path = (
                output / "real_point_warmups"
                / f"{next_index:04d}_{label}_warmup_{warmup_index + 1}"
            )
            warmup_payload = measure_current(warmup_path)
            warmup_payload["params"] = dict(params)
            write_json(warmup_path / "candidate_measurement.json", warmup_payload)
        counter += 1
        physical_counter += 1
        path = output / "real_validations" / f"{counter:04d}_{label}"
        payload = measure_current(path)
        payload["params"] = dict(params)
        write_json(path / "candidate_measurement.json", payload)
        if model is not None and update_model:
            model.add(params, payload["resource"], payload)
        return payload

    started = time.monotonic()
    try:
        for warmup_index in range(args.warmup_runs):
            real_measure(initial, f"warmup_{warmup_index + 1}")
        baseline = real_measure(initial, "baseline")
        score_cap_arguments = {
            "cpu": args.score_cpu_cap,
            "memory": args.score_memory_cap,
            "io": args.score_io_cap,
        }
        if args.scenario != "none" and score_cap_arguments[args.scenario] <= 0.0:
            if not args.allow_auto_score_cap:
                raise ValueError(
                    "an explicit score cap is required for the bottleneck resource; "
                    "pass --score-*-cap (legacy auto cap needs --allow-auto-score-cap)"
                )
            capability[args.scenario] = float(baseline[args.scenario]) / 0.95
        baseline_measurement = measurement_from_payload(baseline["resource"], baseline)
        target_upper = (
            args.target_sweet_upper
            if args.scenario != "none"
            else None
        )
        if args.scenario != "none" and args.target_sweet_upper_ratio > 0.0:
            target_upper = float(baseline[args.scenario]) * args.target_sweet_upper_ratio
            args.target_sweet_upper = target_upper
        elif args.scenario != "none" and target_upper <= 0.0:
            target_upper = capability[args.scenario] * 0.90
            args.target_sweet_upper = target_upper
        objective = FreshBaselineObjective(
            args.scenario,
            baseline_measurement,
            resource_limits=capability,
            min_qps_gain_ratio=0.15,
            min_target_drop_ratio=0.05,
            max_target_drop_ratio=0.10,
            target_sweet_upper=target_upper,
        )
        model = FreshExperienceModel(
            objective,
            {name: (bound.min_value, bound.max_value) for name, bound in ranges.items()},
            max_neighbors=args.model_max_neighbors,
        )
        model.add(initial, baseline["resource"], baseline)

        def calibrate(_sql_set: Any, params: dict[str, float]) -> dict[str, Any]:
            return real_measure(params, "calibration", update_model=False)

        model_quality_gate = run_mysql_fresh_model_quality_gate(
            model,
            calibrate,
            {"benchmark": args.benchmark},
            initial,
            ranges,
            active,
            scenario_dir=output,
            thresholds=FreshModelQualityThresholds(),
        )
        initial_error_bounds = preflight_resource_error_bounds(model_quality_gate)

        def validate(_sql_set: Any, params: dict[str, float]) -> dict[str, Any]:
            return real_measure(params, "candidate")

        optimizer = DoubleLoopGridOptimizer(
            model.predict,
            validate,
            capability,
            grid_config=GridInnerLoopConfig(
                max_workers=args.inner_workers,
                max_passes=args.inner_passes,
                no_improvement_patience=args.convergence_patience,
                combination_probe_enabled=True,
                combination_min_size=2,
                combination_max_size=max(2, args.combination_max_size),
                combination_values_per_param=args.combination_values_per_param,
                combination_max_candidates=args.combination_max_candidates,
            ),
            outer_config=OuterLoopConfig(
                max_rounds=effective_outer_round_budget(
                    ranges,
                    active,
                    args.epsilon,
                    args.outer_rounds,
                ),
                real_validation_top_k=args.real_top_k,
                real_validation_top_fraction=1.0,
                topk_controller="decision_tree",
                adaptive_topk_min_k=args.adaptive_topk_min_k,
                adaptive_topk_batch_size=args.adaptive_topk_batch_size,
                adaptive_topk_inversion_threshold=args.adaptive_topk_inversion_threshold,
                adaptive_topk_score_tie_margin=args.adaptive_topk_score_tie_margin,
                no_improvement_patience=0,
                epsilon=args.epsilon,
                target_resource=(args.scenario if args.scenario != "none" else None),
            ),
            estimated_score_func=objective.score,
            real_score_func=objective.score,
            resource_box_upper_bound_func=objective.optimistic_score_in_resource_box,
            outer_candidate_acceptance_func=objective.outer_acceptance,
            initial_resource_error_bounds=initial_error_bounds,
            preflight_quality_gate=model_quality_gate,
            verbose=True,
        )
        model_quality_gate["optimizer_started"] = True
        write_json(output / "fresh_model_quality_gate.json", model_quality_gate)
        inner_path = output / "inner_iterations.jsonl"
        result = optimizer.optimize(
            initial,
            ranges,
            sql_set={"benchmark": args.benchmark},
            candidate_params=active,
            initial_real_resource=baseline,
            log_path=str(output / "optimizer.log"),
            inner_iteration_log_path=str(inner_path),
        )
        args.final_topk_selection_audit = {
            "policy": "optimizer_outer_anchor_only",
            "selected": False,
            "legacy_requested_policy": getattr(args, "final_topk_selection", "optimizer"),
            "legacy_policy_ignored": getattr(args, "final_topk_selection", "optimizer") != "optimizer",
            "reason": "PostgreSQL Algorithm B forbids post-optimizer Top-K reselection",
        }
        final = real_measure(result.optimized_params, "independent_final")
        final_decision = objective.final_decide(
            measurement_from_payload(final["resource"], final)
        )
        result.final_real_resource = dict(final["resource"])
        result.final_real_score = float(objective.score(final["resource"], final))
        result.successful = bool(final_decision.successful)
        args.total_physical_measurement_count = physical_counter
        standard = build_standard_log(
            args, result, baseline, final, final_decision, capability,
            time.monotonic() - started, read_jsonl(inner_path),
        )
        result_path = output / f"{args.exp_id}_r{args.repeat_id}.json"
        write_json(result_path, standard)
        write_json(output / "run_summary.json", {
            "result_path": str(result_path),
            "final_goal_decision": final_decision,
            "optimizer": {
                "optimized_params": result.optimized_params,
                "stop_reason": result.stop_reason,
                "real_validation_count": result.real_validation_count,
            },
        })
        return result_path
    finally:
        adapter.restore(snapshot)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exp-id", required=True)
    parser.add_argument("--repeat-id", type=int, default=1)
    parser.add_argument("--scenario", choices=("cpu", "memory", "io", "none"), required=True)
    parser.add_argument("--benchmark", choices=(
        "tpch", "tpcc", "job", "hybench_ap", "hybench_tp", "hybench_xp",
    ), required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", default=os.environ.get("MYSQL_PWD", ""))
    parser.add_argument("--mysqld-pid", type=int, required=True)
    parser.add_argument("--benchbase-jar", type=Path, default=ROOT / "benchbase" / "benchbase.jar")
    parser.add_argument("--hybench-home", type=Path, default=ROOT / "hybench_runtime")
    parser.add_argument("--hybench-scale-factor", default="10x")
    parser.add_argument("--hybench-extra-properties", default="{}",
                        help="JSON object of native HyBench workload properties")
    parser.add_argument("--job-manifest", type=Path)
    parser.add_argument("--job-repetitions", type=int, default=1)
    parser.add_argument("--tpch-native-manifest", type=Path,
                        help="immutable manifest of official TPC-H SELECT statements")
    parser.add_argument("--tpch-native-repetitions", type=int, default=1)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--active-params", default="io_block_read_cost,row_evaluate_cost")
    parser.add_argument("--initial-param-values", default="{}",
                        help="JSON object used as the baseline point after the restore snapshot")
    parser.add_argument("--fixed-param-values", default="{}",
                        help="JSON object applied to every real point but excluded from search")
    parser.add_argument("--parameter-ranges", default="{}",
                        help="JSON object mapping active names to explicit [min,max] bounds")
    parser.add_argument("--tpch-query-ids", default="",
                        help="comma-separated stable TPC-H subset; empty means all queries")
    parser.add_argument("--tpcc-weights", default="",
                        help="NewOrder,Payment,OrderStatus,Delivery,StockLevel weights")
    parser.add_argument(
        "--benchbase-rate-limit", type=float, default=0.0,
        help="positive total requests/s cap; zero keeps BenchBase unlimited",
    )
    parser.add_argument(
        "--benchbase-client-processes", type=int, default=1,
        help="independent BenchBase processes; each uses --terminals workers",
    )
    parser.add_argument("--min-multiplier", type=float, default=.25)
    parser.add_argument("--max-multiplier", type=float, default=4.0)
    parser.add_argument("--grid-points", type=int, default=15)
    parser.add_argument("--inner-workers", type=int, default=8)
    parser.add_argument("--inner-passes", type=int, default=20)
    parser.add_argument("--convergence-patience", type=int, default=2)
    parser.add_argument("--combination-max-size", type=int, default=4)
    parser.add_argument("--combination-values-per-param", type=int, default=3)
    parser.add_argument("--combination-max-candidates", type=int, default=0)
    parser.add_argument("--outer-rounds", type=int, default=0)
    parser.add_argument("--real-top-k", type=int, default=30)
    parser.add_argument("--adaptive-topk-min-k", type=int, default=15)
    parser.add_argument("--adaptive-topk-batch-size", type=int, default=1)
    parser.add_argument("--adaptive-topk-inversion-threshold", type=float, default=0.10)
    parser.add_argument("--adaptive-topk-score-tie-margin", type=float, default=0.01)
    parser.add_argument(
        "--final-topk-selection", choices=("optimizer", "strict_margin"), default="optimizer",
        help="legacy compatibility only; strict_margin is ignored by unified Algorithm B",
    )
    parser.add_argument("--model-max-neighbors", type=int, default=2)
    parser.add_argument("--epsilon", type=float, default=1e-4)
    parser.add_argument(
        "--calibration-endpoints",
        action="store_true",
        help="deprecated compatibility flag; fixed Top15 calibration always runs",
    )
    parser.add_argument("--duration-s", type=int, default=30)
    parser.add_argument("--warmup-runs", type=int, default=1,
                        help="unscored initial-parameter replays before the authoritative baseline")
    parser.add_argument("--terminals", type=int, default=1)
    parser.add_argument("--scale-factor", type=float, default=.1)
    parser.add_argument("--sample-interval", type=float, default=1.0)
    parser.add_argument(
        "--post-apply-settle-s", type=float, default=0.0,
        help="fixed stabilization delay after applying each real MySQL parameter point",
    )
    parser.add_argument(
        "--per-point-warmup-runs", type=int, default=0,
        help="unscored workload replays after applying every real parameter point",
    )
    parser.add_argument("--validation-timeout", type=float, default=600.0)
    parser.add_argument("--cpu-cores", type=float, default=4.0)
    parser.add_argument("--mem-gb", type=float, default=8.0)
    parser.add_argument("--disk-bw-mb", type=float, default=100.0)
    parser.add_argument("--score-cpu-cap", type=float, default=0.0)
    parser.add_argument("--score-memory-cap", type=float, default=0.0)
    parser.add_argument("--score-io-cap", type=float, default=0.0)
    parser.add_argument(
        "--allow-auto-score-cap", action="store_true",
        help="legacy only: derive the bottleneck scoring cap from baseline / 0.95",
    )
    parser.add_argument(
        "--memory-mode", choices=("rss", "sql_dynamic", "sql_total"), default="rss",
        help="memory metric: process RSS, dynamic SQL memory, or buffer pool plus dynamic SQL memory",
    )
    parser.add_argument("--target-sweet-upper", type=float, default=0.0)
    parser.add_argument(
        "--target-sweet-upper-ratio", type=float, default=0.0,
        help="set the physical target upper bound as this fraction of this run's baseline",
    )
    parser.add_argument(
        "--max-non-target-cpu-increase-pp", type=float,
        help="optional final gate: CPU increase in utilization pp; final CPU <= 1.0 also passes",
    )
    parser.add_argument("--protocol-id", default="")
    parser.add_argument("--attempt-index", type=int, default=1)
    parser.add_argument("--attempt-limit", type=int, default=1)
    parser.add_argument("--workload-seed", type=int)
    return parser.parse_args()


if __name__ == "__main__":
    parsed = parse_args()
    try:
        path = run(parsed)
    except Exception as exc:
        failure = {
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
        try:
            write_json(parsed.output_dir.resolve() / "run_failure.json", failure)
        except Exception:
            pass
        raise
    print(path, flush=True)
