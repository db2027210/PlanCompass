#!/usr/bin/env python3
"""Deterministic, QPS-free diversity selection for Algorithm B Top-K."""

from __future__ import annotations

import math
from typing import Any, Callable, Mapping, Sequence, TypeVar


PointT = TypeVar("PointT")
DEFAULT_POOL_SIZE = 64
DEFAULT_SCORE_WINDOW = 0.05


def _normalized_log_distance(
    one: Mapping[str, float],
    two: Mapping[str, float],
    ranges: Mapping[str, Any],
    active: Sequence[str],
) -> float:
    components: list[float] = []
    for name in active:
        bounds = ranges[name]
        lower = float(bounds["lower"])
        upper = float(bounds["upper"])
        left = float(one[name])
        right = float(two[name])
        if min(lower, upper, left, right) <= 0.0:
            raise ValueError(f"positive log-space values required: {name}")
        width = math.log(upper / lower)
        components.append(
            0.0 if width <= 1e-15 else abs(math.log(left / right)) / width
        )
    return math.sqrt(sum(value * value for value in components) / len(components))


def select_diverse_points(
    ranked: Sequence[PointT],
    *,
    ranges: Mapping[str, Any],
    active: Sequence[str],
    top_k: int,
    negative_count: Callable[[PointT], int],
    pool_size: int = DEFAULT_POOL_SIZE,
    score_window: float = DEFAULT_SCORE_WINDOW,
) -> list[PointT]:
    """Select a score-qualified, parameter-diverse Top-K.

    The input order remains the resource-score order.  Soft-negative evidence
    is still lexicographically primary.  QPS is neither accepted nor read by
    this selector.
    """
    if top_k <= 0 or len(ranked) < top_k or not active:
        raise ValueError("valid Top-K cardinality and active parameters required")
    if pool_size < top_k or score_window < 0.0:
        raise ValueError("diversity pool configuration differs")

    ordered = list(ranked)
    minimum_negative = min(int(negative_count(point)) for point in ordered)
    clean = [
        point for point in ordered
        if int(negative_count(point)) == minimum_negative
    ]
    if len(clean) < top_k:
        clean = ordered
    pool = clean[:pool_size]
    best_score = float(getattr(pool[0], "estimated_score"))
    qualified = [
        point for point in pool
        if float(getattr(point, "estimated_score")) >= best_score - score_window
    ]
    if len(qualified) < top_k:
        qualified = pool

    selected = [qualified.pop(0)]
    while len(selected) < top_k:
        best_index = max(
            range(len(qualified)),
            key=lambda index: (
                min(
                    _normalized_log_distance(
                        getattr(qualified[index], "params"),
                        getattr(chosen, "params"),
                        ranges,
                        active,
                    )
                    for chosen in selected
                ),
                -index,
            ),
        )
        selected.append(qualified.pop(best_index))
    return selected


__all__ = [
    "DEFAULT_POOL_SIZE",
    "DEFAULT_SCORE_WINDOW",
    "select_diverse_points",
]
