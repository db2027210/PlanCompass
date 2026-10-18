"""Auditing helpers for answer-free Phase2 teacher recovery experiments."""

from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Sequence


COST_PARAM_NAMES = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)


def _finite_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _range_bounds(value: Any) -> Optional[tuple[float, float]]:
    if isinstance(value, Mapping):
        low = _finite_float(value.get("min_value"))
        high = _finite_float(value.get("max_value"))
    else:
        low = _finite_float(getattr(value, "min_value", None))
        high = _finite_float(getattr(value, "max_value", None))
    if low is None or high is None:
        return None
    return (min(low, high), max(low, high))


def normalized_param_distance(
    params: Mapping[str, Any],
    teacher_params: Mapping[str, Any],
    initial_ranges: Mapping[str, Any],
) -> Dict[str, Any]:
    """Return per-parameter and aggregate distance normalized by initial widths."""
    per_param: Dict[str, float] = {}
    missing = []
    for name in COST_PARAM_NAMES:
        actual = _finite_float(params.get(name))
        teacher = _finite_float(teacher_params.get(name))
        bounds = _range_bounds(initial_ranges.get(name))
        if actual is None or teacher is None or bounds is None:
            missing.append(name)
            continue
        width = max(bounds[1] - bounds[0], 1e-12)
        per_param[name] = abs(actual - teacher) / width
    values = list(per_param.values())
    rms = math.sqrt(sum(value * value for value in values) / len(values)) if values else None
    return {
        "per_param": per_param,
        "max": max(values) if values else None,
        "rms": rms,
        "missing_params": missing,
        "complete": not missing and len(values) == len(COST_PARAM_NAMES),
    }


def audit_candidate_rankings(
    candidate_rankings: Sequence[Mapping[str, Any]],
    teacher_params: Mapping[str, Any],
    initial_ranges: Mapping[str, Any],
    *,
    top_k: int = 15,
    exact_tolerance: float = 1e-6,
) -> Dict[str, Any]:
    """Find the teacher's natural nearest candidate without changing ranking."""
    closest: Optional[Dict[str, Any]] = None
    for ordinal, record in enumerate(candidate_rankings, start=1):
        params = record.get("params") if isinstance(record.get("params"), Mapping) else {}
        distance = normalized_param_distance(params, teacher_params, initial_ranges)
        max_distance = distance.get("max")
        rms_distance = distance.get("rms")
        if max_distance is None or rms_distance is None:
            continue
        rank = int(record.get("rank") or ordinal)
        item = {
            "rank": rank,
            "ordinal": ordinal,
            "params": dict(params),
            "distance": distance,
            "source": record.get("source"),
            "selection_reason": record.get("selection_reason"),
            "score": record.get("score", record.get("utility_score")),
            "plan_change_count": record.get("plan_change_count"),
            "phase2_db_qps_proxy": record.get("phase2_db_qps_proxy"),
            "estimated_resource": record.get(
                "estimated_resource",
                record.get("resource"),
            ),
        }
        key = (float(max_distance), float(rms_distance), rank)
        if closest is None or key < closest["_key"]:
            closest = {**item, "_key": key}
    if closest is None:
        return {
            "candidate_count": len(candidate_rankings),
            "teacher_candidate_found": False,
            "exact_match": False,
            "within_top_k": False,
            "top_k": int(top_k),
            "reason": "no_complete_candidate_params",
        }
    closest.pop("_key", None)
    max_distance = float((closest.get("distance") or {}).get("max") or 0.0)
    exact_match = max_distance <= max(0.0, float(exact_tolerance))
    rank = int(closest.get("rank") or 0)
    return {
        "candidate_count": len(candidate_rankings),
        "teacher_candidate_found": True,
        "exact_match": exact_match,
        "within_top_k": rank > 0 and rank <= max(1, int(top_k)),
        "top_k": max(1, int(top_k)),
        "closest": closest,
    }


