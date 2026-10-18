"""Resource-only replay gate for experience-table directed plan boundaries."""

from __future__ import annotations

import copy
import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


LINEAR_PARAMETERS = frozenset({"parallel_setup_cost"})


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _metadata(candidate: Mapping[str, Any]) -> Mapping[str, Any]:
    value = candidate.get("final_estimation_metadata")
    return value if isinstance(value, Mapping) else {}


def _plan_cluster(candidate: Mapping[str, Any]) -> Optional[str]:
    metadata = _metadata(candidate)
    for container in (candidate, metadata):
        for key in (
            "workload_plan_fingerprint",
            "plan_similarity_cluster_id",
            "plan_fingerprint",
        ):
            value = str(container.get(key) or "").strip()
            if value:
                return value
    return None


def _baseline_plan_cluster(candidate: Mapping[str, Any]) -> Optional[str]:
    value = str(_metadata(candidate).get("baseline_plan_fingerprint") or "").strip()
    return value or None


def _direction_distance(
    candidate: Mapping[str, Any],
    incumbent_params: Mapping[str, Any],
) -> float:
    params = candidate.get("optimized_params")
    if not isinstance(params, Mapping):
        return math.inf
    squared = 0.0
    changed = 0
    for name, raw_before in incumbent_params.items():
        before = _finite(raw_before)
        after = _finite(params.get(name))
        if before is None or after is None or abs(after - before) <= 1e-15:
            continue
        if name in LINEAR_PARAMETERS:
            delta = (after - before) / max(abs(before), 1.0)
        elif before > 0.0 and after > 0.0:
            delta = math.log(after / before)
        else:
            return math.inf
        squared += delta * delta
        changed += 1
    return math.sqrt(squared) if changed else math.inf


def _target_prediction(candidate: Mapping[str, Any], target: str) -> Optional[float]:
    resource = candidate.get("final_resource")
    if not isinstance(resource, Mapping):
        resource = candidate.get("predicted_resource")
    if not isinstance(resource, Mapping):
        return None
    return _finite(resource.get(target))


def _target_error_bound(candidate: Mapping[str, Any], target: str) -> Optional[float]:
    bounds = _metadata(candidate).get("resource_error_bounds")
    if not isinstance(bounds, Mapping):
        return None
    record = bounds.get(target)
    if isinstance(record, Mapping):
        return _finite(record.get("absolute"))
    return _finite(record)


