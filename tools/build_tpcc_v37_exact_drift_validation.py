#!/usr/bin/env python3
"""Build the next TPC-C diagnostic Top-5 without any workload invocation.

The calibration key is the exact pair ``(validated plan fingerprint, exact
parameter vector)``.  Trusted observations alone may train a trusted point;
probe observations remain diagnostic-only forever.  A contemporaneous drift
offset is learned from the latest clean trusted rows and is applied only after
the exact-point calibration.  Trusted ordering uses a robust lower confidence
bound and explicit leave-one-out score/resource error gates.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
from statistics import median
from typing import Any, Dict, List, Mapping, Sequence, Tuple

try:
    import build_tpcc_unique_exact_validation_v6 as v6
    from tpcc_score_consistent_representative import score_consistent_representative
except ModuleNotFoundError:
    from tools import build_tpcc_unique_exact_validation_v6 as v6
    from tools.tpcc_score_consistent_representative import score_consistent_representative


CAP = 5
RESOURCES = ("cpu", "io", "memory")
SELECTION_CONTRACT = "tpcc_v37_exact_plan_param_drift_lcb_trusted3_probe2_v1"
BUILD_CONTRACT = "tpcc_v37_zero_call_exact_calibration_build_v1"
SCORE_LOO_MAE_MAX = 0.05
RESOURCE_LOO_MAE_PP_MAX = {"cpu": 5.0, "io": 5.0, "memory": 2.0}
MIN_TRUSTED_OBSERVATIONS = 3


def _role_evidence(
    descriptors: Sequence[Mapping[str, Any]],
    current_ledger_sha: str,
) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for descriptor in descriptors:
        selection_path = Path(str(descriptor["selection_path"]))
        ledger_path = Path(str(descriptor["ledger_path"]))
        selection = v6._load(selection_path)
        ledger = v6._load(ledger_path)
        pairs = v6._validate_clean_batch(selection, ledger, str(descriptor["name"]))
        for candidate, observation in pairs:
            params = v6._params(candidate)
            plan = str(candidate["workload_plan_fingerprint"])
            key = v6._point_key(params, plan)
            role = str(candidate.get("selection_role") or "uncertain_probe")
            trusted = bool(role == "trusted" and candidate.get("trusted") is True)
            grouped.setdefault(key, []).append(
                {
                    "selection_path": str(selection_path.resolve()),
                    "selection_sha256": str(descriptor["selection_sha256"]),
                    "ledger_path": str(ledger_path.resolve()),
                    "ledger_sha256": str(descriptor["ledger_sha256"]),
                    "rank": int(observation["rank"]),
                    "selection_role": role,
                    "trusted_training_eligible": trusted,
                    "is_current_batch": descriptor["ledger_sha256"] == current_ledger_sha,
                    "actual_score": float(observation["actual_score"]),
                    "actual_resource": {
                        name: float(observation["actual_resource"][name])
                        for name in RESOURCES
                    },
                }
            )
    return grouped


def _loo_metrics(observations: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    if len(observations) < 2:
        return {
            "defined": False,
            "score_mae": None,
            "score_max_absolute_error": None,
            "resource_mae_pp": {name: None for name in RESOURCES},
            "resource_max_absolute_error_pp": {name: None for name in RESOURCES},
            "score_absolute_errors": [],
        }
    score_errors: List[float] = []
    resource_errors: Dict[str, List[float]] = {name: [] for name in RESOURCES}
    for held_out_index, held_out in enumerate(observations):
        training = [
            item for index, item in enumerate(observations) if index != held_out_index
        ]
        predicted_resource = {
            name: median(float(item["actual_resource"][name]) for item in training)
            for name in RESOURCES
        }
        predicted_score = v6._score(predicted_resource)
        score_errors.append(abs(predicted_score - float(held_out["actual_score"])))
        for name in RESOURCES:
            predicted_u = predicted_resource[name] / v6.repeat_v2.CAPABILITY[name]
            actual_u = (
                float(held_out["actual_resource"][name])
                / v6.repeat_v2.CAPABILITY[name]
            )
            resource_errors[name].append(abs(predicted_u - actual_u) * 100.0)
    return {
        "defined": True,
        "score_mae": sum(score_errors) / len(score_errors),
        "score_max_absolute_error": max(score_errors),
        "resource_mae_pp": {
            name: sum(values) / len(values) for name, values in resource_errors.items()
        },
        "resource_max_absolute_error_pp": {
            name: max(values) for name, values in resource_errors.items()
        },
        "score_absolute_errors": score_errors,
    }


def _robust_exact_resource(
    observations: Sequence[Mapping[str, Any]],
) -> Tuple[Dict[str, float], float, Dict[str, Any]]:
    scores = [float(item["actual_score"]) for item in observations]
    resources = [dict(item["actual_resource"]) for item in observations]
    return score_consistent_representative(scores, resources, v6._score)


def _drift_calibration(
    selection: Mapping[str, Any], ledger: Mapping[str, Any]
) -> Dict[str, Any]:
    candidates = {
        int(item["rank"]): item
        for item in selection.get("top5") or []
        if item.get("selection_role") == "trusted" and item.get("trusted") is True
    }
    observations = {int(item["rank"]): item for item in ledger.get("successful") or []}
    if set(candidates) != {1, 2, 3} or not set(candidates).issubset(observations):
        raise RuntimeError("latest clean batch lacks exactly three trusted drift anchors")
    residuals: Dict[str, List[float]] = {name: [] for name in RESOURCES}
    for rank in sorted(candidates):
        candidate = candidates[rank]
        observation = observations[rank]
        for name in RESOURCES:
            residuals[name].append(
                float(observation["actual_resource"][name])
                - float(candidate["predicted_resource"][name])
            )
    offsets = {name: median(values) for name, values in residuals.items()}
    score_errors = []
    for rank in sorted(candidates):
        corrected = {
            name: max(0.0, float(candidates[rank]["predicted_resource"][name]) + offsets[name])
            for name in RESOURCES
        }
        score_errors.append(abs(v6._score(corrected) - float(observations[rank]["actual_score"])))
    return {
        "contract": "tpcc_contemporaneous_trusted_residual_median_v1",
        "anchor_ranks": sorted(candidates),
        "anchor_count": len(candidates),
        "resource_offset": offsets,
        "resource_residuals": residuals,
        "score_absolute_errors_after_offset": score_errors,
        "score_mae_after_offset": sum(score_errors) / len(score_errors),
        "score_max_absolute_error_after_offset": max(score_errors),
        "probe_rows_used": 0,
    }


def _historical_fingerprint_ids(descriptors: Sequence[Mapping[str, Any]]) -> set:
    result = set()
    for descriptor in descriptors:
        selection = v6._load(Path(str(descriptor["selection_path"])))
        for candidate in selection.get("top5") or []:
            fingerprint_id = (candidate.get("three_level_fingerprint") or {}).get("id")
            if fingerprint_id:
                result.add(str(fingerprint_id))
    return result


def _parameter_corner(candidate: Mapping[str, Any], ranges: Mapping[str, Any]) -> bool:
    params = v6._params(candidate)
    at_boundary = 0
    for name, value in params.items():
        spec = ranges.get(name) or {}
        lower = float(spec.get("min_value", value))
        upper = float(spec.get("max_value", value))
        tolerance = max(1e-12, abs(upper - lower) * 1e-12)
        at_boundary += int(abs(value - lower) <= tolerance or abs(value - upper) <= tolerance)
    return at_boundary == len(params)


def build(
    source_selection: Dict[str, Any],
    *,
    source_selection_path: Path,
    source_ledger_path: Path,
    excluded_fingerprint_ids: Sequence[str] = (),
    drift_selection: Mapping[str, Any] | None = None,
    drift_ledger: Mapping[str, Any] | None = None,
    defer_trusted_fingerprint_novelty: bool = False,
) -> Dict[str, Any]:
    pool, descriptors = v6.reconstruct_exact_pool(
        source_selection,
        source_selection_path=source_selection_path,
        source_ledger_path=source_ledger_path,
    )
    current_ledger_sha = v6._sha256(source_ledger_path)
    current_ledger = v6._load(source_ledger_path)
    if current_ledger.get("technical_failures") or int(
        current_ledger.get("real_workload_invocation_count", -1)
    ) != CAP or len(current_ledger.get("successful") or []) != CAP:
        raise RuntimeError("v37 requires one clean 5/5/0 diagnostic source batch")
    evidence_by_key = _role_evidence(descriptors, current_ledger_sha)
    effective_drift_selection = drift_selection or source_selection
    effective_drift_ledger = drift_ledger or current_ledger
    drift = _drift_calibration(effective_drift_selection, effective_drift_ledger)
    historical_ids = _historical_fingerprint_ids(descriptors)
    externally_excluded_ids = {str(value) for value in excluded_fingerprint_ids}
    forbidden_ids = historical_ids | externally_excluded_ids
    ranges = source_selection.get("candidate_param_ranges") or {}
    current_roles = {
        v6._point_key(v6._params(item), str(item["workload_plan_fingerprint"])): str(
            item.get("selection_role") or "uncertain_probe"
        )
        for item in source_selection.get("top5") or []
    }

    calibrated: List[Dict[str, Any]] = []
    for raw_candidate in pool:
        candidate = copy.deepcopy(raw_candidate)
        key = str(candidate["exact_point_key"])
        evidence = evidence_by_key.get(key) or []
        trusted_observations = [
            item for item in evidence if item["trusted_training_eligible"] is True
        ]
        probe_observations = [
            item for item in evidence if item["trusted_training_eligible"] is not True
        ]
        historical_trusted_observations = [
            item for item in trusted_observations if item["is_current_batch"] is not True
        ]
        historical_any_observations = [
            item for item in evidence if item["is_current_batch"] is not True
        ]
        # The latest batch trains the drift layer.  Excluding it from the base
        # exact-point representative prevents applying the same drift twice.
        calibration_observations = (
            historical_trusted_observations
            or historical_any_observations
            or trusted_observations
            or evidence
        )
        representative, historical_score, representative_audit = _robust_exact_resource(
            calibration_observations
        )
        corrected_resource = {
            name: max(0.0, representative[name] + drift["resource_offset"][name])
            for name in RESOURCES
        }
        corrected_utilization = {
            name: corrected_resource[name] / v6.repeat_v2.CAPABILITY[name]
            for name in RESOURCES
        }
        corrected_score = v6._score(corrected_resource)
        loo = _loo_metrics(trusted_observations)
        trusted_score_values = [float(item["actual_score"]) for item in trusted_observations]
        robust_mad = (
            median(abs(value - median(trusted_score_values)) for value in trusted_score_values)
            if trusted_score_values
            else 0.0
        )
        loo_median = median(loo["score_absolute_errors"]) if loo["score_absolute_errors"] else 0.0
        margin = max(
            loo_median,
            1.4826 * robust_mad,
            float(drift["score_max_absolute_error_after_offset"]),
        )
        lower = corrected_score - margin
        upper = corrected_score + margin
        fingerprint = v6._make_fingerprint(candidate, corrected_resource, corrected_utilization)
        loo_gate = bool(
            len(trusted_observations) >= MIN_TRUSTED_OBSERVATIONS
            and loo["defined"] is True
            and float(loo["score_mae"]) <= SCORE_LOO_MAE_MAX
            and all(
                float(loo["resource_mae_pp"][name]) <= RESOURCE_LOO_MAE_PP_MAX[name]
                for name in RESOURCES
            )
        )
        current_probe = current_roles.get(key) == "uncertain_probe"
        trusted_eligible = bool(
            loo_gate
            and not current_probe
            and not _parameter_corner(candidate, ranges)
            and (
                defer_trusted_fingerprint_novelty
                or fingerprint["id"] not in forbidden_ids
            )
        )
        candidate.update(
            {
                # candidate_score remains the score implied by
                # predicted_resource; ranking uses formal_lcb_score below.
                "candidate_score": corrected_score,
                "candidate_score_recomputed": corrected_score,
                "predicted_score": corrected_score,
                "final_score": corrected_score,
                "formal_lcb_score": lower,
                "robust_acceptance_score": lower,
                "predicted_score_interval": {
                    "lower": lower,
                    "center": corrected_score,
                    "upper": upper,
                    "source": "exact_plan_params_plus_trusted_temporal_drift_lcb_v37",
                },
                "predicted_resource": corrected_resource,
                "final_resource": copy.deepcopy(corrected_resource),
                "uncalibrated_predicted_resource": representative,
                "utilization": corrected_utilization,
                "three_level_fingerprint": fingerprint,
                "boundary_corner": False,
                "diagnostic_only": True,
                "incumbent_update_eligible": False,
                "used_for_gradient": False,
                "used_for_inner_incumbent_update": False,
                "formal_improvement_eligible": False,
                "exact_trusted_observation_count": len(trusted_observations),
                "historical_exact_trusted_observation_count": len(
                    historical_trusted_observations
                ),
                "probe_observation_count_excluded_from_trusted": len(probe_observations),
                "current_batch_role": current_roles.get(key),
                "loo_validation": loo,
                "trusted_loo_gate_passed": loo_gate,
                "trusted_eligible_v37": trusted_eligible,
                "prediction_source": "exact_plan_params_trusted_only_drift_corrected_v37",
                "repeat_prediction_source": "exact_plan_params_trusted_only_drift_corrected_v37",
            }
        )
        confidence = copy.deepcopy(candidate.get("confidence") or {})
        confidence.update(
            {
                "contract": "tpcc_v37_loo_robust_confidence_v1",
                "exact_plan_params": 1.0,
                "trusted_measurement_count": len(trusted_observations),
                "probe_measurement_count_excluded": len(probe_observations),
                "loo": loo,
                "robust_mad": robust_mad,
                "drift_score_error_max": drift["score_max_absolute_error_after_offset"],
                "score_lower": lower,
                "score_center": corrected_score,
                "score_upper": upper,
                "trusted_eligible": trusted_eligible,
            }
        )
        candidate["confidence"] = confidence
        candidate["plan_regime_calibration"] = {
            "contract": "exact_validated_plan_plus_exact_params_calibration_v37",
            "key": {
                "validated_plan": str(candidate["workload_plan_fingerprint"]),
                "exact_params_sha256": v6._canonical_sha256(v6._params(candidate)),
            },
            "historical_resource": representative,
            "historical_resource_score": historical_score,
            "score_consistent_representative_audit": representative_audit,
            "temporal_drift": copy.deepcopy(drift),
            "corrected_resource": corrected_resource,
            "corrected_score": corrected_score,
            "trusted_rows_only": bool(trusted_observations),
            "probe_rows_used_for_trusted_calibration": 0,
            "trusted_eligible": trusted_eligible,
        }
        candidate["source_provenance"] = {
            "contract": "tpcc_v37_exact_role_separated_evidence_v1",
            "exact_point_key": key,
            "validated_plan": str(candidate["workload_plan_fingerprint"]),
            "exact_params_sha256": v6._canonical_sha256(v6._params(candidate)),
            "trusted_observations": trusted_observations,
            "probe_observations_excluded_from_trusted": probe_observations,
        }
        calibrated.append(candidate)

    trusted_pool = [item for item in calibrated if item["trusted_eligible_v37"] is True]
    trusted = sorted(
        trusted_pool,
        key=lambda item: (-float(item["formal_lcb_score"]), str(item["exact_point_key"])),
    )[:3]
    if len(trusted) != 3:
        raise RuntimeError(f"v37 has {len(trusted)} trusted candidates after LOO/error gates")
    used_ids = {str(item["three_level_fingerprint"]["id"]) for item in trusted}
    trusted_keys = {str(item["exact_point_key"]) for item in trusted}
    probe_pool = [
        item
        for item in calibrated
        if str(item["exact_point_key"]) not in trusted_keys
        and str(item["three_level_fingerprint"]["id"]) not in forbidden_ids
        and str(item["three_level_fingerprint"]["id"]) not in used_ids
        and not _parameter_corner(item, ranges)
        and item.get("current_batch_role") != "trusted"
    ]
    # Unknown/held-out probes are deliberately low-confidence.  Maximin gives
    # diversity; score is only a deterministic tie-breaker.
    probes = v6._maximin(probe_pool, 2, ranges, anchors=trusted)
    selected = trusted + probes
    for rank, candidate in enumerate(selected, 1):
        role = "trusted" if rank <= 3 else "uncertain_probe"
        candidate["rank"] = rank
        candidate["selection_role"] = role
        candidate["trusted"] = role == "trusted"
        candidate["uncertain_probe"] = role != "trusted"
        candidate["exact_plan_regime_validated"] = role == "trusted"
        candidate["probe_kind"] = None if role == "trusted" else "v37_unknown_or_heldout_maximin"
        candidate["probe_reason"] = None if role == "trusted" else "not_trusted_by_exact_role_separated_loo_gate"
        candidate["plan_regime_calibration"]["trusted_eligible"] = role == "trusted"

    ids = [str(item["three_level_fingerprint"]["id"]) for item in selected]
    trusted_ids = ids[:3]
    probe_ids = ids[3:]
    if (
        len(set(ids)) != CAP
        or any(value in forbidden_ids for value in probe_ids)
        or (
            not defer_trusted_fingerprint_novelty
            and any(value in forbidden_ids for value in trusted_ids)
        )
    ):
        raise RuntimeError("v37 failed the five-new-fingerprint gate")
    if any(item.get("boundary_corner") is True for item in selected):
        raise RuntimeError("v37 selected a corner candidate")
    roles = [item["selection_role"] for item in selected]
    if roles != ["trusted", "trusted", "trusted", "uncertain_probe", "uncertain_probe"]:
        raise RuntimeError("v37 role contract mismatch")
    required_fields = {
        "feature_ratio",
        "predicted_resource",
        "utilization",
        "dScore/dU",
        "T",
        "confidence",
        "g_j",
        "eta",
        "delta_param",
        "candidate_score",
    }
    for candidate in selected:
        missing = sorted(required_fields - set(candidate))
        if missing:
            raise RuntimeError(f"candidate log contract missing fields: {missing}")

    output = copy.deepcopy(source_selection)
    output.update(
        {
            "schema_version": "tpcc_exact_drift_validation_v37",
            "selection_contract": SELECTION_CONTRACT,
            "diagnostic_only": True,
            "diagnostic_role_evaluation_only": True,
            "maximum_real_workload_invocations": CAP,
            "qps_role": "outer_audit_only_not_inner_score",
            "state_mutation_contract": {
                "accepted_update": False,
                "gradient_update": False,
                "incumbent_update": False,
                "range_update": False,
            },
            "top5": selected,
            "source_batches": descriptors,
            "v37_build_contract": {
                "contract": BUILD_CONTRACT,
                "source_selection_path": str(source_selection_path.resolve()),
                "source_selection_sha256": v6._sha256(source_selection_path),
                "source_ledger_path": str(source_ledger_path.resolve()),
                "source_ledger_sha256": current_ledger_sha,
                "calibration_key": "validated_plan_fingerprint_plus_exact_params",
                "probe_rows_used_for_trusted_calibration": 0,
                "unknown_exact_points_must_be_probes": True,
                "trusted_order": "descending_robust_confidence_lower_bound",
                "score_loo_mae_max": SCORE_LOO_MAE_MAX,
                "resource_loo_mae_pp_max": RESOURCE_LOO_MAE_PP_MAX,
                "minimum_trusted_observations": MIN_TRUSTED_OBSERVATIONS,
                "historical_fingerprint_count": len(historical_ids),
                "externally_excluded_fingerprint_count": len(
                    externally_excluded_ids
                ),
                "five_new_noncorner_fingerprints": True,
                "fingerprint_ids": ids,
                "roles": roles,
                "state_neutral": True,
                "physical_workload_calls_added": 0,
            },
        }
    )
    output.pop("v36_repair_contract", None)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    selection_path = args.selection.resolve()
    ledger_path = args.ledger.resolve()
    result = build(
        v6._load(selection_path),
        source_selection_path=selection_path,
        source_ledger_path=ledger_path,
    )
    v6.repeat_v2.import_base().atomic_json(args.output.resolve(), result)
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "roles": [item["selection_role"] for item in result["top5"]],
                "fingerprints": [item["three_level_fingerprint"]["id"] for item in result["top5"]],
                "trusted_lcb": [item["formal_lcb_score"] for item in result["top5"][:3]],
                "new_workload_calls": 0,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