def audit_teacher_range_containment(
    current_ranges: Mapping[str, Any],
    teacher_params: Mapping[str, Any],
    *,
    fixed_params: Optional[Mapping[str, Any]] = None,
    tolerance: float = 1e-9,
) -> Dict[str, Any]:
    """Check whether each teacher coordinate remains searchable or fixed to it."""
    fixed_params = fixed_params or {}
    details: Dict[str, Dict[str, Any]] = {}
    outside = []
    for name in COST_PARAM_NAMES:
        teacher = _finite_float(teacher_params.get(name))
        bounds = _range_bounds(current_ranges.get(name))
        if teacher is None:
            details[name] = {"contained": False, "reason": "teacher_missing"}
            outside.append(name)
            continue
        if bounds is not None:
            slack = max(float(tolerance), (bounds[1] - bounds[0]) * float(tolerance))
            contained = bounds[0] - slack <= teacher <= bounds[1] + slack
            details[name] = {
                "contained": contained,
                "mode": "active_range",
                "min": bounds[0],
                "max": bounds[1],
                "teacher": teacher,
            }
        else:
            fixed = _finite_float(fixed_params.get(name))
            slack = max(float(tolerance), abs(teacher) * float(tolerance))
            contained = fixed is not None and abs(fixed - teacher) <= slack
            details[name] = {
                "contained": contained,
                "mode": "fixed_param",
                "fixed": fixed,
                "teacher": teacher,
            }
        if not contained:
            outside.append(name)
    return {
        "contained": not outside,
        "outside_params": outside,
        "details": details,
    }


def params_match_teacher(
    params: Mapping[str, Any],
    teacher_params: Mapping[str, Any],
    initial_ranges: Mapping[str, Any],
    *,
    tolerance: float = 1e-6,
) -> bool:
    distance = normalized_param_distance(params, teacher_params, initial_ranges)
    max_distance = distance.get("max")
    return bool(
        distance.get("complete")
        and max_distance is not None
        and float(max_distance) <= max(0.0, float(tolerance))
    )


def validate_blind_recovery_inputs(
    teacher_params: Mapping[str, Any],
    initial_ranges: Mapping[str, Any],
    *,
    teacher_label_excluded_from_training: bool,
    forced_seed_records: Sequence[Mapping[str, Any]] = (),
    require_no_forced_seed_records: bool = True,
    tolerance: float = 1e-6,
) -> Dict[str, Any]:
    """Reject answer leakage before a blind recovery run starts."""
    missing_teacher = [name for name in COST_PARAM_NAMES if _finite_float(teacher_params.get(name)) is None]
    missing_ranges = [name for name in COST_PARAM_NAMES if _range_bounds(initial_ranges.get(name)) is None]
    matching_seed_indices = []
    for index, record in enumerate(forced_seed_records):
        params = record.get("params") if isinstance(record.get("params"), Mapping) else record
        if isinstance(params, Mapping) and params_match_teacher(
            params,
            teacher_params,
            initial_ranges,
            tolerance=tolerance,
        ):
            matching_seed_indices.append(index)
    passed = (
        not missing_teacher
        and not missing_ranges
        and bool(teacher_label_excluded_from_training)
        and (
            not require_no_forced_seed_records
            or not forced_seed_records
        )
        and not matching_seed_indices
    )
    return {
        "passed": passed,
        "missing_teacher_params": missing_teacher,
        "missing_initial_ranges": missing_ranges,
        "teacher_label_excluded_from_training": bool(
            teacher_label_excluded_from_training
        ),
        "require_no_forced_seed_records": bool(require_no_forced_seed_records),
        "forced_seed_record_count": len(forced_seed_records),
        "matching_forced_seed_indices": matching_seed_indices,
        "reason": (
            "ok"
            if passed
            else "teacher_answer_leakage_or_incomplete_configuration"
        ),
    }
