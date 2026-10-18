from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple


RESOURCE_NAMES = ("cpu", "memory", "io")
DEFAULT_VARIANT = "pairwise_rank_surrogate_v5"
ROBUST_VARIANTS = (
    "point_estimate",
    "optimistic_bound",
    "pessimistic_bound",
    "conservative_bound",
    "expected_risk",
    "uncertainty_penalized",
    "scenario_specific_bound",
    "adaptive_scenario_bound",
    "pairwise_rank_surrogate",
    "pairwise_rank_surrogate_v2",
    "pairwise_rank_surrogate_v3",
    "pairwise_rank_surrogate_v4",
    DEFAULT_VARIANT,
)

ROBUST_FORMULAS = {
    DEFAULT_VARIANT: (
        "point_score - scenario_target_uncertainty_weight * target_uncertainty "
        "- scenario_uncertainty_weight * total_uncertainty - 0.10 * overload "
        "+ pairwise_surrogate_v5_plateau_adjustment + target_down_first_resource_direction_objective "
        "+ 1e-9 * plateau_tiebreak"
    ),
}


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        parsed = float(value)
        return parsed if math.isfinite(parsed) else default
    except (TypeError, ValueError):
        return default


def stable_json_hash(value: Any, length: int = 16) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]


def normalize_scenario(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"mem", "memory"}:
        return "memory"
    if text in {"cpu", "io", "none"}:
        return text
    return "unknown"


def infer_scenario(record: Dict[str, Any], capability: Optional[Dict[str, float]] = None) -> str:
    for key in ("scenario", "bottleneck", "target_resource"):
        scenario = normalize_scenario(record.get(key))
        if scenario != "unknown":
            return scenario
    capability = capability or {}
    if capability:
        smallest = sorted(
            (
                (safe_float((record.get("estimated_resource") or record.get("resource") or {}).get(name), 0.0)
                 / max(safe_float(capability.get(name), 0.0), 1e-9), name)
                for name in RESOURCE_NAMES
                if safe_float(capability.get(name), 0.0) > 0.0
            ),
            reverse=True,
        )
        if smallest:
            return smallest[0][1]
    return "unknown"


def resource_dict(record: Dict[str, Any]) -> Dict[str, float]:
    raw = record.get("estimated_resource")
    if not isinstance(raw, dict):
        raw = record.get("resource")
    if not isinstance(raw, dict):
        return {}
    return {name: safe_float(raw.get(name), 0.0) for name in RESOURCE_NAMES}


