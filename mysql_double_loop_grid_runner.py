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
from mysql_algorithm_b_policy import (  # noqa: E402
    effective_outer_round_budget,
    preflight_resource_error_bounds,
    run_mysql_fresh_model_quality_gate,
)
from mysql_cost_adapter import MySQLCLIClient, MySQLCostAdapter  # noqa: E402
from mysql_live_measurement import measure_benchbase, measure_hybench, measure_job  # noqa: E402
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


def assert_qps_guidance_isolation(objective: Any, measurement: Any) -> dict[str, Any]:
    """Fail closed if QPS can change the inner-loop resource score."""
    resource = measurement.as_resource()
    qps_values = (
        max(float(measurement.db_qps) * 0.01, 1e-12),
        float(measurement.db_qps),
        float(measurement.db_qps) * 100.0,
    )
    scores = [
        float(objective.score(resource, {"db_qps": qps}))
        for qps in qps_values
    ]
    if not all(
        math.isclose(score, scores[0], rel_tol=0.0, abs_tol=1e-12)
        for score in scores[1:]
    ):
        raise ValueError(
            "QPS guidance isolation failed: QPS changed the inner-loop score"
        )
    return {
        "schema_version": "mysql_algorithm_b_qps_guidance_isolation_v1",
        "passed": True,
        "qps_used_by_inner_score": False,
        "qps_used_by_candidate_ranking": False,
        "qps_role": "outer_acceptance_only",
        "probe_qps_values": list(qps_values),
        "probe_scores": scores,
    }


def _raw_payload(validation: Any) -> dict[str, Any]:
    raw = getattr(validation, "raw_result", None)
    return dict(raw) if isinstance(raw, Mapping) else {}


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
    outer_rows: list[dict[str, Any]] = [{
        "iter": 0,
        "params": dict(result.initial_params),
        "qps": float(baseline["db_qps"]),
        "latency_p99_ms": float(baseline["p99_ms"]),
        "latency_p95_ms": float(baseline["p95_ms"]),
        "resource": normalized_resource(baseline["resource"], capability),
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
        "resource": normalized_resource(final["resource"], capability),
        "utility": float(result.final_real_score),
        "explain_samples": list(final.get("explain_samples") or []),
        "phase": "independent_final",
        "outer_accepted": bool(final_decision.successful),
        "outer_acceptance_reason": str(getattr(final_decision, "reason", "final_goal_decision")),
    })
    best_estimated = max(
        (float(row.get("estimated_resource_score")) for row in inner_rows
         if isinstance(row.get("estimated_resource_score"), (int, float))),
        default=float(result.initial_real_score),
    )
    baseline_score = float(result.initial_real_score)
    final_score = float(result.final_real_score)
    delta_u = (final_score - baseline_score) / max(abs(baseline_score), 1e-12) * 100.0
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
            "delta_u_pct": delta_u,
            "qps_before": float(baseline["db_qps"]),
            "qps_after": float(final["db_qps"]),
            "p99_before_ms": float(baseline["p99_ms"]),
            "p99_after_ms": float(final["p99_ms"]),
            "bad_config": not bool(final_decision.successful),
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
            "final_resource_physical": dict(final["resource"]),
            "final_resource_normalized": normalized_resource(final["resource"], capability),
            "independent_final_real_replay": True,
            "initial_param_values": json.loads(args.initial_param_values),
            "tpch_query_ids": args.tpch_query_ids or None,
            "score_resource_limits": dict(capability),
            "memory_measurement_mode": args.memory_mode,
            "final_rss_memory_gib": final.get("rss_memory_gib"),
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


