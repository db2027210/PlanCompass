from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


COST_PARAMS: Tuple[str, ...] = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)

RESOURCE_KEYS: Tuple[str, ...] = ("cpu", "memory", "io")
RESOURCE_SCORE_VERSION = "phase2_vm_band_target_resource_only_v1"
RESOURCE_TARGET_RANGES: Dict[str, Dict[str, float]] = {
    "cpu": {"min_pct": 65.0, "max_pct": 80.0, "hard_pct": 90.0, "weight": 1.0},
    "memory": {"min_pct": 70.0, "max_pct": 80.0, "hard_pct": 90.0, "weight": 1.0},
    "io": {"min_pct": 50.0, "max_pct": 75.0, "hard_pct": 90.0, "weight": 3.0},
}


class IntervalInvariantError(ValueError):
    """Raised when frozen nested-search evidence violates a lineage invariant."""


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def exact_cost_params(values: Mapping[str, Any]) -> Dict[str, float]:
    if set(values) != set(COST_PARAMS):
        raise IntervalInvariantError(f"expected exactly seven cost keys, got={sorted(values)}")
    result = {name: float(values[name]) for name in COST_PARAMS}
    if any(not math.isfinite(value) for value in result.values()):
        raise IntervalInvariantError("cost parameter values must be finite")
    if any(value <= 0.0 for value in result.values()):
        raise IntervalInvariantError("cost parameter values must be positive")
    return result


def exact_resources(values: Mapping[str, Any]) -> Dict[str, float]:
    if set(values) != set(RESOURCE_KEYS):
        raise IntervalInvariantError("resources must contain exactly cpu, memory, and io")
    result = {name: float(values[name]) for name in RESOURCE_KEYS}
    if any(not math.isfinite(value) or value < 0.0 for value in result.values()):
        raise IntervalInvariantError("resource values must be finite and non-negative")
    return result


def normalize_bounds(bounds: Mapping[str, Any]) -> Dict[str, Tuple[float, float]]:
    if set(bounds) != set(COST_PARAMS):
        raise IntervalInvariantError(f"expected exactly seven bound keys, got={sorted(bounds)}")
    normalized: Dict[str, Tuple[float, float]] = {}
    for name in COST_PARAMS:
        item = bounds[name]
        if isinstance(item, Mapping):
            low = float(item.get("lower", item.get("min_value")))
            high = float(item.get("upper", item.get("max_value")))
        else:
            low, high = (float(value) for value in item)
        if not math.isfinite(low) or not math.isfinite(high):
            raise IntervalInvariantError(f"non-finite bounds for {name}")
        if low < 0.0 or high <= low:
            raise IntervalInvariantError(f"invalid bounds for {name}: {low}..{high}")
        normalized[name] = (low, high)
    return normalized


