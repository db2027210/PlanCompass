#!/usr/bin/env python3
"""Construct a score-consistent robust TPC-C resource representative."""

from __future__ import annotations

import math
import statistics
from typing import Any, Callable, Dict, Mapping, Sequence, Tuple


RESOURCES = ("cpu", "io", "memory")


def _resource(row: Mapping[str, Any]) -> Dict[str, float]:
    value = {name: float(row[name]) for name in RESOURCES}
    if any(not math.isfinite(number) for number in value.values()):
        raise RuntimeError("resource history contains a non-finite value")
    return value


def _interpolate(
    left: Mapping[str, float],
    right: Mapping[str, float],
    fraction: float,
) -> Dict[str, float]:
    return {
        name: float(left[name])
        + fraction * (float(right[name]) - float(left[name]))
        for name in RESOURCES
    }


def score_consistent_representative(
    scores: Sequence[float],
    resources: Sequence[Mapping[str, Any]],
    score_resource: Callable[[Mapping[str, Any]], float],
    *,
    score_tolerance: float = 1e-13,
    maximum_bisection_steps: int = 100,
) -> Tuple[Dict[str, float], float, Dict[str, Any]]:
    """Return a robust resource vector whose utility equals median utility.

    Component-wise resource medians are not score-consistent for a nonlinear
    utility.  For an odd observation count, the resource vector attached to
    the median observed score is already the desired representative.  For an
    even count, deterministically bisect the segment between the two central
    score observations.  Continuity and the bracketing endpoint scores make
    the result reproducible without fitting an extra scalar calibration.
    """
    numeric_scores = [float(value) for value in scores]
    numeric_resources = [_resource(row) for row in resources]
    if (
        not numeric_scores
        or len(numeric_scores) != len(numeric_resources)
        or any(not math.isfinite(value) for value in numeric_scores)
    ):
        raise RuntimeError("score/resource history is invalid")

    ordered = sorted(
        zip(numeric_scores, numeric_resources),
        key=lambda item: (
            item[0],
            tuple(item[1][name] for name in RESOURCES),
        ),
    )
    target = float(statistics.median(numeric_scores))
    count = len(ordered)
    if count % 2 == 1:
        representative = dict(ordered[count // 2][1])
        recomputed = float(score_resource(representative))
        if not math.isclose(
            recomputed,
            target,
            rel_tol=0.0,
            abs_tol=score_tolerance,
        ):
            raise RuntimeError(
                "median-score observation is not resource-score consistent: "
                f"target={target} recomputed={recomputed}"
            )
        return representative, target, {
            "method": "observed_median_score_resource_v1",
            "observation_count": count,
            "target_score": target,
            "recomputed_score": recomputed,
            "bisection_steps": 0,
        }

    left_score, left_resource = ordered[count // 2 - 1]
    right_score, right_resource = ordered[count // 2]
    left_value = float(score_resource(left_resource)) - target
    right_value = float(score_resource(right_resource)) - target
    if abs(left_value) <= score_tolerance:
        return dict(left_resource), target, {
            "method": "even_center_endpoint_v1",
            "observation_count": count,
            "target_score": target,
            "recomputed_score": left_value + target,
            "bisection_steps": 0,
        }
    if abs(right_value) <= score_tolerance:
        return dict(right_resource), target, {
            "method": "even_center_endpoint_v1",
            "observation_count": count,
            "target_score": target,
            "recomputed_score": right_value + target,
            "bisection_steps": 0,
        }
    if left_value * right_value > 0.0:
        raise RuntimeError(
            "central score resources do not bracket the robust score: "
            f"observed=({left_score}, {right_score}) "
            f"recomputed_delta=({left_value}, {right_value})"
        )

    low_fraction = 0.0
    high_fraction = 1.0
    low_value = left_value
    representative = dict(left_resource)
    recomputed = left_value + target
    steps = 0
    for steps in range(1, maximum_bisection_steps + 1):
        fraction = (low_fraction + high_fraction) / 2.0
        candidate = _interpolate(left_resource, right_resource, fraction)
        candidate_score = float(score_resource(candidate))
        value = candidate_score - target
        representative = candidate
        recomputed = candidate_score
        if abs(value) <= score_tolerance:
            break
        if low_value * value <= 0.0:
            high_fraction = fraction
        else:
            low_fraction = fraction
            low_value = value
    if not math.isclose(
        recomputed,
        target,
        rel_tol=0.0,
        abs_tol=score_tolerance,
    ):
        raise RuntimeError(
            "score-consistent resource bisection did not converge: "
            f"target={target} recomputed={recomputed}"
        )
    return representative, target, {
        "method": "central_observation_segment_bisection_v1",
        "observation_count": count,
        "target_score": target,
        "recomputed_score": recomputed,
        "bisection_steps": steps,
    }
