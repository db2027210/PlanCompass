#!/usr/bin/env python3
"""Training-only parameter support domain for Algorithm C predictors."""

from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Sequence, Tuple

import numpy as np


PARAMETER_SUPPORT_CONTRACT = "algorithm_c_training_parameter_support_l2_v1"
ZERO_SCALE_TOLERANCE = 1e-9


def build_parameter_support_domain(
    matrix: np.ndarray,
    active_params: Sequence[str],
) -> Dict[str, Any]:
    """Build a leave-one-out support radius without holdout outcomes or QPS."""
    values = np.asarray(matrix, dtype=float)
    if (
        values.ndim != 2
        or values.shape[0] < 2
        or values.shape[1] != len(active_params)
        or not np.all(np.isfinite(values))
    ):
        raise ValueError("parameter support training matrix is invalid")
    scales = np.std(values, axis=0, ddof=0)
    varying = scales > ZERO_SCALE_TOLERANCE
    normalized = np.zeros_like(values)
    normalized[:, varying] = values[:, varying] / scales[varying]
    delta = normalized[:, None, :] - normalized[None, :, :]
    distances = np.sqrt(np.sum(np.square(delta), axis=2))
    np.fill_diagonal(distances, np.inf)
    nearest = np.min(distances, axis=1)
    threshold = float(np.max(nearest))
    if not math.isfinite(threshold):
        raise ValueError("parameter support threshold is not finite")
    return {
        "contract": PARAMETER_SUPPORT_CONTRACT,
        "active_params": list(active_params),
        "distance": "population_std_scaled_euclidean_l2",
        "population_std_ddof": 0,
        "zero_scale_tolerance": ZERO_SCALE_TOLERANCE,
        "zero_scale_policy": "same_value_zero_contribution_else_unsupported",
        "threshold_semantics": "maximum_training_leave_one_out_nearest_neighbor_distance",
        "support_predicate": "minimum_training_distance_le_threshold",
        "threshold": threshold,
        "scales": {
            name: float(value) for name, value in zip(active_params, scales)
        },
        "reference_points": values.tolist(),
        "training_row_count": int(values.shape[0]),
        "independent_holdout_used": False,
        "qps_used": False,
    }


def parameter_support_assessment(
    vector: Sequence[float],
    domain: Mapping[str, Any],
    active_params: Sequence[str],
) -> Dict[str, Any]:
    """Assess one baseline-relative log-parameter vector against a domain."""
    if str(domain.get("contract") or "") != PARAMETER_SUPPORT_CONTRACT:
        raise ValueError("parameter support contract mismatch")
    if list(domain.get("active_params") or []) != list(active_params):
        raise ValueError("parameter support active parameter mismatch")
    query = np.asarray(vector, dtype=float)
    reference = np.asarray(domain.get("reference_points"), dtype=float)
    if (
        query.shape != (len(active_params),)
        or reference.ndim != 2
        or reference.shape[0] < 2
        or reference.shape[1] != len(active_params)
        or not np.all(np.isfinite(query))
        or not np.all(np.isfinite(reference))
    ):
        raise ValueError("parameter support assessment arrays are invalid")
    scale_map = domain.get("scales")
    if not isinstance(scale_map, Mapping) or set(scale_map) != set(active_params):
        raise ValueError("parameter support scales are invalid")
    scales = np.asarray([float(scale_map[name]) for name in active_params], dtype=float)
    if not np.all(np.isfinite(scales)) or np.any(scales < 0.0):
        raise ValueError("parameter support scales contain invalid values")
    threshold = float(domain.get("threshold"))
    if not math.isfinite(threshold) or threshold < 0.0:
        raise ValueError("parameter support threshold is invalid")

    varying = scales > ZERO_SCALE_TOLERANCE
    constant = ~varying
    zero_scale_deviation = bool(
        np.any(np.abs(reference[:, constant] - query[constant]) > ZERO_SCALE_TOLERANCE)
    ) if np.any(constant) else False
    if zero_scale_deviation:
        minimum_distance = math.inf
    else:
        delta = np.zeros_like(reference)
        delta[:, varying] = (
            query[varying] - reference[:, varying]
        ) / scales[varying]
        minimum_distance = float(np.min(np.sqrt(np.sum(np.square(delta), axis=1))))
    supported = math.isfinite(minimum_distance) and minimum_distance <= threshold
    return {
        "contract": PARAMETER_SUPPORT_CONTRACT,
        "supported": supported,
        "support_abstained": not supported,
        "minimum_distance": minimum_distance,
        "threshold": threshold,
        "distance_to_threshold_ratio": (
            minimum_distance / threshold
            if math.isfinite(minimum_distance) and threshold > 0.0
            else (0.0 if minimum_distance == 0.0 else math.inf)
        ),
        "zero_scale_deviation": zero_scale_deviation,
        "qps_used": False,
    }


def apply_parameter_support_abstention(
    predictions: np.ndarray,
    queries: np.ndarray,
    domain: Mapping[str, Any],
    active_params: Sequence[str],
) -> Tuple[np.ndarray, list[Dict[str, Any]]]:
    """Set unsupported point predictions to neutral while preserving labels."""
    predicted = np.asarray(predictions, dtype=float).copy()
    matrix = np.asarray(queries, dtype=float)
    if predicted.shape != (matrix.shape[0],):
        raise ValueError("support abstention prediction arrays are incompatible")
    assessments = [
        parameter_support_assessment(row, domain, active_params)
        for row in matrix
    ]
    for index, assessment in enumerate(assessments):
        if assessment["support_abstained"]:
            predicted[index] = 0.0
    return predicted, assessments
