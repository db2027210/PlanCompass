#!/usr/bin/env python3
"""Build TPC-C v39 Top-5 with an audited accumulated-probe promotion.

Three independent clean observations may promote the exact point for the
current diagnostic selection when its leave-one-out and empirical-noise gates
pass.  Historical probe roles are immutable and none of those rows updates
formal state.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
from statistics import median
from typing import Any, Dict, List, Mapping, Sequence

try:
    import build_tpcc_unique_exact_validation_v6 as v6
    import build_tpcc_v37_exact_drift_validation as v37
    from tpcc_score_consistent_representative import score_consistent_representative
    from tpcc_v38_replay_policy import CORRECTNESS_POLICY_SHA256
except ModuleNotFoundError:
    from tools import build_tpcc_unique_exact_validation_v6 as v6
    from tools import build_tpcc_v37_exact_drift_validation as v37
    from tools.tpcc_score_consistent_representative import score_consistent_representative
    from tools.tpcc_v38_replay_policy import CORRECTNESS_POLICY_SHA256


CAP = 5
RESOURCES = ("cpu", "io", "memory")
BUILD_CONTRACT = "tpcc_v39_clean_probe_plan_regime_absorption_v1"
PROMOTION_CONTRACT = "tpcc_v39_accumulated_clean_probe_promotion_certificate_v1"
# The sealed v38 runner/auditor uses this compatibility contract.  v39's
# stronger algorithm contract is independently mandatory below.
SELECTION_CONTRACT = "tpcc_v38_fresh_unique_no_retry_trusted3_probe2_v1"
EXPECTED_SOURCE_SHA256 = {
    "selection": "44d1ace6cdd3b5d776db6b463429701322c355cf101f6d57db3a03c1dfb55684",
    "ledger": "110b3f6195b562cad034ef66ae97de5cc11e401183ec8aaa8645500366e2e2c6",
    "events": "941de2f8b51bed7ce7288c01c8b715b20cd7cc586e7753c344fa97af190d5509",
    "summary": "0b0fa99b68f786649524c143d18a4ae8ec89344abe97b1a8bfb6d05c225b8b79",
    "gate": "aad8c4b057ad7faee1784d5ded3381e5c29e871d34a6430a549584b12f607183",
    "formal_state": "62e3fd40c93c163727367afd20b151c291c3646d63fbe0c5c5e2d32c3160aeb0",
}


def load_object(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def _finite(value: Any, label: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise RuntimeError(f"non-finite {label}")
    return number


def validate_v38_authority(
    selection_path: Path,
    ledger_path: Path,
    events_path: Path,
    summary_path: Path,
    gate_path: Path,
    formal_state_path: Path,
) -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    paths = {
        "selection": selection_path,
        "ledger": ledger_path,
        "events": events_path,
        "summary": summary_path,
        "gate": gate_path,
        "formal_state": formal_state_path,
    }
    for name, path in paths.items():
        if not path.is_file() or v6._sha256(path) != EXPECTED_SOURCE_SHA256[name]:
            raise RuntimeError(f"TPC-C v39 sealed authority SHA mismatch: {name}")
    selection = load_object(selection_path)
    ledger = load_object(ledger_path)
    summary = load_object(summary_path)
    gate = load_object(gate_path)
    formal = load_object(formal_state_path)
    pairs = v6._validate_clean_batch(selection, ledger, "v38_clean5_authority")
    checks = (gate.get("quality_gate") or {}).get("checks") or {}
    failed_checks = sorted(name for name, passed in checks.items() if passed is not True)
    if (
        len(pairs) != CAP
        or int(summary.get("real_workload_invocation_count", -1)) != CAP
        or int(summary.get("successful_real_replay_count", -1)) != CAP
        or int(summary.get("technical_failure_count", -1)) != 0
        or int(summary.get("preworkload_failure_count", -1)) != 0
        or summary.get("baseline_params_restored") is not True
        or summary.get("baseline_snapshot_restored") is not True
        or summary.get("gradient_update_applied") is not False
        or summary.get("incumbent_updated") is not False
        or summary.get("range_updated") is not False
        or gate.get("passed") is not False
        or gate.get("formal_start_authorized") is not False
        or failed_checks != ["actual_best_in_predicted_top3"]
        or int((gate.get("quality_gate") or {}).get("actual_best_predicted_rank", -1)) != 5
        or int(formal.get("accepted_count", -1)) != 0
        or int(formal.get("accepted_target", -1)) != 10
        or int(formal.get("formal_physical_call_count", -1)) != 0
        or int(formal.get("formal_valid_replay_count", -1)) != 0
        or int(formal.get("formal_technical_failure_count", -1)) != 0
    ):
        raise RuntimeError("TPC-C v39 v38 authority contract mismatch")
    event_lines = [line for line in events_path.read_text(encoding="utf-8").splitlines() if line]
    if not event_lines:
        raise RuntimeError("TPC-C v39 events authority is empty")
    return selection, ledger, gate


def _recent_representative(
    evidence: Sequence[Mapping[str, Any]],
) -> tuple[Dict[str, float], float, Dict[str, Any], List[Mapping[str, Any]]]:
    if len(evidence) < 2:
        raise RuntimeError("TPC-C v39 candidate lacks two clean observations")
    recent = list(evidence[-2:])
    scores = [_finite(item["actual_score"], "actual score") for item in recent]
    resources = [
        {name: _finite(item["actual_resource"][name], name) for name in RESOURCES}
        for item in recent
    ]
    resource, score, audit = score_consistent_representative(scores, resources, v6._score)
    return resource, float(score), audit, recent


def _plan_models(
    evidence_by_key: Mapping[str, Sequence[Mapping[str, Any]]],
    plan_by_key: Mapping[str, str],
) -> Dict[str, Any]:
    models: Dict[str, Any] = {}
    for point_key, evidence in evidence_by_key.items():
        plan = str(plan_by_key[point_key])
        for row in evidence:
            model = models.setdefault(
                plan,
                {
                    "validated_plan": plan,
                    "clean_observations": [],
                    "trusted_observations": [],
                    "probe_observations": [],
                },
            )
            copied = copy.deepcopy(dict(row))
            model["clean_observations"].append(copied)
            key = (
                "trusted_observations"
                if row.get("trusted_training_eligible") is True
                else "probe_observations"
            )
            model[key].append(copied)
    for model in models.values():
        scores = [float(item["actual_score"]) for item in model["clean_observations"]]
        model.update(
            {
                "contract": "tpcc_v39_role_separated_plan_regime_model_v1",
                "clean_observation_count": len(scores),
                "trusted_observation_count": len(model["trusted_observations"]),
                "probe_observation_count": len(model["probe_observations"]),
                "actual_score_min": min(scores),
                "actual_score_max": max(scores),
                "actual_score_span": max(scores) - min(scores),
                "trusted_eligible": len(model["trusted_observations"]) >= 2,
                "probe_rows_used_for_plan_prediction": len(model["probe_observations"]),
                "probe_rows_used_for_trust_eligibility": 0,
                "probe_rows_used_for_formal_acceptance": 0,
            }
        )
    return models


def _maximin(
    candidates: Sequence[Dict[str, Any]],
    count: int,
    ranges: Mapping[str, Any],
    anchors: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    return v6._maximin(candidates, count, ranges, anchors=anchors)


def _promotion_certificate(
    *,
    exact_point_key: str,
    plan: str,
    params: Mapping[str, float],
    observations: Sequence[Mapping[str, Any]],
    evidence_by_key: Mapping[str, Sequence[Mapping[str, Any]]],
) -> Dict[str, Any]:
    rows = [copy.deepcopy(dict(item)) for item in observations]
    spans = [
        max(float(item["actual_score"]) for item in values)
        - min(float(item["actual_score"]) for item in values)
        for values in evidence_by_key.values()
        if len(values) >= 3
    ]
    if not spans:
        raise RuntimeError("TPC-C v39 has no empirical repeated-point noise authority")
    noise_bound = float(median(spans))
    scores = [float(item["actual_score"]) for item in rows]
    score_span = max(scores) - min(scores)
    loo = v37._loo_metrics(rows)
    checks = {
        "exactly_three_independent_clean_probe_rows": bool(
            len(rows) == 3
            and len({item["selection_sha256"] for item in rows}) == 3
            and len({item["ledger_sha256"] for item in rows}) == 3
            and all(
                item.get("selection_role") == "uncertain_probe"
                and item.get("trusted_training_eligible") is False
                for item in rows
            )
        ),
        "score_loo_mae_within_limit": bool(
            loo.get("defined") is True
            and float(loo["score_mae"]) <= v37.SCORE_LOO_MAE_MAX
        ),
        "resource_loo_mae_within_limits": bool(
            loo.get("defined") is True
            and all(
                float(loo["resource_mae_pp"][name])
                <= v37.RESOURCE_LOO_MAE_PP_MAX[name]
                for name in RESOURCES
            )
        ),
        "score_span_within_empirical_noise": score_span <= noise_bound + 1e-12,
        "historical_roles_preserved_as_probe": all(
            item.get("selection_role") == "uncertain_probe"
            and item.get("trusted_training_eligible") is False
            for item in rows
        ),
        "formal_state_update_rows_zero": True,
    }
    certificate = {
        "contract": PROMOTION_CONTRACT,
        "exact_point_key": exact_point_key,
        "validated_plan": plan,
        "params": {name: float(value) for name, value in sorted(params.items())},
        "promotion_evaluation_rows": 3,
        "formal_state_update_rows": 0,
        "source_rows": rows,
        "source_selection_sha256s": [item["selection_sha256"] for item in rows],
        "source_ledger_sha256s": [item["ledger_sha256"] for item in rows],
        "leave_one_out": loo,
        "score_loo_mae_limit": v37.SCORE_LOO_MAE_MAX,
        "resource_loo_mae_pp_limits": copy.deepcopy(v37.RESOURCE_LOO_MAE_PP_MAX),
        "actual_score_span": score_span,
        "empirical_noise_bound": noise_bound,
        "history_role_rewrite_applied": False,
        "promotion_scope": "current_v39_diagnostic_selection_only",
        "checks": checks,
        "passed": all(checks.values()),
    }
    if certificate["passed"] is not True:
        raise RuntimeError("TPC-C v39 accumulated-probe promotion gate failed")
    return certificate


def build(
    *,
    selection_path: Path,
    ledger_path: Path,
    events_path: Path,
    summary_path: Path,
    gate_path: Path,
    formal_state_path: Path,
) -> Dict[str, Any]:
    source, _ledger, gate = validate_v38_authority(
        selection_path,
        ledger_path,
        events_path,
        summary_path,
        gate_path,
        formal_state_path,
    )
    pool, descriptors = v6.reconstruct_exact_pool(
        source,
        source_selection_path=selection_path,
        source_ledger_path=ledger_path,
    )
    current_ledger_sha = v6._sha256(ledger_path)
    evidence_by_key = v37._role_evidence(descriptors, current_ledger_sha)
    historical_ids = v37._historical_fingerprint_ids(descriptors)
    plan_models = _plan_models(
        evidence_by_key,
        {
            str(candidate["exact_point_key"]): str(
                candidate["workload_plan_fingerprint"]
            )
            for candidate in pool
        },
    )
    source_by_rank = {int(item["rank"]): item for item in source["top5"]}
    actual_best_rank = int((gate.get("quality_gate") or {})["actual_best_predicted_rank"])
    absorbed = source_by_rank[actual_best_rank]
    absorbed_key = v6._point_key(
        v6._params(absorbed), str(absorbed["workload_plan_fingerprint"])
    )
    absorbed_plan = str(absorbed["workload_plan_fingerprint"])
    if (
        absorbed.get("selection_role") != "uncertain_probe"
        or absorbed.get("trusted") is not False
        or absorbed_key not in evidence_by_key
        or len(evidence_by_key[absorbed_key]) < 3
        or absorbed_plan not in plan_models
        or plan_models[absorbed_plan]["trusted_eligible"] is not True
    ):
        raise RuntimeError("TPC-C v39 actual-best probe cannot be safely absorbed")
    promotion_certificate = _promotion_certificate(
        exact_point_key=absorbed_key,
        plan=absorbed_plan,
        params=v6._params(absorbed),
        observations=evidence_by_key[absorbed_key],
        evidence_by_key=evidence_by_key,
    )

    calibrated: List[Dict[str, Any]] = []
    ranges = source.get("candidate_param_ranges") or {}
    for raw in pool:
        candidate = copy.deepcopy(raw)
        key = str(candidate["exact_point_key"])
        evidence = list(evidence_by_key[key])
        trusted_rows = [item for item in evidence if item["trusted_training_eligible"] is True]
        probe_rows = [item for item in evidence if item["trusted_training_eligible"] is not True]
        if len(evidence) < 2:
            continue
        resource, score, representative_audit, recent = _recent_representative(evidence)
        utilization = {
            name: resource[name] / v6.repeat_v2.CAPABILITY[name] for name in RESOURCES
        }
        fingerprint = v6._make_fingerprint(candidate, resource, utilization)
        if fingerprint["id"] in historical_ids or v37._parameter_corner(candidate, ranges):
            continue
        recent_scores = [float(item["actual_score"]) for item in recent]
        plan = str(candidate["workload_plan_fingerprint"])
        plan_model = plan_models[plan]
        promoted_by_certificate = bool(
            key == absorbed_key and promotion_certificate["passed"] is True
        )
        trusted_eligible = bool(
            promoted_by_certificate
            or (len(trusted_rows) >= 2 and plan_model["trusted_eligible"] is True)
        )
        candidate.update(
            {
                "candidate_score": score,
                "candidate_score_recomputed": score,
                "predicted_score": score,
                "final_score": score,
                "formal_lcb_score": min(recent_scores),
                "robust_acceptance_score": min(recent_scores),
                "predicted_resource": resource,
                "final_resource": copy.deepcopy(resource),
                "uncalibrated_predicted_resource": copy.deepcopy(resource),
                "utilization": utilization,
                "three_level_fingerprint": fingerprint,
                "boundary_corner": False,
                "boundary_plateau": False,
                "diagnostic_only": True,
                "incumbent_update_eligible": False,
                "used_for_gradient": False,
                "used_for_inner_incumbent_update": False,
                "formal_improvement_eligible": False,
                "exact_trusted_observation_count": len(trusted_rows),
                "probe_observation_count_excluded_from_trust": len(probe_rows),
                "trusted_eligible_v39": trusted_eligible,
                "prediction_source": "two_most_recent_clean_exact_observations_v39",
                "repeat_prediction_source": "two_most_recent_clean_exact_observations_v39",
                "predicted_score_interval": {
                    "lower": min(recent_scores),
                    "center": score,
                    "upper": max(recent_scores),
                    "source": "two_most_recent_clean_exact_observations_v39",
                },
                "plan_regime_calibration": {
                    "contract": "tpcc_v39_clean_probe_absorbed_role_separated_plan_regime_v1",
                    "validated_plan": plan,
                    "plan_model": copy.deepcopy(plan_model),
                    "recent_exact_observation_count": len(recent),
                    "recent_exact_observations": copy.deepcopy(recent),
                    "recent_representative_audit": representative_audit,
                    "actual_best_probe_plan_absorbed": plan == absorbed_plan,
                    "actual_best_probe_promoted_by_certificate": promoted_by_certificate,
                    "probe_rows_used_for_plan_prediction": len(plan_model["probe_observations"]),
                    "probe_rows_used_for_trust_eligibility": 0,
                    "probe_rows_used_for_formal_acceptance": 0,
                    "trusted_eligible": trusted_eligible,
                },
                "source_provenance": {
                    "contract": "tpcc_v39_exact_role_separated_plan_absorption_v1",
                    "exact_point_key": key,
                    "validated_plan": plan,
                    "trusted_observations": copy.deepcopy(trusted_rows),
                    "probe_observations_excluded_from_trusted": copy.deepcopy(probe_rows),
                    "all_clean_observations": copy.deepcopy(evidence),
                },
                "accumulated_probe_promotion_certificate": (
                    copy.deepcopy(promotion_certificate)
                    if promoted_by_certificate
                    else None
                ),
            }
        )
        confidence = copy.deepcopy(candidate.get("confidence") or {})
        confidence.update(
            {
                "contract": (
                    "tpcc_v39_accumulated_probe_promotion_confidence_v1"
                    if promoted_by_certificate
                    else "tpcc_v39_recent_exact_plan_confidence_v1"
                ),
                "measurement_count": len(evidence),
                "trusted_measurement_count": len(trusted_rows),
                "probe_measurement_count_excluded_from_trust": len(probe_rows),
                "independent_actual_scores": [float(item["actual_score"]) for item in evidence],
                "recent_actual_scores": recent_scores,
                "score_lower": min(recent_scores),
                "score_center": score,
                "score_upper": max(recent_scores),
                "trusted_eligible": trusted_eligible,
                "promotion_evaluation_rows": 3 if promoted_by_certificate else 0,
                "formal_state_update_rows": 0,
            }
        )
        candidate["confidence"] = confidence
        calibrated.append(candidate)

    trusted_pool = [item for item in calibrated if item["trusted_eligible_v39"] is True]
    promoted_pool = [
        item for item in trusted_pool if item["exact_point_key"] == absorbed_key
    ]
    if len(promoted_pool) != 1:
        raise RuntimeError("TPC-C v39 promoted exact point is not uniquely eligible")
    mandatory = promoted_pool[0]
    trusted = [mandatory]
    for candidate in sorted(
        trusted_pool,
        key=lambda item: (
            -float(item["formal_lcb_score"]),
            -float(item["candidate_score"]),
            str(item["exact_point_key"]),
        ),
    ):
        if candidate["exact_point_key"] == mandatory["exact_point_key"]:
            continue
        trusted.append(candidate)
        if len(trusted) == 3:
            break
    trusted.sort(
        key=lambda item: (
            -float(item["formal_lcb_score"]),
            -float(item["candidate_score"]),
            str(item["exact_point_key"]),
        )
    )
    trusted_keys = {str(item["exact_point_key"]) for item in trusted}
    probe_pool = [
        item
        for item in calibrated
        if str(item["exact_point_key"]) not in trusted_keys
        and len(
            (item.get("source_provenance") or {}).get(
                "probe_observations_excluded_from_trusted"
            )
            or []
        )
        >= 2
    ]
    probes = _maximin(probe_pool, 2, ranges, trusted)
    selected = trusted + probes
    for rank, candidate in enumerate(selected, 1):
        role = "trusted" if rank <= 3 else "uncertain_probe"
        candidate["rank"] = rank
        candidate["selection_role"] = role
        candidate["trusted"] = role == "trusted"
        candidate["uncertain_probe"] = role != "trusted"
        candidate["exact_plan_regime_validated"] = role == "trusted"
        candidate["probe_kind"] = (
            None if role == "trusted" else "v39_temporal_or_unknown_regime_maximin"
        )
        candidate["probe_reason"] = (
            None
            if role == "trusted"
            else "not_selected_by_role_separated_trusted_lcb_gate"
        )
        candidate["plan_regime_calibration"]["trusted_eligible"] = role == "trusted"

    identifiers = [item["three_level_fingerprint"]["id"] for item in selected]
    roles = [item["selection_role"] for item in selected]
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
    if (
        len(selected) != CAP
        or len(set(identifiers)) != CAP
        or set(identifiers) & historical_ids
        or roles != ["trusted", "trusted", "trusted", "uncertain_probe", "uncertain_probe"]
        or any(item.get("boundary_corner") is not False for item in selected)
        or any(required_fields - set(item) for item in selected)
        or absorbed_key not in {str(item["exact_point_key"]) for item in trusted}
    ):
        raise RuntimeError("TPC-C v39 Top-5 admission contract failed")

    output = copy.deepcopy(source)
    output.update(
        {
            "schema_version": "tpcc_v39_plan_regime_absorption_validation_v1",
            "selection_contract": SELECTION_CONTRACT,
            "diagnostic_only": True,
            "maximum_real_workload_invocations": CAP,
            "qps_role": "outer_audit_only_not_inner_score",
            "state_mutation_contract": {
                "accepted_update": False,
                "incumbent_update": False,
                "range_update": False,
                "gradient_update": False,
            },
            "source_discovery": str(selection_path.resolve()),
            "source_discovery_sha256": v6._sha256(selection_path),
            "source_discovery_ledger": str(ledger_path.resolve()),
            "source_discovery_ledger_sha256": v6._sha256(ledger_path),
            "source_batches": descriptors,
            "top5": selected,
            "v38_build_contract": {
                "contract": "tpcc_v38_zero_call_fresh_unique_build_v1",
                "five_new_noncorner_fingerprints": True,
                "same_rank_retry_allowed": False,
                "technical_call_advances_to_next_unique_rank": True,
                "correctness_policy_sha256": CORRECTNESS_POLICY_SHA256,
                "state_neutral": True,
                "physical_workload_calls_added": 0,
                "compatibility_for": BUILD_CONTRACT,
            },
            "v39_plan_regime_absorption_contract": {
                "contract": BUILD_CONTRACT,
                "source_selection_path": str(selection_path.resolve()),
                "source_selection_sha256": v6._sha256(selection_path),
                "source_ledger_path": str(ledger_path.resolve()),
                "source_ledger_sha256": v6._sha256(ledger_path),
                "source_events_path": str(events_path.resolve()),
                "source_events_sha256": v6._sha256(events_path),
                "source_summary_path": str(summary_path.resolve()),
                "source_summary_sha256": v6._sha256(summary_path),
                "source_gate_path": str(gate_path.resolve()),
                "source_gate_sha256": v6._sha256(gate_path),
                "source_formal_state_path": str(formal_state_path.resolve()),
                "source_formal_state_sha256": v6._sha256(formal_state_path),
                "source_gate_only_failure": "actual_best_in_predicted_top3",
                "absorbed_probe_rank": actual_best_rank,
                "absorbed_probe_exact_point_key": absorbed_key,
                "absorbed_probe_plan": absorbed_plan,
                "absorbed_probe_actual_score": float(
                    {int(item["rank"]): item for item in _ledger["successful"]}[actual_best_rank][
                        "actual_score"
                    ]
                ),
                "absorbed_probe_updates_plan_prediction": True,
                "historical_probe_roles_rewritten": False,
                "promotion_certificate_updates_current_selection_role": True,
                "absorbed_probe_updates_formal_acceptance": False,
                "absorbed_exact_point_promoted_by_certificate": True,
                "promotion_evaluation_rows": 3,
                "formal_state_update_rows": 0,
                "promotion_certificate": copy.deepcopy(promotion_certificate),
                "recent_prediction_window": 2,
                "trusted_selection": "mandatory_certificate_promoted_exact_point_then_descending_recent_lcb",
                "probe_selection": "maximin_from_nonselected_points_with_two_probe_observations",
                "all_historical_fingerprint_count": len(historical_ids),
                "new_fingerprint_ids": identifiers,
                "five_new_noncorner_fingerprints": True,
                "roles": roles,
                "same_rank_retry_allowed": False,
                "technical_call_advances_to_next_unique_rank": True,
                "qps_role": "outer_audit_only_not_inner_score",
                "state_neutral": True,
                "physical_workload_calls_added": 0,
                "manual_candidate_selection": False,
            },
        }
    )
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--gate", type=Path, required=True)
    parser.add_argument("--formal-state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build(
        selection_path=args.selection.resolve(),
        ledger_path=args.ledger.resolve(),
        events_path=args.events.resolve(),
        summary_path=args.summary.resolve(),
        gate_path=args.gate.resolve(),
        formal_state_path=args.formal_state.resolve(),
    )
    v6.repeat_v2.import_base().atomic_json(args.output.resolve(), result)
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "roles": [item["selection_role"] for item in result["top5"]],
                "fingerprints": [
                    item["three_level_fingerprint"]["id"] for item in result["top5"]
                ],
                "predicted_scores": [item["predicted_score"] for item in result["top5"]],
                "formal_lcb_scores": [item["formal_lcb_score"] for item in result["top5"]],
                "new_workload_calls": 0,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
