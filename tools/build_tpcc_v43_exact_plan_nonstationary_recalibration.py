#!/usr/bin/env python3
"""Absorb a failed v42 clean5 batch and build a new state-neutral Top-5."""

from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
from statistics import median
from typing import Any, Dict, Mapping, Sequence

try:
    import audit_tpcc_v43_postrun_gate as gate_module
    import build_tpcc_unique_exact_validation_v6 as v6
    import build_tpcc_v37_exact_drift_validation as v37
    import build_tpcc_v42_exact_plan_io_recalibration as v42
except ModuleNotFoundError:
    from tools import audit_tpcc_v43_postrun_gate as gate_module
    from tools import build_tpcc_unique_exact_validation_v6 as v6
    from tools import build_tpcc_v37_exact_drift_validation as v37
    from tools import build_tpcc_v42_exact_plan_io_recalibration as v42


CAP = 5
BUILD_CONTRACT = "tpcc_v43_exact_plan_nonstationary_score_resource_recalibration_v1"


def _load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def _require_sha(path: Path, expected: str, label: str) -> None:
    if not path.is_file() or v6._sha256(path) != expected:
        raise RuntimeError(f"TPC-C v43 sealed SHA mismatch: {label}")


def _parameter_distance(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    ranges: Mapping[str, Any],
) -> float:
    left_params = v6._params(left)
    right_params = v6._params(right)
    terms = []
    for name in sorted(left_params):
        spec = ranges.get(name) or {}
        span = float(spec.get("max_value", left_params[name])) - float(
            spec.get("min_value", left_params[name])
        )
        denominator = span if span > 0.0 else max(abs(left_params[name]), 1.0)
        terms.append(((left_params[name] - right_params[name]) / denominator) ** 2)
    return math.sqrt(sum(terms) / max(len(terms), 1))


def _local_nonstationary_model(
    candidate: Mapping[str, Any],
    historical_resource: Mapping[str, Any],
    historical_score: float,
    anchors: Sequence[tuple[Mapping[str, Any], Mapping[str, Any]]],
    ranges: Mapping[str, Any],
) -> Dict[str, Any]:
    if not anchors:
        raise RuntimeError("TPC-C v43 local model lacks role-appropriate anchors")
    weighted = []
    for anchor_candidate, observation in anchors:
        distance = _parameter_distance(candidate, anchor_candidate, ranges)
        weight = 1.0 / (0.02 + distance) ** 2
        resource_residual = {
            name: float(observation["actual_resource"][name])
            - float(anchor_candidate["predicted_resource"][name])
            for name in v6.RESOURCES
        }
        score_residual = float(observation["actual_score"]) - float(
            anchor_candidate["predicted_score"]
        )
        weighted.append(
            {
                "rank": int(anchor_candidate["rank"]),
                "distance": distance,
                "raw_weight": weight,
                "resource_residual": resource_residual,
                "score_residual": score_residual,
                "selection_role": anchor_candidate["selection_role"],
            }
        )
    weight_sum = sum(row["raw_weight"] for row in weighted)
    for row in weighted:
        row["weight"] = row["raw_weight"] / weight_sum
    resource_offset = {
        name: sum(
            row["weight"] * row["resource_residual"][name] for row in weighted
        )
        for name in v6.RESOURCES
    }
    score_offset = sum(row["weight"] * row["score_residual"] for row in weighted)
    score_target = historical_score + score_offset

    def resource_at(alpha: float) -> Dict[str, float]:
        return {
            name: max(
                0.0,
                float(historical_resource[name]) + alpha * resource_offset[name],
            )
            for name in v6.RESOURCES
        }

    # The local model estimates both resource drift and score residual.  Pick
    # the deterministic all-resource residual scale whose utility is closest
    # to the independently estimated score target; this avoids an IO-only
    # scalar while keeping candidate_score exactly resource-derived.
    choices = []
    for step in range(2001):
        alpha = step / 1000.0
        resource = resource_at(alpha)
        score = v6._score(resource)
        choices.append((abs(score - score_target), abs(alpha - 1.0), alpha, resource, score))
    _, _, alpha, resource, score = min(choices, key=lambda row: row[:3])
    residuals = [float(row["score_residual"]) for row in weighted]
    center = median(residuals)
    dispersion = median(abs(value - center) for value in residuals)
    return {
        "contract": "tpcc_v43_distance_weighted_exact_plan_nonstationarity_v1",
        "anchor_rows": weighted,
        "resource_offset": resource_offset,
        "score_offset": score_offset,
        "score_target_before_resource_reconciliation": score_target,
        "all_resource_reconciliation_alpha": alpha,
        "predicted_resource": resource,
        "predicted_score": score,
        "score_target_reconciliation_error": abs(score - score_target),
        "score_residual_mad": dispersion,
        "io_only_scalar_calibration": False,
    }


