"""Fail-open agreement gate between an Agent direction and resource model."""

from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Sequence


RESOURCE_NAMES = ("cpu", "memory", "io")
VALID_DIRECTIONS = {"decrease", "near_zero", "increase", "unknown"}


def model_interval_direction(
    interval: Mapping[str, Any],
    *,
    epsilon_pp: float = 0.05,
) -> str:
    """Classify a model delta interval without pretending a zero-crossing is directional."""
    try:
        low = float(interval["low"])
        p50 = float(interval["p50"])
        high = float(interval["high"])
    except (KeyError, TypeError, ValueError):
        return "unknown"
    if not all(math.isfinite(value) for value in (low, p50, high)) or not low <= p50 <= high:
        return "unknown"
    if high < -epsilon_pp:
        return "decrease"
    if low > epsilon_pp:
        return "increase"
    if low >= -epsilon_pp and high <= epsilon_pp:
        return "near_zero"
    return "unknown"


def evaluate_agent_model_direction_agreement(
    agent_prediction: Mapping[str, Any],
    model_delta_intervals: Mapping[str, Mapping[str, Any]],
    *,
    resources: Sequence[str] = RESOURCE_NAMES,
    min_agent_confidence: float = 0.70,
    epsilon_pp: float = 0.05,
) -> Dict[str, Any]:
    """Allow optimistic-interval pruning only when every resource agrees.

    This gate never computes Utility and never reads QPS. A disagreement,
    uncertain interval, low-confidence Agent answer, OOD plan, or malformed
    value fails open and leaves the candidate eligible for real replay.
    """
    if not 0.0 <= min_agent_confidence <= 1.0:
        raise ValueError("min_agent_confidence must be in [0,1]")
    requested = tuple(str(name) for name in resources)
    if not requested or any(name not in RESOURCE_NAMES for name in requested):
        raise ValueError("resources must be a non-empty CPU/Memory/IO subset")
    agent_directions = agent_prediction.get("direction") or {}
    agent_confidence = agent_prediction.get("confidence") or {}
    out_of_distribution = agent_prediction.get("out_of_distribution") is True
    checks = []
    for resource in requested:
        direction = str(agent_directions.get(resource) or "unknown")
        if direction not in VALID_DIRECTIONS:
            direction = "unknown"
        try:
            confidence = float(agent_confidence.get(resource) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        confidence_valid = math.isfinite(confidence) and 0.0 <= confidence <= 1.0
        model_direction = model_interval_direction(
            model_delta_intervals.get(resource) or {},
            epsilon_pp=epsilon_pp,
        )
        direction_known = direction != "unknown" and model_direction != "unknown"
        agreed = direction_known and direction == model_direction
        passed = bool(
            not out_of_distribution
            and confidence_valid
            and confidence >= min_agent_confidence
            and agreed
        )
        checks.append(
            {
                "resource": resource,
                "agent_direction": direction,
                "agent_confidence": confidence if confidence_valid else None,
                "model_direction": model_direction,
                "direction_agreed": agreed,
                "passed": passed,
            }
        )
    allowed = bool(checks and all(check["passed"] for check in checks))
    return {
        "optimistic_interval_pruning_allowed": allowed,
        "policy": "all_required_resource_directions_must_agree",
        "out_of_distribution": out_of_distribution,
        "min_agent_confidence": min_agent_confidence,
        "epsilon_pp": epsilon_pp,
        "checks": checks,
        "on_rejection": "fail_open_keep_candidate_for_recall_or_real_replay",
        "qps_used": False,
    }


__all__ = [
    "evaluate_agent_model_direction_agreement",
    "model_interval_direction",
]