def run(args: argparse.Namespace) -> Path:
    if args.warmup_runs < 0:
        raise ValueError("--warmup-runs cannot be negative")
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
    if args.real_top_k != 5:
        raise ValueError("--real-top-k must equal the global hard cap 5")
    if args.adaptive_topk_min_k != 5:
        raise ValueError("--adaptive-topk-min-k must equal the global hard cap 5")
    if args.adaptive_topk_batch_size < 1:
        raise ValueError("--adaptive-topk-batch-size must be positive")
    if not 0.0 <= args.adaptive_topk_inversion_threshold <= 1.0:
        raise ValueError("--adaptive-topk-inversion-threshold must be in [0,1]")
    if args.outer_rounds < 0:
        raise ValueError("--outer-rounds cannot be negative")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    endpoint = MySQLEndpoint(args.host, args.port, args.database, args.user, args.password)
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
    seed_values = json.loads(args.initial_param_values)
    if not isinstance(seed_values, dict):
        raise ValueError("--initial-param-values must be a JSON object")
    bad_seed_names = sorted(set(seed_values) - set(active))
    if bad_seed_names:
        raise ValueError(f"seed values must be active parameters: {bad_seed_names}")
    seed_values = {str(name): float(value) for name, value in seed_values.items()}
    if any(not math.isfinite(value) or value <= 0.0 for value in seed_values.values()):
        raise ValueError("initial parameter values must be finite and positive")
    if seed_values:
        adapter.apply(seed_values)
        initial.update(seed_values)
    ranges = {
        name: ParameterRange(
            initial[name] * args.min_multiplier,
            initial[name] * args.max_multiplier,
            discrete_count=args.grid_points,
            priority=priority,
            transform="log",
        )
        for priority, name in enumerate(active, start=1)
    }
    capability = {
        "cpu": args.score_cpu_cap or args.cpu_cores,
        "memory": args.score_memory_cap or args.mem_gb,
        "io": args.score_io_cap or args.disk_bw_mb,
    }
    counter = 0
    model: FreshExperienceModel | None = None

    def real_measure(
        params: Mapping[str, float],
        label: str,
        *,
        update_model: bool = True,
    ) -> dict[str, Any]:
        nonlocal counter
        adapter.apply(params)
        counter += 1
        path = output / "real_validations" / f"{counter:04d}_{label}"
        if args.benchmark in {"tpch", "tpcc"}:
            payload = measure_benchbase(
                args.benchmark, endpoint, path,
                mysqld_pid=args.mysqld_pid, jar_path=args.benchbase_jar,
                duration_s=args.duration_s, terminals=args.terminals,
                scale_factor=args.scale_factor, interval_s=args.sample_interval,
                timeout_s=args.validation_timeout,
                transaction_weights=(tpch_weights(args.tpch_query_ids) if args.benchmark == "tpch" else None),
                memory_mode=args.memory_mode,
            )
        elif args.benchmark.startswith("hybench_"):
            payload = measure_hybench(
                args.benchmark.split("_", 1)[1], endpoint, path,
                mysqld_pid=args.mysqld_pid, hybench_home=args.hybench_home,
                duration_s=args.duration_s, clients=args.terminals,
                scale_factor=args.hybench_scale_factor,
                interval_s=args.sample_interval, timeout_s=args.validation_timeout,
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
        if args.memory_mode == "sql_dynamic" and args.score_memory_cap <= 0.0:
            capability["memory"] = float(baseline["memory"]) / 0.95
        baseline_measurement = measurement_from_payload(baseline["resource"], baseline)
        target_upper = (
            args.target_sweet_upper
            if args.scenario != "none"
            else None
        )
        if args.scenario != "none" and target_upper <= 0.0:
            target_upper = (
                capability["memory"] * 0.90
                if args.scenario == "memory" and args.memory_mode == "sql_dynamic"
                else float(getattr(baseline_measurement, args.scenario)) * 0.95
            )
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
        qps_guidance_contract = assert_qps_guidance_isolation(
            objective, baseline_measurement
        )
        write_json(output / "qps_guidance_contract.json", qps_guidance_contract)
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
                # The shared core deduplicates and ranks the union of every
                # coordinate/combination candidate before this fixed Top-5.
                topk_controller="fixed",
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
        final = real_measure(result.optimized_params, "independent_final")
        final_decision = objective.final_decide(
            measurement_from_payload(final["resource"], final)
        )
        args.total_physical_measurement_count = counter
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
                "topk_controller": "fixed",
                "candidate_scope": "global_deduplicated_union",
                "adaptive_topk_min_k": args.adaptive_topk_min_k,
                "real_top_k": args.real_top_k,
            },
            "fresh_model_quality_gate": model_quality_gate,
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
    parser.add_argument("--benchbase-jar", type=Path, default=Path("/home/anonymous/benchbase-mysql/benchbase.jar"))
    parser.add_argument("--hybench-home", type=Path, default=Path("/home/anonymous/HyBench-2024-master"))
    parser.add_argument("--hybench-scale-factor", default="10x")
    parser.add_argument("--job-manifest", type=Path)
    parser.add_argument("--job-repetitions", type=int, default=1)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--active-params", default="io_block_read_cost,row_evaluate_cost")
    parser.add_argument("--initial-param-values", default="{}",
                        help="JSON object used as the baseline point after the restore snapshot")
    parser.add_argument("--tpch-query-ids", default="",
                        help="comma-separated stable TPC-H subset; empty means all queries")
    parser.add_argument("--min-multiplier", type=float, default=.25)
    parser.add_argument("--max-multiplier", type=float, default=4.0)
    parser.add_argument("--grid-points", type=int, default=15)
    parser.add_argument("--inner-workers", type=int, default=8)
    parser.add_argument("--inner-passes", type=int, default=20)
    parser.add_argument("--convergence-patience", type=int, default=2)
    parser.add_argument("--combination-max-size", type=int, default=4)
    parser.add_argument("--combination-values-per-param", type=int, default=3)
    parser.add_argument("--combination-max-candidates", type=int, default=0)
    parser.add_argument(
        "--outer-rounds",
        type=int,
        default=0,
        help="outer safety limit; 0 derives it from ranges and epsilon",
    )
    parser.add_argument(
        "--real-top-k",
        type=int,
        default=5,
        help="global real-replay hard cap; must remain 5",
    )
    parser.add_argument(
        "--adaptive-topk-min-k",
        type=int,
        default=5,
        help="deprecated compatibility option; must remain 5",
    )
    parser.add_argument("--adaptive-topk-batch-size", type=int, default=1)
    parser.add_argument("--adaptive-topk-inversion-threshold", type=float, default=0.10)
    parser.add_argument("--adaptive-topk-score-tie-margin", type=float, default=0.01)
    parser.add_argument("--model-max-neighbors", type=int, default=2)
    parser.add_argument("--epsilon", type=float, default=1e-4)
    parser.add_argument(
        "--calibration-endpoints",
        action="store_true",
        help="deprecated compatibility flag; Algorithm B always runs one fixed Top15 calibration",
    )
    parser.add_argument("--duration-s", type=int, default=30)
    parser.add_argument("--warmup-runs", type=int, default=1,
                        help="unscored initial-parameter replays before the authoritative baseline")
    parser.add_argument("--terminals", type=int, default=1)
    parser.add_argument("--scale-factor", type=float, default=.1)
    parser.add_argument("--sample-interval", type=float, default=1.0)
    parser.add_argument("--validation-timeout", type=float, default=600.0)
    parser.add_argument("--cpu-cores", type=float, default=4.0)
    parser.add_argument("--mem-gb", type=float, default=8.0)
    parser.add_argument("--disk-bw-mb", type=float, default=100.0)
    parser.add_argument("--score-cpu-cap", type=float, default=0.0)
    parser.add_argument("--score-memory-cap", type=float, default=0.0)
    parser.add_argument("--score-io-cap", type=float, default=0.0)
    parser.add_argument("--memory-mode", choices=("rss", "sql_dynamic"), default="rss")
    parser.add_argument("--target-sweet-upper", type=float, default=0.0)
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
