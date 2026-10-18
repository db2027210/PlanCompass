#!/usr/bin/env python3
"""Fresh current-workload T calibration followed by one Algorithm C lane.

Calibration is isolated from formal optimizer state.  Each physical batch has
at most five invocations, technical failures consume slots but are excluded
from train/holdout, and every batch restores both workers to the baseline
template and baseline GUCs before continuing.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
import signal
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from binary_plan_resource_tuner import (  # noqa: E402
    PlanFeatureProbe,
    acquire_configured_shared_ssd_workload_coordination,
    apply_pg_params,
    plan_diff,
    plan_signature_set_fingerprint,
    pg_conn_from_worker,
    restore_worker_from_hybench_template,
    release_shared_ssd_coordination,
    run_real_validation_safe,
    validation_workers,
    worker_with_prepared_restore,
    workload_queries_with_sampling,
)
from algorithm_c_calibration_release import (  # noqa: E402
    DEFAULT_RELEASE_ROOT,
    find_compatible_calibration_release,
    materialize_calibration_release,
    publish_calibration_release,
)
from build_algorithm_c_experience_table_from_replays import (  # noqa: E402
    load_samples,
    robust_target_down,
)
from algorithm_c_parameter_support import (  # noqa: E402
    parameter_support_assessment,
)
from run_pg_algorithm_c_scene import (  # type: ignore  # noqa: E402
    ACTIVE_PARAMS,
    DEFAULT_RANGES,
    LaneController,
    RESOURCE_PREDICTION_CALIBRATION_CONTRACT,
    append_jsonl,
    apply_ssd_wave_qualification,
    baseline_parameter_readback_audit,
    canonical_sha256,
    formal_inner_scoring_options,
    physical_valid,
    read_json,
    validate_experience_artifact,
    worker_key,
    write_json,
)


CALIBRATION_SIGN_MATRIX: Tuple[Tuple[int, ...], ...] = (
    (+1, +1, +1, +1, +1, +1, +1),
    (+1, +1, -1, +1, -1, -1, -1),
    (-1, +1, +1, -1, +1, -1, -1),
    (-1, +1, -1, -1, -1, +1, +1),
    (+1, -1, -1, -1, +1, +1, -1),
    (+1, -1, +1, -1, -1, -1, +1),
    (-1, -1, -1, +1, +1, -1, +1),
    (-1, -1, +1, +1, -1, +1, -1),
    (+1, +1, +1, +1, -1, -1, -1),
    (+1, +1, -1, +1, +1, +1, +1),
    (-1, +1, +1, -1, -1, +1, +1),
    (-1, +1, -1, -1, +1, -1, -1),
    (+1, -1, -1, -1, -1, -1, +1),
    (+1, -1, +1, -1, +1, +1, -1),
    (-1, -1, -1, +1, -1, +1, -1),
    (+1, +1, -1, -1, +1, -1, +1),
    (+1, -1, +1, +1, -1, -1, -1),
    (-1, +1, -1, +1, +1, -1, +1),
    (-1, -1, +1, -1, +1, +1, -1),
    (+1, -1, -1, +1, -1, +1, +1),
    (+1, +1, -1, -1, -1, +1, -1),
    (+1, -1, +1, +1, +1, -1, +1),
    (-1, +1, -1, +1, -1, +1, -1),
    (-1, -1, +1, +1, +1, -1, -1),
    (+1, -1, -1, +1, +1, +1, +1),
)
CALIBRATION_BATCH_MAGNITUDES = (0.18, 0.28, 0.40, 0.65, 0.85)
ADAPTIVE_DIRECTIONAL_EXPLAIN_CANDIDATE_LIMIT = 32
ADAPTIVE_MINIMUM_PREDICTED_TARGET_DOWN = 5
ADAPTIVE_HISTORICAL_CONTINUATIONS_PER_MASK = 4
ADAPTIVE_SINGLE_DIRECTION_CONTINUATIONS_PER_MASK = 1
ADAPTIVE_DIRECTIONAL_ONLY_START_BATCH_INDEX = 8
ADAPTIVE_MIXED_HOLDOUT_MINIMUM_REPLICATION_COUNT = 2
ADAPTIVE_MIXED_HOLDOUT_MINIMUM_REPLICATION_ROUNDS = 2
ADAPTIVE_MAX_REPLICATIONS_PER_PLAN_CLUSTER = 3
ADAPTIVE_DIRECTIONAL_MAGNITUDES = (
    0.30,
    0.40,
    0.52,
    0.65,
    0.72,
    0.85,
    0.95,
    1.10,
    1.30,
    1.80,
    2.60,
    4.00,
    6.50,
    9.00,
)
ADAPTIVE_REPLICATION_MAGNITUDE_OFFSETS = (
    -0.03,
    0.03,
    -0.05,
    0.05,
    -0.08,
    0.08,
    -0.12,
    0.12,
)
MAXIMUM_CALIBRATION_CANDIDATE_BATCHES = 14


def deterministic_design_point(index: int, baseline: Mapping[str, float]) -> Dict[str, float]:
    """Return one row of the frozen full-rank 15x7 log-space design."""
    if not 0 <= index < len(CALIBRATION_SIGN_MATRIX):
        raise IndexError(f"calibration design index out of range: {index}")
    signs = CALIBRATION_SIGN_MATRIX[index]
    magnitude = CALIBRATION_BATCH_MAGNITUDES[index // 5]
    result: Dict[str, float] = {}
    for parameter_index, name in enumerate(ACTIVE_PARAMS):
        factor = math.exp(float(signs[parameter_index]) * magnitude)
        low, high = DEFAULT_RANGES[name]
        value = float(baseline[name]) * factor
        if not low <= value <= high:
            raise RuntimeError(
                f"calibration design would clip {name}: index={index} value={value} range=({low},{high})"
            )
        result[name] = value
    return result


def choose_calibration_focus_resource(
    *,
    target: str,
    scored_resource_errors: Mapping[str, Any],
    resource_weights: Mapping[str, Any],
) -> str:
    """Prioritize the highest-weight scored model that still fails admission."""
    failed = [
        resource
        for resource in ("cpu", "memory", "io")
        if scored_resource_errors.get(resource)
    ]
    if not failed:
        return target
    return max(
        failed,
        key=lambda resource: (
            float(resource_weights.get(resource) or 0.0),
            len(scored_resource_errors.get(resource) or []),
            resource == target,
        ),
    )


def calibrated_target_log_ratio(
    *,
    target: str,
    params: Mapping[str, float],
    baseline_params: Mapping[str, float],
    baseline_probe: Mapping[str, Any],
    candidate_probe: Mapping[str, Any],
    prediction_calibration: Mapping[str, Any],
) -> Dict[str, Any]:
    """Apply the admitted training-only predictor without using QPS."""
    if (
        prediction_calibration.get("contract")
        != RESOURCE_PREDICTION_CALIBRATION_CONTRACT
    ):
        raise RuntimeError("adaptive prediction calibration contract mismatch")
    if prediction_calibration.get("qps_used") is not False:
        raise RuntimeError("adaptive prediction calibration must exclude QPS")
    calibration_row = (prediction_calibration.get("resources") or {}).get(target)
    if not isinstance(calibration_row, Mapping):
        raise RuntimeError(f"adaptive prediction calibration missing target: {target}")
    historical_validation_direction_admitted = (
        calibration_row.get("historical_validation_direction_admitted")
        is not False
    )
    coefficients = calibration_row.get("parameter_coefficients") or {}
    parameter_component = 0.0
    parameter_log_deltas: Dict[str, float] = {}
    for name in ACTIVE_PARAMS:
        baseline_value = float(baseline_params[name])
        candidate_value = float(params[name])
        if baseline_value <= 0.0 or candidate_value <= 0.0:
            raise RuntimeError(f"non-positive adaptive parameter: {name}")
        delta = math.log(candidate_value / baseline_value)
        parameter_log_deltas[name] = delta
        parameter_component += float(coefficients.get(name) or 0.0) * delta
    baseline_resource = float((baseline_probe.get("resource_avg") or {}).get(target) or 0.0)
    candidate_resource = float((candidate_probe.get("resource_avg") or {}).get(target) or 0.0)
    if baseline_resource <= 0.0 or candidate_resource <= 0.0:
        raise RuntimeError(
            f"invalid adaptive raw plan resource ratio: {target}="
            f"{candidate_resource}/{baseline_resource}"
        )
    raw_plan_log_ratio = math.log(candidate_resource / baseline_resource)
    plan_coefficient = float(calibration_row.get("plan_log_ratio_coefficient") or 0.0)
    predicted_log_ratio = parameter_component + plan_coefficient * raw_plan_log_ratio
    target_prediction_safety_mode = str(
        calibration_row.get("target_prediction_safety_mode")
        or "direct_resource_response"
    )
    monotone_parameter_l2_scale = float(
        calibration_row.get("monotone_parameter_l2_scale") or 0.0
    )
    monotone_reference_l2 = float(
        calibration_row.get("monotone_reference_l2") or 1.0
    )
    if target_prediction_safety_mode in {
        "flat_target_safe_abstention",
        "irreducible_noise_safe_abstention",
    }:
        predicted_log_ratio = 0.0
    elif target_prediction_safety_mode == "monotone_target_down_distance":
        parameter_l2 = math.sqrt(
            sum(value ** 2 for value in parameter_log_deltas.values())
        )
        if monotone_parameter_l2_scale <= 0.0 or monotone_reference_l2 <= 0.0:
            raise RuntimeError("invalid adaptive monotone target calibration")
        predicted_log_ratio = -monotone_parameter_l2_scale * min(
            2.0,
            parameter_l2 / monotone_reference_l2,
        )
        # The safety predictor supersedes the historical direct-model gate.
        historical_validation_direction_admitted = True
    elif target_prediction_safety_mode != "direct_resource_response":
        raise RuntimeError(
            "unknown adaptive target prediction safety mode: "
            f"{target_prediction_safety_mode}"
        )
    support_assessment: Dict[str, Any] = {
        "enabled": False,
        "supported": True,
        "support_abstained": False,
        "qps_used": False,
    }
    support_domain = calibration_row.get("parameter_support_domain")
    if calibration_row.get("parameter_support_abstention_enabled") is True:
        if not isinstance(support_domain, Mapping):
            raise RuntimeError("adaptive target parameter support domain missing")
        support_assessment = {
            "enabled": True,
            **parameter_support_assessment(
                [parameter_log_deltas[name] for name in ACTIVE_PARAMS],
                support_domain,
                ACTIVE_PARAMS,
            ),
        }
        if support_assessment["support_abstained"]:
            predicted_log_ratio = 0.0
    if not historical_validation_direction_admitted:
        predicted_log_ratio = 0.0
        support_assessment = {
            **support_assessment,
            "supported": False,
            "support_abstained": True,
            "abstention_reason": (
                "historical_validation_direction_not_admitted"
            ),
        }
    if not math.isfinite(predicted_log_ratio):
        raise RuntimeError(f"non-finite adaptive target prediction: {predicted_log_ratio}")
    uncertainty_log_ratio = float(
        calibration_row.get("training_loo_mae_log_ratio") or math.inf
    )
    if not math.isfinite(uncertainty_log_ratio) or uncertainty_log_ratio < 0.0:
        raise RuntimeError(
            f"invalid adaptive target prediction uncertainty: {uncertainty_log_ratio}"
        )
    return {
        "family": calibration_row.get("family"),
        "parameter_log_deltas": parameter_log_deltas,
        "parameter_log_component": parameter_component,
        "raw_plan_log_ratio": raw_plan_log_ratio,
        "plan_log_ratio_coefficient": plan_coefficient,
        "target_prediction_safety_mode": target_prediction_safety_mode,
        "monotone_parameter_l2_scale": monotone_parameter_l2_scale,
        "monotone_reference_l2": monotone_reference_l2,
        "predicted_target_log_ratio": predicted_log_ratio,
        "parameter_support": support_assessment,
        "support_abstained": bool(support_assessment.get("support_abstained")),
        "prediction_uncertainty_log_ratio": uncertainty_log_ratio,
        "predicted_target_upper_log_ratio": (
            predicted_log_ratio + uncertainty_log_ratio
        ),
        "qps_used": False,
    }


def select_directional_adaptive_candidates(
    eligible: Sequence[Mapping[str, Any]],
    *,
    deadband_log_ratio: float,
    candidate_limit: int = 5,
    minimum_predicted_target_down: int = ADAPTIVE_MINIMUM_PREDICTED_TARGET_DOWN,
    require_directional_batch: bool = False,
) -> Dict[str, Any]:
    """Prefer repeatable real target-down directions, then model predictions."""
    ordered = sorted(
        eligible,
        key=lambda row: (
            float(row.get("predicted_target_log_ratio") or 0.0),
            canonical_sha256(row.get("params") or {}),
        ),
    )
    predicted_down = [
        row
        for row in ordered
        if row.get("support_abstained") is not True
        and robust_target_down(
            float(row.get("predicted_target_upper_log_ratio") or math.inf),
            0.0,
            float(deadband_log_ratio),
        )
    ]
    errors: List[str] = []
    if len(ordered) < candidate_limit:
        errors.append(
            f"adaptive_expansion_eligible_candidate_count_{len(ordered)}_below_{candidate_limit}"
        )
    warnings: List[str] = []
    if len(predicted_down) < minimum_predicted_target_down:
        warnings.append(
            "adaptive_expansion_predicted_target_down_candidate_count_"
            f"{len(predicted_down)}_below_{minimum_predicted_target_down}"
        )
    selected: List[Dict[str, Any]] = []
    selected_signatures: set[str] = set()
    selected_historical_mask_counts: Dict[int, int] = {}
    if not errors:
        historically_supported = sorted(
            (
                row
                for row in ordered
                if (
                    (row.get("historical_direction_evidence") or {}).get(
                        "trusted"
                    )
                    is True
                    or (row.get("historical_direction_evidence") or {}).get(
                        "exploratory"
                    )
                    is True
                )
                and row.get("support_abstained") is not True
            ),
            key=lambda row: (
                -int(
                    (row.get("historical_direction_evidence") or {}).get(
                        "trusted"
                    )
                    is True
                ),
                -int(
                    (row.get("historical_direction_evidence") or {}).get(
                        "target_down_count"
                    )
                    or 0
                ),
                float(row.get("magnitude") or math.inf),
                canonical_sha256(row.get("params") or {}),
            ),
        )
        for row in historically_supported:
            if len(selected) >= candidate_limit:
                break
            mask = int(row.get("mask") or 0)
            evidence = row.get("historical_direction_evidence") or {}
            trusted = evidence.get("trusted") is True
            per_mask_limit = (
                ADAPTIVE_HISTORICAL_CONTINUATIONS_PER_MASK
                if trusted
                else ADAPTIVE_SINGLE_DIRECTION_CONTINUATIONS_PER_MASK
            )
            if (
                selected_historical_mask_counts.get(mask, 0)
                >= per_mask_limit
            ):
                continue
            item = copy.deepcopy(dict(row))
            item["calibration_selection_role"] = (
                "historical_target_down_direction_continuation"
                if trusted
                else "single_observation_target_down_probe"
            )
            selected.append(item)
            selected_historical_mask_counts[mask] = (
                selected_historical_mask_counts.get(mask, 0) + 1
            )
            selected_signatures.add(canonical_sha256(item.get("params") or {}))
        for row in predicted_down[:candidate_limit]:
            if len(selected) >= candidate_limit:
                break
            signature = canonical_sha256(row.get("params") or {})
            if signature in selected_signatures:
                continue
            item = copy.deepcopy(dict(row))
            item["calibration_selection_role"] = "predicted_target_down"
            selected.append(item)
            selected_signatures.add(signature)
        for row in ordered:
            if len(selected) >= candidate_limit:
                break
            signature = canonical_sha256(row.get("params") or {})
            if signature in selected_signatures:
                continue
            item = copy.deepcopy(dict(row))
            item["calibration_selection_role"] = "model_identification_probe"
            selected.append(item)
            selected_signatures.add(signature)
    robust_predicted_selected_count = sum(
        robust_target_down(
            float(row.get("predicted_target_upper_log_ratio") or math.inf),
            0.0,
            float(deadband_log_ratio),
        )
        for row in selected
    )
    directional_selected_count = sum(
        bool(
            robust_target_down(
                float(
                    row.get("predicted_target_upper_log_ratio") or math.inf
                ),
                0.0,
                float(deadband_log_ratio),
            )
            or (
                (row.get("historical_direction_evidence") or {}).get("trusted")
                is True
                and row.get("calibration_selection_role")
                == "historical_target_down_direction_continuation"
            )
        )
        for row in selected
    )
    if require_directional_batch and directional_selected_count < candidate_limit:
        errors.append(
            "adaptive_expansion_directional_selected_count_"
            f"{directional_selected_count}_below_{candidate_limit}"
        )
    return {
        "selected": selected,
        "eligible_count": len(ordered),
        "predicted_target_down_count": len(predicted_down),
        "predicted_target_down_selected_count": robust_predicted_selected_count,
        "directional_selected_count": directional_selected_count,
        "require_directional_batch": bool(require_directional_batch),
        "historical_direction_continuation_count": sum(
            row.get("calibration_selection_role")
            == "historical_target_down_direction_continuation"
            for row in selected
        ),
        "single_observation_direction_probe_count": sum(
            row.get("calibration_selection_role")
            == "single_observation_target_down_probe"
            for row in selected
        ),
        "model_identification_probe_count": sum(
            row.get("calibration_selection_role") == "model_identification_probe"
            for row in selected
        ),
        "selection_purpose": "calibration_only_not_formal_improvement_candidates",
        "deadband_log_ratio": float(deadband_log_ratio),
        "errors": errors,
        "warnings": warnings,
        "ready": not errors,
    }


def build_mixed_independent_calibration_holdout(
    directional: Mapping[str, Any],
    replication_rows: Sequence[Mapping[str, Any]],
    *,
    deadband_log_ratio: float,
    candidate_limit: int = 5,
) -> Optional[Dict[str, Any]]:
    """Build a calibration-only holdout from repeatability and ID probes.

    A late calibration batch must not pretend that model-identification probes
    are formal improvement candidates.  It may, however, use them to complete
    an independent five-point holdout when at least two fresh parameter-vector
    replications of real target-down plan clusters come from two prior rounds.
    Exact parameter vectors remain unique.  A previously observed target-down
    plan cluster may appear more than once only as an explicitly labelled
    fresh-parameter replication.  This is repeatability evidence for the
    calibration holdout, not a formal Top-5 candidate batch; formal full-plan
    uniqueness and all post-replay admission gates remain unchanged.
    """
    replication_rounds = {
        int(
            (row.get("calibration_replication_source") or {}).get(
                "round_index"
            )
            or 0
        )
        for row in replication_rows
    } - {0}
    if (
        len(replication_rows)
        < ADAPTIVE_MIXED_HOLDOUT_MINIMUM_REPLICATION_COUNT
        or len(replication_rounds)
        < ADAPTIVE_MIXED_HOLDOUT_MINIMUM_REPLICATION_ROUNDS
    ):
        return None

    combined: List[Dict[str, Any]] = []
    parameter_signatures: set[str] = set()
    plan_fingerprint_counts: Dict[str, int] = {}
    ordered_rows = list(replication_rows) + list(
        directional.get("selected") or []
    )
    for raw_row in ordered_rows:
        params = raw_row.get("params")
        plan_fingerprint = str(raw_row.get("plan_fingerprint") or "")
        if not isinstance(params, Mapping) or not plan_fingerprint:
            continue
        replication_source = raw_row.get("calibration_replication_source")
        if (
            isinstance(replication_source, Mapping)
            and replication_source.get("exact_parameter_vector_reused") is True
        ):
            continue
        parameter_signature = canonical_sha256(params)
        is_replication = (
            raw_row.get("calibration_selection_role")
            == "historical_plan_cluster_replication_holdout"
        )
        fingerprint_count = int(
            plan_fingerprint_counts.get(plan_fingerprint, 0)
        )
        if parameter_signature in parameter_signatures:
            continue
        if not is_replication and fingerprint_count > 0:
            continue
        if (
            is_replication
            and fingerprint_count
            >= ADAPTIVE_MAX_REPLICATIONS_PER_PLAN_CLUSTER
        ):
            continue
        combined.append(copy.deepcopy(dict(raw_row)))
        parameter_signatures.add(parameter_signature)
        plan_fingerprint_counts[plan_fingerprint] = fingerprint_count + 1
        if len(combined) >= candidate_limit:
            break
    if len(combined) != candidate_limit:
        return None

    robust_down_count = sum(
        robust_target_down(
            float(row.get("predicted_target_upper_log_ratio") or math.inf),
            0.0,
            float(deadband_log_ratio),
        )
        for row in combined
    )
    replication_count = sum(
        row.get("calibration_selection_role")
        == "historical_plan_cluster_replication_holdout"
        for row in combined
    )
    if replication_count < ADAPTIVE_MIXED_HOLDOUT_MINIMUM_REPLICATION_COUNT:
        return None
    return {
        "selected": combined,
        "eligible_count": len(combined),
        "predicted_target_down_count": sum(
            float(row.get("predicted_target_log_ratio") or math.inf)
            <= -float(deadband_log_ratio)
            for row in combined
        ),
        "predicted_target_down_selected_count": robust_down_count,
        "directional_selected_count": replication_count + sum(
            row.get("calibration_selection_role")
            == "historical_target_down_direction_continuation"
            for row in combined
        ),
        "require_directional_batch": False,
        "historical_direction_continuation_count": sum(
            row.get("calibration_selection_role")
            == "historical_target_down_direction_continuation"
            for row in combined
        ),
        "single_observation_direction_probe_count": sum(
            row.get("calibration_selection_role")
            == "single_observation_target_down_probe"
            for row in combined
        ),
        "model_identification_probe_count": sum(
            row.get("calibration_selection_role")
            == "model_identification_probe"
            for row in combined
        ),
        "historical_plan_cluster_replication_count": replication_count,
        "distinct_plan_fingerprint_count": len(plan_fingerprint_counts),
        "repeated_plan_cluster_replication_count": sum(
            max(0, count - 1)
            for count in plan_fingerprint_counts.values()
        ),
        "calibration_plan_cluster_replication_allowed": True,
        "formal_top5_plan_uniqueness_contract_unchanged": True,
        "selection_purpose": (
            "mixed_independent_plan_cluster_replication_and_model_"
            "identification_holdout_only_not_formal_candidates"
        ),
        "deadband_log_ratio": float(deadband_log_ratio),
        "errors": [],
        "warnings": [
            "directional_only_pool_below_five_using_mixed_independent_"
            "calibration_holdout"
        ],
        "ready": True,
    }


def build_model_identification_calibration_holdout(
    directional: Mapping[str, Any],
    *,
    candidate_limit: int = 5,
    deadband_log_ratio: float = math.log(1.01),
) -> Optional[Dict[str, Any]]:
    """Build a plan-unique calibration-only model-identification holdout.

    Some workloads have no repeatable target-down plan cluster yet.  That is
    evidence that the predictor needs more independent observations, not a
    reason to deadlock calibration.  In that case five fresh parameter vectors
    with five distinct complete physical-plan fingerprints may be replayed as
    model-identification probes.  They are never advertised as improvement
    candidates and do not relax the formal Top-5 uniqueness or direction gates.
    """
    selected: List[Dict[str, Any]] = []
    parameter_signatures: set[str] = set()
    plan_fingerprints: set[str] = set()
    allowed_roles = {
        "model_identification_probe",
        "predicted_target_down",
        "single_observation_target_down_probe",
    }
    for raw_row in directional.get("selected") or []:
        params = raw_row.get("params")
        plan_fingerprint = str(raw_row.get("plan_fingerprint") or "")
        if (
            not isinstance(params, Mapping)
            or not plan_fingerprint
            or raw_row.get("calibration_selection_role") not in allowed_roles
        ):
            continue
        parameter_signature = canonical_sha256(params)
        if (
            parameter_signature in parameter_signatures
            or plan_fingerprint in plan_fingerprints
        ):
            continue
        selected.append(copy.deepcopy(dict(raw_row)))
        parameter_signatures.add(parameter_signature)
        plan_fingerprints.add(plan_fingerprint)
        if len(selected) >= candidate_limit:
            break
    if len(selected) != candidate_limit:
        return None

    return {
        "selected": selected,
        "eligible_count": len(selected),
        "predicted_target_down_count": sum(
            float(row.get("predicted_target_log_ratio") or math.inf)
            <= -float(deadband_log_ratio)
            for row in selected
        ),
        "predicted_target_down_selected_count": sum(
            robust_target_down(
                float(
                    row.get("predicted_target_upper_log_ratio") or math.inf
                ),
                0.0,
                float(deadband_log_ratio),
            )
            for row in selected
        ),
        "directional_selected_count": 0,
        "require_directional_batch": False,
        "historical_direction_continuation_count": 0,
        "single_observation_direction_probe_count": sum(
            row.get("calibration_selection_role")
            == "single_observation_target_down_probe"
            for row in selected
        ),
        "model_identification_probe_count": sum(
            row.get("calibration_selection_role")
            == "model_identification_probe"
            for row in selected
        ),
        "predicted_target_down_probe_count": sum(
            row.get("calibration_selection_role") == "predicted_target_down"
            for row in selected
        ),
        "historical_plan_cluster_replication_count": 0,
        "distinct_plan_fingerprint_count": len(plan_fingerprints),
        "calibration_plan_cluster_replication_allowed": False,
        "formal_top5_plan_uniqueness_contract_unchanged": True,
        "selection_purpose": (
            "plan_unique_model_identification_holdout_only_not_formal_"
            "improvement_candidates"
        ),
        "deadband_log_ratio": float(deadband_log_ratio),
        "errors": [],
        "warnings": [
            "no_repeatable_target_down_clusters_using_plan_unique_"
            "model_identification_holdout"
        ],
        "ready": True,
    }


def historical_target_direction_evidence(
    samples: Sequence[Mapping[str, Any]],
    *,
    target: str,
    deadband_log_ratio: float,
) -> Dict[int, Dict[str, Any]]:
    """Find repeated resource-improving sign directions from prior replays.

    This evidence is used only to generate a new calibration probe.  It is not
    a hard accept/reject or operator-pruning rule, and it never reads QPS.
    """
    grouped: Dict[int, Dict[str, Any]] = {}
    for sample in samples:
        x = sample.get("x")
        outcomes = sample.get("outcomes")
        if not isinstance(x, Sequence) or not isinstance(outcomes, Mapping):
            continue
        if len(x) != len(ACTIVE_PARAMS):
            continue
        try:
            target_log_ratio = float(outcomes[target])
            values = [float(value) for value in x]
            replay_round = int(sample.get("round_index") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(target_log_ratio) or not all(
            math.isfinite(value) for value in values
        ):
            continue
        mask = sum(1 << index for index, value in enumerate(values) if value > 0.0)
        row = grouped.setdefault(
            mask,
            {
                "mask": mask,
                "sample_count": 0,
                "material_count": 0,
                "target_down_count": 0,
                "target_up_count": 0,
                "neutral_count": 0,
                "rounds": set(),
                "target_log_ratios": [],
                "qps_used": False,
            },
        )
        row["sample_count"] += 1
        row["rounds"].add(replay_round)
        row["target_log_ratios"].append(target_log_ratio)
        if target_log_ratio <= -deadband_log_ratio:
            row["target_down_count"] += 1
            row["material_count"] += 1
        elif target_log_ratio >= deadband_log_ratio:
            row["target_up_count"] += 1
            row["material_count"] += 1
        else:
            row["neutral_count"] += 1

    evidence: Dict[int, Dict[str, Any]] = {}
    for mask, raw_row in grouped.items():
        row = copy.deepcopy(raw_row)
        row["rounds"] = sorted(raw_row["rounds"])
        row["distinct_round_count"] = len(row["rounds"])
        row["trusted"] = bool(
            row["target_down_count"] >= 2
            and row["target_up_count"] == 0
            and row["distinct_round_count"] >= 2
        )
        # A single real target-down observation is not trusted evidence and
        # can never accept or prune a formal candidate.  It is nevertheless a
        # safer next calibration direction than another model-only probe when
        # the admitted predictor has repeatedly chosen target-up holdouts.  A
        # new physical replay must independently confirm it.
        row["exploratory"] = bool(
            row["target_down_count"] >= 1
            and row["target_up_count"] == 0
        )
        evidence[mask] = row
    return evidence


def calibration_design_audit(baseline: Mapping[str, float]) -> Dict[str, Any]:
    points = [
        deterministic_design_point(index, baseline)
        for index in range(len(CALIBRATION_SIGN_MATRIX))
    ]
    matrix = np.asarray(
        [
            [math.log(float(point[name]) / float(baseline[name])) for name in ACTIVE_PARAMS]
            for point in points
        ],
        dtype=float,
    )
    # The resource-quality contract reserves at least twenty observations for
    # fitting and ten grouped observations for holdout.  Twenty-five frozen
    # vectors are available before a five-point plan-change/adaptive batch
    # completes the minimum thirty-observation corpus.
    training = matrix[:20]
    column_norms = np.linalg.norm(training, axis=0)
    scaled = training / column_norms
    rank = int(np.linalg.matrix_rank(training))
    condition = float(np.linalg.cond(scaled))
    maximum_column_correlation = 0.0
    correlation = np.corrcoef(training, rowvar=False)
    for left in range(correlation.shape[0]):
        for right in range(left + 1, correlation.shape[1]):
            maximum_column_correlation = max(
                maximum_column_correlation,
                abs(float(correlation[left, right])),
            )
    errors: List[str] = []
    if rank != len(ACTIVE_PARAMS):
        errors.append("training_design_rank_below_active_parameter_count")
    if not math.isfinite(condition) or condition > 1000.0:
        errors.append("training_design_condition_number_above_limit")
    if maximum_column_correlation >= 0.999:
        errors.append("training_design_columns_collinear")
    if len(
        {
            tuple(format(float(point[name]), ".17g") for name in ACTIVE_PARAMS)
            for point in points
        }
    ) != len(points):
        errors.append("calibration_design_parameter_vectors_not_unique")
    design_contract = {
        "schema_version": 1,
        "active_params": list(ACTIVE_PARAMS),
        "sign_matrix": [list(row) for row in CALIBRATION_SIGN_MATRIX],
        "batch_magnitudes": list(CALIBRATION_BATCH_MAGNITUDES),
        "batch_size": 5,
        "staged_batch_count": len(CALIBRATION_BATCH_MAGNITUDES),
    }
    return {
        "contract": "algorithm_c_resource_quality_calibration_design_v2",
        "ready": not errors,
        "errors": errors,
        "training_row_count": 20,
        "maximum_frozen_design_row_count": len(points),
        "holdout_row_count": 10,
        "minimum_total_observation_count": 30,
        "training_rank": rank,
        "active_parameter_count": len(ACTIVE_PARAMS),
        "scaled_training_condition_number": condition,
        "maximum_abs_column_correlation": maximum_column_correlation,
        "design_sha256": canonical_sha256(design_contract),
        "design": design_contract,
    }


class FreshCalibration:
    def __init__(
        self,
        cfg_path: Path,
        output_dir: Path,
        target: str,
    ) -> None:
        self.cfg_path = cfg_path
        self.cfg = read_json(cfg_path)
        self.output_dir = output_dir
        self.target = target
        self.algorithm_cfg = self.cfg.get("algorithm_c") or {}
        pruning_cfg = (
            (((self.cfg.get("binary_plan_tuning") or {}).get("binary_search") or {}).get(
                "replay_candidate_pruning"
            ))
            or {}
        )
        if self.algorithm_cfg.get("operator_resource_pruning_enabled") is not False:
            raise RuntimeError("calibration requires operator-resource pruning to be explicitly disabled")
        if not isinstance(pruning_cfg, Mapping) or pruning_cfg.get("enabled") is not False:
            raise RuntimeError("calibration requires replay candidate pruning enabled=false")
        self.initial_params = {
            name: float((self.algorithm_cfg.get("initial_params") or {})[name])
            for name in ACTIVE_PARAMS
        }
        self.workers = validation_workers(self.cfg, target)
        if len(self.workers) != 2:
            raise RuntimeError(f"fresh calibration requires exactly two workers, got {len(self.workers)}")
        self.state_path = output_dir / "calibration_state.json"
        self.log_path = output_dir / "calibration_replays.jsonl"
        self.plan_predictions_path = output_dir / "calibration_plan_predictions.json"
        self.t_path = Path(str(self.algorithm_cfg.get("experience_table_path") or ""))
        self.workload_contract_path = Path(
            str(self.algorithm_cfg.get("workload_contract_path") or "")
        )
        self.stop_requested = False
        self.lane_controller: Optional[LaneController] = None
        # A clean receipt is deliberately process-local.  A controller restart
        # must prove cleanliness again; phase/state files are not sufficient.
        self._db_mutation_generation = 0
        self._clean_restore_receipt: Optional[Dict[str, Any]] = None
        release_cfg = self.algorithm_cfg.get("calibration_release") or {}
        self.calibration_release_enabled = bool(release_cfg.get("enabled", False))
        self.calibration_release_root = Path(
            str(release_cfg.get("root") or DEFAULT_RELEASE_ROOT)
        )
        self.design_audit = calibration_design_audit(self.initial_params)
        if self.design_audit.get("ready") is not True:
            raise RuntimeError(f"calibration design invalid: {self.design_audit.get('errors')}")
        self.state = (
            read_json(self.state_path)
            if self.state_path.is_file()
            else {
                "phase": "startup",
                "physical_workload_invocations": 0,
                "valid_replays": 0,
                "technical_failures": 0,
                "completed_batches": [],
                "failed_batches": [],
                "formal_accepted_count": 0,
                "calibration_updates_formal_state": False,
            }
        )
        if not isinstance(self.state.get("failed_batches"), list):
            self.state["failed_batches"] = []
        requested_fresh_round = int(
            os.environ.get(
                "ALGORITHM_C_MINIMUM_FRESH_DIRECTION_HOLDOUT_ROUND",
                "0",
            )
        )
        if requested_fresh_round < 0:
            raise ValueError(
                "minimum fresh direction holdout round must be non-negative"
            )
        requested_maximum_batches = int(
            os.environ.get(
                "ALGORITHM_C_MAXIMUM_CALIBRATION_CANDIDATE_BATCHES",
                str(MAXIMUM_CALIBRATION_CANDIDATE_BATCHES),
            )
        )
        if requested_maximum_batches < MAXIMUM_CALIBRATION_CANDIDATE_BATCHES:
            raise ValueError(
                "maximum calibration candidate batches cannot be below the "
                f"default {MAXIMUM_CALIBRATION_CANDIDATE_BATCHES}"
            )
        self.maximum_calibration_candidate_batches = requested_maximum_batches
        stored_fresh_round = int(
            self.state.get("minimum_fresh_direction_holdout_round") or 0
        )
        self.minimum_fresh_direction_holdout_round = max(
            requested_fresh_round,
            stored_fresh_round,
        )
        if self.minimum_fresh_direction_holdout_round > stored_fresh_round:
            self.state["minimum_fresh_direction_holdout_round"] = (
                self.minimum_fresh_direction_holdout_round
            )
            self.state["minimum_fresh_direction_holdout_reason"] = (
                "directional_admission_contract_changed_after_prior_holdout_"
                "results_were_inspected"
            )
            self.state["minimum_fresh_direction_holdout_formal_state_mutation"] = False
            self.save()

    def failed_batch_names(self) -> set[str]:
        """Return immutable failed batch names recorded in calibration state."""
        return {
            str(row.get("batch_name") or "")
            for row in (self.state.get("failed_batches") or [])
            if isinstance(row, Mapping) and row.get("batch_name")
        }

    def record_failed_batch(
        self,
        *,
        batch_index: int,
        physical_count: int,
        valid_count: int,
        source: str,
    ) -> None:
        """Seal a partially valid physical batch so it can never be replayed."""
        batch_name = f"candidate_batch_{batch_index:02d}"
        if batch_name in self.failed_batch_names():
            return
        record = {
            "batch_name": batch_name,
            "batch_index": int(batch_index),
            "physical_workload_invocations": int(physical_count),
            "valid_replays": int(valid_count),
            "technical_failures": int(physical_count - valid_count),
            "immutable": True,
            "do_not_retry": True,
            "formal_state_mutation": False,
            "source": source,
            "recorded_at_epoch_s": time.time(),
        }
        self.state.setdefault("failed_batches", []).append(record)
        self.state.setdefault("candidate_batch_progress", {}).pop(
            batch_name, None
        )
        self.state["phase"] = "candidate_batch_technical_failure_recorded_clean"
        self.save()
        self.event("algorithm_c_calibration_failed_batch_sealed", failed_batch=record)

    @staticmethod
    def candidate_position(row: Mapping[str, Any], design_start: int) -> int:
        """Recover the one-based c1..c5 slot from durable replay evidence."""

        try:
            explicit = int(row.get("candidate_position"))
        except (TypeError, ValueError):
            explicit = 0
        if 1 <= explicit <= 5:
            return explicit
        label = str(row.get("label") or "")
        match = re.search(r"_c(\d{2})_", label)
        if match:
            position = int(match.group(1))
            if 1 <= position <= 5:
                return position
        try:
            position = int(row.get("candidate_index")) - int(design_start) + 1
        except (TypeError, ValueError):
            position = 0
        return position if 1 <= position <= 5 else 0

    def logged_candidate_batch_rows(self, batch_index: int) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        if not self.log_path.is_file():
            return rows
        for raw_line in self.log_path.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines():
            try:
                row = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if (
                row.get("event") == "candidate_validation"
                and int(row.get("round") or 0) == int(batch_index)
            ):
                rows.append(row)
        return rows

    def frozen_adaptive_batch_audit(
        self,
        batch_index: int,
    ) -> Optional[Dict[str, Any]]:
        """Recover the first audited Top-5 for a partially replayed adaptive batch.

        Adaptive selection depends on the current fitted model.  Once any physical
        replay for a batch has been recorded, rebuilding the model may reorder or
        replace that batch's candidates.  The immutable replay rows must instead
        remain tied to the first audited candidate set, with a resumed controller
        filling only the missing c1..c5 positions.
        """

        if not self.logged_candidate_batch_rows(batch_index):
            return None
        audit_path = self.output_dir / "calibration_audit.jsonl"
        if not audit_path.is_file():
            return None
        for raw_line in audit_path.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines():
            try:
                payload = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if payload.get("event") != "algorithm_c_calibration_adaptive_plan_change_expansion":
                continue
            audit = payload.get("audit")
            if not isinstance(audit, Mapping):
                continue
            if int(audit.get("batch_index") or 0) != int(batch_index):
                continue
            candidates = audit.get("selected_candidates")
            if not isinstance(candidates, list) or len(candidates) != 5:
                continue
            return copy.deepcopy(dict(audit))
        return None

    def account_candidate_batch_rows(
        self,
        *,
        batch_index: int,
        completed: Sequence[Mapping[str, Any]],
        source: str,
    ) -> int:
        """Account newly durable rows exactly once without invalidating them."""

        if not completed:
            return 0
        batch_name = f"candidate_batch_{batch_index:02d}"
        progress_map = self.state.setdefault("candidate_batch_progress", {})
        progress = progress_map.setdefault(
            batch_name,
            {
                "batch_index": int(batch_index),
                "accounted_row_sha256": [],
                "completed_positions": [],
                "physical_workload_invocations": 0,
                "valid_replays": 0,
                "technical_failures": 0,
                "immutable_jsonl": True,
            },
        )
        accounted = set(progress.get("accounted_row_sha256") or [])
        new_rows: List[Mapping[str, Any]] = []
        new_hashes: List[str] = []
        for row in completed:
            row_hash = canonical_sha256(row)
            if row_hash in accounted:
                continue
            accounted.add(row_hash)
            new_hashes.append(row_hash)
            new_rows.append(row)
        if not new_rows:
            return 0
        physical_count = len(new_rows)
        valid_count = sum(1 for row in new_rows if physical_valid(row))
        self.state["physical_workload_invocations"] = int(
            self.state.get("physical_workload_invocations") or 0
        ) + physical_count
        self.state["valid_replays"] = int(
            self.state.get("valid_replays") or 0
        ) + valid_count
        self.state["technical_failures"] = int(
            self.state.get("technical_failures") or 0
        ) + physical_count - valid_count
        progress["accounted_row_sha256"] = sorted(accounted)
        positions = {
            int(value) for value in (progress.get("completed_positions") or [])
        }
        for row in new_rows:
            position = self.candidate_position(row, 0)
            if position:
                positions.add(position)
        progress["completed_positions"] = sorted(positions)
        progress["physical_workload_invocations"] = int(
            progress.get("physical_workload_invocations") or 0
        ) + physical_count
        progress["valid_replays"] = int(progress.get("valid_replays") or 0) + valid_count
        progress["technical_failures"] = int(
            progress.get("technical_failures") or 0
        ) + physical_count - valid_count
        progress["last_source"] = source
        progress["updated_at_epoch_s"] = time.time()
        self.save()
        self.event(
            "algorithm_c_calibration_partial_batch_accounted",
            batch_index=batch_index,
            batch_name=batch_name,
            newly_accounted_physical=physical_count,
            newly_accounted_valid=valid_count,
            newly_accounted_technical=physical_count - valid_count,
            progress=copy.deepcopy(progress),
            old_jsonl_mutated=False,
        )
        return physical_count

    def recover_legacy_failed_batch_state(self) -> None:
        """Reconcile every JSONL candidate batch not represented in state.

        Older controllers could return at a wave boundary after appending c1-c4
        without updating counters.  The JSONL is immutable; resume accounts
        those invocations exactly once and later executes only missing slots.
        A requested clean stop is not a technical failure and therefore must
        not discard otherwise valid replay evidence.
        """
        rows_by_round: Dict[int, List[Dict[str, Any]]] = {}
        stopped_batches: set[int] = set()
        out_of_band_aborted_batches: set[int] = set()
        audit_path = self.output_dir / "calibration_audit.jsonl"
        if audit_path.is_file():
            for line in audit_path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event_name = event.get("event")
                if event_name == "algorithm_c_calibration_batch_aborted_out_of_band":
                    try:
                        aborted_batch_index = int(event.get("batch_index") or 0)
                    except (TypeError, ValueError):
                        aborted_batch_index = 0
                    if aborted_batch_index > 0:
                        out_of_band_aborted_batches.add(aborted_batch_index)
                    continue
                if event_name != "algorithm_c_stop_requested":
                    continue
                match = re.fullmatch(
                    r"candidate_batch_(\d+)", str(event.get("active_phase") or "")
                )
                if match:
                    stopped_batches.add(int(match.group(1)))
        if self.log_path.is_file():
            for line in self.log_path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("event") != "candidate_validation":
                    continue
                batch_index = int(row.get("round") or 0)
                rows_by_round.setdefault(batch_index, []).append(row)
        completed = set(self.state.get("completed_batches") or [])
        migrated = 0
        for batch_index in sorted(rows_by_round):
            rows = rows_by_round[batch_index]
            batch_name = f"candidate_batch_{batch_index:02d}"
            if batch_name in completed or batch_name in self.failed_batch_names():
                continue
            migrated += self.account_candidate_batch_rows(
                batch_index=batch_index,
                completed=rows,
                source="unaccounted_calibration_jsonl_fail_closed_migration",
            )
            partial_all_technical = bool(
                0 < len(rows) < 5
                and all(not physical_valid(row) for row in rows)
            )
            partial_out_of_band_abort = bool(
                batch_index in out_of_band_aborted_batches
                and 0 < len(rows) < 5
            )
            if (
                partial_all_technical or partial_out_of_band_abort
            ) and 0 < len(rows) < 5:
                if partial_out_of_band_abort:
                    failed_source = (
                        "ssd_out_of_band_partial_batch_do_not_resume"
                    )
                elif partial_all_technical:
                    failed_source = "all_technical_partial_batch_do_not_resume"
                self.record_failed_batch(
                    batch_index=batch_index,
                    physical_count=len(rows),
                    valid_count=sum(physical_valid(row) for row in rows),
                    source=failed_source,
                )
            elif batch_index in stopped_batches and 0 < len(rows) < 5:
                self.event(
                    "algorithm_c_calibration_clean_stop_partial_batch_resumable",
                    batch_index=batch_index,
                    completed_positions=sorted(
                        self.candidate_position(row, (batch_index - 1) * 5)
                        for row in rows
                    ),
                    pending_positions=sorted(
                        set(range(1, 6))
                        - {
                            self.candidate_position(
                                row,
                                (batch_index - 1) * 5,
                            )
                            for row in rows
                        }
                    ),
                    physical_workload_invocations=0,
                    candidate_batch_retry=False,
                    resume_missing_positions_only=True,
                    formal_state_mutation=False,
                )
        if migrated:
            self.event(
                "algorithm_c_calibration_unaccounted_jsonl_batches_migrated",
                migrated_physical_row_count=migrated,
                candidate_batch_progress=copy.deepcopy(
                    self.state.get("candidate_batch_progress") or {}
                ),
                old_jsonl_mutated=False,
                next_action="resume_only_missing_candidate_positions",
            )
            return
        if (
            self.state.get("phase") == "candidate_batch_technical_failure"
            and not self.failed_batch_names()
        ):
            raise RuntimeError(
                "candidate_batch_technical_failure has no JSONL evidence to seal"
            )

    def reclassify_accounted_legacy_over_wall_rows(self) -> None:
        """Correct durable rows accepted by a pre-hard-wall controller.

        The immutable JSONL is retained.  Only its durable accounting is
        corrected, exactly once per row hash.  Rows not yet represented in
        state are left for ``recover_legacy_failed_batch_state``, which uses
        the current ``physical_valid`` predicate and therefore counts them
        correctly on first accounting.
        """
        log_path = getattr(self, "log_path", None)
        if not isinstance(log_path, Path) or not log_path.is_file():
            return
        rows_by_batch: Dict[int, List[Dict[str, Any]]] = {}
        for line in log_path.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("event") != "candidate_validation":
                continue
            batch_index = int(row.get("round") or 0)
            if batch_index > 0:
                rows_by_batch.setdefault(batch_index, []).append(row)

        reclassified = set(
            self.state.get("legacy_over_wall_reclassified_row_sha256") or []
        )
        corrections: List[Dict[str, Any]] = []
        completed = set(self.state.get("completed_batches") or [])
        failed_by_name = {
            str(record.get("batch_name") or ""): record
            for record in (self.state.get("failed_batches") or [])
            if isinstance(record, dict)
        }
        progress_map = self.state.get("candidate_batch_progress") or {}
        for batch_index, rows in sorted(rows_by_batch.items()):
            batch_name = f"candidate_batch_{batch_index:02d}"
            progress = progress_map.get(batch_name)
            accounted_hashes = set(
                (progress or {}).get("accounted_row_sha256") or []
            )
            batch_is_durably_accounted = (
                batch_name in completed or batch_name in failed_by_name
            )
            corrected_in_batch = 0
            for row in rows:
                benchmark = (
                    ((row.get("hybench") or {}).get("benchmark"))
                    if isinstance(row.get("hybench"), Mapping)
                    else None
                )
                if (
                    not isinstance(benchmark, Mapping)
                    or benchmark.get("completed_within_target_wall_time")
                    is not False
                ):
                    continue
                row_hash = canonical_sha256(row)
                if row_hash in reclassified:
                    continue
                if not batch_is_durably_accounted and row_hash not in accounted_hashes:
                    continue
                legacy_view = copy.deepcopy(row)
                legacy_view.setdefault("hybench", {}).setdefault(
                    "benchmark", {}
                )["completed_within_target_wall_time"] = True
                if not physical_valid(legacy_view):
                    # The row was already technical for an independent reason;
                    # no valid->technical counter correction is needed.
                    reclassified.add(row_hash)
                    continue
                self.state["valid_replays"] = max(
                    0, int(self.state.get("valid_replays") or 0) - 1
                )
                self.state["technical_failures"] = int(
                    self.state.get("technical_failures") or 0
                ) + 1
                if isinstance(progress, dict) and row_hash in accounted_hashes:
                    progress["valid_replays"] = max(
                        0, int(progress.get("valid_replays") or 0) - 1
                    )
                    progress["technical_failures"] = int(
                        progress.get("technical_failures") or 0
                    ) + 1
                reclassified.add(row_hash)
                corrected_in_batch += 1
                corrections.append(
                    {
                        "batch_index": batch_index,
                        "batch_name": batch_name,
                        "label": row.get("label"),
                        "row_sha256": row_hash,
                        "wall_time_s": benchmark.get("wall_time_s"),
                        "target_wall_time_s": benchmark.get(
                            "target_wall_time_s"
                        ),
                    }
                )
            if corrected_in_batch <= 0:
                continue
            failed_record = failed_by_name.get(batch_name)
            if isinstance(failed_record, dict):
                failed_record["valid_replays"] = max(
                    0,
                    int(failed_record.get("valid_replays") or 0)
                    - corrected_in_batch,
                )
                failed_record["technical_failures"] = int(
                    failed_record.get("technical_failures") or 0
                ) + corrected_in_batch
                failed_record["legacy_wall_time_reclassification"] = True
            elif batch_name in completed:
                self.state["completed_batches"] = [
                    value
                    for value in (self.state.get("completed_batches") or [])
                    if value != batch_name
                ]
                failed_record = {
                    "batch_name": batch_name,
                    "batch_index": batch_index,
                    "physical_workload_invocations": len(rows),
                    "valid_replays": sum(physical_valid(row) for row in rows),
                    "technical_failures": sum(
                        not physical_valid(row) for row in rows
                    ),
                    "immutable": True,
                    "do_not_retry": True,
                    "formal_state_mutation": False,
                    "source": "legacy_over_wall_reclassification",
                    "recorded_at_epoch_s": time.time(),
                    "legacy_wall_time_reclassification": True,
                }
                self.state.setdefault("failed_batches", []).append(
                    failed_record
                )
                failed_by_name[batch_name] = failed_record
                completed.discard(batch_name)
        if not corrections:
            return
        self.state["legacy_over_wall_reclassified_row_sha256"] = sorted(
            reclassified
        )
        self.state["legacy_over_wall_reclassification_count"] = len(
            reclassified
        )
        self.save()
        self.event(
            "algorithm_c_calibration_legacy_over_wall_rows_reclassified",
            corrections=corrections,
            corrected_valid_to_technical_count=len(corrections),
            physical_workload_invocations_unchanged=True,
            immutable_jsonl_preserved=True,
            formal_state_mutation=False,
        )

    def audit_plan_change_expansion(
        self,
        *,
        batch_index: int,
        design_start: int,
    ) -> Dict[str, Any]:
        """Fail closed unless every extended-holdout point changes a full plan.

        This is read-only EXPLAIN evidence.  It neither consumes a physical
        workload slot nor changes calibration/formal optimizer state.
        """
        candidates = [
            deterministic_design_point(design_start + offset, self.initial_params)
            for offset in range(5)
        ]
        probe = PlanFeatureProbe(self.cfg)
        queries, sampling = workload_queries_with_sampling(self.cfg, probe)
        baseline = probe.probe(queries, copy.deepcopy(self.initial_params))
        baseline_coverage = (
            (baseline.get("exact_plan_metadata") or {}).get("plan_fingerprint_coverage")
            or {}
        )
        errors: List[str] = []
        if sampling.get("sampling_applied"):
            errors.append("expansion_plan_workload_sampling_applied")
        if baseline_coverage.get("complete") is not True:
            errors.append("expansion_baseline_plan_coverage_incomplete")
        baseline_signatures = baseline.get("plan_signatures") or {}
        prior_plan_fingerprints: Dict[str, int] = {}
        for prior_index in range(design_start):
            prior_result = probe.probe(
                queries,
                deterministic_design_point(prior_index, self.initial_params),
            )
            prior_fingerprint = plan_signature_set_fingerprint(
                prior_result.get("plan_signatures") or {}
            )
            if prior_fingerprint:
                prior_plan_fingerprints.setdefault(prior_fingerprint, prior_index)
        observations: List[Dict[str, Any]] = []
        candidate_fingerprints: List[str] = []
        for offset, params in enumerate(candidates):
            result = probe.probe(queries, params)
            coverage = (
                (result.get("exact_plan_metadata") or {}).get("plan_fingerprint_coverage")
                or {}
            )
            changes = plan_diff(
                baseline_signatures,
                result.get("plan_signatures") or {},
            )
            fingerprint = plan_signature_set_fingerprint(
                result.get("plan_signatures") or {}
            )
            candidate_index = design_start + offset
            if coverage.get("complete") is not True:
                errors.append(f"candidate_{candidate_index}_plan_coverage_incomplete")
            if changes.get("changed") is not True:
                errors.append(f"candidate_{candidate_index}_plan_unchanged")
            if not fingerprint:
                errors.append(f"candidate_{candidate_index}_plan_fingerprint_empty")
            if fingerprint in prior_plan_fingerprints:
                errors.append(
                    f"candidate_{candidate_index}_duplicates_prior_plan_"
                    f"{prior_plan_fingerprints[fingerprint]}"
                )
            candidate_fingerprints.append(fingerprint)
            observations.append(
                {
                    "candidate_index": candidate_index,
                    "params": params,
                    "plan_fingerprint": fingerprint,
                    "changed_query_count": int(changes.get("changed_count") or 0),
                    "changed_query_keys": list(changes.get("changed_keys") or []),
                    "plan_coverage": coverage,
                    "probe_error_count": len(result.get("errors") or []),
                }
            )
        if len(set(candidate_fingerprints)) != len(candidate_fingerprints):
            errors.append("expansion_candidate_plan_fingerprints_not_unique")
        audit = {
            "contract": "algorithm_c_calibration_plan_change_only_expansion_v1",
            "batch_index": batch_index,
            "design_start": design_start,
            "physical_workload_invocations": 0,
            "full_workload_query_count": len(queries),
            "sampling": sampling,
            "baseline_plan_fingerprint": plan_signature_set_fingerprint(
                baseline_signatures
            ),
            "baseline_plan_coverage": baseline_coverage,
            "candidate_count": len(candidates),
            "candidate_plan_fingerprints_unique": (
                len(set(candidate_fingerprints)) == len(candidate_fingerprints)
            ),
            "prior_design_point_count": design_start,
            "new_candidate_plans_distinct_from_prior_design": not any(
                fingerprint in prior_plan_fingerprints
                for fingerprint in candidate_fingerprints
            ),
            "observations": observations,
            "errors": errors,
            "ready": not errors,
        }
        write_json(
            self.output_dir / f"calibration_plan_change_expansion_round_{batch_index:02d}.json",
            audit,
        )
        self.event(
            "algorithm_c_calibration_plan_change_expansion",
            audit=audit,
            physical_batch_limit=5,
            physical_workload_invocations=0,
        )
        return audit

    def audit_adaptive_plan_change_expansion(
        self,
        *,
        batch_index: int,
    ) -> Dict[str, Any]:
        """Find five new full-plan behaviors for the weakest scored model."""
        frozen_audit = self.frozen_adaptive_batch_audit(batch_index)
        if frozen_audit is not None:
            self.event(
                "algorithm_c_calibration_adaptive_batch_frozen_reused",
                batch_index=batch_index,
                completed_positions=sorted(
                    {
                        self.candidate_position(
                            row,
                            len(CALIBRATION_SIGN_MATRIX),
                        )
                        for row in self.logged_candidate_batch_rows(batch_index)
                    }
                    - {0}
                ),
                selected_candidates=frozen_audit["selected_candidates"],
                physical_workload_invocations=0,
                formal_state_mutation=False,
            )
            return frozen_audit
        probe = PlanFeatureProbe(self.cfg)
        queries, sampling = workload_queries_with_sampling(self.cfg, probe)
        baseline = probe.probe(queries, copy.deepcopy(self.initial_params))
        baseline_signatures = baseline.get("plan_signatures") or {}
        baseline_coverage = (
            (baseline.get("exact_plan_metadata") or {}).get(
                "plan_fingerprint_coverage"
            )
            or {}
        )
        errors: List[str] = []
        if sampling.get("sampling_applied"):
            errors.append("adaptive_expansion_workload_sampling_applied")
        if baseline_coverage.get("complete") is not True:
            errors.append("adaptive_expansion_baseline_plan_coverage_incomplete")

        artifact = read_json(self.t_path)
        formal_admission = artifact.get("formal_admission") or {}
        scored_resource_errors = (
            formal_admission.get("scored_resource_errors") or {}
        )
        blocking_scored_resource_errors = (
            formal_admission.get("blocking_scored_resource_errors")
            or scored_resource_errors
        )
        failed_scored_resources = [
            resource
            for resource in ("cpu", "memory", "io")
            if blocking_scored_resource_errors.get(resource)
        ]
        scoring_options = formal_inner_scoring_options(self.target)
        resource_weights = scoring_options.get("resource_weights") or {}
        calibration_focus_resource = choose_calibration_focus_resource(
            target=self.target,
            scored_resource_errors=blocking_scored_resource_errors,
            resource_weights=resource_weights,
        )
        prediction_calibration = artifact.get("resource_prediction_calibration") or {}
        if (
            prediction_calibration.get("contract")
            != RESOURCE_PREDICTION_CALIBRATION_CONTRACT
        ):
            errors.append("adaptive_expansion_prediction_calibration_contract_mismatch")
        if prediction_calibration.get("qps_used") is not False:
            errors.append("adaptive_expansion_prediction_calibration_uses_qps")
        observed_scored_directions = (
            formal_admission.get("observed_scored_resource_direction") or {}
        )
        focus_direction = (
            observed_scored_directions.get(calibration_focus_resource)
            or formal_admission.get("observed_target_direction")
            or {}
        )
        deadband_log_ratio = float(
            focus_direction.get("deadband_log_ratio")
            or math.log(1.01)
        )
        historical_samples: List[Dict[str, Any]] = []
        historical_source_audit: Dict[str, Any] = {}
        if self.log_path.is_file():
            historical_samples, historical_source_audit = load_samples(
                [self.log_path],
                ACTIVE_PARAMS,
            )
        direction_evidence = historical_target_direction_evidence(
            historical_samples,
            target=calibration_focus_resource,
            deadband_log_ratio=deadband_log_ratio,
        )
        trusted_direction_masks = sorted(
            mask
            for mask, evidence in direction_evidence.items()
            if evidence.get("trusted") is True
        )
        supported_direction_masks = sorted(
            direction_evidence,
            key=lambda mask: (
                0 if direction_evidence[mask].get("trusted") is True else 1,
                -int(direction_evidence[mask].get("target_down_count") or 0),
                mask,
            ),
        )
        supported_direction_masks = [
            mask
            for mask in supported_direction_masks
            if direction_evidence[mask].get("exploratory") is True
        ]

        prior_plan_fingerprints: set[str] = set()
        prior_param_signatures: set[str] = set()
        for prior_index in range(len(CALIBRATION_SIGN_MATRIX)):
            params = deterministic_design_point(prior_index, self.initial_params)
            prior_param_signatures.add(canonical_sha256(params))
            result = probe.probe(queries, params)
            fingerprint = plan_signature_set_fingerprint(
                result.get("plan_signatures") or {}
            )
            if fingerprint:
                prior_plan_fingerprints.add(fingerprint)
        for pattern in (
            "calibration_plan_change_expansion_round_*.json",
            "calibration_adaptive_plan_change_expansion_round_*.json",
        ):
            for audit_path in self.output_dir.glob(pattern):
                prior_audit = read_json(audit_path)
                for fingerprint in prior_audit.get("selected_plan_fingerprints") or []:
                    if fingerprint:
                        prior_plan_fingerprints.add(str(fingerprint))
        if self.log_path.is_file():
            for raw_line in self.log_path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines():
                try:
                    row = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                params = row.get("params")
                if row.get("event") == "candidate_validation" and isinstance(
                    params, Mapping
                ):
                    prior_param_signatures.add(canonical_sha256(dict(params)))

        selected: List[Dict[str, float]] = []
        observations: List[Dict[str, Any]] = []
        eligible_rows: List[Dict[str, Any]] = []
        eligible_fingerprints: set[str] = set()
        magnitudes = ADAPTIVE_DIRECTIONAL_MAGNITUDES
        hashed_masks = sorted(
            range(1, 1 << len(ACTIVE_PARAMS)),
            key=lambda value: canonical_sha256(
                {"batch_index": batch_index, "mask": value}
            ),
        )
        candidate_pairs = [
            (magnitude, mask)
            for mask in supported_direction_masks
            for magnitude in magnitudes
        ]
        candidate_pairs.extend(
            (magnitude, mask)
            for magnitude in magnitudes
            for mask in hashed_masks
            if mask not in supported_direction_masks
        )
        for magnitude, mask in candidate_pairs:
            params: Dict[str, float] = {}
            for param_index, name in enumerate(ACTIVE_PARAMS):
                sign = 1.0 if mask & (1 << param_index) else -1.0
                lower, upper = DEFAULT_RANGES[name]
                params[name] = min(
                    float(upper),
                    max(
                        float(lower),
                        float(self.initial_params[name])
                        * math.exp(sign * magnitude),
                    ),
                )
            signature = canonical_sha256(params)
            if signature in prior_param_signatures:
                continue
            result = probe.probe(queries, params)
            coverage = (
                (result.get("exact_plan_metadata") or {}).get(
                    "plan_fingerprint_coverage"
                )
                or {}
            )
            changes = plan_diff(
                baseline_signatures,
                result.get("plan_signatures") or {},
            )
            fingerprint = plan_signature_set_fingerprint(
                result.get("plan_signatures") or {}
            )
            eligible = bool(
                coverage.get("complete") is True
                and changes.get("changed") is True
                and fingerprint
                and fingerprint not in prior_plan_fingerprints
                and fingerprint not in eligible_fingerprints
            )
            prediction: Dict[str, Any] = {}
            prediction_error = ""
            if eligible and not errors:
                try:
                    prediction = calibrated_target_log_ratio(
                        target=calibration_focus_resource,
                        params=params,
                        baseline_params=self.initial_params,
                        baseline_probe=baseline,
                        candidate_probe=result,
                        prediction_calibration=prediction_calibration,
                    )
                except (KeyError, TypeError, ValueError, RuntimeError) as exc:
                    prediction_error = str(exc)
                    eligible = False
            observations.append(
                {
                    "magnitude": magnitude,
                    "mask": mask,
                    "params": params,
                    "plan_fingerprint": fingerprint,
                    "changed_query_count": int(changes.get("changed_count") or 0),
                    "plan_coverage_complete": coverage.get("complete") is True,
                    "eligible": eligible,
                    "selected": False,
                    "prediction": prediction,
                    "prediction_error": prediction_error,
                    "historical_direction_evidence": copy.deepcopy(
                        direction_evidence.get(mask)
                    ),
                }
            )
            if prediction_error:
                errors.append(
                    f"adaptive_candidate_prediction_failed_mask_{mask}: {prediction_error}"
                )
            if not eligible:
                continue
            eligible_rows.append(
                {
                    "params": params,
                    "plan_fingerprint": fingerprint,
                    "predicted_target_log_ratio": prediction[
                        "predicted_target_log_ratio"
                    ],
                    "prediction_uncertainty_log_ratio": prediction[
                        "prediction_uncertainty_log_ratio"
                    ],
                    "predicted_target_upper_log_ratio": prediction[
                        "predicted_target_upper_log_ratio"
                    ],
                    "mask": mask,
                    "magnitude": magnitude,
                    "historical_direction_evidence": copy.deepcopy(
                        direction_evidence.get(mask)
                    ),
                    "observation_index": len(observations) - 1,
                }
            )
            eligible_fingerprints.add(fingerprint)
            if len(eligible_rows) >= ADAPTIVE_DIRECTIONAL_EXPLAIN_CANDIDATE_LIMIT:
                break
        directional = select_directional_adaptive_candidates(
            eligible_rows,
            deadband_log_ratio=deadband_log_ratio,
            require_directional_batch=(
                batch_index >= ADAPTIVE_DIRECTIONAL_ONLY_START_BATCH_INDEX
            ),
        )
        replication_source_count = 0
        replication_distinct_round_count = 0
        replication_distinct_plan_count = 0
        replication_fingerprint_counts: Dict[str, int] = {}
        if (
            directional.get("ready") is not True
            and batch_index >= ADAPTIVE_DIRECTIONAL_ONLY_START_BATCH_INDEX
        ):
            # Once globally new plan clusters are exhausted, independently
            # replicate the strongest historical target-down behaviors at a
            # nearby, never-before-replayed parameter vector.  The full plan
            # must remain identical to its source behavior, and the current
            # training-only predictor must still predict target-down.  Fresh
            # parameter vectors may repeat that plan cluster solely to measure
            # repeatability; this is never a formal Top-5 or an
            # in-sample holdout of the exact same parameter vector.
            replication_rows: List[Dict[str, Any]] = []
            replication_fingerprints: set[str] = set()
            ordered_sources = sorted(
                (
                    sample
                    for sample in historical_samples
                    if float((sample.get("outcomes") or {}).get(
                        calibration_focus_resource
                    ) or 0.0)
                    <= -deadband_log_ratio
                ),
                key=lambda sample: (
                    float((sample.get("outcomes") or {}).get(
                        calibration_focus_resource
                    ) or 0.0),
                    int(sample.get("round_index") or 0),
                    str(sample.get("label") or ""),
                ),
            )
            for source in ordered_sources:
                if len(replication_rows) >= 5:
                    break
                source_params = source.get("params")
                source_x = source.get("x")
                if not isinstance(source_params, Mapping) or not isinstance(
                    source_x, Sequence
                ) or len(source_x) != len(ACTIVE_PARAMS):
                    continue
                source_result = probe.probe(queries, dict(source_params))
                source_coverage = (
                    (source_result.get("exact_plan_metadata") or {}).get(
                        "plan_fingerprint_coverage"
                    )
                    or {}
                )
                source_changes = plan_diff(
                    baseline_signatures,
                    source_result.get("plan_signatures") or {},
                )
                source_fingerprint = plan_signature_set_fingerprint(
                    source_result.get("plan_signatures") or {}
                )
                if not (
                    source_coverage.get("complete") is True
                    and source_changes.get("changed") is True
                    and source_fingerprint
                    and int(
                        replication_fingerprint_counts.get(
                            source_fingerprint,
                            0,
                        )
                    ) < ADAPTIVE_MAX_REPLICATIONS_PER_PLAN_CLUSTER
                ):
                    continue
                try:
                    source_values = [float(value) for value in source_x]
                except (TypeError, ValueError):
                    continue
                source_magnitude = float(
                    statistics.median(abs(value) for value in source_values)
                )
                source_mask = sum(
                    1 << index
                    for index, value in enumerate(source_values)
                    if value > 0.0
                )
                for offset in ADAPTIVE_REPLICATION_MAGNITUDE_OFFSETS:
                    if len(replication_rows) >= 5:
                        break
                    magnitude = max(0.01, source_magnitude + float(offset))
                    params: Dict[str, float] = {}
                    for param_index, name in enumerate(ACTIVE_PARAMS):
                        sign = 1.0 if source_mask & (1 << param_index) else -1.0
                        lower, upper = DEFAULT_RANGES[name]
                        params[name] = min(
                            float(upper),
                            max(
                                float(lower),
                                float(self.initial_params[name])
                                * math.exp(sign * magnitude),
                            ),
                        )
                    signature = canonical_sha256(params)
                    if signature in prior_param_signatures:
                        continue
                    result = probe.probe(queries, params)
                    coverage = (
                        (result.get("exact_plan_metadata") or {}).get(
                            "plan_fingerprint_coverage"
                        )
                        or {}
                    )
                    changes = plan_diff(
                        baseline_signatures,
                        result.get("plan_signatures") or {},
                    )
                    fingerprint = plan_signature_set_fingerprint(
                        result.get("plan_signatures") or {}
                    )
                    if not (
                        coverage.get("complete") is True
                        and changes.get("changed") is True
                        and fingerprint == source_fingerprint
                        and int(
                            replication_fingerprint_counts.get(
                                fingerprint,
                                0,
                            )
                        ) < ADAPTIVE_MAX_REPLICATIONS_PER_PLAN_CLUSTER
                    ):
                        continue
                    try:
                        prediction = calibrated_target_log_ratio(
                            target=calibration_focus_resource,
                            params=params,
                            baseline_params=self.initial_params,
                            baseline_probe=baseline,
                            candidate_probe=result,
                            prediction_calibration=prediction_calibration,
                        )
                    except (KeyError, TypeError, ValueError, RuntimeError):
                        continue
                    if not robust_target_down(
                        float(prediction["predicted_target_log_ratio"]),
                        float(prediction["prediction_uncertainty_log_ratio"]),
                        float(deadband_log_ratio),
                    ):
                        continue
                    observation_index = len(observations)
                    observations.append(
                        {
                            "magnitude": magnitude,
                            "mask": source_mask,
                            "params": params,
                            "plan_fingerprint": fingerprint,
                            "changed_query_count": int(
                                changes.get("changed_count") or 0
                            ),
                            "plan_coverage_complete": True,
                            "eligible": True,
                            "selected": False,
                            "prediction": prediction,
                            "prediction_error": "",
                            "calibration_replication_source": {
                                "label": source.get("label"),
                                "round_index": source.get("round_index"),
                                "actual_target_log_ratio": float(
                                    (source.get("outcomes") or {})[
                                        calibration_focus_resource
                                    ]
                                ),
                                "source_plan_fingerprint": source_fingerprint,
                                "exact_parameter_vector_reused": False,
                                "qps_used": False,
                            },
                        }
                    )
                    replication_rows.append(
                        {
                            "params": params,
                            "plan_fingerprint": fingerprint,
                            "predicted_target_log_ratio": prediction[
                                "predicted_target_log_ratio"
                            ],
                            "prediction_uncertainty_log_ratio": prediction[
                                "prediction_uncertainty_log_ratio"
                            ],
                            "predicted_target_upper_log_ratio": prediction[
                                "predicted_target_upper_log_ratio"
                            ],
                            "mask": source_mask,
                            "magnitude": magnitude,
                            "historical_direction_evidence": copy.deepcopy(
                                direction_evidence.get(source_mask)
                            ),
                            "calibration_selection_role": (
                                "historical_plan_cluster_replication_holdout"
                            ),
                            "calibration_replication_source": copy.deepcopy(
                                observations[observation_index][
                                    "calibration_replication_source"
                                ]
                            ),
                            "observation_index": observation_index,
                        }
                    )
                    replication_fingerprints.add(fingerprint)
                    replication_fingerprint_counts[fingerprint] = (
                        int(replication_fingerprint_counts.get(fingerprint, 0))
                        + 1
                    )
                    prior_param_signatures.add(signature)
            replication_rounds = {
                int(
                    (row.get("calibration_replication_source") or {}).get(
                        "round_index"
                    )
                    or 0
                )
                for row in replication_rows
            }
            replication_source_count = len(replication_rows)
            replication_distinct_round_count = len(replication_rounds - {0})
            replication_distinct_plan_count = len(replication_fingerprints)
            if (
                replication_source_count == 5
                and replication_distinct_round_count >= 2
            ):
                directional = {
                    "selected": replication_rows,
                    "eligible_count": len(replication_rows),
                    "predicted_target_down_count": sum(
                        float(row["predicted_target_log_ratio"])
                        <= -deadband_log_ratio
                        for row in replication_rows
                    ),
                    "predicted_target_down_selected_count": sum(
                        robust_target_down(
                            float(row["predicted_target_upper_log_ratio"]),
                            0.0,
                            float(deadband_log_ratio),
                        )
                        for row in replication_rows
                    ),
                    "directional_selected_count": 5,
                    "require_directional_batch": True,
                    "historical_direction_continuation_count": 0,
                    "single_observation_direction_probe_count": 0,
                    "model_identification_probe_count": 0,
                    "historical_plan_cluster_replication_count": 5,
                    "distinct_plan_fingerprint_count": (
                        replication_distinct_plan_count
                    ),
                    "repeated_plan_cluster_replication_count": (
                        replication_source_count
                        - replication_distinct_plan_count
                    ),
                    "calibration_plan_cluster_replication_allowed": True,
                    "formal_top5_plan_uniqueness_contract_unchanged": True,
                    "selection_purpose": (
                        "independent_parameter_vector_plan_cluster_"
                        "replication_holdout_only_not_formal_candidates"
                    ),
                    "deadband_log_ratio": deadband_log_ratio,
                    "errors": [],
                    "warnings": [
                        "globally_new_directional_plan_clusters_exhausted_"
                        "using_fresh_parameter_vector_replication_holdout"
                    ],
                    "ready": True,
                }
            else:
                mixed_holdout = build_mixed_independent_calibration_holdout(
                    directional,
                    replication_rows,
                    deadband_log_ratio=deadband_log_ratio,
                )
                if mixed_holdout is not None:
                    directional = mixed_holdout
                else:
                    model_identification_holdout = (
                        build_model_identification_calibration_holdout(
                            directional,
                            deadband_log_ratio=deadband_log_ratio,
                        )
                    )
                    if model_identification_holdout is not None:
                        directional = model_identification_holdout
                    else:
                        directional.setdefault("errors", []).append(
                            "adaptive_expansion_replication_candidate_count_"
                            f"{replication_source_count}_below_5_or_rounds_"
                            f"{replication_distinct_round_count}_below_2"
                        )
        errors.extend(directional.get("errors") or [])
        selected_rows = directional.get("selected") or []
        selected_fingerprints: set[str] = set()
        for row in selected_rows:
            selected.append(dict(row["params"]))
            selected_fingerprints.add(str(row["plan_fingerprint"]))
            selected_observation = observations[int(row["observation_index"])]
            selected_observation["selected"] = True
            selected_observation["calibration_selection_role"] = row.get(
                "calibration_selection_role"
            )
        audit = {
            "contract": "algorithm_c_adaptive_scored_resource_plan_change_expansion_v5",
            "batch_index": batch_index,
            "calibration_focus_resource": calibration_focus_resource,
            "failed_scored_resources": failed_scored_resources,
            "scored_resource_errors": copy.deepcopy(scored_resource_errors),
            "blocking_scored_resource_errors": copy.deepcopy(
                blocking_scored_resource_errors
            ),
            "physical_workload_invocations": 0,
            "full_workload_query_count": len(queries),
            "sampling": sampling,
            "baseline_plan_coverage": baseline_coverage,
            "prior_frozen_design_point_count": len(CALIBRATION_SIGN_MATRIX),
            "prior_plan_fingerprint_count": len(prior_plan_fingerprints),
            "candidate_probe_count": len(observations),
            "eligible_candidate_count": directional.get("eligible_count"),
            "predicted_target_down_candidate_count": directional.get(
                "predicted_target_down_count"
            ),
            "predicted_target_down_selected_count": directional.get(
                "predicted_target_down_selected_count"
            ),
            "directional_selected_count": directional.get(
                "directional_selected_count"
            ),
            "require_directional_batch": directional.get(
                "require_directional_batch"
            ),
            "directional_magnitude_schedule": list(magnitudes),
            "historical_plan_cluster_replication_count": directional.get(
                "historical_plan_cluster_replication_count", 0
            ),
            "replication_source_count": replication_source_count,
            "replication_distinct_round_count": (
                replication_distinct_round_count
            ),
            "replication_distinct_plan_count": (
                replication_distinct_plan_count
            ),
            "replication_plan_fingerprint_counts": copy.deepcopy(
                replication_fingerprint_counts
            ),
            "replication_magnitude_offsets": list(
                ADAPTIVE_REPLICATION_MAGNITUDE_OFFSETS
            ),
            "model_identification_probe_count": directional.get(
                "model_identification_probe_count"
            ),
            "historical_direction_continuation_count": directional.get(
                "historical_direction_continuation_count"
            ),
            "single_observation_direction_probe_count": directional.get(
                "single_observation_direction_probe_count"
            ),
            "trusted_historical_direction_masks": trusted_direction_masks,
            "exploratory_historical_direction_masks": [
                mask
                for mask in supported_direction_masks
                if mask not in trusted_direction_masks
            ],
            "historical_direction_evidence": {
                str(mask): evidence
                for mask, evidence in direction_evidence.items()
                if evidence.get("trusted") is True
            },
            "historical_direction_source_audit": historical_source_audit,
            "selection_purpose": directional.get("selection_purpose"),
            "formal_improvement_candidate": False,
            "directional_deadband_log_ratio": deadband_log_ratio,
            "directional_selection_policy": (
                "weakest_high_weight_resource_then_historical_direction_v4"
            ),
            "qps_used_for_selection": False,
            "selected_candidate_count": len(selected),
            "selected_candidates": selected,
            "selected_plan_fingerprints": sorted(selected_fingerprints),
            "observations": observations,
            "errors": errors,
            "warnings": directional.get("warnings") or [],
            "ready": not errors,
        }
        write_json(
            self.output_dir
            / f"calibration_adaptive_plan_change_expansion_round_{batch_index:02d}.json",
            audit,
        )
        self.event(
            "algorithm_c_calibration_adaptive_plan_change_expansion",
            audit=audit,
            physical_batch_limit=5,
            physical_workload_invocations=0,
        )
        return audit

    def request_stop(self, signum: int) -> None:
        self.stop_requested = True
        if self.lane_controller is not None:
            self.lane_controller.stop_requested = True
        self.event(
            "algorithm_c_stop_requested",
            signal=signal.Signals(signum).name,
            active_phase=self.state.get("phase"),
        )

    def save(self) -> None:
        self.state["updated_at_epoch_s"] = time.time()
        write_json(self.state_path, self.state)

    def event(self, name: str, **extra: Any) -> None:
        payload = {
            "event": name,
            "time": time.time(),
            "scene": self.algorithm_cfg.get("scene"),
            "target": self.target,
            **extra,
        }
        append_jsonl(self.output_dir / "calibration_audit.jsonl", payload)
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)

    def try_reuse_calibration_release(self) -> bool:
        """Reuse a verified T only for a pristine pre-calibration lane."""
        if not bool(getattr(self, "calibration_release_enabled", False)):
            return False
        if (
            self.state.get("completed_batches")
            or int(self.state.get("physical_workload_invocations") or 0) != 0
            or int(self.state.get("valid_replays") or 0) != 0
            or int(self.state.get("technical_failures") or 0) != 0
        ):
            self.event(
                "algorithm_c_calibration_release_reuse_skipped",
                reason="non_pristine_calibration_state_resumes_in_place",
                completed_batches=copy.deepcopy(
                    self.state.get("completed_batches") or []
                ),
                physical_workload_invocations=int(
                    self.state.get("physical_workload_invocations") or 0
                ),
            )
            return False
        release = find_compatible_calibration_release(
            self.cfg,
            release_root=Path(
                getattr(self, "calibration_release_root", DEFAULT_RELEASE_ROOT)
            ),
        )
        if release is None:
            self.event(
                "algorithm_c_calibration_release_not_found",
                release_root=str(
                    getattr(self, "calibration_release_root", DEFAULT_RELEASE_ROOT)
                ),
                action="run_fresh_calibration",
                physical_workload_invocations=0,
                formal_state_mutation=False,
            )
            return False

        self.state["phase"] = "calibration_release_plan_revalidation"
        self.save()
        self.restore_clean("calibration_release_plan_revalidation")
        probe = PlanFeatureProbe(self.cfg)
        queries, sampling = workload_queries_with_sampling(self.cfg, probe)
        baseline = probe.probe(queries, copy.deepcopy(self.initial_params))
        coverage = (
            (baseline.get("exact_plan_metadata") or {}).get(
                "plan_fingerprint_coverage"
            )
            or {}
        )
        current_fingerprint = str(
            (baseline.get("exact_plan_metadata") or {}).get(
                "workload_plan_fingerprint"
            )
            or ""
        )
        expected_fingerprint = str(
            release.get("baseline_plan_fingerprint") or ""
        )
        errors: List[str] = []
        if sampling.get("sampling_applied"):
            errors.append("release_revalidation_workload_sampling_applied")
        if coverage.get("complete") is not True:
            errors.append("release_revalidation_plan_coverage_incomplete")
        if current_fingerprint != expected_fingerprint:
            errors.append("release_revalidation_plan_fingerprint_mismatch")
        self.event(
            "algorithm_c_calibration_release_plan_revalidation",
            manifest_path=release.get("manifest_path"),
            expected_plan_fingerprint=expected_fingerprint,
            current_plan_fingerprint=current_fingerprint,
            plan_coverage=coverage,
            sampling=sampling,
            errors=errors,
            ready=not errors,
            physical_workload_invocations=0,
            formal_state_mutation=False,
        )
        if errors:
            self.state["phase"] = "calibration_release_rejected_fresh_required"
            self.state["calibration_release_rejection"] = {
                "manifest_path": release.get("manifest_path"),
                "errors": errors,
                "expected_plan_fingerprint": expected_fingerprint,
                "current_plan_fingerprint": current_fingerprint,
            }
            self.save()
            return False

        materialized = materialize_calibration_release(
            release,
            destination=self.t_path,
        )
        artifact = read_json(self.t_path)
        validate_experience_artifact(artifact, self.target)
        self.state["phase"] = "calibration_reused_formal_ready"
        self.state["experience_table_path"] = str(self.t_path)
        self.state["experience_table_artifact_sha256"] = artifact.get(
            "artifact_sha256"
        )
        self.state["calibration_release_reuse"] = {
            **materialized,
            "compatibility_sha256": release.get("compatibility_sha256"),
            "baseline_plan_fingerprint": current_fingerprint,
            "fresh_physical_calibration_invocations": 0,
            "historical_calibration_invocations_not_recounted": True,
            "formal_state_mutation": False,
        }
        self.save()
        self.event(
            "algorithm_c_calibration_release_reused",
            release=self.state["calibration_release_reuse"],
            formal_accepted_start=0,
            physical_workload_invocations=0,
            formal_state_mutation=False,
        )
        return True

    def handoff_formal(self) -> None:
        self.ensure_clean_restore("calibration_complete_or_reused")
        if self.stop_requested:
            self.finish_signal_stop("signal_stop_before_formal_handoff")
            self.event(
                "algorithm_c_formal_handoff_cancelled_clean",
                experience_table_path=str(self.t_path),
                formal_accepted_start=0,
                physical_workload_invocations=0,
                formal_state_mutation=False,
            )
            return
        self.event(
            "algorithm_c_formal_handoff",
            experience_table_path=str(self.t_path),
            formal_accepted_start=0,
            unattended=True,
        )
        self.lane_controller = LaneController(self.cfg_path, self.output_dir, self.target)
        # The calibration controller and formal controller live in the same
        # process.  Pass the just-verified clean receipt across that in-process
        # handoff so formal baseline repetition 1 does not clone both databases
        # again before any workload or parameter mutation has occurred.
        self.lane_controller.prepared_clean_handoff_receipt = copy.deepcopy(
            getattr(self, "_clean_restore_receipt", None)
        )
        if self.stop_requested:
            self.lane_controller.stop_requested = True
        self.lane_controller.run()

    def clean_restore_contract(self) -> str:
        """Fingerprint the process-local facts covered by a clean receipt."""
        worker_contract = []
        for worker in self.workers:
            connection = pg_conn_from_worker(self.cfg, worker)
            def connection_value(name: str) -> Any:
                if isinstance(connection, Mapping):
                    return connection.get(name)
                return getattr(connection, name, None)

            worker_contract.append(
                {
                    "worker": worker_key(worker),
                    "host": connection_value("host"),
                    "port": connection_value("port"),
                    "dbname": connection_value("dbname"),
                    "template_db": worker.get("template_db"),
                    "container": worker.get("container"),
                }
            )
        return canonical_sha256(
            {
                "cfg_sha256": hashlib.sha256(self.cfg_path.read_bytes()).hexdigest(),
                "workers": sorted(worker_contract, key=lambda row: str(row["worker"])),
                "initial_params": self.initial_params,
            }
        )

    def mark_database_mutated(self) -> None:
        """Invalidate any clean receipt before a real or persistent DB action."""
        self._db_mutation_generation = int(
            getattr(self, "_db_mutation_generation", 0)
        ) + 1
        self._clean_restore_receipt = None

    def ensure_clean_restore(self, reason: str) -> Dict[str, Any]:
        """Reuse a verified in-process restore only when no mutation followed it."""
        contract = self.clean_restore_contract()
        receipt = getattr(self, "_clean_restore_receipt", None) or {}
        if (
            receipt.get("generation")
            == int(getattr(self, "_db_mutation_generation", 0))
            and receipt.get("contract_sha256") == contract
        ):
            self.event(
                "algorithm_c_calibration_clean_restore_reused",
                source_reason=receipt.get("reason"),
                current_reason=reason,
                contract_sha256=contract,
                no_workload_since=True,
                no_parameter_mutation_since=True,
                no_database_mutation_since=True,
            )
            return copy.deepcopy(receipt)
        return self.restore_clean(reason)

    def restore_clean(self, reason: str) -> Dict[str, Any]:
        self.mark_database_mutated()
        rows: List[Dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=2) as executor:
            future_map = {
                executor.submit(
                    restore_worker_from_hybench_template,
                    self.cfg,
                    worker,
                    label=f"calibration_restore_{reason}_{worker_key(worker)}",
                ): worker
                for worker in self.workers
            }
            for future in as_completed(future_map):
                worker = future_map[future]
                restore = future.result()
                if not isinstance(restore, Mapping) or restore.get("status") != "ok":
                    raise RuntimeError(f"calibration clean restore failed: {restore}")
                # CREATE DATABASE normally becomes connectable before psql
                # returns.  Immediately after a force-stopped predecessor,
                # however, PostgreSQL can briefly report that the freshly
                # cloned target database does not exist while its catalog
                # invalidation reaches a new backend.  A single readback must
                # not turn a successful physical restore into a controller
                # crash.  Retry only this narrow transient; every other error
                # and the final attempt remain fail-closed.
                readback = None
                for readback_attempt in range(1, 4):
                    try:
                        readback = apply_pg_params(
                            pg_conn_from_worker(self.cfg, worker),
                            self.initial_params,
                        )
                        break
                    except RuntimeError as exc:
                        missing_database = (
                            "database" in str(exc).lower()
                            and "does not exist" in str(exc).lower()
                        )
                        if not missing_database or readback_attempt >= 3:
                            raise
                        self.event(
                            "algorithm_c_calibration_post_restore_readback_retry",
                            worker=worker_key(worker),
                            attempt=readback_attempt,
                            maximum_attempts=3,
                            error=str(exc),
                            formal_state_mutation=False,
                            physical_workload_invocations=0,
                        )
                        time.sleep(float(readback_attempt))
                if readback is None:
                    raise RuntimeError(
                        "calibration post-restore parameter readback unavailable"
                    )
                readback_audit = baseline_parameter_readback_audit(
                    self.initial_params,
                    readback,
                )
                if readback_audit.get("passed") is not True:
                    raise RuntimeError(
                        "calibration baseline parameter readback failed: "
                        f"{worker_key(worker)} {readback_audit}"
                    )
                rows.append(
                    {
                        "worker": worker_key(worker),
                        "restore": restore,
                        "baseline_params_applied": True,
                        "baseline_parameter_readback": readback_audit,
                    }
                )
        if len(rows) != len(self.workers):
            raise RuntimeError("calibration clean restore did not cover every worker")
        contract = self.clean_restore_contract()
        self._clean_restore_receipt = {
            "generation": self._db_mutation_generation,
            "contract_sha256": contract,
            "reason": reason,
            "completed_at_epoch_s": time.time(),
            "workers": copy.deepcopy(rows),
        }
        self.event("algorithm_c_calibration_clean_restore", reason=reason, workers=rows)
        return copy.deepcopy(self._clean_restore_receipt)

    def finish_signal_stop(self, reason: str) -> None:
        """Publish a clean stop only after both workers have a restore receipt."""

        self.state["phase"] = "signal_stop_pending_clean_restore"
        self.save()
        self.event("algorithm_c_signal_stop_restore_start", reason=reason)
        try:
            receipt = self.ensure_clean_restore(reason)
        except Exception as exc:
            self.state["phase"] = "signal_stop_clean_restore_failed"
            self.state["last_signal_stop_restore_failure"] = {
                "reason": reason,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            self.save()
            self.event(
                "algorithm_c_signal_stop_restore_failed",
                reason=reason,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise
        self.state["signal_stop_clean_receipt"] = copy.deepcopy(receipt)
        self.state["phase"] = "stopped_by_signal_clean"
        self.save()
        self.event(
            "algorithm_c_signal_stop_complete",
            reason=reason,
            clean_receipt=receipt,
        )

    def qualify_io_scene_baseline(self, rows: Sequence[Mapping[str, Any]]) -> None:
        """Calibrate IO capacity and reject a CPU/memory co-bottleneck."""
        qualification_cfg = self.algorithm_cfg.get("io_scene_qualification") or {}
        if self.target != "io" or qualification_cfg.get("enabled") is not True:
            return
        capacity = (
            ((self.cfg.get("binary_plan_tuning") or {}).get("acceptance_policy") or {}).get(
                "resource_capacity"
            )
            or {}
        )
        cpu_capacity = float(capacity.get("cpu_cores") or 0.0)
        memory_capacity = float(capacity.get("memory_gib") or 0.0)
        if cpu_capacity <= 0.0 or memory_capacity <= 0.0:
            raise RuntimeError(f"invalid non-target capacity for IO qualification: {capacity}")

        observations: List[Dict[str, Any]] = []
        for row in rows:
            resources = row.get("resources") or {}
            resource_stats = row.get("resource_stats") or {}
            ssd_stats = resource_stats.get("ssd_util_percent") or {}
            sample_counts = resource_stats.get("stable_sample_count") or {}
            ssd_mean = float(ssd_stats.get("avg") or 0.0)
            io_mib_s = float(resources.get("io") or 0.0)
            cpu_cores = float(resources.get("cpu") or 0.0)
            memory_gib = float(resources.get("memory") or 0.0)
            if (
                int(sample_counts.get("io_util_pct") or 0) <= 0
                or not 0.0 < ssd_mean <= 100.0
                or io_mib_s <= 0.0
            ):
                raise RuntimeError(
                    f"missing SSD/cgroup IO baseline evidence: "
                    f"worker={row.get('worker_key')} stats={ssd_stats} "
                    f"sample_counts={sample_counts} io={io_mib_s}"
                )
            observations.append(
                {
                    "worker": row.get("worker_key"),
                    "cpu_cores": cpu_cores,
                    "cpu_util_pct": cpu_cores / cpu_capacity * 100.0,
                    "memory_gib": memory_gib,
                    "memory_util_pct": memory_gib / memory_capacity * 100.0,
                    "io_mib_s": io_mib_s,
                    "ssd_mean_util_pct": ssd_mean,
                    "ssd_peak_util_pct": float(ssd_stats.get("max") or 0.0),
                    "empirical_io_capacity_mib_s": io_mib_s / (ssd_mean / 100.0),
                }
            )

        ssd_mean = statistics.median(
            float(row["ssd_mean_util_pct"]) for row in observations
        )
        max_cpu_util = max(float(row["cpu_util_pct"]) for row in observations)
        max_memory_util = max(float(row["memory_util_pct"]) for row in observations)
        empirical_capacity = statistics.median(
            float(row["empirical_io_capacity_mib_s"]) for row in observations
        )
        minimum_ssd = float(qualification_cfg.get("minimum_ssd_mean_util_pct") or 65.0)
        maximum_ssd = float(qualification_cfg.get("maximum_ssd_mean_util_pct") or 85.0)
        maximum_cpu = float(
            qualification_cfg.get("maximum_non_target_cpu_util_pct") or 80.0
        )
        maximum_memory = float(
            qualification_cfg.get("maximum_non_target_memory_util_pct") or 80.0
        )
        errors: List[str] = []
        if ssd_mean < minimum_ssd:
            errors.append("ssd_mean_utilization_below_io_target_window")
        if ssd_mean > maximum_ssd:
            errors.append("ssd_mean_utilization_above_stable_window")
        if max_cpu_util >= maximum_cpu:
            errors.append("non_target_cpu_is_bottleneck")
        if max_memory_util >= maximum_memory:
            errors.append("non_target_memory_is_bottleneck")
        if not math.isfinite(empirical_capacity) or empirical_capacity <= 0.0:
            errors.append("invalid_empirical_io_capacity")

        result = {
            "contract": "xp_io_same_run_empirical_capacity_v1",
            "passed": not errors,
            "errors": errors,
            "observations": observations,
            "ssd_mean_util_pct": ssd_mean,
            "maximum_cpu_util_pct": max_cpu_util,
            "maximum_memory_util_pct": max_memory_util,
            "derived_io_capacity_mib_s": empirical_capacity,
            "capacity_formula": "median(worker_io_mib_s / worker_ssd_mean_util_fraction)",
            "qps_used": False,
            "thresholds": {
                "minimum_ssd_mean_util_pct": minimum_ssd,
                "maximum_ssd_mean_util_pct": maximum_ssd,
                "maximum_non_target_cpu_util_pct": maximum_cpu,
                "maximum_non_target_memory_util_pct": maximum_memory,
            },
        }
        self.event("algorithm_c_io_scene_qualification", qualification=result)
        self.state["io_scene_qualification"] = result
        if errors:
            self.state["phase"] = "io_scene_qualification_failed_clean"
            self.save()
            self.restore_clean("io_scene_qualification_failed")
            raise RuntimeError(f"XP IO scene qualification failed: {errors}")

        capacity["io_mib_s"] = empirical_capacity
        contract = read_json(self.workload_contract_path)
        contract.setdefault("resource_capacity", {})["io_mib_s"] = empirical_capacity
        contract["io_capacity_calibration"] = copy.deepcopy(result)
        self.algorithm_cfg["workload_contract"] = contract
        self.algorithm_cfg["io_capacity_calibration"] = copy.deepcopy(result)
        write_json(self.workload_contract_path, contract)
        write_json(self.cfg_path, self.cfg)
        self.state["effective_io_capacity_mib_s"] = empirical_capacity
        self.save()

    def run_baseline(self) -> None:
        if "baseline" in self.state["completed_batches"]:
            return
        if self.stop_requested:
            self.event(
                "algorithm_c_calibration_stop_before_baseline_workload_lease",
                physical_workload_invocations=0,
                formal_state_mutation=False,
            )
            return
        self.state["phase"] = "fresh_baseline"
        self.save()
        rows: List[Dict[str, Any]] = []
        try:
            workload_lease, workload_lease_wait_s = (
                acquire_configured_shared_ssd_workload_coordination(
                    cancel_requested=lambda: self.stop_requested,
                )
            )
        except RuntimeError:
            if self.stop_requested:
                self.event(
                    "algorithm_c_calibration_stop_while_waiting_baseline_workload_lease",
                    physical_workload_invocations=0,
                    formal_state_mutation=False,
                )
                return
            raise
        self.event(
            "algorithm_c_calibration_workload_lease_acquired",
            phase="calibration_baseline",
            wait_s=workload_lease_wait_s,
            physical_workload_invocations=0,
            formal_state_mutation=False,
        )
        try:
            self.mark_database_mutated()
            with ThreadPoolExecutor(max_workers=2) as executor:
                future_map = {
                    executor.submit(
                        run_real_validation_safe,
                        self.cfg,
                        self.initial_params,
                        output_dir=self.output_dir / "calibration" / "baseline" / worker_key(worker),
                        label=f"calibration_baseline_{worker_key(worker)}",
                        # ``run()`` always restores both workers immediately
                        # before an incomplete baseline.  Mark that restore as
                        # prepared so validation does not clone the same database
                        # a second time before applying the baseline parameters.
                        worker=worker_with_prepared_restore(worker),
                        force_new_replay=True,
                    ): worker
                    for worker in self.workers
                }
                for future in as_completed(future_map):
                    worker = future_map[future]
                    row = future.result()
                    row["event"] = "baseline_worker_validation"
                    row["worker_key"] = worker_key(worker)
                    rows.append(row)
        finally:
            release_shared_ssd_coordination(workload_lease)
        qualification = apply_ssd_wave_qualification(
            rows,
            target=self.target,
            phase="calibration_baseline",
            cfg=self.cfg,
        )
        self.event(
            "algorithm_c_ssd_wave_qualification",
            phase="calibration_baseline",
            qualification=qualification,
        )
        for row in rows:
            append_jsonl(self.log_path, row)
        self.state["physical_workload_invocations"] += len(rows)
        valid_count = sum(physical_valid(row) for row in rows)
        self.state["valid_replays"] += valid_count
        self.state["technical_failures"] += len(rows) - valid_count
        if valid_count != 2:
            self.state["phase"] = "baseline_technical_failure"
            self.save()
            self.restore_clean("baseline_technical_failure")
            raise RuntimeError("fresh calibration baseline did not yield two valid rows")
        self.qualify_io_scene_baseline(rows)
        self.state["completed_batches"].append("baseline")
        self.save()
        self.restore_clean("baseline")

    def run_candidate_batch(
        self,
        batch_index: int,
        design_start: int,
        *,
        candidates_override: Optional[Sequence[Mapping[str, float]]] = None,
    ) -> bool:
        batch_name = f"candidate_batch_{batch_index:02d}"
        if batch_name in self.state["completed_batches"]:
            return True
        if batch_name in self.failed_batch_names():
            self.event(
                "algorithm_c_calibration_failed_batch_not_replayed",
                batch_index=batch_index,
                batch_name=batch_name,
                next_action="use_distinct_plan_change_only_batch",
                physical_workload_invocations=0,
                formal_state_mutation=False,
            )
            return False
        if self.stop_requested:
            self.event(
                "algorithm_c_calibration_stop_before_batch",
                batch_index=batch_index,
                batch_name=batch_name,
                physical_workload_invocations=0,
                formal_state_mutation=False,
            )
            return False
        candidates = (
            [
                {name: float(value) for name, value in candidate.items()}
                for candidate in candidates_override
            ]
            if candidates_override is not None
            else [
                deterministic_design_point(design_start + offset, self.initial_params)
                for offset in range(5)
            ]
        )
        if len(candidates) != 5:
            raise RuntimeError(
                f"calibration batch requires exactly five candidates, got {len(candidates)}"
            )
        existing_rows = self.logged_candidate_batch_rows(batch_index)
        self.account_candidate_batch_rows(
            batch_index=batch_index,
            completed=existing_rows,
            source="run_candidate_batch_resume_jsonl_reconciliation",
        )
        rows_by_position: Dict[int, Dict[str, Any]] = {}
        duplicate_positions: List[int] = []
        for row in existing_rows:
            position = self.candidate_position(row, design_start)
            if position <= 0:
                self.state["phase"] = "candidate_batch_resume_evidence_invalid_clean"
                self.save()
                raise RuntimeError(
                    f"calibration batch {batch_index} has a row without a valid c1-c5 position"
                )
            if position in rows_by_position:
                duplicate_positions.append(position)
            rows_by_position[position] = row
        if len(existing_rows) > 5 or duplicate_positions:
            valid_count = sum(physical_valid(row) for row in existing_rows)
            self.record_failed_batch(
                batch_index=batch_index,
                physical_count=len(existing_rows),
                valid_count=valid_count,
                source="resume_detected_duplicate_or_over_budget_positions",
            )
            self.event(
                "algorithm_c_calibration_batch_resume_fail_closed",
                batch_index=batch_index,
                physical_workload_invocations=len(existing_rows),
                duplicate_positions=sorted(set(duplicate_positions)),
                physical_batch_limit=5,
                candidate_batch_retry=False,
            )
            return False
        for position, row in rows_by_position.items():
            observed_params = row.get("params")
            expected_params = candidates[position - 1]
            if not isinstance(observed_params, Mapping) or any(
                name not in observed_params
                or not math.isclose(
                    float(observed_params[name]),
                    float(expected_params[name]),
                    rel_tol=0.0,
                    abs_tol=1e-15,
                )
                for name in ACTIVE_PARAMS
            ):
                self.state["phase"] = "candidate_batch_resume_evidence_invalid_clean"
                self.save()
                raise RuntimeError(
                    "calibration resume candidate vector mismatch: "
                    f"batch={batch_index} position={position}"
                )
        if len(rows_by_position) == 5:
            valid_count = sum(physical_valid(row) for row in rows_by_position.values())
            if valid_count != 5:
                self.record_failed_batch(
                    batch_index=batch_index,
                    physical_count=5,
                    valid_count=valid_count,
                    source="resume_full_batch_with_technical_failure",
                )
                self.restore_clean(batch_name)
                return False
            self.state["completed_batches"].append(batch_name)
            self.state.setdefault("candidate_batch_progress", {}).pop(batch_name, None)
            self.save()
            self.restore_clean(f"{batch_name}_resume_complete")
            self.event(
                "algorithm_c_calibration_batch_resumed_without_replay",
                batch_index=batch_index,
                completed_positions=[1, 2, 3, 4, 5],
                physical_workload_invocations=0,
            )
            return True
        completed_positions = set(rows_by_position)
        self.state["phase"] = batch_name
        self.save()
        self.event(
            "algorithm_c_calibration_batch_start",
            batch_index=batch_index,
            physical_batch_limit=5,
            candidate_count=5,
            replay_parallelism=2,
            formal_state_mutation=False,
            candidates=candidates,
            design_audit=self.design_audit,
        )
        completed: List[Dict[str, Any]] = []
        batch_aborted_out_of_band = False
        for wave_start in range(0, 5, 2):
            if self.stop_requested:
                partial_accounted = self.account_candidate_batch_rows(
                    batch_index=batch_index,
                    completed=self.logged_candidate_batch_rows(batch_index),
                    source="stop_before_wave_restore_partial_batch",
                )
                self.event(
                    "algorithm_c_calibration_stop_before_wave_restore",
                    batch_index=batch_index,
                    wave_start=wave_start,
                    physical_workload_invocations=len(completed),
                    newly_accounted_physical=partial_accounted,
                    partial_batch_sealed=False,
                    formal_state_mutation=False,
                )
                return False
            self.mark_database_mutated()
            wave: List[Tuple[int, Dict[str, float], Dict[str, Any], str, Dict[str, Any]]] = []
            restore_requests: List[
                Tuple[int, Dict[str, float], Dict[str, Any], str]
            ] = []
            for position in range(wave_start, min(wave_start + 2, 5)):
                if position + 1 in completed_positions:
                    continue
                worker = self.workers[position % 2]
                label = (
                    f"calibration_r{batch_index:02d}_c{position + 1:02d}_"
                    f"{worker_key(worker)}"
                )
                restore_requests.append(
                    (position, candidates[position], worker, label)
                )
            restore_by_position: Dict[int, Dict[str, Any]] = {}
            with ThreadPoolExecutor(max_workers=2) as executor:
                future_map = {
                    executor.submit(
                        restore_worker_from_hybench_template,
                        self.cfg,
                        worker,
                        label=label,
                    ): (position, worker)
                    for position, _params, worker, label in restore_requests
                }
                for future in as_completed(future_map):
                    position, worker = future_map[future]
                    restore = future.result()
                    if restore.get("status") != "ok":
                        raise RuntimeError(
                            "calibration restore failed: "
                            f"worker={worker_key(worker)} restore={restore}"
                        )
                    restore_by_position[position] = restore
            for position, params, worker, label in restore_requests:
                wave.append(
                    (position, params, worker, label, restore_by_position[position])
                )
            if not wave:
                continue
            self.event(
                "algorithm_c_calibration_restore_barrier",
                batch_index=batch_index,
                wave_start=wave_start,
                worker_count=len(wave),
            )
            if self.stop_requested:
                partial_accounted = self.account_candidate_batch_rows(
                    batch_index=batch_index,
                    completed=self.logged_candidate_batch_rows(batch_index),
                    source="stop_after_restore_before_replay_partial_batch",
                )
                self.event(
                    "algorithm_c_calibration_stop_after_restore_before_replay",
                    batch_index=batch_index,
                    wave_start=wave_start,
                    worker_count=len(wave),
                    physical_workload_invocations=len(completed),
                    newly_accounted_physical=partial_accounted,
                    partial_batch_sealed=False,
                    formal_state_mutation=False,
                )
                return False
            try:
                workload_lease, workload_lease_wait_s = (
                    acquire_configured_shared_ssd_workload_coordination(
                        cancel_requested=lambda: self.stop_requested,
                    )
                )
            except RuntimeError:
                if self.stop_requested:
                    self.event(
                        "algorithm_c_calibration_stop_while_waiting_workload_lease",
                        batch_index=batch_index,
                        wave_start=wave_start,
                        physical_workload_invocations=len(completed),
                        formal_state_mutation=False,
                    )
                    return False
                raise
            self.event(
                "algorithm_c_calibration_workload_lease_acquired",
                phase="calibration_candidate",
                batch_index=batch_index,
                wave_start=wave_start,
                wait_s=workload_lease_wait_s,
                physical_workload_invocations=0,
                formal_state_mutation=False,
            )
            try:
                with ThreadPoolExecutor(max_workers=2) as executor:
                    future_map = {
                        executor.submit(
                            run_real_validation_safe,
                            self.cfg,
                            params,
                            output_dir=self.output_dir / "calibration" / batch_name,
                            label=label,
                            worker=worker_with_prepared_restore(worker),
                            force_new_replay=True,
                        ): (position, worker, restore)
                        for position, params, worker, label, restore in wave
                    }
                    for future in as_completed(future_map):
                        position, worker, restore = future_map[future]
                        row = future.result()
                        row["event"] = "candidate_validation"
                        row["round"] = batch_index
                        row["candidate_index"] = design_start + position
                        row["candidate_position"] = position + 1
                        row["worker_key"] = worker_key(worker)
                        row["restore"] = restore
                        completed.append(row)
            finally:
                release_shared_ssd_coordination(workload_lease)
            wave_rows = completed[-len(wave):]
            qualification = apply_ssd_wave_qualification(
                wave_rows,
                target=self.target,
                phase="calibration_candidate",
                cfg=self.cfg,
            )
            self.event(
                "algorithm_c_ssd_wave_qualification",
                phase="calibration_candidate",
                batch_index=batch_index,
                wave_start=wave_start,
                qualification=qualification,
            )
            for row in wave_rows:
                append_jsonl(self.log_path, row)
            if qualification.get("passed") is not True:
                batch_aborted_out_of_band = True
                self.event(
                    "algorithm_c_calibration_batch_aborted_out_of_band",
                    batch_index=batch_index,
                    executed_slots=len(completed),
                    remaining_slots=5 - len(completed),
                    formal_state_mutation=False,
                )
                break
            if self.stop_requested:
                break
        self.account_candidate_batch_rows(
            batch_index=batch_index,
            completed=self.logged_candidate_batch_rows(batch_index),
            source="current_controller",
        )
        all_rows = self.logged_candidate_batch_rows(batch_index)
        all_by_position = {
            self.candidate_position(row, design_start): row for row in all_rows
        }
        if len(all_rows) > 5 or 0 in all_by_position or len(all_by_position) != len(all_rows):
            valid_count = sum(physical_valid(row) for row in all_rows)
            self.record_failed_batch(
                batch_index=batch_index,
                physical_count=len(all_rows),
                valid_count=valid_count,
                source="current_controller_duplicate_or_over_budget",
            )
            self.restore_clean(batch_name)
            return False
        if len(all_by_position) < 5:
            if batch_aborted_out_of_band:
                valid_count = sum(
                    physical_valid(row) for row in all_by_position.values()
                )
                self.record_failed_batch(
                    batch_index=batch_index,
                    physical_count=len(all_by_position),
                    valid_count=valid_count,
                    source="ssd_out_of_band_partial_batch_do_not_resume",
                )
                self.restore_clean(batch_name)
                self.event(
                    "algorithm_c_calibration_out_of_band_partial_batch_sealed",
                    batch_index=batch_index,
                    completed_positions=sorted(all_by_position),
                    pending_positions=sorted(
                        set(range(1, 6)) - set(all_by_position)
                    ),
                    total_physical_workload_invocations=len(all_rows),
                    candidate_batch_retry=False,
                    formal_state_mutation=False,
                )
                return False
            self.state["phase"] = "candidate_batch_partial_accounted_clean"
            self.save()
            self.restore_clean(batch_name)
            self.event(
                "algorithm_c_calibration_partial_batch_pending_resume",
                batch_index=batch_index,
                completed_positions=sorted(all_by_position),
                pending_positions=sorted(set(range(1, 6)) - set(all_by_position)),
                total_physical_workload_invocations=len(all_rows),
                candidate_batch_retry=False,
                resume_missing_positions_only=True,
            )
            return False
        valid_count = sum(physical_valid(row) for row in all_by_position.values())
        if valid_count != 5:
            self.record_failed_batch(
                batch_index=batch_index,
                physical_count=5,
                valid_count=valid_count,
                source="current_controller_full_batch_with_technical_failure",
            )
            self.restore_clean(batch_name)
            return False
        self.state["completed_batches"].append(batch_name)
        self.state.setdefault("candidate_batch_progress", {}).pop(batch_name, None)
        self.save()
        self.restore_clean(batch_name)
        return True

    def build_table(self) -> Dict[str, Any]:
        self.materialize_plan_predictions()
        scoring_options = formal_inner_scoring_options(self.target)
        resource_weights = scoring_options.get("resource_weights") or {}
        scored_resources = [
            resource
            for resource in ("cpu", "memory", "io")
            if float(resource_weights.get(resource) or 0.0) > 0.0
        ]
        command = [
            sys.executable,
            str(ROOT / "shell/build_algorithm_c_experience_table_from_replays.py"),
            "--history",
            str(self.log_path),
            "--source-algorithm",
            "algorithm_c",
            "--benchmark",
            str(self.algorithm_cfg.get("benchmark")),
            "--target",
            self.target,
            "--active-params",
            ",".join(ACTIVE_PARAMS),
            "--workload-contract",
            str(self.workload_contract_path),
            "--output",
            str(self.t_path),
            "--plan-predictions",
            str(self.plan_predictions_path),
        ]
        if self.algorithm_cfg.get("target_support_abstention_enabled") is True:
            command.append("--enable-target-support-abstention")
        for resource in scored_resources:
            command.extend(["--scored-resource", resource])
        completed = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.event(
            "algorithm_c_calibration_table_build",
            command=command,
            returncode=completed.returncode,
            stdout=completed.stdout[-4000:],
            stderr=completed.stderr[-4000:],
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"experience-table builder failed: {completed.stderr or completed.stdout}"
            )
        return read_json(self.t_path)

    def build_table_for_admission(self) -> Dict[str, Any]:
        """Represent insufficient calibration evidence as a non-ready gate.

        The table builder correctly requires ten valid, distinct parameter
        vectors.  Technical wave failures can leave fewer than ten vectors;
        that is a reason to collect another independent batch, not a reason
        for the controller itself to die.  Every other builder error remains
        fatal and is re-raised unchanged.
        """
        try:
            artifact = self.build_table()
            required_round = int(
                getattr(self, "minimum_fresh_direction_holdout_round", 0)
            )
            holdout_rounds = [
                int(value)
                for value in ((artifact.get("fit") or {}).get("holdout_outer_rounds") or [])
            ]
            observed_round = max(holdout_rounds, default=0)
            if required_round > 0 and observed_round < required_round:
                artifact = copy.deepcopy(artifact)
                admission = artifact.setdefault("formal_admission", {})
                gate_error = (
                    "direction_contract_fresh_holdout_round_below_"
                    f"{required_round}"
                )
                errors = list(admission.get("errors") or [])
                if gate_error not in errors:
                    errors.append(gate_error)
                admission["ready"] = False
                admission["errors"] = errors
                admission["direction_contract_fresh_holdout_gate"] = {
                    "required_minimum_round": required_round,
                    "observed_latest_holdout_round": observed_round,
                    "passed": False,
                    "reason": (
                        "directional_admission_contract_changed_after_prior_"
                        "holdout_results_were_inspected"
                    ),
                    "same_holdout_train_and_declare_forbidden": True,
                    "formal_state_mutation": False,
                }
                artifact.pop("artifact_sha256", None)
                artifact["artifact_sha256"] = canonical_sha256(artifact)
                write_json(self.t_path, artifact)
                self.event(
                    "algorithm_c_direction_contract_fresh_holdout_required",
                    required_minimum_round=required_round,
                    observed_latest_holdout_round=observed_round,
                    errors=[gate_error],
                    physical_workload_invocations=0,
                    formal_state_mutation=False,
                )
            return artifact
        except RuntimeError as exc:
            match = re.search(
                r"current-scope T requires at least 30 valid physical "
                r"observations, got (\d+)",
                str(exc),
            )
            if match is None:
                raise
            observed_count = int(match.group(1))
            error = "valid_physical_resource_observation_count_below_30"
            self.event(
                "algorithm_c_calibration_table_insufficient_valid_vectors",
                observed_valid_distinct_parameter_vectors=observed_count,
                minimum_valid_physical_resource_observations=30,
                errors=[error],
                action="collect_fresh_independent_plan_change_batch",
                qps_used=False,
                physical_workload_invocations=0,
                formal_state_mutation=False,
            )
            return {
                "contract": "algorithm_c_insufficient_calibration_evidence_v1",
                "formal_admission": {
                    "ready": False,
                    "errors": [error],
                    "observed_valid_distinct_parameter_vectors": observed_count,
                    "minimum_valid_physical_resource_observations": 30,
                },
            }

    def materialize_plan_predictions(self) -> None:
        """Build QPS-free full-plan predictions for calibration admission."""
        rows: List[Dict[str, Any]] = []
        if self.log_path.is_file():
            for raw_line in self.log_path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines():
                try:
                    row = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                if row.get("event") != "candidate_validation":
                    continue
                if not physical_valid(row):
                    continue
                if not isinstance(row.get("params"), Mapping):
                    continue
                rows.append(row)
        if not rows:
            raise RuntimeError("no valid calibration rows for plan prediction evidence")

        probe = PlanFeatureProbe(self.cfg)
        queries, sampling = workload_queries_with_sampling(self.cfg, probe)
        if sampling.get("sampling_applied"):
            raise RuntimeError(
                f"calibration plan prediction forbids workload sampling: {sampling}"
            )
        baseline = probe.probe(queries, copy.deepcopy(self.initial_params))
        baseline_coverage = (
            (baseline.get("exact_plan_metadata") or {}).get(
                "plan_fingerprint_coverage"
            )
            or {}
        )
        if baseline_coverage.get("complete") is not True:
            raise RuntimeError(
                f"calibration baseline plan coverage incomplete: {baseline_coverage}"
            )
        baseline_resources = baseline.get("resource_avg") or {}
        predictions: List[Dict[str, Any]] = []
        for row in rows:
            candidate = probe.probe(queries, dict(row["params"]))
            coverage = (
                (candidate.get("exact_plan_metadata") or {}).get(
                    "plan_fingerprint_coverage"
                )
                or {}
            )
            if coverage.get("complete") is not True:
                raise RuntimeError(
                    f"calibration candidate plan coverage incomplete: "
                    f"label={row.get('label')} coverage={coverage}"
                )
            candidate_resources = candidate.get("resource_avg") or {}
            predicted_log_ratios: Dict[str, float] = {}
            for resource in ("cpu", "memory", "io"):
                baseline_value = float(baseline_resources.get(resource) or 0.0)
                candidate_value = float(candidate_resources.get(resource) or 0.0)
                if baseline_value <= 0.0 or candidate_value <= 0.0:
                    raise RuntimeError(
                        f"invalid plan resource estimate: label={row.get('label')} "
                        f"resource={resource} baseline={baseline_value} "
                        f"candidate={candidate_value}"
                    )
                predicted_log_ratios[resource] = math.log(
                    candidate_value / baseline_value
                )
            predictions.append(
                {
                    "label": str(row.get("label") or ""),
                    "candidate_index": int(row.get("candidate_index") or 0),
                    "round": int(row.get("round") or 0),
                    "params_sha256": canonical_sha256(dict(row["params"])),
                    "predicted_log_ratios": predicted_log_ratios,
                    "workload_plan_fingerprint": (
                        (candidate.get("exact_plan_metadata") or {}).get(
                            "workload_plan_fingerprint"
                        )
                    ),
                    "plan_coverage": coverage,
                    "probe_error_count": len(candidate.get("errors") or []),
                }
            )
        artifact = {
            "contract": "algorithm_c_pre_replay_plan_prediction_v1",
            "selection_predictor_contract": "executor_physics_full_plan_ratio_v2",
            "qps_used": False,
            "actual_results_used": False,
            "physical_workload_invocations": 0,
            "workload_query_count": len(queries),
            "sampling": sampling,
            "baseline_plan_fingerprint": (
                (baseline.get("exact_plan_metadata") or {}).get(
                    "workload_plan_fingerprint"
                )
            ),
            "baseline_plan_coverage": baseline_coverage,
            "predictions": predictions,
        }
        artifact["artifact_sha256"] = canonical_sha256(artifact)
        write_json(self.plan_predictions_path, artifact)
        self.event(
            "algorithm_c_calibration_plan_predictions",
            evidence_path=str(self.plan_predictions_path),
            artifact_sha256=artifact["artifact_sha256"],
            prediction_count=len(predictions),
            workload_query_count=len(queries),
            qps_used=False,
            actual_results_used=False,
            physical_workload_invocations=0,
        )

    def run(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        controller_path = Path(__file__).resolve()
        self.event(
            "algorithm_c_calibration_controller_source_manifest",
            controller_path=str(controller_path),
            controller_sha256=hashlib.sha256(
                controller_path.read_bytes()
            ).hexdigest(),
            formal_state_mutation=False,
            physical_workload_invocations=0,
        )
        self.apply_preformal_runtime_contract_repairs()
        self.reclassify_accounted_legacy_over_wall_rows()
        self.recover_legacy_failed_batch_state()
        if self.try_reuse_calibration_release():
            self.handoff_formal()
            return
        self.event(
            "algorithm_c_fresh_calibration_start",
            physical_batch_limit=5,
            formal_accepted_count=0,
            historical_accepted_inherited=False,
            calibration_design=self.design_audit,
        )
        if "baseline" not in self.state["completed_batches"]:
            self.state["phase"] = "startup_clean_restore"
            self.save()
            self.restore_clean("startup_before_fresh_baseline")
            if self.stop_requested:
                self.finish_signal_stop("signal_stop_after_startup_clean_restore")
                return
        self.run_baseline()
        if self.stop_requested:
            self.finish_signal_stop("signal_stop_after_baseline")
            return
        # Fresh fallback calibration is deliberately bounded in five-point
        # waves.  The resource-quality model needs at least twenty grouped
        # training observations plus ten independent holdout observations;
        # prediction quality is a hard formal-start gate.  Normally an exact
        # immutable offline A/B/C-trained release is reused and these waves
        # are skipped entirely.
        batch_1_completed = self.run_candidate_batch(1, 0)
        if self.stop_requested:
            self.finish_signal_stop("signal_stop_after_candidate_batch_01")
            return
        if not batch_1_completed:
            # A calibration batch is a physical-budget boundary, not a
            # semantic prerequisite.  Out-of-band/non-target saturation makes
            # the affected rows technical failures and seals the immutable
            # batch, but later distinct batches can still provide the required
            # ten training rows plus an independent five-row holdout.  Never
            # replay batch 1 and never let its technical rows enter the model.
            self.event(
                "algorithm_c_calibration_training_batch_unavailable",
                batch_index=1,
                action="advance_to_distinct_batch_02_without_retry",
                failed_batches=copy.deepcopy(
                    self.state.get("failed_batches") or []
                ),
                physical_workload_invocations=0,
                candidate_batch_retry=False,
                technical_failures_used_for_model=False,
                formal_state_mutation=False,
            )
        batch_2_completed = self.run_candidate_batch(2, 5)
        if self.stop_requested:
            self.finish_signal_stop("signal_stop_after_candidate_batch_02")
            return
        if not batch_2_completed:
            # Batch 1 is the minimum training set and remains mandatory.  Batch
            # 2 is the first independent holdout.  A technically invalid
            # holdout is immutable and must never be replayed, but it must not
            # permanently deadlock calibration either: build the table from
            # the remaining valid evidence, let admission fail closed, and
            # collect batch 3 as a fresh independent holdout below.
            self.event(
                "algorithm_c_calibration_holdout_batch_unavailable",
                batch_index=2,
                action="skip_immutable_failed_batch_collect_fresh_batch_03",
                failed_batches=copy.deepcopy(
                    self.state.get("failed_batches") or []
                ),
                physical_workload_invocations=0,
                candidate_batch_retry=False,
                formal_state_mutation=False,
            )
            replacement_batch_completed = self.run_candidate_batch(3, 10)
            if self.stop_requested:
                self.finish_signal_stop(
                    "signal_stop_after_replacement_candidate_batch_03"
                )
                return
            if not replacement_batch_completed:
                # A second out-of-band failure is still not a model result,
                # but it must not terminate the controller.  Both partial
                # batches are already immutable and excluded from holdout
                # admission; continue from the remaining valid evidence and
                # let the plan-change/adaptive stages choose a fresh batch.
                self.event(
                    "algorithm_c_calibration_replacement_holdout_unavailable",
                    batch_index=3,
                    action="continue_with_distinct_plan_change_only_batch",
                    failed_batches=copy.deepcopy(
                        self.state.get("failed_batches") or []
                    ),
                    physical_workload_invocations=0,
                    formal_state_mutation=False,
                )
        artifact = self.build_table_for_admission()
        if (artifact.get("formal_admission") or {}).get("ready") is not True:
            self.event(
                "algorithm_c_calibration_holdout_failed",
                attempt=1,
                errors=(artifact.get("formal_admission") or {}).get("errors"),
                action="collect_new_independent_holdout",
            )
            batch_3_completed = self.run_candidate_batch(3, 10)
            if self.stop_requested:
                self.finish_signal_stop("signal_stop_after_candidate_batch_03")
                return
            if not batch_3_completed:
                self.event(
                    "algorithm_c_calibration_optional_holdout_batch_unavailable",
                    batch_index=3,
                    action="continue_with_distinct_plan_change_only_batch",
                    failed_batches=copy.deepcopy(
                        self.state.get("failed_batches") or []
                    ),
                    formal_state_mutation=False,
                )
            artifact = self.build_table_for_admission()
        if (artifact.get("formal_admission") or {}).get("ready") is not True:
            self.event(
                "algorithm_c_calibration_holdout_failed",
                attempt=2,
                errors=(artifact.get("formal_admission") or {}).get("errors"),
                action="collect_plan_change_only_extended_independent_holdout",
            )
            batch_4_name = "candidate_batch_04"
            batch_4_available = batch_4_name not in self.failed_batch_names()
            if batch_4_available:
                expansion_audit = self.audit_plan_change_expansion(
                    batch_index=4,
                    design_start=15,
                )
                if expansion_audit.get("ready") is not True:
                    self.state["phase"] = "calibration_expansion_plan_gate_failed_clean"
                    self.save()
                    self.ensure_clean_restore("expansion_plan_gate_failed")
                    raise RuntimeError(
                        "fresh T expansion plan-change gate failed: "
                        f"{expansion_audit.get('errors')}"
                    )
                batch_4_available = self.run_candidate_batch(4, 15)
                if self.stop_requested:
                    self.finish_signal_stop("signal_stop_after_candidate_batch_04")
                    return
            if batch_4_available:
                artifact = self.build_table_for_admission()
        if (artifact.get("formal_admission") or {}).get("ready") is not True:
            self.event(
                "algorithm_c_calibration_holdout_failed",
                attempt=3,
                errors=(artifact.get("formal_admission") or {}).get("errors"),
                action="collect_distinct_batch_after_failed_or_inaccurate_round_4",
                failed_batches=copy.deepcopy(self.state.get("failed_batches") or []),
            )
            expansion_audit = self.audit_plan_change_expansion(
                batch_index=5,
                design_start=20,
            )
            if expansion_audit.get("ready") is not True:
                self.state["phase"] = "calibration_expansion_plan_gate_failed_clean"
                self.save()
                self.ensure_clean_restore("expansion_plan_gate_failed_round_05")
                raise RuntimeError(
                    "fresh T round-5 expansion plan-change gate failed: "
                    f"{expansion_audit.get('errors')}"
                )
            batch_5_available = self.run_candidate_batch(5, 20)
            if self.stop_requested:
                self.finish_signal_stop("signal_stop_after_candidate_batch_05")
                return
            if batch_5_available:
                artifact = self.build_table_for_admission()
        adaptive_attempt = 4
        while (artifact.get("formal_admission") or {}).get("ready") is not True:
            used_batch_indices = {
                int(item.get("batch_index") or 0)
                for item in (self.state.get("failed_batches") or [])
                if int(item.get("batch_index") or 0) > 0
            }
            for name in self.state.get("completed_batches") or []:
                match = re.fullmatch(r"candidate_batch_(\d+)", str(name))
                if match:
                    used_batch_indices.add(int(match.group(1)))
            for failed_batch in self.state.get("failed_batches") or []:
                if not isinstance(failed_batch, Mapping):
                    continue
                try:
                    failed_batch_index = int(failed_batch.get("batch_index") or 0)
                except (TypeError, ValueError):
                    failed_batch_index = 0
                if failed_batch_index > 0:
                    used_batch_indices.add(failed_batch_index)
            adaptive_batch_index = max({5, *used_batch_indices}) + 1
            if adaptive_batch_index > self.maximum_calibration_candidate_batches:
                self.state["phase"] = "calibration_holdout_budget_exhausted_clean"
                self.save()
                self.ensure_clean_restore("calibration_holdout_budget_exhausted")
                self.event(
                    "algorithm_c_calibration_holdout_budget_exhausted",
                    maximum_candidate_batches=(
                        self.maximum_calibration_candidate_batches
                    ),
                    next_batch_index=adaptive_batch_index,
                    formal_accepted_count=0,
                    formal_state_mutation=False,
                    technical_failure=False,
                    errors=(artifact.get("formal_admission") or {}).get("errors"),
                )
                raise RuntimeError(
                    "fresh T holdout remained inaccurate after bounded calibration"
                )
            self.event(
                "algorithm_c_calibration_holdout_failed",
                attempt=adaptive_attempt,
                errors=(artifact.get("formal_admission") or {}).get("errors"),
                action="collect_adaptive_distinct_plan_change_only_holdout",
                adaptive_batch_index=adaptive_batch_index,
                failed_batches=copy.deepcopy(self.state.get("failed_batches") or []),
            )
            adaptive_audit = self.audit_adaptive_plan_change_expansion(
                batch_index=adaptive_batch_index,
            )
            if adaptive_audit.get("ready") is not True:
                self.state["phase"] = "calibration_expansion_plan_gate_failed_clean"
                self.save()
                self.ensure_clean_restore(
                    f"adaptive_expansion_plan_gate_failed_round_{adaptive_batch_index:02d}"
                )
                raise RuntimeError(
                    "fresh T adaptive expansion plan gate failed for batch "
                    f"{adaptive_batch_index}: {adaptive_audit.get('errors')}"
                )
            batch_completed = self.run_candidate_batch(
                adaptive_batch_index,
                len(CALIBRATION_SIGN_MATRIX),
                candidates_override=adaptive_audit.get("selected_candidates") or [],
            )
            if self.stop_requested:
                self.finish_signal_stop(
                    f"signal_stop_after_candidate_batch_{adaptive_batch_index:02d}"
                )
                return
            if not batch_completed:
                self.event(
                    "algorithm_c_calibration_adaptive_failed_batch_advanced",
                    failed_batch_index=adaptive_batch_index,
                    next_batch_index=adaptive_batch_index + 1,
                    physical_workload_invocations=0,
                    formal_state_mutation=False,
                    failed_batches=copy.deepcopy(
                        self.state.get("failed_batches") or []
                    ),
                )
                artifact = self.build_table_for_admission()
                adaptive_attempt += 1
                continue
            artifact = self.build_table_for_admission()
            adaptive_attempt += 1
        self.state["phase"] = "calibration_complete_formal_ready"
        self.state["experience_table_path"] = str(self.t_path)
        self.state["experience_table_artifact_sha256"] = artifact.get("artifact_sha256")
        if bool(getattr(self, "calibration_release_enabled", False)):
            release = publish_calibration_release(
                cfg=self.cfg,
                source_dir=self.output_dir,
                artifact_path=self.t_path,
                release_root=Path(
                    getattr(self, "calibration_release_root", DEFAULT_RELEASE_ROOT)
                ),
            )
            self.state["calibration_release"] = {
                "manifest_path": release.get("manifest_path"),
                "manifest_sha256": release.get("manifest_sha256"),
                "compatibility_sha256": release.get("compatibility_sha256"),
                "artifact_internal_sha256": release.get(
                    "artifact_internal_sha256"
                ),
                "immutable": True,
            }
        self.save()
        self.handoff_formal()

    def apply_preformal_runtime_contract_repairs(self) -> None:
        """Verify analytical-workload hard wall-time contracts without mutation.

        Legacy JOB and TPC-H configs used multi-hour outer timeouts.  Once 900
        seconds became a hard workload contract, those settings made an
        already-invalid point keep consuming the lane and contradicted the
        serialized authority.  Runtime code must fail closed instead of
        silently changing that contract.
        """
        scene = str(self.algorithm_cfg.get("scene") or "")
        if scene not in {"job_memory", "tpch_memory"}:
            return
        formal_state_path = self.output_dir / "algorithm_c_state.json"
        if formal_state_path.is_file():
            formal_state = read_json(formal_state_path)
            if formal_state.get("formal_started") is True:
                raise RuntimeError(
                    f"{scene} runtime timeout verification is forbidden "
                    "after formal start"
                )
        validation = ((self.cfg.get("binary_plan_tuning") or {}).get("validation") or {})
        hybench = validation.get("hybench_xp") or {}
        concurrency = (
            (self.algorithm_cfg.get("workload_contract") or {}).get(
                "benchmark_concurrency"
            )
            or {}
        )
        target_wall_time_s = int(concurrency.get("target_wall_time_s") or 0)
        timeout_s = int(hybench.get("timeout_s") or 0)
        timeout_grace_s = int(hybench.get("timeout_grace_s") or 0)
        error_prefix = "job" if scene == "job_memory" else "tpch"
        errors = []
        if target_wall_time_s != 900:
            errors.append(f"{error_prefix}_target_wall_time_not_900")
        if concurrency.get("target_wall_time_is_advisory") is not False:
            errors.append(f"{error_prefix}_target_wall_time_not_hard")
        if timeout_s <= target_wall_time_s:
            errors.append(
                f"{error_prefix}_outer_timeout_does_not_allow_teardown"
            )
        if timeout_s > target_wall_time_s + 120:
            errors.append(
                f"{error_prefix}_outer_timeout_exceeds_hard_gate_allowance"
            )
        if timeout_grace_s > 120:
            errors.append(f"{error_prefix}_timeout_grace_exceeds_120")
        verification = {
            "contract": (
                "algorithm_c_analytical_hard_wall_time_verification_v2"
            ),
            "scene": scene,
            "target_wall_time_s": target_wall_time_s,
            "target_wall_time_is_advisory": concurrency.get(
                "target_wall_time_is_advisory"
            ),
            "timeout_s": timeout_s,
            "timeout_grace_s": timeout_grace_s,
            "errors": errors,
            "passed": not errors,
            "formal_state_mutation": False,
            "candidate_batch_retry": False,
        }
        self.event(
            "algorithm_c_preformal_runtime_contract_verification",
            verification=verification,
        )
        if errors:
            raise RuntimeError(
                f"{scene} hard wall-time contract invalid: " + ",".join(errors)
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--target", required=True, choices=("cpu", "memory", "io"))
    args = parser.parse_args()
    runner = FreshCalibration(
        args.config.resolve(),
        args.output_dir.resolve(),
        args.target,
    )

    def request_stop(signum: int, _frame: Any) -> None:
        runner.request_stop(signum)

    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, request_stop)
    runner.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
