#!/usr/bin/env python3
"""Export one double-loop grid run to the experiment JSON contract."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from double_loop_scenario import sigmoid_linear_resource_score_details


COST_PARAMS = {
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
}


def finite(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
            else:
                raise ValueError(f"{path}:{line_number}: expected JSON object")
    return rows


def same_params(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    if set(left) != set(right):
        return False
    return all(
        finite(left[name]) is not None
        and finite(right[name]) is not None
        and math.isclose(float(left[name]), float(right[name]), rel_tol=0.0, abs_tol=1e-12)
        for name in left
    )


def best_raw_result(round_row: Mapping[str, Any]) -> Dict[str, Any]:
    best = round_row.get("small_loop_best_candidate") or {}
    best_params = best.get("params") if isinstance(best, Mapping) else {}
    for item in round_row.get("validated_points") or []:
        if not isinstance(item, Mapping):
            continue
        if item.get("is_small_loop_best") is True or (
            isinstance(best_params, Mapping)
            and isinstance(item.get("params"), Mapping)
            and same_params(best_params, item["params"])
        ):
            raw = item.get("raw_result")
            return dict(raw) if isinstance(raw, Mapping) else {}
    return {}


def explain_samples(raw: Mapping[str, Any]) -> List[str]:
    samples: List[str] = []
    for key in ("explain_samples", "explain_analyze", "explain_output"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            samples.append(value.strip())
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            samples.extend(str(item) for item in value if item)
    plan = raw.get("plan_capture")
    if isinstance(plan, Mapping):
        samples.append(json.dumps(plan, ensure_ascii=False, sort_keys=True))
    return samples[:20]


def pruning_explain_samples(scenario_dir: Path) -> List[str]:
    """Return current-run workload plans when replay rows lack plan capture.

    The fallback is deliberately labelled as a fresh workload-pruning sample,
    not as a candidate-specific EXPLAIN.  This keeps every exported outer-loop
    row drawable without misrepresenting the provenance of the plan.
    """
    pruning_path = scenario_dir / "fresh_workload_pruning.json"
    if not pruning_path.is_file():
        return []
    pruning = read_json(pruning_path)
    result: List[str] = []
    for sample in pruning.get("baseline_explain_samples") or []:
        if not isinstance(sample, Mapping):
            continue
        result.append(
            json.dumps(
                {
                    "source": "fresh_workload_pruning_baseline",
                    "candidate_specific": False,
                    "current_run_only": True,
                    "sample": sample,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    return result[:20]


def resource_fractions(
    resource: Mapping[str, Any], capability: Mapping[str, Any]
) -> Dict[str, Optional[float]]:
    result: Dict[str, Optional[float]] = {}
    for source, target in (("cpu", "cpu"), ("memory", "mem"), ("io", "io")):
        value = finite(resource.get(source))
        limit = finite(capability.get(source))
        result[target] = value / limit if value is not None and limit and limit > 0.0 else None
    return result


def score_resources(
    scenario: str,
    resource: Mapping[str, Any],
    limits: Mapping[str, Any],
    sweet_lower: Optional[float],
    sweet_upper: Optional[float],
    io_reward_weight: float,
) -> Optional[float]:
    if not all(finite(resource.get(name)) is not None for name in ("cpu", "memory", "io")):
        return None
    if not all(finite(limits.get(name)) not in (None, 0.0) for name in ("cpu", "memory", "io")):
        return None

    details = sigmoid_linear_resource_score_details(
        scenario=scenario,  # type: ignore[arg-type]
        resource={name: float(resource[name]) for name in ("cpu", "memory", "io")},
        resource_limits={name: float(limits[name]) for name in ("cpu", "memory", "io")},
        target_sweet_lower=sweet_lower,
        target_sweet_upper=sweet_upper,
        io_reward_weight=io_reward_weight,
    )
    return float(details["total"])


def inner_loop_summary(
    scenario_dir: Path,
    summary: Mapping[str, Any],
) -> Dict[str, Any]:
    inner_path = scenario_dir / str(summary.get("inner_iteration_log") or "inner_iterations.jsonl")
    if not inner_path.is_file():
        inner_path = scenario_dir / "inner_iterations.jsonl"
    rows = read_jsonl(inner_path)
    timestamps: Dict[int, List[datetime]] = defaultdict(list)
    scores: List[float] = []
    for row in rows:
        try:
            round_index = int(row.get("outer_round", row.get("round")))
        except (TypeError, ValueError):
            continue
        raw_timestamp = row.get("timestamp")
        if isinstance(raw_timestamp, str):
            try:
                timestamps[round_index].append(datetime.strptime(raw_timestamp, "%Y-%m-%d %H:%M:%S"))
            except ValueError:
                pass
        score = finite(
            row.get(
                "estimated_resource_score",
                row.get("score_after", row.get("estimated_objective")),
            )
        )
        if score is not None:
            scores.append(score)
    wall_time_ms = sum(
        max(0.0, (max(values) - min(values)).total_seconds()) * 1000.0
        for values in timestamps.values()
        if values
    )
    final_params = (summary.get("final_confirmation") or {}).get("params") or summary.get("optimized_params") or {}
    return {
        "total_iterations": len(rows),
        "wall_time_ms": round(wall_time_ms),
        "best_estimated_utility": max(scores) if scores else None,
        "best_params": final_params,
    }


def completion_errors(
    document: Mapping[str, Any],
    *,
    successful: bool,
    bottleneck: str,
) -> List[str]:
    """Validate the complete structured-log contract before export acceptance."""
    errors: List[str] = []
    meta = document.get("meta") if isinstance(document.get("meta"), Mapping) else {}
    outer = document.get("outer_loop") if isinstance(document.get("outer_loop"), list) else []
    inner = (
        document.get("inner_loop_summary")
        if isinstance(document.get("inner_loop_summary"), Mapping)
        else {}
    )
    result = document.get("result") if isinstance(document.get("result"), Mapping) else {}
    audit = document.get("audit") if isinstance(document.get("audit"), Mapping) else {}

    if not successful:
        errors.append("run is not independently confirmed successful")
    machine = meta.get("machine_spec") if isinstance(meta.get("machine_spec"), Mapping) else {}
    for key in ("cpu_cores", "mem_gb", "disk_bw_mb"):
        if finite(machine.get(key)) is None:
            errors.append(f"meta.machine_spec.{key} is missing")
    if bottleneck != "none" and finite(meta.get("bottleneck_target")) is None:
        errors.append("meta.bottleneck_target is missing")

    if not outer:
        errors.append("outer_loop is empty")
    for index, row in enumerate(outer, start=1):
        if not isinstance(row, Mapping):
            errors.append(f"outer_loop[{index}] is not an object")
            continue
        if set(row.get("params") or {}) != COST_PARAMS:
            errors.append(f"outer_loop[{index}].params is not exactly seven cost parameters")
        if not row.get("explain_samples"):
            errors.append(f"outer_loop[{index}].explain_samples is empty")
        for key in ("qps", "latency_p99_ms", "latency_p95_ms", "utility"):
            if finite(row.get(key)) is None:
                errors.append(f"outer_loop[{index}].{key} is missing")
        resource = row.get("resource") if isinstance(row.get("resource"), Mapping) else {}
        for key in ("cpu", "mem", "io"):
            if finite(resource.get(key)) is None:
                errors.append(f"outer_loop[{index}].resource.{key} is missing")

    if int(inner.get("total_iterations") or 0) <= 0:
        errors.append("inner_loop_summary.total_iterations is not positive")
    if finite(inner.get("wall_time_ms")) is None:
        errors.append("inner_loop_summary.wall_time_ms is missing")
    if finite(inner.get("best_estimated_utility")) is None:
        errors.append("inner_loop_summary.best_estimated_utility is missing")
    if set(inner.get("best_params") or {}) != COST_PARAMS:
        errors.append("inner_loop_summary.best_params is not exactly seven cost parameters")

    if result.get("bad_config") is not False:
        errors.append("result.bad_config is not false")
    if set(result.get("final_params") or {}) != COST_PARAMS:
        errors.append("result.final_params is not exactly seven cost parameters")
    for key in (
        "delta_u_pct",
        "qps_before",
        "qps_after",
        "p99_before_ms",
        "p99_after_ms",
        "total_wall_time_s",
    ):
        if finite(result.get(key)) is None:
            errors.append(f"result.{key} is missing")
    if int(result.get("converge_outer_iters") or -1) != len(outer):
        errors.append("result.converge_outer_iters does not match outer_loop")
    if audit.get("independent_final_real_replay") is not True:
        errors.append("audit.independent_final_real_replay is false")
    raw_path = audit.get("final_raw_validation_path")
    if not isinstance(raw_path, str) or not Path(raw_path).is_file():
        errors.append("audit.final_raw_validation_path is missing")
    return errors


def build_document(args: argparse.Namespace) -> Dict[str, Any]:
    scenario_dir = args.scenario_dir.resolve()
    summary = read_json(scenario_dir / "scenario_summary.json")
    baseline_doc = read_json(scenario_dir / "fresh_baseline.json")
    baseline = baseline_doc.get("measurement") or summary.get("fresh_baseline") or {}
    baseline_raw = baseline_doc.get("raw") or {}
    final = summary.get("final_confirmation") or {}
    final_resource = final.get("measurement") or final.get("resources") or {}
    final_raw = {}
    raw_path = final.get("raw_validation_path")
    if isinstance(raw_path, str) and Path(raw_path).is_file():
        final_raw = read_json(Path(raw_path))
    physical = baseline_doc.get("physical_semantics") or summary.get("physical_semantics") or {}
    capability = physical.get("resource_capacity") or final_raw.get("runtime_capability") or {}
    limits = physical.get("scoring_resource_limits") or {}
    sweet_lower = finite(physical.get("target_sweet_lower"))
    sweet_upper = finite(physical.get("target_sweet_upper"))
    io_reward_weight = finite(summary.get("io_reward_weight")) or 0.25
    baseline_utility = score_resources(
        args.bottleneck, baseline, limits, sweet_lower, sweet_upper, io_reward_weight
    )
    final_utility = score_resources(
        args.bottleneck, final_resource, limits, sweet_lower, sweet_upper, io_reward_weight
    )

    outer_loop: List[Dict[str, Any]] = []
    fallback_explains = pruning_explain_samples(scenario_dir)
    candidate_specific_explain_count = 0
    for round_row in summary.get("rounds") or []:
        if not isinstance(round_row, Mapping):
            continue
        best = round_row.get("small_loop_best_candidate")
        if not isinstance(best, Mapping):
            continue
        raw = best_raw_result(round_row)
        resource = best.get("real_resource") or {}
        round_explains = explain_samples(raw)
        if round_explains:
            candidate_specific_explain_count += 1
        else:
            round_explains = list(fallback_explains)
        outer_loop.append(
            {
                "iter": int(round_row.get("round", len(outer_loop))) + 1,
                "params": best.get("params") or {},
                "qps": finite(best.get("real_db_qps") or resource.get("db_qps")),
                "latency_p99_ms": finite(raw.get("p99_ms")),
                "latency_p95_ms": finite(raw.get("p95_ms")),
                "resource": resource_fractions(resource, capability),
                "utility": finite(best.get("real_resource_score")),
                "explain_samples": round_explains,
            }
        )

    qps_before = finite(baseline.get("db_qps"))
    qps_after = finite(final_resource.get("db_qps") or final.get("db_qps"))
    p99_before = finite(baseline_raw.get("p99_ms"))
    p99_after = finite(final.get("p99_ms") or final_raw.get("p99_ms"))
    baseline_end = finite((baseline_raw.get("window") or [None, None])[-1])
    baseline_elapsed = finite(baseline_raw.get("elapsed_s")) or 0.0
    final_end = finite((final_raw.get("window") or [None, None])[-1])
    total_wall_time_s = (
        max(0.0, final_end - (baseline_end - baseline_elapsed))
        if baseline_end is not None and final_end is not None
        else None
    )
    target_capacity = finite(capability.get(args.bottleneck)) if args.bottleneck != "none" else None
    bottleneck_target = (
        sweet_upper / target_capacity
        if sweet_upper is not None and target_capacity not in (None, 0.0)
        else None
    )
    final_goal = final.get("final_goal_decision") or summary.get("final_decision") or {}
    independent = final.get("independent_real_replay") is True
    successful = summary.get("successful") is True and final_goal.get("successful") is True and independent
    delta_u_pct = (
        (final_utility - baseline_utility) / max(abs(baseline_utility), 1e-12) * 100.0
        if baseline_utility is not None and final_utility is not None
        else None
    )
    document = {
        "meta": {
            "exp_id": args.exp_id,
            "benchmark": args.benchmark,
            "db": args.db,
            "method": args.method,
            "bottleneck": args.bottleneck,
            "bottleneck_target": bottleneck_target,
            "repeat_id": args.repeat_id,
            "machine_spec": {
                "cpu_cores": finite(capability.get("cpu")),
                "mem_gb": finite(capability.get("memory")),
                "disk_bw_mb": finite(capability.get("io")),
            },
        },
        "outer_loop": outer_loop,
        "inner_loop_summary": inner_loop_summary(scenario_dir, summary),
        "result": {
            "final_params": final.get("params") or summary.get("optimized_params") or {},
            "delta_u_pct": delta_u_pct,
            "qps_before": qps_before,
            "qps_after": qps_after,
            "p99_before_ms": p99_before,
            "p99_after_ms": p99_after,
            "resource_before": resource_fractions(baseline, capability),
            "resource_after": resource_fractions(final_resource, capability),
            "bad_config": not successful,
            "converge_outer_iters": int(summary.get("outer_round_count") or len(outer_loop)),
            "total_wall_time_s": total_wall_time_s,
        },
        "audit": {
            "source_scenario_dir": str(scenario_dir),
            "score_formula": summary.get("score_formula"),
            "qps_used_in_resource_score": False,
            "search_stop_reason": summary.get("stop_reason"),
            "independent_final_real_replay": independent,
            "fresh_baseline_path": str((scenario_dir / "fresh_baseline.json").resolve()),
            "final_raw_validation_path": (
                str(Path(raw_path).resolve()) if isinstance(raw_path, str) else None
            ),
            "final_goal_decision": final_goal,
            "outer_iterations_with_explain": sum(bool(row["explain_samples"]) for row in outer_loop),
            "outer_iteration_count": len(outer_loop),
            "candidate_specific_explain_count": candidate_specific_explain_count,
            "fallback_explain_source": (
                "fresh_workload_pruning_baseline"
                if fallback_explains
                else None
            ),
        },
    }
    if args.require_complete:
        errors = completion_errors(
            document,
            successful=successful,
            bottleneck=args.bottleneck,
        )
        if errors:
            raise RuntimeError("; ".join(errors))
    return document


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario_dir", type=Path)
    parser.add_argument("--exp-id", required=True)
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--bottleneck", choices=("cpu", "memory", "io", "none"), required=True)
    parser.add_argument("--repeat-id", type=int, default=1)
    parser.add_argument("--db", default="pg17")
    parser.add_argument(
        "--method",
        default="coordinate_grid_resource_estimation_topk_replay",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-complete", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    document = build_document(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
