"""Calibrate resource directions across exact PostgreSQL plan boundaries.

The module deliberately separates four kinds of evidence:

* exact per-query structural plan changes;
* client-observed query-time share;
* concurrency-apportioned cgroup resource exposure estimates;
* endpoint uncertainty and joint QPS/resource improvement.

Cgroup samples cannot identify the resource consumption of one concurrent
query exactly.  Such attribution is therefore labelled as an estimate and is
never promoted to exact evidence.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from statistics import mean, stdev
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


T_CRITICAL_95 = {
    1: 12.706,
    2: 4.303,
    3: 3.182,
    4: 2.776,
    5: 2.571,
    6: 2.447,
    7: 2.365,
    8: 2.306,
    9: 2.262,
    10: 2.228,
    11: 2.201,
    12: 2.179,
    13: 2.160,
    14: 2.145,
    15: 2.131,
    16: 2.120,
    17: 2.110,
    18: 2.101,
    19: 2.093,
    20: 2.086,
    21: 2.080,
    22: 2.074,
    23: 2.069,
    24: 2.064,
    25: 2.060,
    26: 2.056,
    27: 2.052,
    28: 2.048,
    29: 2.045,
    30: 2.042,
}


@dataclass(frozen=True)
class MeanInterval:
    sample_count: int
    mean: Optional[float]
    standard_error: Optional[float]
    half_width_95: Optional[float]
    available: bool


@dataclass(frozen=True)
class DifferenceGate:
    before: MeanInterval
    after: MeanInterval
    delta_after_minus_before: Optional[float]
    combined_error_bound: Optional[float]
    credible: bool
    direction: str
    reason: str


def canonical_sha(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _walk_plan(node: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    yield node
    for child in node.get("Plans") or []:
        if isinstance(child, Mapping):
            yield from _walk_plan(child)


def _counter_delta(
    before: Mapping[str, int], after: Mapping[str, int]
) -> Dict[str, Dict[str, int]]:
    keys = sorted(set(before) | set(after))
    added = {
        key: int(after.get(key, 0)) - int(before.get(key, 0))
        for key in keys
        if int(after.get(key, 0)) > int(before.get(key, 0))
    }
    removed = {
        key: int(before.get(key, 0)) - int(after.get(key, 0))
        for key in keys
        if int(before.get(key, 0)) > int(after.get(key, 0))
    }
    return {"added": added, "removed": removed}


def summarize_plan(plan: Mapping[str, Any]) -> Dict[str, Any]:
    operators: Counter[str] = Counter()
    joins: Counter[str] = Counter()
    scans: Counter[str] = Counter()
    parallel_node_count = 0
    workers_planned = 0
    for node in _walk_plan(plan):
        node_type = str(node.get("Node Type") or "unknown")
        operators[node_type] += 1
        if "Join" in node_type or node.get("Join Type") is not None:
            joins[f"{node_type}:{node.get('Join Type') or 'unspecified'}"] += 1
        if "Scan" in node_type:
            scans[node_type] += 1
        if node.get("Parallel Aware") is True:
            parallel_node_count += 1
        workers_planned += int(node.get("Workers Planned") or 0)
    return {
        "operator_histogram": dict(sorted(operators.items())),
        "join_histogram": dict(sorted(joins.items())),
        "scan_histogram": dict(sorted(scans.items())),
        "parallel_node_count": parallel_node_count,
        "workers_planned": workers_planned,
        "node_count": sum(operators.values()),
    }


def diff_query_plans(
    before_plans: Mapping[str, Mapping[str, Any]],
    after_plans: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    query_ids = sorted(
        set(str(key) for key in before_plans) | set(str(key) for key in after_plans),
        key=lambda value: (not value.isdigit(), int(value) if value.isdigit() else value),
    )
    changes: List[Dict[str, Any]] = []
    for query_id in query_ids:
        before = before_plans.get(query_id)
        after = after_plans.get(query_id)
        if before is None or after is None:
            changes.append(
                {
                    "query_id": query_id,
                    "changed": True,
                    "missing_side": "before" if before is None else "after",
                }
            )
            continue
        before_summary = summarize_plan(before)
        after_summary = summarize_plan(after)
        before_sha = canonical_sha(before)
        after_sha = canonical_sha(after)
        if before_sha == after_sha:
            continue
        changes.append(
            {
                "query_id": query_id,
                "changed": True,
                "before_sha256": before_sha,
                "after_sha256": after_sha,
                "operators": _counter_delta(
                    before_summary["operator_histogram"],
                    after_summary["operator_histogram"],
                ),
                "joins": _counter_delta(
                    before_summary["join_histogram"],
                    after_summary["join_histogram"],
                ),
                "scans": _counter_delta(
                    before_summary["scan_histogram"],
                    after_summary["scan_histogram"],
                ),
                "parallel": {
                    "before_parallel_node_count": before_summary["parallel_node_count"],
                    "after_parallel_node_count": after_summary["parallel_node_count"],
                    "parallel_node_delta": (
                        after_summary["parallel_node_count"]
                        - before_summary["parallel_node_count"]
                    ),
                    "before_workers_planned": before_summary["workers_planned"],
                    "after_workers_planned": after_summary["workers_planned"],
                    "workers_planned_delta": (
                        after_summary["workers_planned"]
                        - before_summary["workers_planned"]
                    ),
                },
                "node_count_delta": (
                    after_summary["node_count"] - before_summary["node_count"]
                ),
            }
        )
    return {
        "schema_version": 1,
        "changed_query_ids": [row["query_id"] for row in changes],
        "changed_query_count": len(changes),
        "query_changes": changes,
    }


def mean_interval_95(values: Sequence[float]) -> MeanInterval:
    finite = [float(value) for value in values if math.isfinite(float(value))]
    if not finite:
        return MeanInterval(0, None, None, None, False)
    value_mean = mean(finite)
    if len(finite) < 2:
        return MeanInterval(len(finite), value_mean, None, None, False)
    standard_error = stdev(finite) / math.sqrt(len(finite))
    degrees = len(finite) - 1
    critical = T_CRITICAL_95.get(degrees, 1.96)
    return MeanInterval(
        len(finite),
        value_mean,
        standard_error,
        critical * standard_error,
        True,
    )


def difference_gate_95(
    before_values: Sequence[float], after_values: Sequence[float]
) -> DifferenceGate:
    before = mean_interval_95(before_values)
    after = mean_interval_95(after_values)
    if before.mean is None or after.mean is None:
        return DifferenceGate(
            before, after, None, None, False, "unknown", "missing_finite_samples"
        )
    delta = after.mean - before.mean
    if not before.available or not after.available:
        return DifferenceGate(
            before,
            after,
            delta,
            None,
            False,
            "uncertain",
            "at_least_two_samples_per_endpoint_required",
        )
    error = float(before.half_width_95) + float(after.half_width_95)
    credible = abs(delta) > error
    if not credible:
        direction = "uncertain"
        reason = "delta_does_not_exceed_combined_endpoint_error_bounds"
    elif delta < 0.0:
        direction = "decrease"
        reason = "credible_decrease"
    else:
        direction = "increase"
        reason = "credible_increase"
    return DifferenceGate(before, after, delta, error, credible, direction, reason)


def _parse_timestamp(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _event_intervals(
    events: Sequence[Mapping[str, Any]],
    query_hash_by_id: Optional[Mapping[str, str]] = None,
) -> List[Dict[str, Any]]:
    reverse_hash = {
        str(sql_hash): str(query_id)
        for query_id, sql_hash in (query_hash_by_id or {}).items()
    }
    result: List[Dict[str, Any]] = []
    for event in events:
        if event.get("event") not in {None, "query_complete"}:
            continue
        if event.get("success") is False:
            continue
        query_id = event.get("query_id")
        if query_id is None:
            query_id = reverse_hash.get(str(event.get("sql_sha256") or ""))
        if query_id is None:
            continue
        latency_ms = float(event.get("latency_ms") or 0.0)
        if latency_ms < 0.0 or not math.isfinite(latency_ms):
            continue
        completed = _parse_timestamp(str(event["timestamp_utc"]))
        result.append(
            {
                "query_id": str(query_id),
                "start": completed - timedelta(milliseconds=latency_ms),
                "end": completed,
                "latency_ms": latency_ms,
            }
        )
    return result


def attribute_query_impact(
    events: Sequence[Mapping[str, Any]],
    resource_samples: Sequence[Mapping[str, Any]],
    *,
    query_hash_by_id: Optional[Mapping[str, str]] = None,
    io_read_cap_bps: Optional[float] = None,
    io_write_cap_bps: Optional[float] = None,
) -> Dict[str, Any]:
    intervals = _event_intervals(events, query_hash_by_id)
    latency_by_query: Dict[str, List[float]] = defaultdict(list)
    for event in intervals:
        latency_by_query[event["query_id"]].append(event["latency_ms"])
    total_latency = sum(sum(values) for values in latency_by_query.values())

    allocated = {
        "cpu_capacity_seconds": defaultdict(float),
        "io_capacity_seconds": defaultdict(float),
        "memory_pressure_seconds": defaultdict(float),
    }
    global_totals = {key: 0.0 for key in allocated}
    attributed_totals = {key: 0.0 for key in allocated}
    ordered_samples = sorted(
        resource_samples, key=lambda row: int(row.get("monotonic_ns") or 0)
    )
    for previous, current in zip(ordered_samples, ordered_samples[1:]):
        before_ns = int(previous.get("monotonic_ns") or 0)
        after_ns = int(current.get("monotonic_ns") or 0)
        elapsed = (after_ns - before_ns) / 1e9
        if elapsed <= 0.0:
            continue
        sample_start = _parse_timestamp(str(previous["timestamp_utc"]))
        sample_end = _parse_timestamp(str(current["timestamp_utc"]))
        quota_us = float(current.get("cpu_quota_us") or 0.0)
        period_us = float(current.get("cpu_period_us") or 0.0)
        quota_cores = quota_us / period_us if quota_us > 0.0 and period_us > 0.0 else 0.0
        cpu_delta = max(
            0.0,
            float(current.get("cpu_usage_ns") or 0.0)
            - float(previous.get("cpu_usage_ns") or 0.0),
        )
        cpu_utilization = (
            cpu_delta / (elapsed * 1e9 * quota_cores) if quota_cores > 0.0 else 0.0
        )
        read_delta = max(
            0.0,
            float(current.get("io_read_bytes") or 0.0)
            - float(previous.get("io_read_bytes") or 0.0),
        )
        write_delta = max(
            0.0,
            float(current.get("io_write_bytes") or 0.0)
            - float(previous.get("io_write_bytes") or 0.0),
        )
        read_util = (
            read_delta / (elapsed * float(io_read_cap_bps))
            if io_read_cap_bps and io_read_cap_bps > 0.0
            else 0.0
        )
        write_util = (
            write_delta / (elapsed * float(io_write_cap_bps))
            if io_write_cap_bps and io_write_cap_bps > 0.0
            else 0.0
        )
        io_utilization = read_util + write_util
        memory_limit = float(current.get("memory_limit_bytes") or 0.0)
        memory_utilization = (
            (
                float(previous.get("memory_current_bytes") or 0.0)
                + float(current.get("memory_current_bytes") or 0.0)
            )
            / (2.0 * memory_limit)
            if memory_limit > 0.0
            else 0.0
        )
        values = {
            "cpu_capacity_seconds": cpu_utilization * elapsed,
            "io_capacity_seconds": io_utilization * elapsed,
            "memory_pressure_seconds": memory_utilization * elapsed,
        }
        for key, value in values.items():
            global_totals[key] += value

        overlaps: List[Tuple[str, float]] = []
        for event in intervals:
            overlap = (
                min(sample_end, event["end"]) - max(sample_start, event["start"])
            ).total_seconds()
            if overlap > 0.0:
                overlaps.append((event["query_id"], overlap))
        overlap_total = sum(value for _, value in overlaps)
        if overlap_total <= 0.0:
            continue
        for key, value in values.items():
            attributed_totals[key] += value
            for query_id, overlap in overlaps:
                allocated[key][query_id] += value * overlap / overlap_total

    query_ids = sorted(
        set(latency_by_query)
        | set(allocated["cpu_capacity_seconds"])
        | set(allocated["io_capacity_seconds"])
        | set(allocated["memory_pressure_seconds"]),
        key=lambda value: (not value.isdigit(), int(value) if value.isdigit() else value),
    )
    query_rows: List[Dict[str, Any]] = []
    for query_id in query_ids:
        latencies = latency_by_query.get(query_id, [])
        row: Dict[str, Any] = {
            "query_id": query_id,
            "execution_count": len(latencies),
            "total_latency_ms": sum(latencies),
            "mean_latency_ms": mean(latencies) if latencies else None,
            "client_time_share": (
                sum(latencies) / total_latency if total_latency > 0.0 else None
            ),
            "client_time_share_quality": "exact_from_client_completion_events",
        }
        for key in allocated:
            denominator = attributed_totals[key]
            row[key] = allocated[key].get(query_id, 0.0)
            row[key.replace("_seconds", "_share")] = (
                allocated[key].get(query_id, 0.0) / denominator
                if denominator > 0.0
                else None
            )
        row["resource_attribution_quality"] = "concurrency_apportioned_estimate"
        query_rows.append(row)
    return {
        "schema_version": 1,
        "query_count": len(query_rows),
        "mapped_event_count": len(intervals),
        "queries": query_rows,
        "resource_attribution": {
            "quality": "concurrency_apportioned_estimate",
            "memory_semantics": "pressure_exposure_not_allocated_bytes",
            "cpu_semantics": "cgroup_cpu_capacity_seconds_apportioned_by_query_overlap",
            "io_semantics": "cgroup_io_capacity_seconds_apportioned_by_query_overlap",
            "coverage": {
                key: (
                    attributed_totals[key] / global_totals[key]
                    if global_totals[key] > 0.0
                    else None
                )
                for key in allocated
            },
        },
    }


def changed_query_impact(
    plan_diff: Mapping[str, Any], impact: Mapping[str, Any]
) -> Dict[str, Any]:
    changed = set(str(value) for value in plan_diff.get("changed_query_ids") or [])
    rows = [
        row for row in impact.get("queries") or [] if str(row.get("query_id")) in changed
    ]
    fields = (
        "client_time_share",
        "cpu_capacity_share",
        "io_capacity_share",
        "memory_pressure_share",
    )
    totals = {
        field: sum(float(row.get(field) or 0.0) for row in rows) for field in fields
    }
    return {
        "changed_query_ids": sorted(changed),
        "changed_query_count": len(changed),
        "changed_queries_with_impact_count": len(rows),
        "shares": totals,
        "resource_attribution_quality": (
            impact.get("resource_attribution") or {}
        ).get("quality", "unavailable"),
    }


def joint_improvement_margin(
    *,
    incumbent_qps: float,
    candidate_qps: float,
    incumbent_target: float,
    candidate_target: float,
    hard_gate_passed: bool,
    candidate_non_target: Mapping[str, float],
    non_target_limit: float = 80.0,
    incumbent_qps_error: Optional[float] = None,
    candidate_qps_error: Optional[float] = None,
    incumbent_target_error: Optional[float] = None,
    candidate_target_error: Optional[float] = None,
) -> Dict[str, Any]:
    qps_delta = float(candidate_qps) - float(incumbent_qps)
    target_delta = float(candidate_target) - float(incumbent_target)
    non_target_safe = all(
        math.isfinite(float(value)) and float(value) < non_target_limit
        for value in candidate_non_target.values()
    )
    strict_acceptance = bool(
        hard_gate_passed and qps_delta > 0.0 and target_delta < 0.0 and non_target_safe
    )
    conservative_qps_delta = None
    if incumbent_qps_error is not None and candidate_qps_error is not None:
        conservative_qps_delta = (
            float(candidate_qps)
            - abs(float(candidate_qps_error))
            - float(incumbent_qps)
            - abs(float(incumbent_qps_error))
        )
    conservative_target_improvement = None
    if incumbent_target_error is not None and candidate_target_error is not None:
        conservative_target_improvement = (
            float(incumbent_target)
            - abs(float(incumbent_target_error))
            - float(candidate_target)
            - abs(float(candidate_target_error))
        )
    return {
        "qps_delta": qps_delta,
        "qps_relative_delta_pct": (
            100.0 * qps_delta / float(incumbent_qps)
            if float(incumbent_qps) != 0.0
            else None
        ),
        "target_delta_after_minus_before": target_delta,
        "target_improvement": -target_delta,
        "hard_gate_passed": bool(hard_gate_passed),
        "non_target_below_limit": non_target_safe,
        "strict_acceptance": strict_acceptance,
        "conservative_qps_delta": conservative_qps_delta,
        "conservative_target_improvement": conservative_target_improvement,
        "joint_margin_vector": {
            "qps_relative_delta_pct": (
                100.0 * qps_delta / float(incumbent_qps)
                if float(incumbent_qps) != 0.0
                else None
            ),
            "target_improvement": -target_delta,
        },
    }


def calibration_decision(
    *,
    plan_diff: Mapping[str, Any],
    changed_impact: Mapping[str, Any],
    target_difference: DifferenceGate,
    before_distance: float,
    after_distance: float,
    minimum_changed_query_time_share: float = 0.05,
) -> Dict[str, Any]:
    reasons: List[str] = []
    if int(plan_diff.get("changed_query_count") or 0) <= 0:
        reasons.append("no_exact_query_plan_change")
    time_share = float(
        (changed_impact.get("shares") or {}).get("client_time_share") or 0.0
    )
    if time_share < minimum_changed_query_time_share:
        reasons.append("changed_queries_below_minimum_time_share")
    if not target_difference.credible:
        reasons.append("target_delta_not_credible")
    distance_delta = float(after_distance) - float(before_distance)
    if not math.isfinite(distance_delta) or distance_delta <= 0.0:
        reasons.append("invalid_boundary_distance")
    learnable = not reasons
    secant = None
    if learnable and target_difference.delta_after_minus_before is not None:
        secant = target_difference.delta_after_minus_before / distance_delta
    return {
        "learnable": learnable,
        "reasons": reasons or ["credible_cross_boundary_calibration"],
        "target_direction": target_difference.direction,
        "target_secant_per_normalized_unit": secant,
        "changed_query_time_share": time_share,
        "minimum_changed_query_time_share": minimum_changed_query_time_share,
        "resource_attribution_quality": changed_impact.get(
            "resource_attribution_quality", "unavailable"
        ),
    }
