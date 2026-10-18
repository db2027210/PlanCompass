#!/usr/bin/env python3
"""Algorithm C offline-training and runtime-prior separation contracts."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence, Tuple


TRAINING_BOUNDARY_CONTRACT = "algorithm_c_offline_resource_training_boundary_v1"
RUNTIME_PRIOR_FIREWALL_CONTRACT = "algorithm_c_runtime_candidate_prior_firewall_v1"
ALLOWED_TRAINING_SOURCE_ALGORITHMS = (
    "algorithm_a",
    "algorithm_b",
    "algorithm_c",
)


def infer_training_source_algorithm(path: Path) -> str:
    """Infer provenance for audit only; the value must never become a feature."""

    lowered = str(path).lower()
    matches = [
        algorithm
        for algorithm in ALLOWED_TRAINING_SOURCE_ALGORITHMS
        if algorithm in lowered
    ]
    if len(matches) != 1:
        raise RuntimeError(
            "training history source algorithm is ambiguous; use an A/B/C "
            f"authority path: {path}"
        )
    return matches[0]


def parameter_interaction_names(
    active_params: Sequence[str],
) -> Tuple[Tuple[str, str], ...]:
    """Return deterministic second-order interaction names without squares."""

    normalized = tuple(str(name) for name in active_params)
    return tuple(
        (left, right)
        for position, left in enumerate(normalized)
        for right in normalized[position + 1 :]
    )


def runtime_prior_firewall() -> Dict[str, Any]:
    """Describe the only information that a formal C run may consume."""

    return {
        "contract": RUNTIME_PRIOR_FIREWALL_CONTRACT,
        "allowed_runtime_candidate_inputs": [
            "current_incumbent_params",
            "current_retained_parameter_ranges",
            "current_full_workload_explain",
            "frozen_resource_prediction_model",
            "algorithm_c_same_campaign_runtime_state",
        ],
        "training_source_parameters_used_as_runtime_candidates": False,
        "training_source_plan_fingerprints_used_as_runtime_candidates": False,
        "training_source_rankings_used_as_runtime_candidates": False,
        "training_source_accept_reject_used_as_runtime_candidates": False,
        "source_algorithm_used_as_model_feature": False,
        "qps_used_as_model_feature_or_target": False,
        "qps_used_for_gradient": False,
        "qps_used_for_candidate_score": False,
        "qps_used_for_candidate_ranking": False,
        "qps_role": "outer_real_replay_acceptance_only",
        "algorithm_a_runtime_path_required": False,
        "algorithm_b_runtime_path_required": False,
    }


def training_boundary(source_counts: Mapping[str, int]) -> Dict[str, Any]:
    """Build immutable provenance while excluding provenance from features."""

    normalized_counts = {
        algorithm: int(source_counts.get(algorithm) or 0)
        for algorithm in ALLOWED_TRAINING_SOURCE_ALGORITHMS
    }
    return {
        "contract": TRAINING_BOUNDARY_CONTRACT,
        "allowed_training_source_algorithms": list(
            ALLOWED_TRAINING_SOURCE_ALGORITHMS
        ),
        "observed_training_source_counts": normalized_counts,
        "source_algorithm_is_audit_metadata_only": True,
        "source_algorithm_used_as_model_feature": False,
        "historical_parameter_identity_used_as_model_feature": False,
        "historical_plan_fingerprint_used_as_model_feature": False,
        "plan_fingerprint_role": "grouped_split_and_deduplication_only",
        "accept_reject_labels_used_for_resource_model": False,
        "qps_present_in_resource_training_rows": False,
        "qps_used_as_model_feature_or_target": False,
        "technical_failures_excluded": True,
        "workload_execution_domain_must_match_or_be_holdout_validated": True,
        "target_runtime_domain_independent_holdout_required": True,
    }


def validate_training_runtime_boundary(artifact: Mapping[str, Any]) -> None:
    """Fail closed if a resource artifact can leak search answers into C."""

    boundary = artifact.get("training_boundary")
    if not isinstance(boundary, Mapping):
        raise RuntimeError("experience artifact training boundary missing")
    if boundary.get("contract") != TRAINING_BOUNDARY_CONTRACT:
        raise RuntimeError("experience artifact training boundary mismatch")
    required_false = (
        "source_algorithm_used_as_model_feature",
        "historical_parameter_identity_used_as_model_feature",
        "historical_plan_fingerprint_used_as_model_feature",
        "accept_reject_labels_used_for_resource_model",
        "qps_present_in_resource_training_rows",
        "qps_used_as_model_feature_or_target",
    )
    for name in required_false:
        if boundary.get(name) is not False:
            raise RuntimeError(f"experience artifact leaks forbidden input: {name}")

    firewall = artifact.get("runtime_candidate_prior_firewall")
    if not isinstance(firewall, Mapping):
        raise RuntimeError("experience artifact runtime prior firewall missing")
    if dict(firewall) != runtime_prior_firewall():
        raise RuntimeError("experience artifact runtime prior firewall mismatch")

    calibration = artifact.get("resource_prediction_calibration")
    if not isinstance(calibration, Mapping):
        raise RuntimeError("experience artifact resource calibration missing")
    if calibration.get("qps_used") is not False:
        raise RuntimeError("resource calibration must exclude QPS")
    resources = calibration.get("resources")
    if not isinstance(resources, Mapping) or set(resources) != {
        "cpu",
        "memory",
        "io",
    }:
        raise RuntimeError("resource calibration may contain only CPU/Memory/I/O")

    holdout = artifact.get("holdout")
    metrics = holdout.get("metrics") if isinstance(holdout, Mapping) else None
    if not isinstance(metrics, Mapping) or set(metrics) != {
        "cpu",
        "memory",
        "io",
    }:
        raise RuntimeError("holdout metrics may contain only CPU/Memory/I/O")


def artifact_without_training_provenance(
    artifact: Mapping[str, Any],
) -> Dict[str, Any]:
    """Return the runtime-relevant payload for no-prior determinism tests."""

    result = copy.deepcopy(dict(artifact))
    result.pop("training_boundary", None)
    fit = result.get("fit")
    if isinstance(fit, dict):
        source_audit = fit.get("source_audit")
        if isinstance(source_audit, dict):
            source_audit.pop("training_source_algorithms", None)
            source_audit.pop("training_source_counts", None)
    return result