def build(
    *,
    selection_path: Path,
    selection_sha256: str,
    ledger_path: Path,
    ledger_sha256: str,
    events_path: Path,
    events_sha256: str,
    summary_path: Path,
    summary_sha256: str,
    gate_path: Path,
    gate_sha256: str,
    formal_state_path: Path,
    formal_state_sha256: str,
) -> Dict[str, Any]:
    pins = {
        "selection": (selection_path, selection_sha256),
        "ledger": (ledger_path, ledger_sha256),
        "events": (events_path, events_sha256),
        "summary": (summary_path, summary_sha256),
        "gate": (gate_path, gate_sha256),
        "formal_state": (formal_state_path, formal_state_sha256),
    }
    for label, (path, expected) in pins.items():
        _require_sha(path, expected, label)
    source = _load(selection_path)
    ledger = _load(ledger_path)
    summary = _load(summary_path)
    gate = _load(gate_path)
    formal = _load(formal_state_path)
    failed = {
        name
        for name, passed in (gate.get("quality_gate") or {}).get("checks", {}).items()
        if passed is not True
    }
    allowed_failures = {
        "actual_best_in_predicted_top3",
        "trusted_spearman_positive",
        "trusted_score_mae_within_gate",
        "trusted_cpu_mae_within_gate",
        "trusted_io_mae_within_gate",
        "trusted_memory_mae_within_gate",
        "trusted_resource_no_order_of_magnitude_error",
        "trusted_score_span_resolvable",
    }
    source_contract = (
        source.get("v43_build_contract") or source.get("v42_build_contract") or {}
    ).get("contract")
    if (
        source_contract not in {v42.BUILD_CONTRACT, BUILD_CONTRACT}
        or gate.get("schema_version")
        != "tpcc_v43_v42_compatible_strict_postrun_gate_v1"
        or gate.get("passed") is not False
        or not failed
        or not failed.issubset(allowed_failures)
        or (gate.get("quality_gate") or {}).get("checks", {}).get(
            "durable_accounting_clean5"
        )
        is not True
        or (gate.get("quality_gate") or {}).get("checks", {}).get(
            "baseline_restored_and_state_neutral"
        )
        is not True
        or (gate.get("quality_gate") or {}).get("checks", {}).get(
            "selection_algorithm_gate"
        )
        is not True
        or int(ledger.get("real_workload_invocation_count", -1)) != CAP
        or len(ledger.get("successful") or []) != CAP
        or ledger.get("technical_failures") not in ([], None)
        or int(summary.get("successful_real_replay_count", -1)) != CAP
        or summary.get("baseline_params_restored") is not True
        or summary.get("baseline_snapshot_restored") is not True
        or int(summary.get("accepted_count_delta", -1)) != 0
        or summary.get("gradient_update_applied") is not False
        or summary.get("incumbent_updated") is not False
        or summary.get("range_updated") is not False
        or int(formal.get("accepted_count", -1)) != 0
        or int(formal.get("formal_physical_call_count", -1)) != 0
    ):
        raise RuntimeError("TPC-C v43 requires the sealed failed-but-clean v42 batch")

    pool, descriptors = v6.reconstruct_exact_pool(
        source,
        source_selection_path=selection_path,
        source_ledger_path=ledger_path,
    )
    evidence_by_key = v37._role_evidence(descriptors, ledger_sha256)
    historical_ids = v37._historical_fingerprint_ids(descriptors)
    ranges = source.get("candidate_param_ranges") or {}
    candidates_by_rank = {int(item["rank"]): item for item in source["top5"]}
    observations_by_rank = {
        int(item["rank"]): item for item in ledger["successful"]
    }
    trusted_anchors = [
        (candidates_by_rank[rank], observations_by_rank[rank]) for rank in (1, 2, 3)
    ]
    probe_anchors = [
        (candidates_by_rank[rank], observations_by_rank[rank]) for rank in (4, 5)
    ]
    current_probe_keys = {
        str(candidates_by_rank[rank]["exact_point_key"]) for rank in (4, 5)
    }
    trusted_plans = {
        str(candidate["workload_plan_fingerprint"]) for candidate, _ in trusted_anchors
    }
    calibrated = []
    for raw in pool:
        candidate = copy.deepcopy(raw)
        key = str(candidate["exact_point_key"])
        if key in current_probe_keys or v37._parameter_corner(candidate, ranges):
            continue
        rows = list(evidence_by_key.get(key) or [])
        trusted_rows = [row for row in rows if row["trusted_training_eligible"] is True]
        probe_rows = [row for row in rows if row["trusted_training_eligible"] is not True]
        role_rows = trusted_rows or probe_rows
        if not role_rows:
            continue
        historical_resource, historical_score, representative_audit = (
            v37._robust_exact_resource(role_rows)
        )
        plan = str(candidate["workload_plan_fingerprint"])
        trusted_eligible = bool(plan in trusted_plans and len(trusted_rows) >= 2)
        anchors = [
            pair
            for pair in (trusted_anchors if trusted_eligible else probe_anchors)
            if str(pair[0]["workload_plan_fingerprint"]) == plan
        ]
        if anchors:
            model = _local_nonstationary_model(
                candidate, historical_resource, historical_score, anchors, ranges
            )
        else:
            # An EXPLAIN-validated but unseen plan regime is allowed only as an
            # uncertain probe.  It receives no trusted calibration and cannot
            # affect formal, gradient, incumbent, or range state.
            model = {
                "contract": "tpcc_v43_unseen_plan_uncertain_probe_v1",
                "anchor_rows": [],
                "resource_offset": {name: 0.0 for name in v6.RESOURCES},
                "score_offset": 0.0,
                "score_target_before_resource_reconciliation": historical_score,
                "all_resource_reconciliation_alpha": 0.0,
                "predicted_resource": historical_resource,
                "predicted_score": historical_score,
                "score_target_reconciliation_error": 0.0,
                "score_residual_mad": 0.20,
                "io_only_scalar_calibration": False,
                "unseen_plan_regime": True,
            }
        predicted_resource = model["predicted_resource"]
        utilization = {
            name: predicted_resource[name] / v6.repeat_v2.CAPABILITY[name]
            for name in v6.RESOURCES
        }
        fingerprint = v6._make_fingerprint(candidate, predicted_resource, utilization)
        if fingerprint["id"] in historical_ids:
            continue
        score = float(model["predicted_score"])
        margin = max(
            gate_module.SCORE_MAE_MAX,
            float(model["score_residual_mad"]),
            float(model["score_target_reconciliation_error"]),
        )
        candidate.update(
            {
                "candidate_score": score,
                "candidate_score_recomputed": score,
                "predicted_score": score,
                "final_score": score,
                "formal_lcb_score": score - margin,
                "robust_acceptance_score": score - margin,
                "predicted_score_interval": {
                    "lower": score - margin,
                    "center": score,
                    "upper": score + margin,
                    "source": "v43_exact_plan_nonstationarity_score_resource_model",
                },
                "predicted_resource": predicted_resource,
                "final_resource": copy.deepcopy(predicted_resource),
                "uncalibrated_predicted_resource": historical_resource,
                "utilization": utilization,
                "three_level_fingerprint": fingerprint,
                "boundary_corner": False,
                "boundary_plateau": False,
                "diagnostic_only": True,
                "incumbent_update_eligible": False,
                "used_for_gradient": False,
                "used_for_inner_incumbent_update": False,
                "formal_improvement_eligible": False,
                "trusted_eligible_v43": trusted_eligible,
                "prediction_source": "v43_exact_plan_nonstationarity_score_resource_model",
                "repeat_prediction_source": "v43_exact_plan_nonstationarity_score_resource_model",
                "source_provenance": {
                    "contract": "tpcc_v43_role_separated_exact_evidence_v1",
                    "exact_point_key": key,
                    "validated_plan": plan,
                    "trusted_observations": copy.deepcopy(trusted_rows),
                    "probe_observations_excluded_from_trusted": copy.deepcopy(probe_rows),
                    "v42_rank4_probe_evidence_present": any(
                        int(row.get("rank", -1)) == 4 and row.get("is_current_batch") is True
                        for row in probe_rows
                    ),
                    "v42_probe_formal_state_update_rows": 0,
                    "v42_probe_gradient_update_rows": 0,
                    "v42_probe_incumbent_update_rows": 0,
                },
                "plan_regime_calibration": {
                    "contract": "tpcc_v43_exact_plan_nonstationarity_calibration_v1",
                    "validated_plan": plan,
                    "historical_resource": historical_resource,
                    "historical_score": historical_score,
                    "representative_audit": representative_audit,
                    "nonstationarity_model": model,
                    "probe_rows_used_for_trusted_calibration": 0,
                    "probe_evidence_affects_formal_or_gradient": False,
                    "trusted_eligible": trusted_eligible,
                },
            }
        )
        confidence = copy.deepcopy(candidate.get("confidence") or {})
        confidence.update(
            {
                "contract": "tpcc_v43_exact_plan_nonstationarity_confidence_v1",
                "trusted_measurement_count": len(trusted_rows),
                "probe_measurement_count_excluded": len(probe_rows),
                "score_lower": score - margin,
                "score_center": score,
                "score_upper": score + margin,
                "trusted_eligible": trusted_eligible,
                "probe_rows_used_for_trusted_calibration": 0,
            }
        )
        candidate["confidence"] = confidence
        calibrated.append(candidate)

    # The sealed EXPLAIN ranking contains validated, non-corner parameter
    # points that have never had a real replay.  They may only supplement the
    # uncertain-probe pool; no unmeasured exact point can become trusted.
    history_path = Path(str(source.get("source_history") or "")).resolve()
    _require_sha(
        history_path,
        str(source.get("source_history_sha256") or ""),
        "source EXPLAIN ranking history",
    )
    ranking_line = int(source.get("source_ranking_line_number") or 0)
    history_lines = history_path.read_text(encoding="utf-8").splitlines()
    if ranking_line <= 0 or ranking_line > len(history_lines):
        raise RuntimeError("TPC-C v43 source ranking line is invalid")
    ranking_event = json.loads(history_lines[ranking_line - 1])
    if ranking_event.get("event") != "experience_table_all_subset_ranking":
        raise RuntimeError("TPC-C v43 source ranking event mismatch")
    measured_keys = {str(item["exact_point_key"]) for item in pool}
    calibrated_keys = {str(item["exact_point_key"]) for item in calibrated}
    for raw in ranking_event.get("all_candidate_rankings") or []:
        candidate = copy.deepcopy(raw)
        original_fingerprint = candidate.get("three_level_fingerprint") or {}
        plan = str(original_fingerprint.get("plan_level") or "")
        if not plan.startswith("ratio-plan-sha256:"):
            continue
        candidate["workload_plan_fingerprint"] = plan
        candidate["candidate_params"] = copy.deepcopy(
            candidate.get("optimized_params") or {}
        )
        if not candidate["candidate_params"]:
            continue
        key = v6._point_key(candidate["candidate_params"], plan)
        candidate["exact_point_key"] = key
        if (
            key in measured_keys
            or key in calibrated_keys
            or v37._parameter_corner(candidate, ranges)
        ):
            continue
        resource_audit = original_fingerprint.get("resource_formula_audit") or {}
        if set(resource_audit) != set(v6.RESOURCES):
            continue
        candidate["feature_ratio"] = {
            name: float(resource_audit[name]["feature_ratio"])
            for name in v6.RESOURCES
        }
        historical_resource = {
            name: float(candidate["final_resource"][name]) for name in v6.RESOURCES
        }
        historical_score = v6._score(historical_resource)
        anchors = [
            pair
            for pair in probe_anchors
            if str(pair[0]["workload_plan_fingerprint"]) == plan
        ]
        if not anchors:
            anchors = [
                pair
                for pair in trusted_anchors
                if str(pair[0]["workload_plan_fingerprint"]) == plan
            ]
        if not anchors:
            continue
        model = _local_nonstationary_model(
            candidate, historical_resource, historical_score, anchors, ranges
        )
        predicted_resource = model["predicted_resource"]
        utilization = {
            name: predicted_resource[name] / v6.repeat_v2.CAPABILITY[name]
            for name in v6.RESOURCES
        }
        fingerprint = v6._make_fingerprint(candidate, predicted_resource, utilization)
        if fingerprint["id"] in historical_ids:
            continue
        score = float(model["predicted_score"])
        margin = max(
            gate_module.SCORE_MAE_MAX,
            float(model["score_residual_mad"]),
            float(model["score_target_reconciliation_error"]),
        )
        candidate.update(
            {
                "candidate_score": score,
                "candidate_score_recomputed": score,
                "predicted_score": score,
                "final_score": score,
                "formal_lcb_score": score - margin,
                "robust_acceptance_score": score - margin,
                "predicted_score_interval": {
                    "lower": score - margin,
                    "center": score,
                    "upper": score + margin,
                    "source": "v43_unmeasured_exact_plan_nonstationarity_probe",
                },
                "predicted_resource": predicted_resource,
                "final_resource": copy.deepcopy(predicted_resource),
                "uncalibrated_predicted_resource": historical_resource,
                "utilization": utilization,
                "three_level_fingerprint": fingerprint,
                "boundary_corner": False,
                "boundary_plateau": False,
                "diagnostic_only": True,
                "incumbent_update_eligible": False,
                "used_for_gradient": False,
                "used_for_inner_incumbent_update": False,
                "formal_improvement_eligible": False,
                "trusted_eligible_v43": False,
                "prediction_source": "v43_unmeasured_exact_plan_nonstationarity_probe",
                "repeat_prediction_source": "v43_unmeasured_exact_plan_nonstationarity_probe",
                "source_provenance": {
                    "contract": "tpcc_v43_sealed_explain_probe_evidence_v1",
                    "exact_point_key": key,
                    "validated_plan": plan,
                    "source_history": str(history_path),
                    "source_history_sha256": source["source_history_sha256"],
                    "source_ranking_line_number": ranking_line,
                    "trusted_observations": [],
                    "probe_observations_excluded_from_trusted": [
                        copy.deepcopy(observation) for _, observation in anchors
                    ],
                    "v42_probe_formal_state_update_rows": 0,
                    "v42_probe_gradient_update_rows": 0,
                    "v42_probe_incumbent_update_rows": 0,
                },
                "plan_regime_calibration": {
                    "contract": "tpcc_v43_exact_plan_nonstationarity_calibration_v1",
                    "validated_plan": plan,
                    "historical_resource": historical_resource,
                    "historical_score": historical_score,
                    "nonstationarity_model": model,
                    "unmeasured_exact_point": True,
                    "probe_rows_used_for_trusted_calibration": 0,
                    "probe_evidence_affects_formal_or_gradient": False,
                    "trusted_eligible": False,
                },
            }
        )
        confidence = copy.deepcopy(candidate.get("confidence") or {})
        confidence.update(
            {
                "contract": "tpcc_v43_unmeasured_exact_plan_probe_confidence_v1",
                "trusted_measurement_count": 0,
                "probe_measurement_count_excluded": len(anchors),
                "score_lower": score - margin,
                "score_center": score,
                "score_upper": score + margin,
                "trusted_eligible": False,
                "probe_rows_used_for_trusted_calibration": 0,
            }
        )
        candidate["confidence"] = confidence
        calibrated.append(candidate)
        calibrated_keys.add(key)

    trusted = sorted(
        [item for item in calibrated if item["trusted_eligible_v43"] is True],
        key=lambda item: (-float(item["formal_lcb_score"]), str(item["exact_point_key"])),
    )[:3]
    if len(trusted) != 3:
        raise RuntimeError(f"TPC-C v43 has only {len(trusted)} trusted candidates")
    trusted_keys = {str(item["exact_point_key"]) for item in trusted}
    probe_pool = [
        item
        for item in calibrated
        if str(item["exact_point_key"]) not in trusted_keys
        and item["trusted_eligible_v43"] is not True
    ]
    probes = v6._maximin(probe_pool, 2, ranges, anchors=trusted)
    top5 = trusted + probes
    for rank, item in enumerate(top5, 1):
        role = "trusted" if rank <= 3 else "uncertain_probe"
        item.update(
            {
                "rank": rank,
                "selection_role": role,
                "trusted": role == "trusted",
                "uncertain_probe": role != "trusted",
                "exact_plan_regime_validated": role == "trusted",
                "probe_kind": None if role == "trusted" else "v43_role_separated_maximin",
                "probe_reason": None if role == "trusted" else "not_admitted_by_trusted_only_nonstationarity_gate",
            }
        )
        item["plan_regime_calibration"]["trusted_eligible"] = role == "trusted"

    identifiers = [str(item["three_level_fingerprint"]["id"]) for item in top5]
    roles = [item["selection_role"] for item in top5]
    if (
        roles != ["trusted", "trusted", "trusted", "uncertain_probe", "uncertain_probe"]
        or len(set(identifiers)) != CAP
        or bool(set(identifiers) & historical_ids)
        or any(item.get("boundary_corner") is not False for item in top5)
    ):
        raise RuntimeError("TPC-C v43 fresh 3T2P admission failed")
    result = copy.deepcopy(source)
    result.update(
        {
            "schema_version": "tpcc_v43_exact_plan_nonstationary_validation_v1",
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
            "top5": top5,
            "source_batches": descriptors,
            "v43_build_contract": {
                "contract": BUILD_CONTRACT,
                "sealed_v42_authority": {
                    label: {"path": str(path.resolve()), "sha256": expected}
                    for label, (path, expected) in pins.items()
                },
                "source_gate_failures": sorted(failed),
                "threshold_changed": False,
                "score_mae_max": gate_module.SCORE_MAE_MAX,
                "resource_mae_pp_max": copy.deepcopy(gate_module.RESOURCE_MAE_PP_MAX),
                "calibration": "distance_weighted_exact_plan_all_resource_plus_score_nonstationarity",
                "io_only_scalar_calibration": False,
                "current_probe_exact_points_replayed": False,
                "v42_rank4_probe_role_rewritten": False,
                "v42_probe_formal_state_update_rows": 0,
                "v42_probe_gradient_update_rows": 0,
                "v42_probe_incumbent_update_rows": 0,
                "same_rank_retry_allowed": False,
                "technical_call_advances_to_next_unique_rank": True,
                "five_new_noncorner_fingerprints": True,
                "roles": roles,
                "fingerprint_ids": identifiers,
                "state_neutral": True,
                "physical_workload_calls_added": 0,
                "manual_candidate_selection": False,
                "candidate_pool_size": len(calibrated),
            },
        }
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("selection", "ledger", "events", "summary", "gate", "formal-state"):
        parser.add_argument(f"--{name}", type=Path, required=True)
        parser.add_argument(f"--{name}-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build(
        selection_path=args.selection.resolve(),
        selection_sha256=args.selection_sha256,
        ledger_path=args.ledger.resolve(),
        ledger_sha256=args.ledger_sha256,
        events_path=args.events.resolve(),
        events_sha256=args.events_sha256,
        summary_path=args.summary.resolve(),
        summary_sha256=args.summary_sha256,
        gate_path=args.gate.resolve(),
        gate_sha256=args.gate_sha256,
        formal_state_path=args.formal_state.resolve(),
        formal_state_sha256=args.formal_state_sha256,
    )
    v6.repeat_v2.import_base().atomic_json(args.output.resolve(), result)
    print(json.dumps({"output": str(args.output.resolve()), "workload_calls_added": 0}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
