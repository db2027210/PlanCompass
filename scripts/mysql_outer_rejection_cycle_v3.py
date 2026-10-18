#!/usr/bin/env python3
"""Deterministic outer-loop rejection progress for MySQL Algorithms B/C.

Acceptance and search-domain progress are deliberately separate:

* only a technically valid point with QPS strictly above the incumbent may
  replace the incumbent or receive accepted-round credit;
* candidate prediction expansion and plan-tree expansion remain part of every
  unresolved outer round;
* two consecutive outer rounds without a higher-QPS point shrink the current
  search domain around the unchanged incumbent, without accepted-round credit;
* transformed width relative to the frozen initial domain is the terminal
  convergence measure.
"""

from __future__ import annotations

import copy
import math
from typing import Any, Mapping, Sequence


POLICY_ID = "mysql_c_pg_half_interval_two_reject_shrink_v3"
REJECTIONS_BEFORE_SHRINK = 2
TERMINAL_NORMALIZED_WIDTH = 0.01
MYSQL_FLOAT_RELATIVE_TOLERANCE = 6e-6
MYSQL_FLOAT_ABSOLUTE_TOLERANCE = 1e-8


def _finite_positive(value: Any, label: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{label} must be finite and positive")
    return result


def _bounds(specification: Mapping[str, Any], name: str) -> tuple[float, float]:
    lower = _finite_positive(specification.get("lower"), f"{name}.lower")
    upper = _finite_positive(specification.get("upper"), f"{name}.upper")
    if lower > upper:
        raise ValueError(f"{name} lower exceeds upper")
    return lower, upper


def shrink_ranges_around_incumbent(
    *,
    ranges: Mapping[str, Any],
    incumbent_params: Mapping[str, Any],
    active_params: Sequence[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Keep the transformed half-interval containing the incumbent.

    PostgreSQL Algorithm C performs a binary split at the transformed
    midpoint.  The second valid miss keeps the left or right half containing
    the unchanged incumbent; it does not center a sliding half-width window
    on that incumbent.
    """
    result = copy.deepcopy(dict(ranges))
    details: dict[str, Any] = {}
    for name in active_params:
        if name not in result or not isinstance(result[name], Mapping):
            raise ValueError(f"missing active range {name}")
        if name not in incumbent_params:
            raise ValueError(f"missing incumbent parameter {name}")
        lower, upper = _bounds(result[name], name)
        raw_anchor = _finite_positive(
            incumbent_params[name], f"{name}.incumbent",
        )
        anchor = raw_anchor
        range_clamped = False
        if anchor < lower:
            if not math.isclose(
                anchor, lower, rel_tol=MYSQL_FLOAT_RELATIVE_TOLERANCE,
                abs_tol=MYSQL_FLOAT_ABSOLUTE_TOLERANCE,
            ):
                raise ValueError(f"{name} incumbent lies outside current range")
            anchor = lower
            range_clamped = True
        elif anchor > upper:
            if not math.isclose(
                anchor, upper, rel_tol=MYSQL_FLOAT_RELATIVE_TOLERANCE,
                abs_tol=MYSQL_FLOAT_ABSOLUTE_TOLERANCE,
            ):
                raise ValueError(f"{name} incumbent lies outside current range")
            anchor = upper
            range_clamped = True

        log_lower = math.log(lower)
        log_upper = math.log(upper)
        old_span = log_upper - log_lower
        midpoint = log_lower + old_span / 2.0
        if math.log(anchor) <= midpoint:
            selected_half = "left"
            proposed_lower, proposed_upper = log_lower, midpoint
        else:
            selected_half = "right"
            proposed_lower, proposed_upper = midpoint, log_upper

        new_lower = math.exp(proposed_lower)
        new_upper = math.exp(proposed_upper)
        result[name] = copy.deepcopy(dict(result[name]))
        result[name]["lower"] = new_lower
        result[name]["upper"] = new_upper
        details[name] = {
            "old_lower": lower,
            "old_upper": upper,
            "incumbent": anchor,
            "incumbent_before_float_tolerance_clamp": raw_anchor,
            "incumbent_range_clamped": range_clamped,
            "mysql_float_relative_tolerance": MYSQL_FLOAT_RELATIVE_TOLERANCE,
            "mysql_float_absolute_tolerance": MYSQL_FLOAT_ABSOLUTE_TOLERANCE,
            "new_lower": new_lower,
            "new_upper": new_upper,
            "transformed_midpoint": math.exp(midpoint),
            "selected_half": selected_half,
            "log_width_ratio": 0.0 if old_span == 0 else (
                (proposed_upper - proposed_lower) / old_span
            ),
        }
    return result, details


def maximum_normalized_width(
    *,
    ranges: Mapping[str, Any],
    initial_ranges: Mapping[str, Any],
    active_params: Sequence[str],
) -> float:
    """Return the largest current/initial log-domain width ratio."""
    ratios: list[float] = []
    for name in active_params:
        current_lower, current_upper = _bounds(dict(ranges[name]), name)
        initial_lower, initial_upper = _bounds(
            dict(initial_ranges[name]), f"initial.{name}"
        )
        initial_span = math.log(initial_upper) - math.log(initial_lower)
        current_span = math.log(current_upper) - math.log(current_lower)
        if initial_span <= 0:
            ratios.append(0.0 if current_span <= 0 else math.inf)
        else:
            ratios.append(max(0.0, current_span / initial_span))
    return max(ratios, default=0.0)


def advance_rejection_cycle(
    *,
    accepted: bool,
    consecutive_rejections: int,
    ranges: Mapping[str, Any],
    incumbent_params: Mapping[str, Any],
    active_params: Sequence[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Advance one outer decision and optionally force an incumbent shrink."""
    prior = int(consecutive_rejections)
    if prior < 0:
        raise ValueError("consecutive_rejections must be non-negative")
    if accepted:
        return copy.deepcopy(dict(ranges)), {
            "policy_id": POLICY_ID,
            "accepted": True,
            "consecutive_rejections_before": prior,
            "consecutive_rejections_after": 0,
            "forced_incumbent_shrink": False,
            "accepted_round_credit": 1,
            "incumbent_changed_by_cycle": False,
        }

    after = prior + 1
    if after < REJECTIONS_BEFORE_SHRINK:
        return copy.deepcopy(dict(ranges)), {
            "policy_id": POLICY_ID,
            "accepted": False,
            "consecutive_rejections_before": prior,
            "consecutive_rejections_after": after,
            "forced_incumbent_shrink": False,
            "accepted_round_credit": 0,
            "next_outer_expansion": "predicted_points_and_plan_tree",
            "incumbent_changed_by_cycle": False,
        }

    updated, details = shrink_ranges_around_incumbent(
        ranges=ranges,
        incumbent_params=incumbent_params,
        active_params=active_params,
    )
    return updated, {
        "policy_id": POLICY_ID,
        "accepted": False,
        "consecutive_rejections_before": prior,
        "consecutive_rejections_after": 0,
        "forced_incumbent_shrink": True,
        "accepted_round_credit": 0,
        "next_outer_expansion": "predicted_points_and_plan_tree",
        "incumbent_changed_by_cycle": False,
        "range_changes": details,
    }


def convergence_document(
    *,
    ranges: Mapping[str, Any],
    initial_ranges: Mapping[str, Any],
    active_params: Sequence[str],
) -> dict[str, Any]:
    width = maximum_normalized_width(
        ranges=ranges,
        initial_ranges=initial_ranges,
        active_params=active_params,
    )
    return {
        "contract": POLICY_ID,
        "normalized_width": width,
        "terminal_normalized_width": TERMINAL_NORMALIZED_WIDTH,
        "converged": width <= TERMINAL_NORMALIZED_WIDTH,
        "accepted_rounds_are_not_inferred_from_forced_shrinks": True,
        "post_search_stability_confirmation": False,
    }


def policy_contract() -> dict[str, Any]:
    return {
        "policy_id": POLICY_ID,
        "incumbent_replacement": "technical_validity_and_db_qps_strictly_higher",
        "equal_qps_accepted": False,
        "lower_qps_accepted": False,
        "candidate_generation_per_unresolved_outer": [
            "predicted_point_expansion",
            "plan_tree_expansion",
        ],
        "consecutive_rejections_before_incumbent_centered_shrink": 2,
        "forced_shrink_changes_incumbent": False,
        "forced_shrink_grants_accepted_round_credit": False,
        "forced_shrink_log_width_ratio": 0.5,
        "forced_shrink_boundary_rule": (
            "keep_transformed_half_interval_containing_incumbent"
        ),
        "terminal_normalized_width": TERMINAL_NORMALIZED_WIDTH,
        "stability_confirmation_required": False,
        "mysql_float_boundary_tolerance": {
            "relative": MYSQL_FLOAT_RELATIVE_TOLERANCE,
            "absolute": MYSQL_FLOAT_ABSOLUTE_TOLERANCE,
            "only_near_boundary_values_are_clamped": True,
        },
    }
