"""Model-error and Top-15 ordering gates shared by MySQL test workflows."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Sequence


def _percentile(values: Sequence[float], quantile: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return float("nan")
    position = (len(ordered) - 1) * quantile
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def regression_metrics(actual: Sequence[float], predicted: Sequence[float]) -> Dict[str, float]:
    if len(actual) != len(predicted) or not actual:
        raise ValueError("actual and predicted must be non-empty and aligned")
    errors = [abs(float(left) - float(right)) for left, right in zip(actual, predicted)]
    relative = [error / max(abs(float(value)), 1e-9) for error, value in zip(errors, actual)]
    mean_actual = sum(float(value) for value in actual) / len(actual)
    residual = sum((float(left) - float(right)) ** 2 for left, right in zip(actual, predicted))
    total = sum((float(value) - mean_actual) ** 2 for value in actual)
    r2 = 1.0 - residual / total if total > 1e-12 else (1.0 if residual <= 1e-12 else 0.0)
    return {
        "sample_count": float(len(actual)),
        "mae": sum(errors) / len(errors),
        "rmse": math.sqrt(residual / len(actual)),
        "relative_error_p95": _percentile(relative, 0.95),
        "relative_error_max": max(relative),
        "r2": r2,
    }


def pairwise_inversion_rate(
    predicted_scores: Sequence[float],
    actual_scores: Sequence[float],
    *,
    tie_margin: float = 0.01,
) -> Dict[str, Any]:
    if len(predicted_scores) != len(actual_scores):
        raise ValueError("predicted and actual score arrays must be aligned")
    inversions = 0
    pairs = 0
    ties = 0
    for left in range(len(predicted_scores)):
        for right in range(left + 1, len(predicted_scores)):
            predicted_delta = float(predicted_scores[left]) - float(predicted_scores[right])
            actual_delta = float(actual_scores[left]) - float(actual_scores[right])
            if abs(predicted_delta) <= tie_margin or abs(actual_delta) <= tie_margin:
                ties += 1
                continue
            pairs += 1
            if predicted_delta * actual_delta < 0:
                inversions += 1
    return {
        "inversions": inversions,
        "comparable_pairs": pairs,
        "ties": ties,
        "inversion_rate": inversions / pairs if pairs else None,
    }


def evaluate_top15_gate(
    rows: Sequence[Mapping[str, Any]],
    thresholds: Mapping[str, Any],
) -> Dict[str, Any]:
    required = max(15, int(thresholds.get("min_samples", 15)))
    if len(rows) < required:
        return {
            "passed": False,
            "reason": "insufficient_current_run_samples",
            "sample_count": len(rows),
            "required_sample_count": required,
        }
    selected = list(rows[:required])
    resource_metrics: Dict[str, Any] = {}
    resource_passed = True
    for resource in ("cpu", "memory", "io"):
        metrics = regression_metrics(
            [float(row["actual_resource"][resource]) for row in selected],
            [float(row["predicted_resource"][resource]) for row in selected],
        )
        checks = {
            "relative_error_p95": metrics["relative_error_p95"]
            <= float(thresholds.get("max_relative_error_p95", 1.0)),
            "relative_error_max": metrics["relative_error_max"]
            <= float(thresholds.get("max_relative_error_max", 5.0)),
            "r2": metrics["r2"] >= float(thresholds.get("min_r2", 0.0)),
        }
        metrics["checks"] = checks
        metrics["passed"] = all(checks.values())
        resource_passed = resource_passed and bool(metrics["passed"])
        resource_metrics[resource] = metrics
    predicted_scores = [float(row["predicted_score"]) for row in selected]
    actual_scores = [float(row["actual_score"]) for row in selected]
    tie_margin = float(thresholds.get("score_tie_margin", 0.01))
    ordering = pairwise_inversion_rate(predicted_scores, actual_scores, tie_margin=tie_margin)
    ordering["threshold"] = float(thresholds.get("max_pairwise_inversion_rate", 0.10))
    ordering["passed"] = (
        ordering["inversion_rate"] is not None
        and ordering["inversion_rate"] <= ordering["threshold"]
    )
    prefixes: Dict[str, Any] = {}
    predicted_order = sorted(range(len(selected)), key=lambda index: (-predicted_scores[index], index))
    for prefix_size in (10, 15):
        indices = predicted_order[:prefix_size]
        prefix = pairwise_inversion_rate(
            [predicted_scores[index] for index in indices],
            [actual_scores[index] for index in indices],
            tie_margin=tie_margin,
        )
        prefix["required_count"] = prefix_size
        prefix["actual_count"] = len(indices)
        prefix["threshold"] = ordering["threshold"]
        prefix["passed"] = (
            len(indices) == prefix_size
            and prefix["inversion_rate"] is not None
            and prefix["inversion_rate"] <= prefix["threshold"]
        )
        prefixes[f"top{prefix_size}"] = prefix
    ordering["prefixes"] = prefixes
    ranking_passed = bool(ordering["passed"]) and all(
        bool(prefixes[name]["passed"]) for name in ("top10", "top15")
    )
    return {
        "passed": resource_passed and ranking_passed,
        "sample_count": len(selected),
        "training_strategy": "current_mysql_top15",
        "resource_metrics": resource_metrics,
        "top15_ordering": ordering,
    }
