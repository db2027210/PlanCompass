#!/usr/bin/env python3
"""Analyze the interleaved JOB ratio-scale canary with paired 95% intervals."""

from __future__ import annotations

from collections import defaultdict
import argparse
import json
import math
from pathlib import Path
import statistics
from typing import Any, Dict, Iterable, List, Mapping


T95_DF2 = 4.302652729696142
RESOURCES = ("cpu", "memory", "io")


def interval(values: Iterable[float]) -> Dict[str, float]:
    samples = [float(value) for value in values]
    mean = statistics.mean(samples)
    if len(samples) < 2:
        return {"mean": mean, "lower95": mean, "upper95": mean}
    half = T95_DF2 * statistics.stdev(samples) / math.sqrt(len(samples))
    return {"mean": mean, "lower95": mean - half, "upper95": mean + half}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def query_latency(path: Path) -> Dict[str, Dict[str, float]]:
    totals: Dict[str, float] = defaultdict(float)
    counts: Dict[str, int] = defaultdict(int)
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("event") != "query_complete" or row.get("success") is not True:
            continue
        query_id = str(row.get("query_id"))
        totals[query_id] += float(row.get("latency_ms") or 0.0)
        counts[query_id] += 1
    total_latency = sum(totals.values())
    return {
        query_id: {
            "count": counts[query_id],
            "latency_ms": value,
            "latency_share": value / total_latency if total_latency > 0.0 else 0.0,
        }
        for query_id, value in sorted(totals.items())
    }


def combine_query_latency(rows: Iterable[Mapping[str, Mapping[str, float]]]) -> Dict[str, Any]:
    totals: Dict[str, float] = defaultdict(float)
    counts: Dict[str, int] = defaultdict(int)
    for row in rows:
        for query_id, values in row.items():
            totals[query_id] += float(values["latency_ms"])
            counts[query_id] += int(values["count"])
    total = sum(totals.values())
    return {
        query_id: {
            "count": counts[query_id],
            "latency_ms": value,
            "latency_share": value / total if total > 0.0 else 0.0,
        }
        for query_id, value in sorted(totals.items())
    }


def sign(value: float, epsilon: float = 1e-12) -> int:
    return 1 if value > epsilon else -1 if value < -epsilon else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--estimator-evidence", type=Path, required=True)
    args = parser.parse_args()
    groups: Dict[str, List[Dict[str, Any]]] = {"baseline": [], "candidate": []}
    query_groups: Dict[str, List[Dict[str, Any]]] = {"baseline": [], "candidate": []}
    for kind in groups:
        for repeat in (1, 2, 3):
            point = args.root / f"{kind}_r{repeat}"
            result = read_json(point / "point_result.json")
            if result.get("status") != "complete":
                raise RuntimeError(f"incomplete point: {point}")
            groups[kind].append(result)
            query_groups[kind].append(query_latency(point / "measurement_queries.jsonl"))

    metrics: Dict[str, Any] = {}
    for metric in ("qps", *RESOURCES):
        if metric == "qps":
            baseline = [row["measurement"]["qps"] for row in groups["baseline"]]
            candidate = [row["measurement"]["qps"] for row in groups["candidate"]]
        else:
            baseline = [
                row["measurement"]["resource_mean_utilization"][metric]
                for row in groups["baseline"]
            ]
            candidate = [
                row["measurement"]["resource_mean_utilization"][metric]
                for row in groups["candidate"]
            ]
        differences = [right - left for left, right in zip(baseline, candidate)]
        metrics[metric] = {
            "baseline_samples": baseline,
            "candidate_samples": candidate,
            "baseline": interval(baseline),
            "candidate": interval(candidate),
            "paired_delta_candidate_minus_baseline": interval(differences),
            "mean_relative_change": (
                statistics.mean(candidate) / statistics.mean(baseline) - 1.0
            ),
        }

    estimator = read_json(args.estimator_evidence)
    predicted_ratios = estimator["estimates"]["first_cross_boundary"]["ratio_scale"][
        "feature_ratios"
    ]
    direction = {}
    for resource in RESOURCES:
        predicted_delta = float(predicted_ratios[resource]) - 1.0
        actual_interval = metrics[resource]["paired_delta_candidate_minus_baseline"]
        actual_delta = actual_interval["mean"]
        if actual_interval["lower95"] > 0.0:
            actual_status = "increase"
            actual_resolved_sign = 1
        elif actual_interval["upper95"] < 0.0:
            actual_status = "decrease"
            actual_resolved_sign = -1
        else:
            actual_status = "unresolved_95pct_interval_crosses_zero"
            actual_resolved_sign = None
        direction[resource] = {
            "predicted_ratio": predicted_ratios[resource],
            "predicted_sign": sign(predicted_delta),
            "actual_paired_delta_mean": actual_delta,
            "actual_paired_delta_95pct": actual_interval,
            "actual_direction_status": actual_status,
            "actual_resolved_sign": actual_resolved_sign,
            "sign_match": (
                None
                if actual_resolved_sign is None
                else sign(predicted_delta) == actual_resolved_sign
            ),
        }

    checks = {
        "all_points_correct": all(
            row["measurement"].get("correctness_passed") is True
            and int(row["measurement"].get("error_count") or 0) == 0
            for rows in groups.values() for row in rows
        ),
        "qps_lower95_delta_positive": (
            metrics["qps"]["paired_delta_candidate_minus_baseline"]["lower95"] > 0.0
        ),
        "memory_upper95_delta_negative": (
            metrics["memory"]["paired_delta_candidate_minus_baseline"]["upper95"] < 0.0
        ),
        "candidate_cpu_upper95_below_80pct": metrics["cpu"]["candidate"]["upper95"] < 0.8,
        "candidate_io_upper95_below_80pct": metrics["io"]["candidate"]["upper95"] < 0.8,
        "candidate_memory_upper95_below_85pct": metrics["memory"]["candidate"]["upper95"] < 0.85,
    }
    primary = {
        "qps_improvement_at_least_15pct": metrics["qps"]["mean_relative_change"] >= 0.15,
        "memory_pressure_drop_at_least_5pct": metrics["memory"]["mean_relative_change"] <= -0.05,
    }
    strict_accept = all(checks.values())
    primary_success = strict_accept and all(primary.values())
    changed_queries = ("26a", "33a")
    latency = {
        kind: combine_query_latency(query_groups[kind]) for kind in query_groups
    }
    changed_query_latency = {
        query_id: {
            kind: latency[kind].get(query_id, {}) for kind in latency
        }
        for query_id in changed_queries
    }
    result = {
        "schema_version": 1,
        "benchmark": "JOB-8-mixed-26a-weighted",
        "replicate_design": "interleaved paired B-C-B-C-B-C",
        "metrics": metrics,
        "ratio_scale_direction_validation": direction,
        "changed_queries_from_plan_scan": list(changed_queries),
        "changed_query_latency": changed_query_latency,
        "hard_gate_checks": checks,
        "primary_goal_checks": primary,
        "strict_accept": strict_accept,
        "primary_success": primary_success,
        "step_effective_for_joint_qps_memory_goal": (
            checks["qps_lower95_delta_positive"]
            and checks["memory_upper95_delta_negative"]
        ),
        "decision": "accept" if strict_accept else "round_rejected_no_feasible_candidate",
        "incumbent_updated": strict_accept,
        "accepted_shrink_count_increment": 1 if strict_accept else 0,
        "parameter_range_shrunk": strict_accept,
    }
    output = args.root / "analysis.json"
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "output": str(output),
        "strict_accept": strict_accept,
        "primary_success": primary_success,
        "metrics": metrics,
        "direction": direction,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