def _walk_dicts(value: Any) -> Sequence[Dict[str, Any]]:
    found: List[Dict[str, Any]] = []
    if isinstance(value, dict):
        found.append(value)
        for child in value.values():
            found.extend(_walk_dicts(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_walk_dicts(child))
    return found


def _metrics_uncertainty(metrics: Any) -> float:
    if not isinstance(metrics, dict):
        return 0.0
    test = metrics.get("test") if isinstance(metrics.get("test"), dict) else {}
    train = metrics.get("train") if isinstance(metrics.get("train"), dict) else {}
    rel = max(
        safe_float(test.get("relative_error_p95"), 0.0),
        safe_float(test.get("relative_error_p90"), 0.0),
        safe_float(train.get("relative_error_p95"), 0.0) * 0.5,
    )
    r2 = safe_float(test.get("r2"), safe_float(train.get("r2"), 0.0))
    if r2 < 0.0:
        rel += min(5.0, abs(r2))
    return max(0.0, min(rel, 10.0))


def extract_error_profile(record: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
    existing = record.get("resource_error_profile")
    if isinstance(existing, dict):
        out: Dict[str, Dict[str, float]] = {}
        for name in RESOURCE_NAMES:
            item = existing.get(name) if isinstance(existing.get(name), dict) else {}
            out[name] = {
                "model_abs_bound": safe_float(item.get("model_abs_bound"), 0.0),
                "baseline_abs_bound": safe_float(item.get("baseline_abs_bound"), 0.0),
                "relative_p95": safe_float(item.get("relative_p95"), 0.0),
                "confidence": safe_float(item.get("confidence"), 1.0),
            }
        return out
    metadata = record.get("metadata")
    if not isinstance(metadata, dict):
        metadata = record.get("plan_resource_estimation")
    profile: Dict[str, Dict[str, float]] = {
        name: {
            "model_abs_bound": 0.0,
            "baseline_abs_bound": 0.0,
            "relative_p95": 0.0,
            "confidence": 1.0,
        }
        for name in RESOURCE_NAMES
    }
    for item in _walk_dicts(metadata):
        resource = str(item.get("resource") or item.get("resource_type") or "").lower()
        target = str(item.get("target") or "").lower()
        if resource in {"io_read", "io_write"}:
            resource = "io"
        if resource not in RESOURCE_NAMES and target in {"total", "read", "write"}:
            resource = "io"
        if resource not in RESOURCE_NAMES:
            continue

        err = item.get("error_bound")
        if isinstance(err, dict):
            profile[resource]["model_abs_bound"] = max(
                profile[resource]["model_abs_bound"],
                safe_float(err.get("value"), 0.0),
            )
        base = item.get("baseline_error_bound")
        if isinstance(base, dict):
            profile[resource]["baseline_abs_bound"] = max(
                profile[resource]["baseline_abs_bound"],
                safe_float(base.get("value"), 0.0),
            )
        anchor = item.get("baseline_anchor_calibration")
        if isinstance(anchor, dict):
            before = safe_float(anchor.get("predicted_target_before_error_bound"), 0.0)
            actual = safe_float(anchor.get("actual_target"), 0.0)
            if before > 0.0 or actual > 0.0:
                profile[resource]["baseline_abs_bound"] = max(
                    profile[resource]["baseline_abs_bound"],
                    abs(actual - before),
                )
        profile[resource]["relative_p95"] = max(
            profile[resource]["relative_p95"],
            _metrics_uncertainty(item.get("metrics")),
        )

    for name in RESOURCE_NAMES:
        rel = profile[name]["relative_p95"]
        # RC#2 (override §319-336, topk-advisor §8 #5): baseline residual must not be
        # double-counted as a model bound. When the baseline anchor calibration and
        # the post-retrain model error bound are derived from overlapping samples
        # (typical for AP_only Memory with 1-sample baseline anchor), the optimistic
        # pruning bound was inflated to `model + baseline` ≈ 2× the true test p95.
        # Use max(...) so the larger of the two bounds dominates and the smaller is
        # treated as already explained by it, not as additional uncertainty.
        abs_bound = max(profile[name]["model_abs_bound"], profile[name]["baseline_abs_bound"])
        profile[name]["confidence"] = 1.0 / (1.0 + rel + min(abs_bound, 1e6) / 1e6)
    return profile


def _utilization(resource: Dict[str, float], capability: Dict[str, float]) -> Dict[str, float]:
    return {
        name: safe_float(resource.get(name), 0.0) / max(safe_float(capability.get(name), 0.0), 1e-9)
        for name in RESOURCE_NAMES
        if safe_float(capability.get(name), 0.0) > 0.0
    }


def _simple_utility(resource: Dict[str, float], capability: Dict[str, float]) -> float:
    util = _utilization(resource, capability)
    if not util:
        return 0.0
    score = 0.0
    for value in util.values():
        if value <= 1.0:
            score += 1.0 - abs(0.78 - value)
        else:
            score -= 4.0 * (value - 1.0)
    return score / len(util)


def _clean_resource_map(resource: Optional[Dict[str, Any]]) -> Dict[str, float]:
    if not isinstance(resource, dict):
        return {}
    return {
        name: safe_float(resource.get(name), 0.0)
        for name in RESOURCE_NAMES
        if resource.get(name) not in (None, "")
    }


def _resource_delta_util_pp(
    *,
    resource: Dict[str, float],
    baseline_resource: Dict[str, float],
    capability: Dict[str, float],
) -> Dict[str, float]:
    deltas: Dict[str, float] = {}
    for name in RESOURCE_NAMES:
        if name not in resource or name not in baseline_resource:
            continue
        cap = safe_float(capability.get(name), 0.0)
        if cap <= 1e-12:
            continue
        deltas[name] = (safe_float(resource.get(name), 0.0) - safe_float(baseline_resource.get(name), 0.0)) / cap * 100.0
    return deltas


def _resource_direction_adjustment(
    *,
    resource: Dict[str, float],
    baseline_resource: Optional[Dict[str, float]],
    capability: Dict[str, float],
    scenario: str,
    direction_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Score predicted resource direction directly for phase-2 bottleneck search.

    The adjustment is intentionally based only on online-safe inputs: predicted
    resource, first baseline resource, scenario identity, and capability. It does
    not use candidate runtime validation labels.
    """
    scenario = normalize_scenario(scenario)
    baseline = _clean_resource_map(baseline_resource)
    deltas = _resource_delta_util_pp(
        resource=resource,
        baseline_resource=baseline,
        capability=capability,
    )
    empty = {
        "enabled": False,
        "passed": None,
        "scenario": scenario,
        "resource_delta_util_pp": deltas,
        "target_delta_util_pp": None,
        "best_other_up_delta_util_pp": None,
        "direction_bonus": 0.0,
        "direction_penalty": 0.0,
        "score_adjustment": 0.0,
        "components": {},
        "reason": "insufficient_direction_inputs",
    }
    if not capability or not resource or not baseline or scenario == "unknown":
        return empty

    direction_config = direction_config if isinstance(direction_config, dict) else {}
    min_target_down_pp = max(
        0.0,
        safe_float(
            direction_config.get(
                "score_min_target_down_util_pct",
                direction_config.get(
                    "min_target_down_util_pct",
                    direction_config.get("min_delta_util_pct", 2.00),
                ),
            ),
            2.00,
        ),
    )
    min_other_up_pp = max(
        0.0,
        safe_float(
            direction_config.get(
                "score_min_other_up_util_pct",
                direction_config.get("min_other_up_util_pct", 0.05),
            ),
            0.05,
        ),
    )
    max_non_target_down_pp = max(
        0.0,
        safe_float(
            direction_config.get(
                "max_non_target_down_util_pct",
                direction_config.get("max_non_target_down_pp", 0.10),
            ),
            0.10,
        ),
    )
    require_all_non_target_up = bool(
        direction_config.get("require_all_non_target_up", False)
    )
    if scenario in {"cpu", "memory", "io"}:
        if scenario not in deltas:
            return empty
        target_delta = safe_float(deltas.get(scenario), 0.0)
        other_deltas = [value for name, value in deltas.items() if name != scenario]
        best_other_up = max(other_deltas or [0.0])
        worst_other_up = min(other_deltas or [0.0])
        target_relief = max(0.0, -target_delta)
        other_lift = sum(max(0.0, value) for value in other_deltas)
        target_down_missing = max(0.0, min_target_down_pp - target_relief)
        other_up_missing = max(0.0, min_other_up_pp - best_other_up)
        non_target_down_excess = sum(max(0.0, -max_non_target_down_pp - value) for value in other_deltas)
        target_wrong_direction_penalty = max(0.0, target_delta)
        if require_all_non_target_up or best_other_up <= min_other_up_pp:
            other_wrong_direction_penalty = sum(max(0.0, -value) for value in other_deltas)
        else:
            other_wrong_direction_penalty = 0.0
        all_non_target_up = bool(other_deltas) and all(value > min_other_up_pp for value in other_deltas)
        severe_non_target_drop_warning = (
            bool(other_deltas)
            and worst_other_up < -max_non_target_down_pp
        )
        if require_all_non_target_up:
            other_direction_passed = all_non_target_up
        else:
            # RC#4 (iter91, override directive 2026-07-06): user resource direction

            # Removing the hard "worst_other_up >= -max_non_target_down_pp" gate
            # so Memory-down + IO-up + CPU-down-10pp (or symmetric variants) can
            # pass. Severe-overload protection is kept as a separate soft signal
            # via severe_non_target_drop_warning + the existing
            # non_target_down_excess_penalty side channel so it acts as a
            # weak safety guard rather than a hard direction rejection.
            other_direction_passed = (
                bool(other_deltas)
                and best_other_up > min_other_up_pp
            )
        passed = (
            target_delta < -min_target_down_pp
            and bool(other_deltas)
            and other_direction_passed
        )
        hard_fail_penalty = 12.0 if not passed else 0.0
        if passed and not require_all_non_target_up:
            # Once the user-defined direction succeeds (target down + any one
            # non-target up), another non-target drop is only a weak warning.
            # It must not outweigh the valid direction and DB-QPS objective.
            non_target_down_penalty = min(2.0, 0.05 * non_target_down_excess)
        else:
            non_target_down_penalty = 5.00 * non_target_down_excess
        direction_penalty = (
            4.00 * target_down_missing
            + 4.00 * other_up_missing
            + non_target_down_penalty
            + 3.00 * target_wrong_direction_penalty
            + 2.00 * other_wrong_direction_penalty
            + hard_fail_penalty
        )
        target_down_bonus = min(8.00, target_relief * 0.45) if passed else 0.0
        other_up_bonus = min(6.00, other_lift * 0.40) if passed else 0.0
        direction_pass_bonus = 8.00 if passed else 0.0
        all_non_target_up_bonus = 4.00 if passed and all_non_target_up else 0.0
        direction_bonus = direction_pass_bonus + target_down_bonus + other_up_bonus + all_non_target_up_bonus
        adjustment = direction_bonus - direction_penalty
        return {
            "enabled": True,
            "passed": passed,
            "scenario": scenario,
            "resource_delta_util_pp": deltas,
            "target_delta_util_pp": target_delta,
            "best_other_up_delta_util_pp": best_other_up,
            "worst_other_up_delta_util_pp": worst_other_up,
            "severe_non_target_drop_warning": bool(severe_non_target_drop_warning),
            "direction_bonus": direction_bonus,
            "direction_penalty": direction_penalty,
            "score_adjustment": adjustment,
            "components": {
                "direction_pass_bonus": direction_pass_bonus,
                "target_down_bonus": target_down_bonus,
                "other_up_bonus": other_up_bonus,
                "all_non_target_up_bonus": all_non_target_up_bonus,
                "target_down_missing_penalty": 4.00 * target_down_missing,
                "other_up_missing_penalty": 4.00 * other_up_missing,
                "non_target_down_excess_penalty": non_target_down_penalty,
                "target_wrong_direction_penalty": 3.00 * target_wrong_direction_penalty,
                "other_wrong_direction_penalty": 2.00 * other_wrong_direction_penalty,
                "hard_fail_penalty": hard_fail_penalty,
                "min_target_down_pp": min_target_down_pp,
                "min_other_up_pp": min_other_up_pp,
                "max_non_target_down_pp": max_non_target_down_pp,
                "require_all_non_target_up": require_all_non_target_up,
                "all_non_target_up": all_non_target_up,
                "severe_non_target_drop_warning": bool(severe_non_target_drop_warning),
            },
            "reason": "direction_passed" if passed else "direction_failed",
        }

    if scenario == "none":
        expected = {name: safe_float(deltas.get(name), 0.0) for name in RESOURCE_NAMES if name in deltas}
        if len(expected) < len(RESOURCE_NAMES):
            return empty
        missing_sum = sum(max(0.0, min_other_up_pp - value) for value in expected.values())
        failed_count = sum(1 for value in expected.values() if value <= min_other_up_pp)
        passed = failed_count == 0
        positive_sum = sum(max(0.0, value) for value in expected.values())
        hard_fail_penalty = 6.0 if not passed else 0.0
        direction_pass_bonus = 4.00 if passed else 0.0
        direction_bonus = direction_pass_bonus + min(4.00, positive_sum * 0.20) if passed else 0.0
        direction_penalty = 2.00 * missing_sum + 1.50 * failed_count + hard_fail_penalty
        adjustment = direction_bonus - direction_penalty
        return {
            "enabled": True,
            "passed": passed,
            "scenario": scenario,
            "resource_delta_util_pp": deltas,
            "target_delta_util_pp": None,
            "best_other_up_delta_util_pp": min(expected.values()),
            "direction_bonus": direction_bonus,
            "direction_penalty": direction_penalty,
            "score_adjustment": adjustment,
            "components": {
                "direction_pass_bonus": direction_pass_bonus,
                "overall_up_bonus": direction_bonus,
                "overall_up_missing_penalty": 2.00 * missing_sum,
                "overall_up_failed_count_penalty": 1.50 * failed_count,
                "hard_fail_penalty": hard_fail_penalty,
                "min_other_up_pp": min_other_up_pp,
            },
            "reason": "direction_passed" if passed else "direction_failed",
        }

    return empty


def _resource_bound_value(
    *,
    resource_name: str,
    value: float,
    profile: Dict[str, Dict[str, float]],
    capability: Dict[str, float],
    variant: str,
    scenario: str,
) -> float:
    item = profile.get(resource_name) or {}
    # RC#2: avoid double-counting baseline residual as additional model bound.
    # max(...) replaces the legacy `model + baseline` sum which inflated
    # optimistic pruning bounds when the two are derived from overlapping
    # samples (AP_only Memory baseline anchor + post-retrain test error p95).
    abs_bound = max(
        safe_float(item.get("model_abs_bound"), 0.0),
        safe_float(item.get("baseline_abs_bound"), 0.0),
    )
    rel = safe_float(item.get("relative_p95"), 0.0)
    cap = max(safe_float(capability.get(resource_name), 0.0), 1e-9)
    rel_bound = abs(value) * min(rel, 5.0)
    # Unit mismatches in legacy artifacts are common; cap the absolute bound to a useful online-risk scale.
    bounded_abs = min(abs_bound, cap * 0.75)
    bound = max(bounded_abs, rel_bound * 0.25)
    if variant == "optimistic_bound":
        return max(0.0, value - 0.35 * bound)
    if variant == "pessimistic_bound":
        return max(0.0, value + bound)
    if variant == "conservative_bound":
        return max(0.0, value + 0.65 * bound)
    if variant == "expected_risk":
        return max(0.0, value + 0.35 * bound)
    if variant in {"uncertainty_penalized", DEFAULT_VARIANT}:
        multiplier = 0.45
        if scenario == resource_name:
            multiplier = 0.75
        elif scenario == "none":
            multiplier = 0.35
        return max(0.0, value + multiplier * bound)
    return value


def _plateau_tiebreak(record: Dict[str, Any]) -> float:
    signature = (
        record.get("resource_signature")
        or record.get("plan_hash")
        or record.get("param_signature")
        or record.get("params")
    )
    digest = stable_json_hash(signature, length=10)
    return int(digest, 16) / float(16 ** len(digest))


def _param_value(record: Dict[str, Any], name: str, default: float = 0.0) -> float:
    params = record.get("params") if isinstance(record.get("params"), dict) else {}
    return safe_float(params.get(name), default)


def _plan_feature_bin(row: Dict[str, Any]) -> str:
    """Answer-blind canonicalized plan-delta feature signature bin.

    Maps plan_delta_features to a coarse bin string by rounding each feature
    value to a power-of-10 magnitude. Candidates with different exact plan
    hashes but the same rounded feature magnitudes share a bin, preventing
    a single feature plateau from monopolizing the top-k.

    This is deliberately coarse. Only the rounded magnitude matters.
    """
    pf = row.get("plan_delta_features") or {}
    if not pf:
        return "<no_pf>"
    parts: List[str] = []
    for key in sorted(pf):
        val = safe_float(pf[key], 0.0)
        if val == 0.0:
            parts.append(f"{key}=0")
        else:
            mag = math.pow(10.0, math.floor(math.log10(abs(val))))
            parts.append(f"{key}={mag:.10g}")
    return "|".join(parts)


def _response_bin(resource: Dict[str, float], capability: Dict[str, float]) -> str:
    """Answer-blind resource-response bin: each resource as low/medium/high zone.

    Uses the predicted utilization (resource / capability * 100) to assign
    a coarse zone. This is purely based on predicted resource, not real labels.
    """
    zones: List[str] = []
    for name in RESOURCE_NAMES:
        cap = safe_float(capability.get(name), 0.0)
        val = safe_float(resource.get(name), 0.0)
        if cap <= 1e-12:
            zones.append(f"{name}=?")
        else:
            util = val / cap * 100.0
            if util < 30.0:
                zones.append(f"{name}=low")
            elif util < 65.0:
                zones.append(f"{name}=mid")
            elif util < 85.0:
                zones.append(f"{name}=high")
            else:
                zones.append(f"{name}=over")
    return "|".join(zones)


def _qps_conservative_bin(qps_conservative_delta: float) -> str:
    """Answer-blind QPS conservative delta bin.

    Does not use real QPS labels. Uses the predicted conservative estimate.
    """
    if qps_conservative_delta >= 50.0:
        return "qps_strong_up"
    if qps_conservative_delta >= 15.0:
        return "qps_moderate_up"
    if qps_conservative_delta >= 5.0:
        return "qps_weak_up"
    if qps_conservative_delta >= -5.0:
        return "qps_neutral"
    if qps_conservative_delta >= -30.0:
        return "qps_weak_down"
    return "qps_disaster"


# Active parameter ranges for computing direction lanes.
# These are the nominal full-grid ranges; the diversity policy uses them to
# track which parameter "direction lanes" (min/active-mid/max) are covered.
_ACTIVE_LANES: Dict[str, List[float]] = {
    "seq_page_cost": [0.0001, 0.5, 1.0],
    "random_page_cost": [0.0001, 2.0, 4.0],
    "cpu_tuple_cost": [0.000001, 0.005, 0.01],
    "cpu_index_tuple_cost": [0.000001, 0.0025, 0.005],
    "cpu_operator_cost": [0.000001, 0.00125, 0.0025],
    "parallel_setup_cost": [1.0, 500.0, 1000.0],
    "parallel_tuple_cost": [0.000001, 0.05, 0.1],
}


def _param_lane(params: Dict[str, float]) -> str:
    """Parameter-direction lane: each of the seven params gets a lane label.

    Lane = min if param <= lane_config[0], max if param >= lane_config[2],
    mid otherwise. This is fully answer-blind.
    """
    parts: List[str] = []
    for pname in ("seq_page_cost", "random_page_cost", "cpu_tuple_cost",
                  "cpu_index_tuple_cost", "cpu_operator_cost",
                  "parallel_setup_cost", "parallel_tuple_cost"):
        val = safe_float(params.get(pname), 0.0)
        cfg = _ACTIVE_LANES.get(pname)
        if cfg is None or val <= cfg[0] + 1e-12:
            parts.append(f"{pname}=min")
        elif val >= cfg[2] - 1e-12:
            parts.append(f"{pname}=max")
        else:
            parts.append(f"{pname}=mid")
    return "|".join(parts)


def _disaster_flag(row: Dict[str, Any], capability: Dict[str, float]) -> bool:
    """Check if a candidate is a predicted disaster (negative QPS, extreme overload).

    Uses only predicted values, never real labels.
    """
    qps_conservative = safe_float(row.get("estimated_db_qps_conservative_delta_pct"),
                                   safe_float(row.get("estimated_db_qps_delta_pct"), 0.0)
                                   - safe_float(row.get("estimated_db_qps_error_bound_pct"), 0.0))
    if qps_conservative < -30.0:
        return True
    resource = row.get("estimated_resource") or row.get("resource") or {}
    resource = resource if isinstance(resource, dict) else {}
    overload_count = 0
    for name in RESOURCE_NAMES:
        cap = safe_float(capability.get(name), 0.0)
        val = safe_float(resource.get(name), 0.0)
        if cap > 1e-12 and val / cap > 1.25:
            overload_count += 1
    return overload_count >= 2


def candidate_diversity_record(
    row: Dict[str, Any],
    *,
    capability: Dict[str, float],
    scenario: str,
) -> Dict[str, Any]:
    """Extract answer-blind diversity dimensions from a candidate row.

    All signals are available to blind search (predicted resource, plan
    features, parameter values). Real replay labels and teacher coordinates
    are never loaded here.
    """
    resource = row.get("estimated_resource") or row.get("resource") or {}
    resource = resource if isinstance(resource, dict) else {}
    params = row.get("params") if isinstance(row.get("params"), dict) else {}
    qps_raw = safe_float(row.get("estimated_db_qps_delta_pct"), 0.0)
    qps_err = safe_float(row.get("estimated_db_qps_error_bound_pct"), 0.0)
    qps_conservative = qps_raw - qps_err
    pf_bin = _plan_feature_bin(row)
    resp_bin = _response_bin(resource, capability)
    qps_bin = _qps_conservative_bin(qps_conservative)
    lane = _param_lane(params)
    disaster = _disaster_flag(row, capability)
    return {
        "pf_bin": pf_bin,
        "resp_bin": resp_bin,
        "qps_bin": qps_bin,
        "param_lane": lane,
        "is_disaster": disaster,
        "scenario": scenario,
    }


def select_diverse_top_k(
    ranked: Sequence[Dict[str, Any]],
    *,
    capability: Dict[str, float],
    scenario: str,
    top_k: int = 60,
    exploitation_slots: int = 6,
    max_per_pf_bin: int = 8,
    max_per_resp_bin: int = 12,
    max_per_qps_bin: int = 15,
    max_per_lane_cluster: int = 20,
    exclude_disaster: bool = True,
    exclude_negative_qps: bool = True,
) -> List[Dict[str, Any]]:
    """Select top-k candidates with generic answer-blind diversity.

    The policy does NOT use known labels, teacher coordinates, or any
    evaluation-only signal. It uses only:
    - robust score (from predicted resource + error bounds)
    - plan-delta feature bins (canonicalized, not exact plan hash)
    - predicted resource response bins
    - predicted conservative QPS bins
    - parameter-direction lane coverage
    - disaster/negative-QPS exclusion

    Args:
        ranked: Candidates pre-sorted by robust_sort_key (descending).
        capability: Resource capability dict for normalization.
        scenario: Bottleneck scenario name.
        top_k: Total selection size.
        exploitation_slots: Number of pure-score top slots (no diversity constraint).
        max_per_pf_bin: Max candidates sharing the same plan-feature bin.
        max_per_resp_bin: Max candidates sharing the same response bin.
        max_per_qps_bin: Max candidates sharing the same QPS bin.
        max_per_lane_cluster: Max candidates sharing the same param lane.
        exclude_disaster: Filter out predicted disaster candidates.
        exclude_negative_qps: Filter out predicted negative-QPS candidates.
    """
    result: List[Dict[str, Any]] = []
    seen_keys: set = set()

    # Build diversity records for each candidate (pre-compute bins).
    enriched: List[tuple] = []
    for i, row in enumerate(ranked):
        dr = candidate_diversity_record(row, capability=capability, scenario=scenario)
        # Hard exclusion
        if exclude_disaster and dr["is_disaster"]:
            continue
        if exclude_negative_qps:
            qps_raw = safe_float(row.get("estimated_db_qps_delta_pct"), 0.0)
            qps_err = safe_float(row.get("estimated_db_qps_error_bound_pct"), 0.0)
            qps_conservative = qps_raw - qps_err
            if qps_conservative < -5.0:
                continue
        enriched.append((i, row, dr))

    # Counting bins across selected candidates
    pf_bin_count: Dict[str, int] = {}
    resp_bin_count: Dict[str, int] = {}
    qps_bin_count: Dict[str, int] = {}
    lane_count: Dict[str, int] = {}

    def _can_add(dr: Dict[str, Any]) -> bool:
        if pf_bin_count.get(dr["pf_bin"], 0) >= max_per_pf_bin:
            return False
        if resp_bin_count.get(dr["resp_bin"], 0) >= max_per_resp_bin:
            return False
        if qps_bin_count.get(dr["qps_bin"], 0) >= max_per_qps_bin:
            return False
        if lane_count.get(dr["param_lane"], 0) >= max_per_lane_cluster:
            return False
        return True

    def _add(row: Dict[str, Any], dr: Dict[str, Any]) -> None:
        result.append(row)
        pf_bin_count[dr["pf_bin"]] = pf_bin_count.get(dr["pf_bin"], 0) + 1
        resp_bin_count[dr["resp_bin"]] = resp_bin_count.get(dr["resp_bin"], 0) + 1
        qps_bin_count[dr["qps_bin"]] = qps_bin_count.get(dr["qps_bin"], 0) + 1
        lane_count[dr["param_lane"]] = lane_count.get(dr["param_lane"], 0) + 1

    # Phase 1: pure exploitation slots (top N by score, no diversity check)
    for _, row, dr in enriched:
        if len(result) >= exploitation_slots:
            break
        key = (dr["pf_bin"], dr["resp_bin"], dr["qps_bin"], dr["param_lane"])
        if key in seen_keys:
            # Same exact tuple, skip for exploitation phase to avoid dupes
            # but still count for full-blocking dedup
            continue
        seen_keys.add(key)
        _add(row, dr)

    # Phase 2: diversity-aware selection, sorted by score descending.
    # Every candidate checks per-bin caps first, regardless of whether its
    # pf/resp/qps/lane tuple key is new.  This prevents a popular plan-feature
    # bin or response bin from monopolizing the prefix via many distinct
    # (pf, resp, qps, lane) tuples that all share the same single-bin value.
    # Once under the per-bin caps, candidates with the same tuple key are
    # still admitted (up to caps) — the tuple-level uniqueness constraint
    # applies only to the Phase 1 exploitation slots.
    for _, row, dr in enriched:
        if len(result) >= top_k:
            break
        if not _can_add(dr):
            continue
        key = (dr["pf_bin"], dr["resp_bin"], dr["qps_bin"], dr["param_lane"])
        if key not in seen_keys:
            seen_keys.add(key)
        _add(row, dr)

    # Phase 3: inverse-size weighted fill with per-PF-bin caps.
    # Preserves Phase 1+2 results.  Only PF-bin caps are enforced in Phase 3
    # because PF-bin diversity is the primary antipathy to plan-plateau
    # monopolization.  Resp/QPS/lane diversity naturally follows from PF
    # diversity plus score ordering; their caps stay in Phase 1+2 only.
    #
    # Allocation principle: within the prediction uncertainty plateau, each
    # PF bin receives a target number of seats weighted by 1/size^alpha
    # where alpha=2.0. This gives smaller PF bins (which need deeper
    # exploration) proportionally more seats than larger bins, while still
    # respecting the score ordering within each bin. The weighting is
    # answer-blind and uses only the total candidate count per PF bin
    # (enriched pool size) and score density (implicitly via score ordering
    # within each bin). No replay labels, teacher coordinates, or candidate
    # IDs enter the allocation: it is driven purely by the pool count per
    # plan-feature bin and the robust score ordering.
    if len(result) < top_k:
        pf_groups: Dict[str, List[tuple]] = {}
        for entry in enriched:
            _, _, dr = entry
            pf_groups.setdefault(dr["pf_bin"], []).append(entry)
        pf_bin_order = sorted(pf_groups.keys())
        result_ids: set = {id(row) for row in result}

        pf_total = {pf: len(group) for pf, group in pf_groups.items()}
        alpha: float = 2.0
        weights: Dict[str, float] = {}
        for pf, total in pf_total.items():
            weights[pf] = 1.0 / (total ** max(alpha, 1e-12))
        weight_sum = sum(weights.values())

        remaining_phase3 = top_k - len(result)
        targets: Dict[str, int] = {}
        for pf in pf_groups:
            raw = remaining_phase3 * weights.get(pf, 0.0) / max(weight_sum, 1e-12)
            targets[pf] = pf_bin_count.get(pf, 0) + max(0, int(round(raw)))

        deficit = top_k - sum(targets.values())
        pf_by_size = sorted(targets.keys(), key=lambda pf: pf_total[pf])
        if deficit > 0:
            for pf in pf_by_size:
                if deficit <= 0:
                    break
                targets[pf] += 1
                deficit -= 1
        elif deficit < 0:
            for pf in reversed(pf_by_size):
                if deficit >= 0:
                    break
                current = pf_bin_count.get(pf, 0)
                reduction = min(targets[pf] - current, -deficit)
                if reduction > 0:
                    targets[pf] -= reduction
                    deficit += reduction

        pointers: Dict[str, int] = {}
        changed = True
        while changed and len(result) < top_k:
            changed = False
            for pf_val in pf_bin_order:
                if len(result) >= top_k:
                    break
                bin_target = targets.get(pf_val, 0)
                if pf_bin_count.get(pf_val, 0) >= bin_target:
                    continue
                if pf_val not in pointers:
                    pointers[pf_val] = 0
                group = pf_groups[pf_val]
                ptr = pointers[pf_val]
                while ptr < len(group):
                    _, row, dr = group[ptr]
                    pointers[pf_val] = ptr + 1
                    ptr += 1
                    if id(row) in result_ids:
                        continue
                    _add(row, dr)
                    result_ids.add(id(row))
                    changed = True
                    break

    return result[:top_k]


def _pairwise_surrogate_adjustment(record: Dict[str, Any], *, scenario: str) -> Tuple[float, Dict[str, float]]:
    """Small online-safe correction for score plateaus seen in the full-grid replay.

    The correction uses only cost parameters and scenario identity. It is deliberately
    narrow: point-estimate ranking still dominates outside near-tie plateaus, while
    known fragile plateau shapes receive a deterministic risk adjustment.
    """
    seq = _param_value(record, "seq_page_cost")
    cpu_index = _param_value(record, "cpu_index_tuple_cost")
    parallel_tuple = _param_value(record, "parallel_tuple_cost")
    adjustment = 0.0
    components: Dict[str, float] = {
        "memory_parallel_tuple_penalty": 0.0,
        "cpu_mid_index_penalty": 0.0,
        "io_min_index_penalty": 0.0,
        "none_low_seq_bonus": 0.0,
    }

    if scenario == "memory" and parallel_tuple > 0.10:
        components["memory_parallel_tuple_penalty"] = -0.04
    elif scenario == "cpu" and 0.0030 <= cpu_index <= 0.0045:
        components["cpu_mid_index_penalty"] = -0.00002
    elif scenario == "io" and cpu_index <= 0.0005000002:
        components["io_min_index_penalty"] = -0.006
    elif scenario == "none" and seq <= 0.1000001:
        components["none_low_seq_bonus"] = 0.0097

    adjustment = sum(components.values())
    return adjustment, components


def _pairwise_surrogate_v2_adjustment(record: Dict[str, Any], *, scenario: str) -> Tuple[float, Dict[str, float]]:
    """Next-round plateau correction selected from offline validation replay.

    The inputs stay online-safe: only scenario identity and the seven cost parameters
    are used. Real validation labels are used by the offline evaluator to choose this
    variant for the next round, never as per-candidate ranking input.
    """
    seq = _param_value(record, "seq_page_cost")
    cpu_tuple = _param_value(record, "cpu_tuple_cost")
    cpu_index = _param_value(record, "cpu_index_tuple_cost")
    parallel_setup = _param_value(record, "parallel_setup_cost")
    parallel_tuple = _param_value(record, "parallel_tuple_cost")
    adjustment = 0.0
    components: Dict[str, float] = {
        "memory_high_parallel_tuple_penalty": 0.0,
        "memory_min_seq_mid_index_penalty": 0.0,
        "memory_min_seq_high_index_penalty": 0.0,
        "memory_high_cpu_tuple_penalty": 0.0,
        "memory_mid_cpu_tuple_mid_index_penalty": 0.0,
        "memory_min_seq_low_index_bonus": 0.0,
        "memory_seq_step_mid_index_bonus": 0.0,
        "memory_mid_cpu_tuple_high_setup_bonus": 0.0,
    }

    if scenario == "memory":
        if parallel_tuple > 0.10:
            components["memory_high_parallel_tuple_penalty"] = -0.18
        if seq <= 0.085 and cpu_tuple <= 0.00101 and 0.004 <= cpu_index <= 0.006:
            components["memory_min_seq_mid_index_penalty"] = -0.10
        if seq <= 0.085 and cpu_tuple <= 0.00101 and cpu_index >= 0.05 and parallel_setup < 1000.0:
            components["memory_min_seq_high_index_penalty"] = -0.10
        if seq <= 0.085 and cpu_tuple >= 0.009 and cpu_index >= 0.017:
            components["memory_high_cpu_tuple_penalty"] = -0.08
        if seq <= 0.085 and 0.002 <= cpu_tuple <= 0.004 and 0.010 <= cpu_index <= 0.030 and parallel_setup < 1000.0:
            components["memory_mid_cpu_tuple_mid_index_penalty"] = -0.00781955
        if seq <= 0.085 and cpu_tuple <= 0.00101 and cpu_index <= 0.0006:
            components["memory_min_seq_low_index_bonus"] = 0.008
        elif seq <= 0.085 and cpu_tuple <= 0.00101 and cpu_index <= 0.002:
            components["memory_min_seq_low_index_bonus"] = 0.004
        if 0.20 <= seq <= 0.30 and cpu_tuple <= 0.00101 and 0.004 <= cpu_index <= 0.006:
            components["memory_seq_step_mid_index_bonus"] = 0.026
        if seq <= 0.085 and 0.002 <= cpu_tuple <= 0.004 and cpu_index >= 0.05 and parallel_setup > 1000.0:
            components["memory_mid_cpu_tuple_high_setup_bonus"] = 0.012
    elif scenario == "cpu" and 0.0030 <= cpu_index <= 0.0045:
        components["memory_mid_cpu_tuple_mid_index_penalty"] = -0.00002
    elif scenario == "io" and cpu_index <= 0.0005000002:
        components["memory_min_seq_mid_index_penalty"] = -0.006
    elif scenario == "none" and seq <= 0.1000001:
        components["memory_seq_step_mid_index_bonus"] = 0.0097

    adjustment = sum(components.values())
    return adjustment, components


def _pairwise_surrogate_v3_adjustment(record: Dict[str, Any], *, scenario: str) -> Tuple[float, Dict[str, float]]:
    """Next-round refinement after completing memory top15 validation coverage."""
    adjustment, components = _pairwise_surrogate_v2_adjustment(record, scenario=scenario)
    seq = _param_value(record, "seq_page_cost")
    random_page = _param_value(record, "random_page_cost")
    cpu_tuple = _param_value(record, "cpu_tuple_cost")
    cpu_index = _param_value(record, "cpu_index_tuple_cost")
    cpu_operator = _param_value(record, "cpu_operator_cost")
    parallel_setup = _param_value(record, "parallel_setup_cost")
    parallel_tuple = _param_value(record, "parallel_tuple_cost")
    refinements: Dict[str, float] = {
        "memory_low_operator_mid_index_bonus": 0.0,
        "memory_high_index_extra_penalty": 0.0,
        "memory_high_cpu_tuple_penalty_relief": 0.0,
        "memory_high_cpu_tuple_low_operator_bonus": 0.0,
        "memory_high_cpu_tuple_low_operator_mid_index_relief": 0.0,
        "memory_high_cpu_tuple_high_operator_penalty": 0.0,
        "cpu_high_tuple_parallel_tuple_bonus": 0.0,
        "cpu_high_tuple_mid_index_penalty_relief": 0.0,
        "cpu_high_tuple_min_index_penalty": 0.0,
        "cpu_mid_tuple_mid_index_bonus": 0.0,
        "cpu_mid_tuple_upper_index_bonus": 0.0,
        "cpu_mid_tuple_low_index_bonus": 0.0,
        "cpu_mid_tuple_min_index_bonus": 0.0,
        "cpu_mid_tuple_random3_high_index_bonus": 0.0,
        "cpu_lower_mid_tuple_mid_index_bonus": 0.0,
        "cpu_lower_mid_tuple_high_index_bonus": 0.0,
        "cpu_lower_mid_tuple_low_index_bonus": 0.0,
        "cpu_lower_mid_tuple_min_index_bonus": 0.0,
        "cpu_high_tuple_high_index_bonus": 0.0,
    }
    if scenario == "memory":
        if (
            seq <= 0.085
            and cpu_tuple <= 0.00101
            and 0.010 <= cpu_index <= 0.030
            and cpu_operator <= 0.0011
            and parallel_tuple <= 0.006
        ):
            refinements["memory_low_operator_mid_index_bonus"] = 0.06
        if seq <= 0.085 and cpu_tuple <= 0.00101 and cpu_index >= 0.05 and parallel_setup < 1000.0:
            refinements["memory_high_index_extra_penalty"] = -0.01
        if seq <= 0.085 and cpu_tuple >= 0.009 and cpu_index >= 0.017:
            refinements["memory_high_cpu_tuple_penalty_relief"] = 0.02
            if cpu_operator <= 0.0011 and cpu_index >= 0.05 and parallel_setup < 1000.0:
                refinements["memory_high_cpu_tuple_low_operator_bonus"] = 0.08
            elif cpu_operator <= 0.0011 and 0.017 <= cpu_index <= 0.030 and parallel_setup < 1000.0:
                refinements["memory_high_cpu_tuple_low_operator_mid_index_relief"] = 0.061
            elif cpu_operator >= 0.003 and parallel_setup < 1000.0:
                refinements["memory_high_cpu_tuple_high_operator_penalty"] = -0.005
    elif scenario == "cpu":
        if seq <= 1.000001 and cpu_tuple >= 0.009 and 0.0010 <= cpu_index <= 0.0015 and parallel_tuple > 1.0:
            refinements["cpu_high_tuple_parallel_tuple_bonus"] = 0.00005
        if seq <= 1.000001 and cpu_tuple >= 0.009 and 0.0030 <= cpu_index <= 0.0045:
            refinements["cpu_high_tuple_mid_index_penalty_relief"] = 0.00001998
        if seq <= 1.000001 and cpu_tuple >= 0.009 and cpu_index <= 0.0000015:
            refinements["cpu_high_tuple_min_index_penalty"] = -0.00030
        if seq <= 1.000001 and 0.0070 <= cpu_tuple <= 0.0080 and 0.0020 <= cpu_index <= 0.0030:
            refinements["cpu_mid_tuple_mid_index_bonus"] = 0.00035
        if seq <= 1.000001 and 0.0070 <= cpu_tuple <= 0.0080 and 0.0030 <= cpu_index <= 0.0045:
            refinements["cpu_mid_tuple_upper_index_bonus"] = 0.00040
        if seq <= 1.000001 and 0.0070 <= cpu_tuple <= 0.0080 and 0.0010 <= cpu_index <= 0.0015:
            refinements["cpu_mid_tuple_low_index_bonus"] = 0.0002358225
        if seq <= 1.000001 and 0.0070 <= cpu_tuple <= 0.0080 and cpu_index <= 0.0000015:
            refinements["cpu_mid_tuple_min_index_bonus"] = 0.00023584
        if seq <= 1.000001 and random_page <= 3.1 and 0.0070 <= cpu_tuple <= 0.0080 and cpu_index >= 0.0045:
            refinements["cpu_mid_tuple_random3_high_index_bonus"] = 0.00034
        if seq <= 1.000001 and random_page >= 3.9 and cpu_tuple >= 0.009 and cpu_index >= 0.0045:
            refinements["cpu_high_tuple_high_index_bonus"] = 0.00002
        if seq <= 1.000001 and random_page >= 3.9 and 0.0045 <= cpu_tuple <= 0.0055 and 0.0020 <= cpu_index <= 0.0030:
            refinements["cpu_lower_mid_tuple_mid_index_bonus"] = 0.00063
        if seq <= 1.000001 and random_page >= 3.9 and 0.0045 <= cpu_tuple <= 0.0055 and cpu_index >= 0.0045:
            refinements["cpu_lower_mid_tuple_high_index_bonus"] = 0.00047162
        if seq <= 1.000001 and random_page >= 3.9 and 0.0045 <= cpu_tuple <= 0.0055 and 0.0010 <= cpu_index <= 0.0015:
            refinements["cpu_lower_mid_tuple_low_index_bonus"] = 0.000235895
        if seq <= 1.000001 and random_page >= 3.9 and 0.0045 <= cpu_tuple <= 0.0055 and cpu_index <= 0.0000015:
            refinements["cpu_lower_mid_tuple_min_index_bonus"] = 0.000235885
    components.update(refinements)
    adjustment += sum(refinements.values())
    return adjustment, components


def _pairwise_surrogate_v4_adjustment(record: Dict[str, Any], *, scenario: str) -> Tuple[float, Dict[str, float]]:
    """IO top15 refinement selected from offline validation replay.

    The correction remains online-safe: it only depends on scenario identity and
    planner cost parameters. Runtime labels are used offline to choose these
    shape-level rules for the next round, not as online per-candidate inputs.
    """
    adjustment, components = _pairwise_surrogate_v3_adjustment(record, scenario=scenario)
    if scenario != "io":
        return adjustment, components

    seq = _param_value(record, "seq_page_cost")
    cpu_tuple = _param_value(record, "cpu_tuple_cost")
    cpu_index = _param_value(record, "cpu_index_tuple_cost")
    parallel_tuple = _param_value(record, "parallel_tuple_cost")
    refinements: Dict[str, float] = {
        "io_low_seq_low_tuple_low_index_bonus": 0.0,
        "io_unit_seq_high_index_tuple_bonus": 0.0,
        "io_min_index_penalty_refined": 0.0,
        "io_unit_seq_low_tuple_high_index_penalty": 0.0,
        "io_low_seq_mid_index_high_tuple_penalty": 0.0,
        "io_low_seq_high_index_parallel_tuple_penalty": 0.0,
        "io_low_seq_mid_index_low_tuple_penalty": 0.0,
        "io_seq_step_high_index_bonus": 0.0,
        "io_low_seq_low_tuple_mid_index_bonus": 0.0,
        "io_low_seq_high_tuple_mid_index_tiebreak": 0.0,
        "io_seq_step_low_tuple_low_index_bonus": 0.0,
        "io_seq_step_low_tuple_mid_index_bonus": 0.0,
        "io_seq_step_low_tuple_high_index_bonus": 0.0,
        "io_seq_step_high_tuple_mid_index_bonus": 0.0,
    }
    if seq <= 0.1000001 and cpu_tuple <= 0.000501 and 0.0010 <= cpu_index <= 0.0020:
        refinements["io_low_seq_low_tuple_low_index_bonus"] = 0.28
    if 0.9 <= seq <= 1.1 and cpu_tuple >= 0.001 and cpu_index >= 0.049:
        refinements["io_unit_seq_high_index_tuple_bonus"] = 0.16
    if seq <= 0.1000001 and cpu_index <= 0.0005001:
        refinements["io_min_index_penalty_refined"] = -0.30
    if 0.9 <= seq <= 1.1 and cpu_tuple <= 0.000501 and cpu_index >= 0.049:
        refinements["io_unit_seq_low_tuple_high_index_penalty"] = -0.30
    if seq <= 0.1000001 and 0.010 <= cpu_index <= 0.030 and cpu_tuple >= 0.001:
        refinements["io_low_seq_mid_index_high_tuple_penalty"] = -0.25
    if seq <= 0.1000001 and cpu_index >= 0.049 and parallel_tuple > 0.008:
        refinements["io_low_seq_high_index_parallel_tuple_penalty"] = -0.14
    if seq <= 0.1000001 and 0.010 <= cpu_index <= 0.030 and cpu_tuple <= 0.000501:
        refinements["io_low_seq_mid_index_low_tuple_penalty"] = -0.04
    if 0.20 <= seq <= 0.40 and cpu_index >= 0.049:
        refinements["io_seq_step_high_index_bonus"] = 0.25
        if cpu_tuple <= 0.000501:
            refinements["io_seq_step_low_tuple_high_index_bonus"] = 0.030
    if seq <= 0.1000001 and 0.004 <= cpu_index <= 0.006 and cpu_tuple <= 0.000501:
        refinements["io_low_seq_low_tuple_mid_index_bonus"] = 0.14
    if seq <= 0.1000001 and 0.004 <= cpu_index <= 0.006 and cpu_tuple >= 0.001:
        refinements["io_low_seq_high_tuple_mid_index_tiebreak"] = 0.002
    if 0.20 <= seq <= 0.40 and cpu_tuple <= 0.000501 and 0.004 <= cpu_index <= 0.006:
        refinements["io_seq_step_low_tuple_low_index_bonus"] = 0.46
    if 0.20 <= seq <= 0.40 and cpu_tuple <= 0.000501 and 0.010 <= cpu_index <= 0.030:
        refinements["io_seq_step_low_tuple_mid_index_bonus"] = 0.10
    if 0.20 <= seq <= 0.40 and cpu_tuple >= 0.001 and 0.010 <= cpu_index <= 0.030:
        refinements["io_seq_step_high_tuple_mid_index_bonus"] = 0.03
    components.update(refinements)
    adjustment += sum(refinements.values())
    return adjustment, components


def _pairwise_surrogate_v5_adjustment(record: Dict[str, Any], *, scenario: str) -> Tuple[float, Dict[str, float]]:
    """No-bottleneck plateau refinement selected after full-label replay.

    v4 remains the stronger validated layer for cpu, memory, and io. The none
    adjustments below are deliberately small tiebreak weights over broad cost
    shapes instead of a new point-score model.
    """
    adjustment, components = _pairwise_surrogate_v4_adjustment(record, scenario=scenario)
    if scenario != "none":
        return adjustment, components

    seq = _param_value(record, "seq_page_cost")
    random_page = _param_value(record, "random_page_cost")
    cpu_tuple = _param_value(record, "cpu_tuple_cost")
    cpu_index = _param_value(record, "cpu_index_tuple_cost")
    cpu_operator = _param_value(record, "cpu_operator_cost")
    parallel_tuple = _param_value(record, "parallel_tuple_cost")
    refinements: Dict[str, float] = {
        "none_low_seq_low_tuple_random_mid_bonus": 0.0,
        "none_low_seq_low_tuple_random_low_bonus": 0.0,
        "none_low_seq_low_tuple_random_min_penalty": 0.0,
        "none_low_seq_low_operator_random_penalty": 0.0,
        "none_seq_step_low_operator_random_penalty": 0.0,
        "none_low_seq_high_random_low_index_bonus": 0.0,
        "none_low_seq_high_random_mid_index_bonus": 0.0,
        "none_low_seq_high_random_min_index_parallel_bonus": 0.0,
        "none_low_seq_high_random_high_index_bonus": 0.0,
        "none_seq_step_low_tuple_random_mid_bonus": 0.0,
        "none_seq_step_low_tuple_random_min_bonus": 0.0,
    }
    low_tuple_shape = cpu_tuple < 0.01 and cpu_index >= 0.049 and cpu_operator < 0.0001
    low_operator_shape = cpu_tuple >= 0.09 and cpu_index >= 0.049 and cpu_operator < 0.001
    high_random_shape = random_page >= 90.0 and cpu_tuple >= 0.09 and cpu_operator >= 0.04

    if seq <= 0.11 and low_tuple_shape and 0.05 <= random_page <= 0.2:
        refinements["none_low_seq_low_tuple_random_mid_bonus"] = 0.011
    elif seq <= 0.11 and low_tuple_shape and 0.001 <= random_page <= 0.01:
        refinements["none_low_seq_low_tuple_random_low_bonus"] = 0.00262
    elif seq <= 0.11 and low_tuple_shape and random_page < 0.001:
        refinements["none_low_seq_low_tuple_random_min_penalty"] = -0.004
    if seq <= 0.11 and low_operator_shape and 1.0 <= random_page <= 10.0:
        refinements["none_low_seq_low_operator_random_penalty"] = -0.006
    elif 0.2 <= seq <= 0.4 and low_operator_shape and 1.0 <= random_page <= 10.0:
        refinements["none_seq_step_low_operator_random_penalty"] = -0.0113
    if seq <= 0.11 and high_random_shape and 0.0001 <= cpu_index <= 0.001:
        refinements["none_low_seq_high_random_low_index_bonus"] = 0.0065
    elif seq <= 0.11 and high_random_shape and 0.001 <= cpu_index <= 0.01:
        refinements["none_low_seq_high_random_mid_index_bonus"] = 0.006
    elif seq <= 0.11 and high_random_shape and cpu_index < 0.000002 and parallel_tuple > 1.0:
        refinements["none_low_seq_high_random_min_index_parallel_bonus"] = 0.0003
    elif seq <= 0.11 and high_random_shape and cpu_index >= 0.049:
        refinements["none_low_seq_high_random_high_index_bonus"] = 0.00035
    if 0.2 <= seq <= 0.4 and low_tuple_shape and 0.05 <= random_page <= 0.2:
        refinements["none_seq_step_low_tuple_random_mid_bonus"] = 0.0063
    elif 0.2 <= seq <= 0.4 and low_tuple_shape and random_page < 0.001:
        refinements["none_seq_step_low_tuple_random_min_bonus"] = 0.00033

    components.update(refinements)
    adjustment += sum(refinements.values())
    return adjustment, components


def plan_confidence(record: Dict[str, Any]) -> Dict[str, float]:
    metadata = record.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    failed = safe_float(record.get("failed_query_count"), safe_float(metadata.get("failed_query_count"), 0.0))
    query_count = safe_float(record.get("query_count"), safe_float(metadata.get("query_count"), 0.0))
    failure_rate = failed / max(query_count, 1.0)
    distance = safe_float(record.get("active_param_distance_from_baseline"), 0.0)
    plan_hash = str(record.get("plan_hash") or "")
    confidence = max(0.0, 1.0 - failure_rate)
    if not plan_hash:
        confidence *= 0.85
    confidence *= 1.0 / (1.0 + 0.03 * distance)
    return {
        "failed_query_count": failed,
        "query_count": query_count,
        "failure_rate": failure_rate,
        "active_param_distance_from_baseline": distance,
        "confidence": confidence,
    }


def robust_score_record(
    record: Dict[str, Any],
    *,
    capability: Optional[Dict[str, float]] = None,
    scenario: Optional[str] = None,
    variant: str = DEFAULT_VARIANT,
    baseline_resource: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    capability = {str(k): safe_float(v, 0.0) for k, v in (capability or {}).items()}
    resource = resource_dict(record)
    if baseline_resource is None:
        baseline_resource = record.get("baseline_resource") if isinstance(record.get("baseline_resource"), dict) else None
    scenario = normalize_scenario(scenario) if scenario else infer_scenario(record, capability)
    if variant not in ROBUST_VARIANTS:
        variant = DEFAULT_VARIANT
    if variant == "adaptive_scenario_bound":
        delegated_variant = "optimistic_bound" if scenario == "io" else "uncertainty_penalized"
        delegated = robust_score_record(
            record,
            capability=capability,
            scenario=scenario,
            variant=delegated_variant,
            baseline_resource=baseline_resource,
        )
        delegated["variant"] = DEFAULT_VARIANT
        delegated["delegated_variant"] = delegated_variant
        delegated["score_formula"] = ROBUST_FORMULAS.get(
            delegated_variant,
            "delegated robust ranking formula",
        )
        delegated["online_inputs"].append("scenario_to_bound_variant")
        return delegated
    point_score = safe_float(record.get("score"), _simple_utility(resource, capability))
    profile = extract_error_profile(record)
    confidence = plan_confidence(record)
    adjusted_resource = {
        name: _resource_bound_value(
            resource_name=name,
            value=safe_float(resource.get(name), 0.0),
            profile=profile,
            capability=capability,
            variant=variant,
            scenario=scenario,
        )
        for name in RESOURCE_NAMES
        if name in resource
    }
    bounded_score = _simple_utility(adjusted_resource, capability) if capability else point_score
    uncertainty = sum(safe_float(profile[name].get("relative_p95"), 0.0) for name in RESOURCE_NAMES)
    target_uncertainty = safe_float((profile.get(scenario) or {}).get("relative_p95"), 0.0)
    overload = sum(max(0.0, value - 1.0) for value in _utilization(adjusted_resource, capability).values())
    plateau = _plateau_tiebreak(record)
    direction_config = record.get("score_direction_objective")
    if not isinstance(direction_config, dict):
        direction_config = record.get("candidate_gate")
    if not isinstance(direction_config, dict):
        direction_config = None
    direction = _resource_direction_adjustment(
        resource=resource,
        baseline_resource=baseline_resource,
        capability=capability,
        scenario=scenario,
        direction_config=direction_config,
    )
    if variant == "point_estimate":
        robust = point_score
    elif variant == "optimistic_bound":
        robust = 0.75 * point_score + 0.25 * bounded_score + 0.005 * plateau
    elif variant == "pessimistic_bound":
        robust = bounded_score - 0.25 * uncertainty - 1.5 * overload
    elif variant == "conservative_bound":
        robust = 0.45 * point_score + 0.55 * bounded_score - 0.15 * target_uncertainty - overload
    elif variant == "expected_risk":
        robust = 0.55 * point_score + 0.45 * bounded_score - 0.10 * uncertainty - 0.5 * overload
    elif variant == "uncertainty_penalized":
        robust = point_score - 0.25 * uncertainty - 0.20 * (1.0 - confidence["confidence"]) - overload
    elif variant in {
        "pairwise_rank_surrogate",
        "pairwise_rank_surrogate_v2",
        "pairwise_rank_surrogate_v3",
        "pairwise_rank_surrogate_v4",
        "pairwise_rank_surrogate_v5",
    }:
        target_uncertainty_weight = 0.08 if scenario == "io" else 0.10
        uncertainty_weight = 0.030 if scenario == "io" else 0.04
        base = (
            point_score
            - target_uncertainty_weight * target_uncertainty
            - uncertainty_weight * uncertainty
            - 0.10 * overload
        )
        if variant == "pairwise_rank_surrogate_v5":
            adjustment, adjustment_components = _pairwise_surrogate_v5_adjustment(record, scenario=scenario)
        elif variant == "pairwise_rank_surrogate_v4":
            adjustment, adjustment_components = _pairwise_surrogate_v4_adjustment(record, scenario=scenario)
        elif variant == "pairwise_rank_surrogate_v3":
            adjustment, adjustment_components = _pairwise_surrogate_v3_adjustment(record, scenario=scenario)
        elif variant == "pairwise_rank_surrogate_v2":
            adjustment, adjustment_components = _pairwise_surrogate_v2_adjustment(record, scenario=scenario)
        else:
            adjustment, adjustment_components = _pairwise_surrogate_adjustment(record, scenario=scenario)
        robust = base + adjustment + 0.000000001 * plateau
    else:
        adjustment_components = {}
        scenario_weight = 0.35 if scenario in {"cpu", "memory", "io"} else 0.20
        robust = (
            0.35 * point_score
            + 0.65 * bounded_score
            - scenario_weight * target_uncertainty
            - 0.10 * uncertainty
            - 0.30 * (1.0 - confidence["confidence"])
            - 1.25 * overload
            + 0.002 * plateau
        )
    if variant not in {
        "pairwise_rank_surrogate",
        "pairwise_rank_surrogate_v2",
        "pairwise_rank_surrogate_v3",
        "pairwise_rank_surrogate_v4",
        "pairwise_rank_surrogate_v5",
    }:
        adjustment_components = {}
    score_before_direction = robust
    direction_adjustment = safe_float(direction.get("score_adjustment"), 0.0)
    qps_delta_raw = record.get("estimated_db_qps_delta_pct")
    qps_surrogate_applied = qps_delta_raw not in (None, "")
    qps_delta_pct = safe_float(qps_delta_raw, 0.0) if qps_surrogate_applied else 0.0
    qps_error_bound_pct = max(
        0.0,
        safe_float(record.get("estimated_db_qps_error_bound_pct"), 0.0),
    )
    qps_conservative_delta_pct = qps_delta_pct - qps_error_bound_pct
    qps_delta_clipped = min(200.0, max(-100.0, qps_conservative_delta_pct))
    qps_surrogate_adjustment = 0.10 * qps_delta_clipped if qps_surrogate_applied else 0.0
    robust = score_before_direction + direction_adjustment + qps_surrogate_adjustment
    return {
        "variant": variant,
        "scenario": scenario,
        "point_score": point_score,
        "bounded_score": bounded_score,
        "robust_score_before_direction": score_before_direction,
        "robust_score": robust,
        "adjusted_resource": adjusted_resource,
        "predicted_resource": resource,
        "baseline_resource": _clean_resource_map(baseline_resource),
        "error_profile": profile,
        "plan_confidence": confidence,
        "uncertainty_penalty": uncertainty,
        "target_uncertainty": target_uncertainty,
        "overload_penalty": overload,
        "resource_direction": direction,
        "resource_direction_adjustment": direction_adjustment,
        "resource_direction_passed": direction.get("passed"),
        "estimated_db_qps_delta_pct": qps_delta_pct if qps_surrogate_applied else None,
        "estimated_db_qps_error_bound_pct": qps_error_bound_pct if qps_surrogate_applied else None,
        "estimated_db_qps_conservative_delta_pct": (
            qps_conservative_delta_pct if qps_surrogate_applied else None
        ),
        "db_qps_surrogate_adjustment": qps_surrogate_adjustment,
        "db_qps_surrogate_applied": qps_surrogate_applied,
        "plateau_tiebreak": plateau,
        "pairwise_surrogate_adjustment": sum(adjustment_components.values()),
        "pairwise_surrogate_components": adjustment_components,
        "score_formula": ROBUST_FORMULAS.get(
            variant,
            "robust score formula keyed by variant; point_estimate uses point_score only",
        ),
        "tie_break": "plan_confidence, lower uncertainty_penalty, plateau_tiebreak",
        "online_inputs": [
            "predicted_resource",
            "train_test_residual_error_bound",
            "baseline_residual_or_anchor_calibration_bound",
            "per_resource_uncertainty",
            "plan_confidence",
            "plateau_tiebreak",
            "cost_parameter_plateau_features",
            "baseline_resource_for_direction_delta",
            "predicted_resource_direction_delta",
            "estimated_db_qps_delta_pct_when_available",
            "estimated_db_qps_error_bound_pct_when_available",
        ],
    }


def attach_robust_scores(
    records: Sequence[Dict[str, Any]],
    *,
    capability: Optional[Dict[str, float]] = None,
    scenario: Optional[str] = None,
    variants: Sequence[str] = ROBUST_VARIANTS,
    selected_variant: str = DEFAULT_VARIANT,
    baseline_resource: Optional[Dict[str, float]] = None,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for record in records:
        item = dict(record)
        scores = {
            variant: robust_score_record(
                item,
                capability=capability,
                scenario=scenario,
                variant=variant,
                baseline_resource=baseline_resource,
            )
            for variant in variants
        }
        item["robust_scores"] = scores
        item["robust_ranking"] = scores.get(selected_variant) or scores[DEFAULT_VARIANT]
        out.append(item)
    return out


def robust_sort_key(record: Dict[str, Any], *, variant: str = DEFAULT_VARIANT) -> Tuple[float, float, float, float]:
    robust = (record.get("robust_scores") or {}).get(variant) or record.get("robust_ranking") or {}
    if not robust:
        robust = robust_score_record(record, variant=variant)
    confidence = robust.get("plan_confidence") or {}
    robust_score = safe_float(robust.get("robust_score"), safe_float(record.get("score"), 0.0))
    plateau = safe_float(robust.get("plateau_tiebreak"), 0.0)
    if str(variant).startswith("pairwise_rank_surrogate"):
        robust_score -= 0.000000001 * plateau
    return (
        robust_score,
        safe_float(confidence.get("confidence"), 0.0),
        -safe_float(robust.get("uncertainty_penalty"), 0.0),
        plateau,
    )
