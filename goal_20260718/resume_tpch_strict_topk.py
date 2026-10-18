#!/usr/bin/env python3
"""Resume a completed TPC-H grid run at strict Top-K final selection.

This does not rerun either search loop.  It consumes the persisted physical
Top-K replays of a completed DoubleLoopGridOptimizer run, chooses a strict
maximin-margin point, performs one independent replay, and emits a complete
standard log in a new directory.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import time
from typing import Any, Mapping

from framework.double_loop_scenario import (
    FreshBaselineObjective, measurement_from_payload,
)
from experiment_matrix_mysql import evaluate_acceptance, load_manifest
from mysql_cost_adapter import MySQLCLIClient, MySQLCostAdapter
from mysql_double_loop_grid_runner import normalized_resource, write_json
from mysql_live_measurement import measure_job
from mysql_workload_adapter import MySQLEndpoint


def _key(objective: FreshBaselineObjective, decision: Any, score: float) -> tuple[float, float, float]:
    qps_margin = float(decision.qps_gain_ratio) - float(decision.min_qps_gain_ratio)
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
    return min(qps_margin, drop_margin, sweet_margin), qps_margin, float(score)


def run(args: argparse.Namespace) -> Path:
    source = args.source.resolve()
    source_result_path = source / f"{args.exp_id}_r{args.repeat_id}.json"
    standard = (
        json.loads(source_result_path.read_text(encoding="utf-8"))
        if source_result_path.is_file() else None
    )
    baseline_path = source / "real_validations" / "0002_baseline" / "candidate_measurement.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    capability = {
        "cpu": float(args.cpu_cores),
        "memory": float(baseline["memory"]) / 0.95,
        "io": float(args.disk_bw_mb),
    }
    objective = FreshBaselineObjective(
        "memory", measurement_from_payload(baseline["resource"], baseline),
        resource_limits=capability,
        min_qps_gain_ratio=0.15,
        min_target_drop_ratio=0.05,
        max_target_drop_ratio=0.10,
        target_sweet_upper=capability["memory"] * 0.90,
    )
    if standard is None:
        inner_path = source / "inner_iterations.jsonl"
        inner_rows = [
            json.loads(line) for line in inner_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ] if inner_path.is_file() else []
        source_files = [path for path in source.rglob("*") if path.is_file()]
        source_wall = (
            max(path.stat().st_mtime for path in source_files)
            - min(path.stat().st_mtime for path in source_files)
            if source_files else 0.0
        )
        baseline_score = objective.score(baseline["resource"], baseline)
        standard = {
            "meta": {
                "exp_id": args.exp_id, "benchmark": "tpch", "db": "mysql8",
                "method": "outer_binary_inner_estimated_grid", "bottleneck": "memory",
                "bottleneck_target": 0.90, "repeat_id": args.repeat_id,
                "machine_spec": {
                    "cpu_cores": args.cpu_cores, "mem_gb": 8.0,
                    "disk_bw_mb": args.disk_bw_mb,
                },
            },
            "outer_loop": [{
                "iter": 0, "params": dict(baseline["params"]),
                "qps": float(baseline["db_qps"]),
                "latency_p99_ms": float(baseline["p99_ms"]),
                "latency_p95_ms": float(baseline["p95_ms"]),
                "resource": normalized_resource(baseline["resource"], capability),
                "utility": float(baseline_score),
                "explain_samples": list(baseline.get("explain_samples") or []),
                "phase": "baseline", "outer_accepted": True,
                "outer_acceptance_reason": "baseline",
            }],
            "inner_loop_summary": {
                "total_iterations": len(inner_rows),
                "wall_time_ms": round(source_wall * 1000.0),
                "best_estimated_utility": max(
                    (float(row["estimated_resource_score"]) for row in inner_rows
                     if isinstance(row.get("estimated_resource_score"), (int, float))),
                    default=float(baseline_score),
                ),
                "best_params": dict(baseline["params"]),
            },
            "result": {
                "final_params": dict(baseline["params"]), "delta_u_pct": 0.0,
                "qps_before": float(baseline["db_qps"]),
                "qps_after": float(baseline["db_qps"]),
                "p99_before_ms": float(baseline["p99_ms"]),
                "p99_after_ms": float(baseline["p99_ms"]),
                "bad_config": True, "converge_outer_iters": 2,
                "total_wall_time_s": source_wall,
            },
            "audit": {
                "framework": "DoubleLoopGridOptimizer",
                "outer_search": "binary_interval_shrink_after_real_topk_acceptance",
                "inner_search": "estimated_coordinate_grid_plus_combinations",
                "source_run_failure": str(source / "run_failure.json"),
            },
        }
    eligible: list[tuple[tuple[float, float, float], Path, dict[str, Any], Any]] = []
    inspected = 0
    for path in sorted((source / "real_validations").glob("*_candidate/candidate_measurement.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        inspected += 1
        measurement = measurement_from_payload(payload["resource"], payload)
        decision = objective.final_decide(measurement)
        if decision.successful:
            eligible.append((_key(objective, decision, objective.score(payload["resource"], payload)), path, payload, decision))
    if not eligible:
        raise RuntimeError(f"no strict-pass physical Top-K point among {inspected} candidates")
    selection_key, selected_path, selected, measured_decision = max(eligible, key=lambda item: item[0])

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    endpoint = MySQLEndpoint(args.host, args.port, args.database, args.user, args.password)
    adapter = MySQLCostAdapter(MySQLCLIClient({
        "host": args.host, "port": args.port, "user": args.user,
        "password": args.password, "database": args.database,
    }))
    snapshot = adapter.snapshot()
    adapter.save_snapshot(output / "mysql_cost_snapshot.json", snapshot)
    started = time.monotonic()
    try:
        adapter.apply(selected["params"])
        final = measure_job(
            endpoint, output / "real_validations" / "0001_independent_final",
            mysqld_pid=args.mysqld_pid, manifest_path=args.tpch_native_manifest,
            concurrency=args.terminals, repetitions=args.repetitions,
            interval_s=args.sample_interval, timeout_s=args.validation_timeout,
            memory_mode="sql_total",
        )
        final["params"] = dict(selected["params"])
        write_json(output / "real_validations" / "0001_independent_final" / "candidate_measurement.json", final)
    finally:
        adapter.restore(snapshot)
    elapsed = time.monotonic() - started
    final_decision = objective.final_decide(measurement_from_payload(final["resource"], final))
    baseline_score = objective.score(baseline["resource"], baseline)
    final_score = objective.score(final["resource"], final)

    outer = [row for row in standard["outer_loop"] if row.get("phase") != "independent_final"]
    outer.append({
        "iter": len(outer), "params": dict(selected["params"]),
        "qps": float(selected["db_qps"]), "latency_p99_ms": float(selected["p99_ms"]),
        "latency_p95_ms": float(selected["p95_ms"]),
        "resource": normalized_resource(selected["resource"], capability),
        "utility": float(objective.score(selected["resource"], selected)),
        "explain_samples": list(selected.get("explain_samples") or []),
        "phase": "strict_topk_reselection", "outer_accepted": True,
        "outer_acceptance_reason": "persisted_real_topk_strict_pass",
    })
    outer.append({
        "iter": len(outer), "params": dict(selected["params"]),
        "qps": float(final["db_qps"]), "latency_p99_ms": float(final["p99_ms"]),
        "latency_p95_ms": float(final["p95_ms"]),
        "resource": normalized_resource(final["resource"], capability),
        "utility": float(final_score),
        "explain_samples": list(final.get("explain_samples") or []),
        "phase": "independent_final", "outer_accepted": bool(final_decision.successful),
        "outer_acceptance_reason": str(final_decision.reason),
    })
    standard["outer_loop"] = outer
    standard["inner_loop_summary"]["best_params"] = dict(selected["params"])
    standard["result"].update({
        "final_params": dict(selected["params"]),
        "delta_u_pct": (final_score - baseline_score) / max(abs(baseline_score), 1e-12) * 100.0,
        "qps_before": float(baseline["db_qps"]), "qps_after": float(final["db_qps"]),
        "p99_before_ms": float(baseline["p99_ms"]), "p99_after_ms": float(final["p99_ms"]),
        "bad_config": not bool(final_decision.successful),
        "total_wall_time_s": float(standard["result"]["total_wall_time_s"]) + elapsed,
    })
    standard["audit"].update({
        "final_goal_decision": vars(final_decision),
        "final_resource_physical": dict(final["resource"]),
        "final_resource_normalized": normalized_resource(final["resource"], capability),
        "final_topk_selection": {
            "policy": "strict_final_gate_then_maximin_margin",
            "source_run": str(source), "source_candidate": str(selected_path),
            "inspected_real_topk": inspected, "strict_eligible": len(eligible),
            "selection_key": {
                "min_constraint_margin": selection_key[0],
                "qps_margin_above_15pct": selection_key[1],
                "real_resource_score": selection_key[2],
            },
            "measured_decision": vars(measured_decision),
        },
        "independent_final_real_replay": True,
        "resume_only_no_search_rerun": True,
    })
    result_path = output / f"{args.exp_id}_r{args.repeat_id}.json"
    write_json(result_path, standard)
    acceptance = evaluate_acceptance(standard, load_manifest()["acceptance"])
    write_json(output / "run_summary.json", {
        "result_path": str(result_path), "acceptance": acceptance,
        "final_goal_decision": vars(final_decision),
    })
    return result_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--exp-id", default="A15")
    parser.add_argument("--repeat-id", type=int, default=1)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=13361)
    parser.add_argument("--database", default="tpch_small")
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", default='CHANGE_ME')
    parser.add_argument("--mysqld-pid", type=int, default=1257907)
    parser.add_argument("--tpch-native-manifest", type=Path, required=True)
    parser.add_argument("--terminals", type=int, default=16)
    parser.add_argument("--repetitions", type=int, default=400)
    parser.add_argument("--sample-interval", type=float, default=1.0)
    parser.add_argument("--validation-timeout", type=float, default=180.0)
    parser.add_argument("--cpu-cores", type=float, default=4.0)
    parser.add_argument("--disk-bw-mb", type=float, default=100.0)
    return parser.parse_args()


if __name__ == "__main__":
    print(run(parse_args()))