def select_resource_qualified_plan_boundaries(
    candidates: Sequence[Mapping[str, Any]],
    *,
    incumbent_params: Mapping[str, Any],
    incumbent_resource: Mapping[str, Any],
    target_resource: str,
    max_distinct_clusters: int = 5,
    min_target_improvement_pct: float = 5.0,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Scan all available plans and return at most five replay candidates.

    Input direction and candidate generation remain owned by experience table T.
    This function treats the nearest changed plan as the minimum effective step,
    applies a conservative target-resource gate, then returns the five highest
    resource-utility scores across distinct plans. Distance is only a tie-break
    and audit field. QPS is deliberately neither read nor scored here.
    """

    target = str(target_resource).strip().lower()
    if target not in {"cpu", "memory", "io"}:
        raise ValueError(f"unsupported target resource: {target_resource!r}")
    if isinstance(max_distinct_clusters, bool):
        raise ValueError("max_distinct_clusters must be an integer")
    scan_limit = int(max_distinct_clusters)
    if scan_limit <= 0 or scan_limit > 5:
        raise ValueError("max_distinct_clusters must be in [1, 5]")
    threshold_pct = float(min_target_improvement_pct)
    if not math.isfinite(threshold_pct) or threshold_pct < 0.0:
        raise ValueError("min_target_improvement_pct must be finite and non-negative")
    incumbent_value = _finite(incumbent_resource.get(target))
    if incumbent_value is None or incumbent_value <= 0.0:
        raise ValueError(f"missing positive incumbent resource: {target}")

    ordered: List[Tuple[float, int, Dict[str, Any]]] = []
    failed_closed: List[Dict[str, Any]] = []
    for position, raw_candidate in enumerate(candidates, start=1):
        candidate = copy.deepcopy(dict(raw_candidate))
        cluster = _plan_cluster(candidate)
        distance = _direction_distance(candidate, incumbent_params)
        resource_score = _finite(candidate.get("final_score"))
        if cluster is None or not math.isfinite(distance) or resource_score is None:
            failed_closed.append(
                {
                    "rank": candidate.get("rank"),
                    "reason": (
                        "missing_plan_cluster"
                        if cluster is None
                        else (
                            "missing_direction_distance"
                            if not math.isfinite(distance)
                            else "missing_resource_utility_score"
                        )
                    ),
                }
            )
            continue
        candidate["resource_boundary_cluster_id"] = cluster
        candidate["resource_boundary_direction_distance"] = distance
        candidate["resource_boundary_resource_score"] = resource_score
        candidate["resource_boundary_input_position"] = position
        ordered.append((distance, position, candidate))
    ordered.sort(key=lambda item: (item[0], item[1]))

    representatives: Dict[str, Dict[str, Any]] = {}
    baseline_cluster: Optional[str] = None
    for _distance, _position, candidate in ordered:
        cluster = str(candidate["resource_boundary_cluster_id"])
        baseline_cluster = baseline_cluster or _baseline_plan_cluster(candidate)
        if cluster == baseline_cluster:
            continue
        previous = representatives.get(cluster)
        if previous is None or (
            -float(candidate["resource_boundary_resource_score"]),
            float(candidate["resource_boundary_direction_distance"]),
            int(candidate["resource_boundary_input_position"]),
        ) < (
            -float(previous["resource_boundary_resource_score"]),
            float(previous["resource_boundary_direction_distance"]),
            int(previous["resource_boundary_input_position"]),
        ):
            representatives[cluster] = candidate
    distinct = sorted(
        representatives.values(),
        key=lambda item: (
            float(item["resource_boundary_direction_distance"]),
            int(item["resource_boundary_input_position"]),
        ),
    )

    qualified: List[Dict[str, Any]] = []
    checks: List[Dict[str, Any]] = []
    for boundary_index, candidate in enumerate(distinct, start=1):
        predicted = _target_prediction(candidate, target)
        error_bound = _target_error_bound(candidate, target)
        raw_improvement = (
            incumbent_value - predicted
            if predicted is not None
            else None
        )
        conservative_improvement = (
            raw_improvement - error_bound
            if raw_improvement is not None and error_bound is not None
            else None
        )
        conservative_pct = (
            conservative_improvement / incumbent_value * 100.0
            if conservative_improvement is not None
            else None
        )
        passed = bool(
            predicted is not None
            and predicted >= 0.0
            and error_bound is not None
            and error_bound > 0.0
            and raw_improvement is not None
            and raw_improvement > error_bound
            and conservative_pct is not None
            and conservative_pct >= threshold_pct
        )
        reason = "resource_gate_passed"
        if predicted is None:
            reason = "missing_target_prediction"
        elif error_bound is None or error_bound <= 0.0:
            reason = "missing_historical_error_bound"
        elif raw_improvement is None or raw_improvement <= error_bound:
            reason = "predicted_improvement_not_above_error_bound"
        elif conservative_pct is None or conservative_pct < threshold_pct:
            reason = "conservative_improvement_below_target_threshold"
        gate = {
            "boundary_index": boundary_index,
            "first_boundary_is_minimum_effective_step": boundary_index == 1,
            "cluster_id": candidate.get("resource_boundary_cluster_id"),
            "rank": candidate.get("rank"),
            "direction_distance": candidate.get(
                "resource_boundary_direction_distance"
            ),
            "resource_utility_score": candidate.get(
                "resource_boundary_resource_score"
            ),
            "target_resource": target,
            "incumbent_value": incumbent_value,
            "predicted_value": predicted,
            "historical_error_bound": error_bound,
            "raw_improvement": raw_improvement,
            "conservative_improvement": conservative_improvement,
            "conservative_improvement_pct": conservative_pct,
            "required_improvement_pct": threshold_pct,
            "passed": passed,
            "reason": reason,
            "qps_consulted": False,
        }
        checks.append(gate)
        candidate["resource_boundary_gate"] = copy.deepcopy(gate)
        if passed:
            qualified.append(candidate)
    qualified.sort(
        key=lambda item: (
            -float(item["resource_boundary_resource_score"]),
            float(item["resource_boundary_direction_distance"]),
            int(item["resource_boundary_input_position"]),
        )
    )
    selected = qualified[:scan_limit]
    for replay_rank, candidate in enumerate(selected, start=1):
        candidate["resource_boundary_replay_rank"] = replay_rank

    return selected, {
        "enabled": True,
        "direction_source": "experience_table_T_upstream_only",
        "candidate_evaluator": "ratio_scale_resource_only",
        "ranking_score": "existing_resource_utility_final_score_desc",
        "qps_role": "outer_real_measurement_only",
        "qps_consulted": False,
        "max_real_replay_candidates": scan_limit,
        "available_distinct_cluster_count": len(distinct),
        "inspected_distinct_cluster_count": len(checks),
        "selected_count": len(selected),
        "resource_qualified_count": len(qualified),
        "first_boundary_policy": "minimum_effective_step_not_automatic_candidate",
        "min_target_improvement_pct": threshold_pct,
        "checks": checks,
        "failed_closed_candidates": failed_closed,
        "no_feasible_candidate": not selected,
    }
