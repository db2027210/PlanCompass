"""Fail-closed, deterministic selection for a partially spent replay budget.

This module is deliberately offline-only.  It does not know how to start a
workload; it only selects already sealed candidates for the serial runner.
"""

from __future__ import annotations

import copy
import json
import math
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Set, Tuple


RESOURCES = ("cpu", "io", "memory")
FINGERPRINT_LEVELS = ("plan_level", "feature_ratio_level", "predicted_resource_level")


def _finite(value: Any, label: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("{} must be finite".format(label))
    return result


def _score(candidate: Mapping[str, Any]) -> float:
    for key in ("candidate_score", "final_score", "score"):
        if candidate.get(key) is not None:
            return _finite(candidate[key], key)
    raise ValueError("candidate has no score")


def _rank(candidate: Mapping[str, Any]) -> int:
    value = candidate.get("rank")
    if value is None:
        value = candidate.get("source_predicted_rank")
    if value is None:
        raise ValueError("candidate has no original predicted rank")
    parsed = int(value)
    if parsed < 1:
        raise ValueError("candidate rank must be positive")
    return parsed


def _resource_map(candidate: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = candidate.get(key)
    if not isinstance(value, Mapping):
        metadata = candidate.get("final_estimation_metadata") or {}
        aliases = {
            "feature_ratio": (
                "feature_ratio_unclipped",
                "plan_signature_calibrated_ratios_unclipped",
                "anchored_feature_ratios_unclipped",
                "feature_ratios_unclipped",
            ),
            "predicted_resource": ("predicted_resources",),
        }
        for alias in aliases.get(key, ()):
            if isinstance(metadata.get(alias), Mapping):
                return metadata[alias]
        return {}
    return value


def _fingerprint_levels(candidate: Mapping[str, Any]) -> Tuple[str, str, str]:
    fingerprint = candidate.get("three_level_fingerprint")
    if not isinstance(fingerprint, Mapping):
        raise ValueError("candidate is missing three_level_fingerprint")
    if fingerprint.get("valid") is not True:
        raise ValueError("candidate has an invalid three_level_fingerprint")
    values = []
    for key in FINGERPRINT_LEVELS:
        if key not in fingerprint:
            raise ValueError("candidate fingerprint lacks {}".format(key))
        values.append(json.dumps(fingerprint[key], sort_keys=True, separators=(",", ":")))
    fingerprint_id = fingerprint.get("id")
    if not isinstance(fingerprint_id, str) or not fingerprint_id:
        raise ValueError("candidate fingerprint id is missing")
    return tuple(values)  # type: ignore[return-value]


def _parameter_names(candidates: Sequence[Mapping[str, Any]]) -> Tuple[str, ...]:
    names: Set[str] = set()
    for candidate in candidates:
        params = candidate.get("candidate_params") or candidate.get("optimized_params")
        if not isinstance(params, Mapping) or not params:
            raise ValueError("candidate parameters are missing")
        names.update(str(name) for name in params)
    return tuple(sorted(names))


def _numeric_vector(
    candidate: Mapping[str, Any],
    parameter_names: Sequence[str],
) -> List[float]:
    vector: List[float] = []
    for key in ("feature_ratio", "predicted_resource"):
        resource_map = _resource_map(candidate, key)
        for resource in RESOURCES:
            value = _finite(resource_map.get(resource), "{}.{}".format(key, resource))
            if value < 0.0:
                raise ValueError("{}.{} must be non-negative".format(key, resource))
            vector.append(value)
    params = candidate.get("candidate_params") or candidate.get("optimized_params")
    for name in parameter_names:
        value = _finite(params.get(name), "parameter {}".format(name))
        if value < 0.0:
            raise ValueError("parameter {} must be non-negative".format(name))
        vector.append(math.log1p(value))
    return vector


def _normalize(vectors: Sequence[Sequence[float]]) -> List[List[float]]:
    width = len(vectors[0])
    if any(len(vector) != width for vector in vectors):
        raise ValueError("candidate vectors have inconsistent dimensions")
    lows = [min(vector[index] for vector in vectors) for index in range(width)]
    highs = [max(vector[index] for vector in vectors) for index in range(width)]
    return [
        [
            0.0
            if highs[index] - lows[index] <= 1e-12
            else (vector[index] - lows[index]) / (highs[index] - lows[index])
            for index in range(width)
        ]
        for vector in vectors
    ]


def _distance(
    left_vector: Sequence[float],
    right_vector: Sequence[float],
    left_levels: Sequence[str],
    right_levels: Sequence[str],
) -> float:
    numeric = math.sqrt(
        sum((left - right) ** 2 for left, right in zip(left_vector, right_vector))
    )
    categorical = sum(left != right for left, right in zip(left_levels, right_levels)) / 3.0
    return numeric + categorical


def select_remaining_candidates(
    candidates: Iterable[Mapping[str, Any]],
    remaining_physical_budget: int,
    invoked_ranks: Iterable[int] = (),
) -> Dict[str, Any]:
    """Select at most the remaining physical calls from unexecuted rank2-5.

    The original ``rank`` is retained on every returned candidate.  The
    function fails closed on malformed evidence or duplicate rank/fingerprint
    identity because silently guessing would make the budget audit invalid.
    """
    if not isinstance(remaining_physical_budget, int) or remaining_physical_budget < 0:
        raise ValueError("remaining_physical_budget must be a non-negative integer")

    invoked = {int(rank) for rank in invoked_ranks}
    sealed = [copy.deepcopy(candidate) for candidate in candidates]
    if not sealed:
        return {
            "selection_contract": "tpcc_remaining_budget_maximin_v1",
            "remaining_physical_budget": remaining_physical_budget,
            "available_ranks": [],
            "selected_ranks": [],
            "selected_candidates": [],
        }

    by_rank: Dict[int, Mapping[str, Any]] = {}
    for candidate in sealed:
        rank = _rank(candidate)
        if rank in by_rank:
            raise ValueError("duplicate candidate rank {}".format(rank))
        by_rank[rank] = candidate
        _score(candidate)
        _fingerprint_levels(candidate)

    # Rank 1 is already accounted for by the required zero-call reclassification.
    available = [
        candidate
        for rank, candidate in sorted(by_rank.items())
        if 2 <= rank <= 5 and rank not in invoked
    ]
    if not available or remaining_physical_budget == 0:
        return {
            "selection_contract": "tpcc_remaining_budget_maximin_v1",
            "remaining_physical_budget": remaining_physical_budget,
            "available_ranks": [_rank(candidate) for candidate in available],
            "selected_ranks": [],
            "selected_candidates": [],
        }

    limit = min(remaining_physical_budget, len(available))
    parameter_names = _parameter_names(available)
    vectors = _normalize([_numeric_vector(candidate, parameter_names) for candidate in available])
    levels = [_fingerprint_levels(candidate) for candidate in available]
    # Seed with the highest predicted score; ties resolve to the original rank.
    selected = [
        min(
            range(len(available)),
            key=lambda index: (-_score(available[index]), _rank(available[index])),
        )
    ]
    while len(selected) < limit:
        remaining = [index for index in range(len(available)) if index not in selected]
        next_index = max(
            remaining,
            key=lambda index: (
                min(
                    _distance(vectors[index], vectors[chosen], levels[index], levels[chosen])
                    for chosen in selected
                ),
                _score(available[index]),
                -_rank(available[index]),
            ),
        )
        selected.append(next_index)

    output = []
    for order, index in enumerate(selected, 1):
        candidate = available[index]
        candidate["source_predicted_rank"] = _rank(candidate)
        candidate["remaining_budget_selection_order"] = order
        candidate["selection_reason"] = (
            "highest_predicted_score_seed"
            if order == 1
            else "maximin_feature_resource_log_parameter_three_level_fingerprint"
        )
        output.append(candidate)
    return {
        "selection_contract": "tpcc_remaining_budget_maximin_v1",
        "remaining_physical_budget": remaining_physical_budget,
        "available_ranks": [_rank(candidate) for candidate in available],
        "selected_ranks": [_rank(candidate) for candidate in output],
        "selected_candidates": output,
    }