def bounds_payload(bounds: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    normalized = normalize_bounds(bounds)
    return {
        name: {
            "lower": low,
            "upper": high,
            "space": "log" if low > 0.0 else "linear_zero_inclusive",
            "width": interval_width(low, high),
        }
        for name, (low, high) in normalized.items()
    }


def interval_midpoint(low: float, high: float) -> float:
    if high <= low or low < 0.0:
        raise IntervalInvariantError(f"invalid interval: {low}..{high}")
    if low > 0.0:
        return math.sqrt(low * high)
    return (low + high) / 2.0


def interval_pair_points(low: float, high: float) -> Tuple[float, float]:
    if high <= low or low < 0.0:
        raise IntervalInvariantError(f"invalid interval: {low}..{high}")
    if low > 0.0:
        log_low = math.log(low)
        log_width = math.log(high) - log_low
        return math.exp(log_low + 0.25 * log_width), math.exp(log_low + 0.75 * log_width)
    width = high - low
    return low + 0.25 * width, low + 0.75 * width


def interval_width(low: float, high: float) -> float:
    if high <= low or low < 0.0:
        raise IntervalInvariantError(f"invalid interval: {low}..{high}")
    if low > 0.0:
        return math.log(high) - math.log(low)
    return high - low


def split_interval_for_value(
    low: float,
    high: float,
    value: float,
    *,
    tie_break: str = "low",
) -> Tuple[float, float]:
    midpoint = interval_midpoint(low, high)
    tied = math.isclose(value, midpoint, rel_tol=1e-12, abs_tol=1e-12)
    if value < midpoint or (tied and tie_break != "high"):
        return low, midpoint
    return midpoint, high


def expand_interval_around_point(
    low: float,
    high: float,
    bound_low: float,
    bound_high: float,
    point: float,
    factor: float,
) -> Tuple[float, float]:
    if factor <= 1.0:
        return low, high
    if bound_high <= bound_low or high <= low:
        raise IntervalInvariantError("invalid interval expansion bounds")
    use_log = bound_low > 0.0 and low > 0.0
    if use_log:
        b_low, b_high = math.log(bound_low), math.log(bound_high)
        c_low, c_high = math.log(low), math.log(high)
        center = math.log(min(max(point, bound_low), bound_high))
    else:
        b_low, b_high = bound_low, bound_high
        c_low, c_high = low, high
        center = min(max(point, bound_low), bound_high)
    width = min(b_high - b_low, (c_high - c_low) * factor)
    new_low, new_high = center - width / 2.0, center + width / 2.0
    if new_low < b_low:
        new_high += b_low - new_low
        new_low = b_low
    if new_high > b_high:
        new_low -= new_high - b_high
        new_high = b_high
    new_low, new_high = max(b_low, new_low), min(b_high, new_high)
    if use_log:
        return math.exp(new_low), math.exp(new_high)
    return new_low, new_high


def interval_metrics(
    child: Mapping[str, Any],
    parent: Optional[Mapping[str, Any]] = None,
    *,
    tolerance: float = 1e-12,
) -> Dict[str, Any]:
    child_bounds = normalize_bounds(child)
    child_widths = {name: interval_width(*child_bounds[name]) for name in COST_PARAMS}
    child_volume = math.prod(child_widths.values())
    if parent is None:
        return {
            "child_subset_parent": None,
            "strictly_contracted": None,
            "axis_widths": child_widths,
            "log_hybrid_volume": child_volume,
            "shrink_ratio": None,
        }
    parent_bounds = normalize_bounds(parent)
    axis_subset: Dict[str, bool] = {}
    axis_strict: Dict[str, bool] = {}
    parent_widths: Dict[str, float] = {}
    for name in COST_PARAMS:
        child_low, child_high = child_bounds[name]
        parent_low, parent_high = parent_bounds[name]
        scale = max(1.0, abs(parent_low), abs(parent_high))
        tol = tolerance * scale
        axis_subset[name] = child_low >= parent_low - tol and child_high <= parent_high + tol
        axis_strict[name] = child_low > parent_low + tol or child_high < parent_high - tol
        parent_widths[name] = interval_width(parent_low, parent_high)
    parent_volume = math.prod(parent_widths.values())
    shrink_ratio = child_volume / parent_volume if parent_volume > 0.0 else math.inf
    subset = all(axis_subset.values())
    strict = subset and any(axis_strict.values()) and shrink_ratio < 1.0 - tolerance
    return {
        "child_subset_parent": subset,
        "strictly_contracted": strict,
        "axis_subset_parent": axis_subset,
        "axis_strictly_contracted": axis_strict,
        "axis_widths": child_widths,
        "parent_axis_widths": parent_widths,
        "log_hybrid_volume": child_volume,
        "parent_log_hybrid_volume": parent_volume,
        "shrink_ratio": shrink_ratio,
    }


def _resource_band_component(resource_name: str, value_pct: float) -> Dict[str, Any]:
    target = RESOURCE_TARGET_RANGES[resource_name]
    target_min = target["min_pct"]
    target_max = target["max_pct"]
    hard_max = target["hard_pct"]
    weight = target["weight"]
    if value_pct < target_min:
        gap = (target_min - value_pct) / target_min
        score = weight * (1.0 - gap * gap)
        status = "under_sweet"
    elif value_pct <= target_max:
        gap = 0.0
        score = weight
        status = "in_sweet"
    else:
        span = max(hard_max - target_max, 5.0)
        gap = (value_pct - target_max) / span
        hard_excess = max(0.0, value_pct - hard_max) / span
        score = weight * (1.0 - gap * gap - 4.0 * hard_excess)
        status = "over_sweet" if value_pct <= hard_max else "hard_overload"
    return {
        "value_pct": value_pct,
        "target": dict(target),
        "status": status,
        "normalized_gap": gap,
        "weighted_score": score,
    }


def io_resource_score(resources: Mapping[str, Any]) -> Dict[str, Any]:
    """Return the Phase2 VM IO score using CPU, Memory, and IO only."""
    point = exact_resources(resources)
    components = {
        name: _resource_band_component(name, point[name])
        for name in RESOURCE_KEYS
    }
    score = sum(float(item["weighted_score"]) for item in components.values())
    return {
        "version": RESOURCE_SCORE_VERSION,
        "resource_score": score,
        "inputs": point,
        "components": components,
        "formula": (
            "sum(weighted band-target score for cpu[65,80], memory[70,80], "
            "io[50,75], with hard overload at 90)"
        ),
    }


def manifest_sha256(manifest: Mapping[str, Any]) -> str:
    payload = dict(manifest)
    payload.pop("interval_manifest_sha256", None)
    return canonical_sha256(payload)


def _forbidden_ranking_fields(row: Mapping[str, Any]) -> List[str]:
    forbidden = {
        "rank_score",
        "candidate_gate",
        "uncertainty",
        "predicted_db_qps",
        "qps_proxy",
        "throughput",
        "plan_hashes",
        "combined_plan_hash",
        "plan_features",
        "plan_novelty",
        "diversity",
        "teacher_distance",
    }
    return sorted(forbidden.intersection(row))


def freeze_round_manifest(
    *,
    round_index: int,
    requested_bounds: Mapping[str, Any],
    effective_bounds: Mapping[str, Any],
    parent_manifest: Mapping[str, Any],
    parent_feedback: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    ranked_predictions: Sequence[Mapping[str, Any]],
    topk_candidate_ids: Sequence[str],
    generation_provenance: Mapping[str, Any],
) -> Dict[str, Any]:
    if round_index <= 0:
        raise IntervalInvariantError("round_index must be positive")
    parent_round = int(parent_manifest.get("round", -1))
    parent_sha = str(parent_manifest.get("interval_manifest_sha256") or manifest_sha256(parent_manifest))
    if round_index != parent_round + 1:
        raise IntervalInvariantError("wrong_parent_feedback: non-consecutive parent round")
    if int(parent_feedback.get("parent_round", -1)) != parent_round:
        raise IntervalInvariantError("wrong_parent_feedback: parent_round mismatch")
    if str(parent_feedback.get("parent_interval_sha") or "") != parent_sha:
        raise IntervalInvariantError("wrong_parent_feedback: parent_interval_sha mismatch")

    requested = normalize_bounds(requested_bounds)
    effective = normalize_bounds(effective_bounds)
    parent_requested = normalize_bounds(parent_manifest["requested_bounds"])
    parent_effective = normalize_bounds(parent_manifest.get("effective_bounds") or parent_manifest["requested_bounds"])
    requested_metrics = interval_metrics(requested, parent_requested)
    effective_metrics = interval_metrics(effective, parent_effective)
    if not requested_metrics["child_subset_parent"] or not effective_metrics["child_subset_parent"]:
        raise IntervalInvariantError("not_strictly_contracted: child is outside parent")
    if not requested_metrics["strictly_contracted"] or not effective_metrics["strictly_contracted"]:
        raise IntervalInvariantError("not_strictly_contracted")

    normalized_candidates: List[Dict[str, Any]] = []
    candidate_ids: List[str] = []
    for source in candidates:
        row = dict(source)
        candidate_id = str(row.get("candidate_id") or "")
        if not candidate_id or candidate_id in candidate_ids:
            raise IntervalInvariantError("interval_candidate_mismatch: duplicate or empty candidate id")
        params = exact_cost_params(row.get("params") or {})
        effective_params = exact_cost_params(row.get("effective_params") or params)
        requested_outside = [
            name
            for name, value in params.items()
            if value < requested[name][0] - 1e-12 * max(1.0, abs(requested[name][0]))
            or value > requested[name][1] + 1e-12 * max(1.0, abs(requested[name][1]))
        ]
        effective_outside = [
            name
            for name, value in effective_params.items()
            if value < effective[name][0] - 1e-12 * max(1.0, abs(effective[name][0]))
            or value > effective[name][1] + 1e-12 * max(1.0, abs(effective[name][1]))
        ]
        if requested_outside or effective_outside:
            raise IntervalInvariantError(
                f"interval_candidate_mismatch: {candidate_id} outside "
                f"requested={requested_outside}, effective={effective_outside}"
            )
        normalized_candidates.append(
            {**row, "candidate_id": candidate_id, "params": params, "effective_params": effective_params}
        )
        candidate_ids.append(candidate_id)

    prediction_ids = [str(row.get("candidate_id") or "") for row in ranked_predictions]
    if len(prediction_ids) != len(set(prediction_ids)) or set(prediction_ids) != set(candidate_ids):
        raise IntervalInvariantError("interval_candidate_mismatch: prediction ids do not match candidates")
    scores: Dict[str, float] = {}
    for row in ranked_predictions:
        candidate_id = str(row.get("candidate_id") or "")
        forbidden = _forbidden_ranking_fields(row)
        if forbidden:
            raise IntervalInvariantError(
                f"resource_ranking_invalid: forbidden fields for {candidate_id}: {forbidden}"
            )
        resources = exact_resources(row.get("predicted_resources") or {})
        expected = io_resource_score(resources)
        actual_score = float(row.get("resource_score", math.nan))
        if not math.isclose(actual_score, float(expected["resource_score"]), rel_tol=1e-12, abs_tol=1e-12):
            raise IntervalInvariantError(f"resource_ranking_invalid: resource_score mismatch for {candidate_id}")
        if dict(row.get("resource_score_evidence") or {}) != expected:
            raise IntervalInvariantError(f"resource_ranking_invalid: score evidence mismatch for {candidate_id}")
        scores[candidate_id] = actual_score
    expected_order = sorted(prediction_ids, key=lambda candidate_id: (-scores[candidate_id], candidate_id))
    if prediction_ids != expected_order:
        raise IntervalInvariantError(
            "resource_ranking_invalid: order must use resource_score then candidate_id only"
        )

    topk_ids = [str(value) for value in topk_candidate_ids]
    if not 1 <= len(topk_ids) <= 4:
        raise IntervalInvariantError("interval_candidate_mismatch: top-k must contain one to four candidates")
    if topk_ids != prediction_ids[: len(topk_ids)]:
        raise IntervalInvariantError("resource_ranking_invalid: top-k must be an unfilled resource-score prefix")
    if bool(generation_provenance.get("holdout_loaded_before_freeze")):
        raise IntervalInvariantError("teacher_leakage: holdout was loaded before freeze")

    manifest: Dict[str, Any] = {
        "schema_version": 2,
        "event": "bonect_nested_interval_round_frozen_before_holdout",
        "evidence_class": "resource_only_algorithm_evidence",
        "round": round_index,
        "parent_round": parent_round,
        "parent_interval_sha": parent_sha,
        "parent_feedback": dict(parent_feedback),
        "cost_keys": list(COST_PARAMS),
        "requested_bounds": {name: list(requested[name]) for name in COST_PARAMS},
        "effective_bounds": {name: list(effective[name]) for name in COST_PARAMS},
        "requested_interval_metrics": requested_metrics,
        "effective_interval_metrics": effective_metrics,
        "candidate_count": len(normalized_candidates),
        "candidate_sha256": canonical_sha256(normalized_candidates),
        "candidate_order_sha256": canonical_sha256(candidate_ids),
        "candidates": normalized_candidates,
        "ranked_predictions": [dict(row) for row in ranked_predictions],
        "ranked_prediction_sha256": canonical_sha256(ranked_predictions),
        "ranking_policy": {
            "resource_score_version": RESOURCE_SCORE_VERSION,
            "resource_target_ranges": RESOURCE_TARGET_RANGES,
            "sort_key": ["resource_score_desc", "candidate_id_asc"],
            "ranking_inputs": ["predicted_resources.cpu", "predicted_resources.memory", "predicted_resources.io"],
            "other_metric_inputs": False,
            "teacher_or_holdout_inputs": False,
        },
        "topk_candidate_ids": topk_ids,
        "topk_sha256": canonical_sha256(topk_ids),
        "topk_count": len(topk_ids),
        "topk_is_unfilled_prefix": True,
        "generation_provenance": dict(generation_provenance),
        "holdout_loaded_before_freeze": False,
        "outer_run_allowed_before_holdout_audit": False,
    }
    manifest["interval_manifest_sha256"] = manifest_sha256(manifest)
    return manifest


def make_root_manifest(
    bounds: Mapping[str, Any],
    *,
    source: str,
    effective_bounds: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    normalized = normalize_bounds(bounds)
    effective = normalize_bounds(effective_bounds or bounds)
    manifest: Dict[str, Any] = {
        "schema_version": 2,
        "event": "bonect_nested_interval_root",
        "round": 0,
        "parent_round": None,
        "parent_interval_sha": None,
        "cost_keys": list(COST_PARAMS),
        "requested_bounds": {name: list(normalized[name]) for name in COST_PARAMS},
        "effective_bounds": {name: list(effective[name]) for name in COST_PARAMS},
        "requested_interval_metrics": interval_metrics(normalized),
        "effective_interval_metrics": interval_metrics(effective),
        "source": source,
        "holdout_loaded_before_freeze": False,
    }
    manifest["interval_manifest_sha256"] = manifest_sha256(manifest)
    return manifest


def _teacher_containment(
    teacher_params: Mapping[str, Any],
    bounds: Mapping[str, Any],
) -> Dict[str, bool]:
    teacher = exact_cost_params(teacher_params)
    normalized = normalize_bounds(bounds)
    return {
        name: normalized[name][0] <= teacher[name] <= normalized[name][1]
        for name in COST_PARAMS
    }


def audit_frozen_round_holdout(
    *,
    manifest: Mapping[str, Any],
    parent_manifest: Mapping[str, Any],
    teacher_params: Mapping[str, Any],
    candidate_sha_after_holdout: str,
    candidate_order_sha_after_holdout: str,
    teacher_resources: Optional[Mapping[str, Any]] = None,
    previous_teacher_resource_rank: Optional[int] = None,
    **_ignored_diagnostic_inputs: Any,
) -> Dict[str, Any]:
    """Audit containment and resource-score rank after the algorithm evidence is frozen."""
    manifest_hash_ok = str(manifest.get("interval_manifest_sha256")) == manifest_sha256(manifest)
    parent_hash = str(parent_manifest.get("interval_manifest_sha256") or manifest_sha256(parent_manifest))
    parent_feedback = manifest.get("parent_feedback") or {}
    parent_feedback_ok = (
        int(manifest.get("parent_round", -1)) == int(parent_manifest.get("round", -2))
        and str(manifest.get("parent_interval_sha") or "") == parent_hash
        and int(parent_feedback.get("parent_round", -1)) == int(parent_manifest.get("round", -2))
        and str(parent_feedback.get("parent_interval_sha") or "") == parent_hash
    )
    requested_metrics = interval_metrics(
        manifest.get("requested_bounds") or {},
        parent_manifest.get("requested_bounds") or {},
    )
    effective_metrics = interval_metrics(
        manifest.get("effective_bounds") or {},
        parent_manifest.get("effective_bounds") or parent_manifest.get("requested_bounds") or {},
    )
    strict_contraction = bool(
        requested_metrics.get("strictly_contracted")
        and effective_metrics.get("strictly_contracted")
    )
    requested_contained = _teacher_containment(teacher_params, manifest.get("requested_bounds") or {})
    effective_contained = _teacher_containment(teacher_params, manifest.get("effective_bounds") or {})
    teacher_contained = all(requested_contained.values()) and all(effective_contained.values())
    candidate_sha_unchanged = str(manifest.get("candidate_sha256")) == str(candidate_sha_after_holdout)
    order_sha_unchanged = str(manifest.get("candidate_order_sha256")) == str(candidate_order_sha_after_holdout)
    provenance = manifest.get("generation_provenance") or {}
    no_teacher_leakage = (
        not bool(manifest.get("holdout_loaded_before_freeze"))
        and not bool(provenance.get("holdout_loaded_before_freeze"))
        and candidate_sha_unchanged
        and order_sha_unchanged
    )

    teacher_score_evidence: Optional[Dict[str, Any]] = None
    teacher_rank: Optional[int] = None
    rank_trend = "missing"
    rank_trend_passed = False
    if teacher_resources is not None:
        teacher_score_evidence = io_resource_score(teacher_resources)
        ranked_rows = [
            (float(row["resource_score"]), str(row["candidate_id"]))
            for row in (manifest.get("ranked_predictions") or [])
        ]
        ranked_rows.append((float(teacher_score_evidence["resource_score"]), "teacher_holdout"))
        ranked_rows.sort(key=lambda item: (-item[0], item[1]))
        teacher_rank = next(
            rank
            for rank, (_score, candidate_id) in enumerate(ranked_rows, start=1)
            if candidate_id == "teacher_holdout"
        )
        if previous_teacher_resource_rank is None:
            rank_trend = "baseline"
            rank_trend_passed = True
        elif teacher_rank < int(previous_teacher_resource_rank):
            rank_trend = "improved"
            rank_trend_passed = True
        elif teacher_rank == int(previous_teacher_resource_rank):
            rank_trend = "stable"
            rank_trend_passed = True
        else:
            rank_trend = "regressed"

    reasons: List[str] = []
    if not manifest_hash_ok:
        reasons.append("interval_manifest_hash_mismatch")
    if not parent_feedback_ok:
        reasons.append("wrong_parent_feedback")
    if not strict_contraction:
        reasons.append("not_strictly_contracted")
    if not candidate_sha_unchanged or not order_sha_unchanged:
        reasons.append("interval_candidate_mismatch")
    if not no_teacher_leakage:
        reasons.append("teacher_leakage")
    if not teacher_contained:
        reasons.append("teacher_not_contained")
    if teacher_score_evidence is None or teacher_rank is None:
        reasons.append("teacher_resource_score_missing")
    elif not rank_trend_passed:
        reasons.append("teacher_resource_rank_regressed")

    interval_reasons = {
        "interval_manifest_hash_mismatch",
        "wrong_parent_feedback",
        "not_strictly_contracted",
        "interval_candidate_mismatch",
        "teacher_leakage",
    }
    return {
        "schema_version": 2,
        "event": "bonect_nested_interval_post_freeze_teacher_supervisor_audit",
        "evidence_class": "post_freeze_supervisor_only",
        "round": manifest.get("round"),
        "interval_manifest_sha256": manifest.get("interval_manifest_sha256"),
        "algorithm_evidence_frozen_before_teacher": manifest_hash_ok,
        "teacher_parameter_sha256": canonical_sha256(exact_cost_params(teacher_params)),
        "teacher_params_redacted_from_receipt": True,
        "teacher_contained": teacher_contained,
        "teacher_contained_requested_by_axis": requested_contained,
        "teacher_contained_effective_by_axis": effective_contained,
        "teacher_resource_score": (
            teacher_score_evidence["resource_score"] if teacher_score_evidence else None
        ),
        "teacher_resource_score_evidence": teacher_score_evidence,
        "teacher_resource_score_rank": teacher_rank,
        "previous_teacher_resource_score_rank": previous_teacher_resource_rank,
        "teacher_resource_score_rank_trend": rank_trend,
        "teacher_resource_score_rank_trend_passed": rank_trend_passed,
        "strict_contraction": strict_contraction,
        "requested_shrink_ratio": requested_metrics.get("shrink_ratio"),
        "effective_shrink_ratio": effective_metrics.get("shrink_ratio"),
        "parent_feedback_ok": parent_feedback_ok,
        "candidate_sha_before": manifest.get("candidate_sha256"),
        "candidate_sha_after": candidate_sha_after_holdout,
        "candidate_order_sha_before": manifest.get("candidate_order_sha256"),
        "candidate_order_sha_after": candidate_order_sha_after_holdout,
        "candidate_and_order_unchanged": candidate_sha_unchanged and order_sha_unchanged,
        "no_teacher_leakage": no_teacher_leakage,
        "interval_invariants_passed": not any(reason in interval_reasons for reason in reasons),
        "fail_closed_reasons": reasons,
        "supervisor_passed": not reasons,
        "outer_run_allowed": not reasons,
        "teacher_feedback_exported_to_algorithm": False,
    }


def assert_outer_allowed(receipt: Mapping[str, Any], manifest: Mapping[str, Any]) -> None:
    if str(receipt.get("interval_manifest_sha256")) != str(manifest.get("interval_manifest_sha256")):
        raise IntervalInvariantError("interval_candidate_mismatch: receipt manifest SHA mismatch")
    if not bool(receipt.get("outer_run_allowed")):
        reasons = receipt.get("fail_closed_reasons") or ["unknown_interval_invariant_failure"]
        raise IntervalInvariantError("outer run blocked: " + ",".join(str(value) for value in reasons))
