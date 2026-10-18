"""Shared prediction-error metrics for strict gradient Top-5 replay rounds."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence


RESOURCES = ("cpu", "memory", "io")


def _finite(value: Any, field: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} is not numeric: {value!r}") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{field} is not finite")
    return parsed


def _descending_average_ranks(values: Sequence[float]) -> List[float]:
    ordered = sorted(range(len(values)), key=lambda index: (-values[index], index))
    ranks = [0.0] * len(values)
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and values[ordered[end]] == values[ordered[start]]:
            end += 1
        average_rank = ((start + 1) + end) / 2.0
        for position in range(start, end):
            ranks[ordered[position]] = average_rank
        start = end
    return ranks


def _pearson(
    left: Sequence[float],
    right: Sequence[float],
) -> Optional[float]:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    left_centered = [value - left_mean for value in left]
    right_centered = [value - right_mean for value in right]
    denominator = math.sqrt(
        sum(value * value for value in left_centered)
        * sum(value * value for value in right_centered)
    )
    if denominator == 0.0:
        return None
    return sum(
        left_value * right_value
        for left_value, right_value in zip(left_centered, right_centered)
    ) / denominator


def build_top5_prediction_metrics(
    successful: Sequence[Mapping[str, Any]],
    capability: Mapping[str, Any],
) -> Dict[str, Any]:
    """Build deterministic resource/score errors and rank correlation."""

    normalized_capability = {
        resource: _finite(capability.get(resource), f"capability.{resource}")
        for resource in RESOURCES
    }
    if any(value <= 0.0 for value in normalized_capability.values()):
        raise ValueError("all resource capabilities must be positive")

    rows: List[Dict[str, Any]] = []
    seen_ranks = set()
    for raw in successful:
        rank = int(raw.get("rank"))
        if rank < 1 or rank > 5 or rank in seen_ranks:
            raise ValueError(f"successful replay rank is invalid or duplicate: {rank}")
        seen_ranks.add(rank)
        predicted_score = _finite(
            raw.get("predicted_score"),
            f"rank {rank} predicted_score",
        )
        actual_score = _finite(
            raw.get("actual_score"),
            f"rank {rank} actual_score",
        )
        predicted_resource = raw.get("predicted_resource") or {}
        actual_utilization = raw.get("actual_utilization") or {}
        actual_resource = raw.get("actual_resource") or {}
        resource_error_pp: Dict[str, float] = {}
        predicted_utilization: Dict[str, float] = {}
        normalized_actual_utilization: Dict[str, float] = {}
        for resource in RESOURCES:
            predicted_value = _finite(
                predicted_resource.get(resource),
                f"rank {rank} predicted_resource.{resource}",
            )
            if resource in actual_utilization:
                actual_util = _finite(
                    actual_utilization.get(resource),
                    f"rank {rank} actual_utilization.{resource}",
                )
            else:
                actual_value = _finite(
                    actual_resource.get(resource),
                    f"rank {rank} actual_resource.{resource}",
                )
                actual_util = actual_value / normalized_capability[resource]
            predicted_util = (
                predicted_value / normalized_capability[resource]
            )
            predicted_utilization[resource] = predicted_util
            normalized_actual_utilization[resource] = actual_util
            resource_error_pp[resource] = abs(
                predicted_util - actual_util
            ) * 100.0
        rows.append(
            {
                "rank": rank,
                "predicted_score": predicted_score,
                "actual_score": actual_score,
                "absolute_score_error": abs(predicted_score - actual_score),
                "predicted_utilization": predicted_utilization,
                "actual_utilization": normalized_actual_utilization,
                "resource_absolute_error_pp": resource_error_pp,
                "algorithmic_gate_rejection": bool(
                    raw.get("algorithmic_gate_rejection")
                ),
                "incumbent_update_eligible": bool(
                    raw.get("incumbent_update_eligible")
                ),
            }
        )
    rows.sort(key=lambda item: item["rank"])

    sample_count = len(rows)
    predicted_scores = [row["predicted_score"] for row in rows]
    actual_scores = [row["actual_score"] for row in rows]
    score_mae = (
        sum(row["absolute_score_error"] for row in rows) / sample_count
        if sample_count
        else None
    )
    resource_mae_pp = {
        resource: (
            sum(
                row["resource_absolute_error_pp"][resource]
                for row in rows
            )
            / sample_count
            if sample_count
            else None
        )
        for resource in RESOURCES
    }

    actual_best_ranks: List[int] = []
    if rows:
        best_actual_score = max(actual_scores)
        actual_best_ranks = [
            int(row["rank"])
            for row in rows
            if row["actual_score"] == best_actual_score
        ]
    predicted_ranks = [float(row["rank"]) for row in rows]
    actual_order_ranks = _descending_average_ranks(actual_scores)
    spearman = _pearson(predicted_ranks, actual_order_ranks)
    predicted_score_spread = (
        max(predicted_scores) - min(predicted_scores)
        if predicted_scores
        else None
    )
    actual_score_spread = (
        max(actual_scores) - min(actual_scores)
        if actual_scores
        else None
    )
    score_error_bound = (
        2.0 * score_mae if score_mae is not None else None
    )
    ranking_resolvable = bool(
        predicted_score_spread is not None
        and score_error_bound is not None
        and predicted_score_spread > score_error_bound
    )
    estimator_gate_passed = bool(
        sample_count == 5
        and score_mae is not None
        and score_mae <= 0.05
        and resource_mae_pp["cpu"] is not None
        and resource_mae_pp["cpu"] <= 5.0
        and resource_mae_pp["io"] is not None
        and resource_mae_pp["io"] <= 5.0
        and resource_mae_pp["memory"] is not None
        and resource_mae_pp["memory"] <= 2.0
    )
    ranking_gate_passed = bool(
        ranking_resolvable
        and spearman is not None
        and spearman >= 0.5
        and actual_best_ranks
        and min(actual_best_ranks) <= 2
    )
    uncertainty_equivalent_ranks = []
    if (
        not ranking_resolvable
        and predicted_scores
        and score_error_bound is not None
    ):
        predicted_best = max(predicted_scores)
        uncertainty_equivalent_ranks = [
            int(row["rank"])
            for row in rows
            if predicted_best - row["predicted_score"] <= score_error_bound
        ]
    uncertainty_tie_gate_passed = bool(
        not ranking_resolvable
        and sample_count == 5
        and actual_best_ranks
        and min(actual_best_ranks) <= 2
        and any(
            rank in uncertainty_equivalent_ranks
            for rank in actual_best_ranks
        )
    )
    ranking_or_tie_passed = (
        ranking_gate_passed
        if ranking_resolvable
        else uncertainty_tie_gate_passed
    )

    return {
        "contract": "top5_real_replay_prediction_error_v1",
        "sample_count": sample_count,
        "complete_top5": [row["rank"] for row in rows] == [1, 2, 3, 4, 5],
        "score_mae": score_mae,
        "resource_mae_pp": resource_mae_pp,
        "actual_best_predicted_rank": (
            min(actual_best_ranks) if actual_best_ranks else None
        ),
        "actual_best_predicted_rank_ties": actual_best_ranks,
        "spearman": {
            "defined": spearman is not None,
            "value": spearman,
            "reason": (
                None
                if spearman is not None
                else (
                    "fewer_than_two_successful_replays"
                    if sample_count < 2
                    else "actual_scores_have_zero_rank_variance"
                )
            ),
        },
        "predicted_score_spread": predicted_score_spread,
        "actual_score_spread": actual_score_spread,
        "uncertainty": {
            "score_mae_multiplier": 2.0,
            "score_error_bound": score_error_bound,
            "ranking_resolvable": ranking_resolvable,
            "mode": (
                "ordered"
                if ranking_resolvable
                else "uncertainty_tie"
            ),
            "equivalent_predicted_ranks": uncertainty_equivalent_ranks,
            "tie_gate_passed": uncertainty_tie_gate_passed,
        },
        "readiness_gate": {
            "estimator_passed": estimator_gate_passed,
            "ranking_passed": ranking_gate_passed,
            "uncertainty_tie_passed": uncertainty_tie_gate_passed,
            "ranking_or_tie_passed": ranking_or_tie_passed,
            "passed": estimator_gate_passed and ranking_or_tie_passed,
            "thresholds": {
                "score_mae_max": 0.05,
                "cpu_mae_pp_max": 5.0,
                "io_mae_pp_max": 5.0,
                "memory_mae_pp_max": 2.0,
                "spearman_min_when_resolvable": 0.5,
                "actual_best_predicted_rank_max": 2,
                "uncertainty_tie_requires_distinct_top5": True,
            },
        },
        "by_rank": rows,
    }


__all__ = ["build_top5_prediction_metrics"]
