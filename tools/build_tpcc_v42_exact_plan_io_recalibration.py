#!/usr/bin/env python3
"""Build a state-neutral TPC-C v42 Top-5 from the sealed v40 clean5 batch."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from statistics import median
from typing import Any, Dict, Mapping, Sequence

try:
    import build_tpcc_unique_exact_validation_v6 as v6
    import build_tpcc_v37_exact_drift_validation as v37
except ModuleNotFoundError:
    from tools import build_tpcc_unique_exact_validation_v6 as v6
    from tools import build_tpcc_v37_exact_drift_validation as v37


CAP = 5
BUILD_CONTRACT = "tpcc_v42_exact_plan_io_role_separated_recalibration_v1"
SCORE_MAE_MAX = 0.05
RESOURCE_MAE_PP_MAX = {"cpu": 5.0, "io": 5.0, "memory": 2.0}
EXPECTED_FAILED_GATES = {
    "actual_best_in_predicted_top3",
    "trusted_io_mae_within_gate",
}


def _load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def _require_sha(path: Path, expected: str, label: str) -> None:
    if not path.is_file() or v6._sha256(path) != expected:
        raise RuntimeError(f"TPC-C v42 sealed SHA mismatch: {label}")


def validate_v40_clean5_authority(
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
    paths = {
        "selection": (selection_path, selection_sha256),
        "ledger": (ledger_path, ledger_sha256),
        "events": (events_path, events_sha256),
        "summary": (summary_path, summary_sha256),
        "gate": (gate_path, gate_sha256),
        "formal_state": (formal_state_path, formal_state_sha256),
    }
    for label, (path, expected) in paths.items():
        _require_sha(path, expected, label)
    selection = _load(selection_path)
    ledger = _load(ledger_path)
    summary = _load(summary_path)
    gate = _load(gate_path)
    formal = _load(formal_state_path)
    quality = gate.get("quality_gate") or {}
    checks = quality.get("checks") or {}
    failed = {name for name, passed in checks.items() if passed is not True}
    successful = ledger.get("successful") or []
    roles = [item.get("selection_role") for item in selection.get("top5") or []]
    observations = {int(item["rank"]): item for item in successful}
    rank4 = observations.get(4) or {}
    if (
        (selection.get("v40_build_contract") or {}).get("contract")
        != "tpcc_v40_sha_sealed_clean5_exact_recalibration_v1"
        or roles
        != ["trusted", "trusted", "trusted", "uncertain_probe", "uncertain_probe"]
        or int(ledger.get("real_workload_invocation_count", -1)) != CAP
        or int(ledger.get("maximum_real_workload_invocations", -1)) != CAP
        or len(successful) != CAP
        or set(observations) != {1, 2, 3, 4, 5}
        or ledger.get("technical_failures") not in ([], None)
        or ledger.get("preworkload_failures") not in ([], None)
        or int(summary.get("real_workload_invocation_count", -1)) != CAP
        or int(summary.get("successful_real_replay_count", -1)) != CAP
        or int(summary.get("technical_failure_count", -1)) != 0
        or int(summary.get("preworkload_failure_count", -1)) != 0
        or summary.get("baseline_params_restored") is not True
        or summary.get("baseline_snapshot_restored") is not True
        or summary.get("gradient_update_applied") is not False
        or summary.get("incumbent_updated") is not False
        or summary.get("range_updated") is not False
        or int(summary.get("accepted_count_delta", -1)) != 0
        or gate.get("passed") is not False
        or gate.get("formal_start_authorized") is not False
        or failed != EXPECTED_FAILED_GATES
        or checks.get("trusted_score_mae_within_gate") is not True
        or float((quality.get("thresholds") or {}).get("score_mae_max", -1))
        != SCORE_MAE_MAX
        or (quality.get("thresholds") or {}).get("resource_mae_pp_max")
        != RESOURCE_MAE_PP_MAX
        or float(quality.get("trusted_score_mae", 1.0)) > SCORE_MAE_MAX
        or (gate.get("v40_algorithm_gate") or {}).get("passed") is not True
        or rank4.get("selection_role") != "uncertain_probe"
        or rank4.get("diagnostic_only") is not True
        or rank4.get("eligible_for_incumbent_or_accepted") is not False
        or rank4.get("incumbent_update_eligible") is not False
        or int(formal.get("accepted_count", -1)) != 0
        or int(formal.get("formal_physical_call_count", -1)) != 0
        or int(formal.get("formal_valid_replay_count", -1)) != 0
        or int(formal.get("formal_technical_failure_count", -1)) != 0
    ):
        raise RuntimeError("TPC-C v42 requires sealed state-neutral v40 clean5 evidence")
    return {
        "selection": selection,
        "ledger": ledger,
        "summary": summary,
        "gate": gate,
        "formal": formal,
        "pins": {
            label: {"path": str(path.resolve()), "sha256": expected}
            for label, (path, expected) in paths.items()
        },
    }


def _resource_offset(
    candidate: Mapping[str, Any], observation: Mapping[str, Any]
) -> Dict[str, float]:
    return {
        name: float(observation["actual_resource"][name])
        - float(candidate["predicted_resource"][name])
        for name in v6.RESOURCES
    }


def _apply_offset(
    resource: Mapping[str, Any], offset: Mapping[str, Any]
) -> Dict[str, float]:
    return {
        name: max(0.0, float(resource[name]) + float(offset[name]))
        for name in v6.RESOURCES
    }


def _score_margin(rows: Sequence[Mapping[str, Any]]) -> float:
    scores = [float(row["actual_score"]) for row in rows]
    if len(scores) < 2:
        return SCORE_MAE_MAX
    center = median(scores)
    return max(SCORE_MAE_MAX, median(abs(value - center) for value in scores))


def build(**kwargs: Any) -> Dict[str, Any]:
    authority = validate_v40_clean5_authority(**kwargs)
    selection_path = Path(kwargs["selection_path"])
    ledger_path = Path(kwargs["ledger_path"])
    source = authority["selection"]
    current_ledger = authority["ledger"]
    pool, descriptors = v6.reconstruct_exact_pool(
        source,
        source_selection_path=selection_path,
        source_ledger_path=ledger_path,
    )
    current_ledger_sha = v6._sha256(ledger_path)
    evidence_by_key = v37._role_evidence(descriptors, current_ledger_sha)
    historical_ids = v37._historical_fingerprint_ids(descriptors)
    ranges = source.get("candidate_param_ranges") or {}
    current_by_key = {
        v6._point_key(v6._params(item), str(item["workload_plan_fingerprint"])): item
        for item in source["top5"]
    }
    observations = {
        int(item["rank"]): item for item in current_ledger["successful"]
    }
    trusted_current = [item for item in source["top5"] if item["selection_role"] == "trusted"]
    probe_current = [
        item for item in source["top5"] if item["selection_role"] == "uncertain_probe"
    ]
    trusted_plans = {str(item["workload_plan_fingerprint"]) for item in trusted_current}
    trusted_drift = v37._drift_calibration(source, current_ledger)
    if (
        trusted_drift.get("probe_rows_used") != 0
        or float(trusted_drift.get("score_mae_after_offset", 1.0)) > SCORE_MAE_MAX
    ):
        raise RuntimeError("TPC-C v42 trusted-only drift calibration is not admissible")
    probe_plan_offsets = {
        str(item["workload_plan_fingerprint"]): _resource_offset(
            item, observations[int(item["rank"])]
        )
        for item in probe_current
    }
    calibrated = []
    rank4_key = v6._point_key(
        v6._params(source["top5"][3]),
        str(source["top5"][3]["workload_plan_fingerprint"]),
    )
    for raw in pool:
        candidate = copy.deepcopy(raw)
        key = str(candidate["exact_point_key"])
        rows = list(evidence_by_key.get(key) or [])
        trusted_rows = [row for row in rows if row["trusted_training_eligible"] is True]
        probe_rows = [row for row in rows if row["trusted_training_eligible"] is not True]
        historical_trusted = [row for row in trusted_rows if row["is_current_batch"] is not True]
        historical_any = [row for row in rows if row["is_current_batch"] is not True]
        base_rows = historical_trusted or historical_any or trusted_rows or rows
        if not base_rows:
            continue
        historical_resource, historical_score, representative_audit = (
            v37._robust_exact_resource(base_rows)
        )
        current = current_by_key.get(key)
        plan = str(candidate["workload_plan_fingerprint"])
        if current is not None:
            predicted_resource = copy.deepcopy(
                observations[int(current["rank"])]["actual_resource"]
            )
            calibration_domain = "current_exact_point_observation"
        elif plan in trusted_plans:
            predicted_resource = _apply_offset(
                historical_resource, trusted_drift["resource_offset"]
            )
            calibration_domain = "trusted_only_exact_point_plus_plan_drift"
        elif plan in probe_plan_offsets:
            predicted_resource = _apply_offset(
                historical_resource, probe_plan_offsets[plan]
            )
            calibration_domain = "probe_only_exact_point_plus_plan_drift"
        else:
            predicted_resource = copy.deepcopy(historical_resource)
            calibration_domain = "historical_exact_point_probe_only"
        utilization = {
            name: predicted_resource[name] / v6.repeat_v2.CAPABILITY[name]
            for name in v6.RESOURCES
        }
        fingerprint = v6._make_fingerprint(
            candidate, predicted_resource, utilization
        )
        if (
            fingerprint["id"] in historical_ids
            or v37._parameter_corner(candidate, ranges)
        ):
            continue
        current_role = None if current is None else current.get("selection_role")
        # A previously measured probe is evidence, not a fresh acquisition
        # point.  Replaying the same exact probe under a changed resource-level
        # fingerprint would both waste budget and make the known probe winner
        # masquerade as a novel candidate.
        if current_role == "uncertain_probe":
            continue
        trusted_eligible = bool(
            plan in trusted_plans
            and len(trusted_rows) >= 2
            and calibration_domain
            in {
                "current_exact_point_observation",
                "trusted_only_exact_point_plus_plan_drift",
            }
        )
        # Probe evidence may improve probe prediction, but can never satisfy
        # this admission predicate or enter any formal/gradient state.
        if key == rank4_key:
            trusted_eligible = False
        score = v6._score(predicted_resource)
        margin = _score_margin(trusted_rows if trusted_eligible else rows)
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
                    "source": "v42_role_separated_exact_plan_io_recalibration",
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
                "trusted_eligible_v42": trusted_eligible,
                "prediction_source": "v42_role_separated_exact_plan_io_recalibration",
                "repeat_prediction_source": "v42_role_separated_exact_plan_io_recalibration",
                "source_provenance": {
                    "contract": "tpcc_v42_role_separated_exact_plan_evidence_v1",
                    "exact_point_key": key,
                    "validated_plan": plan,
                    "trusted_observations": copy.deepcopy(trusted_rows),
                    "probe_observations_excluded_from_trusted": copy.deepcopy(probe_rows),
                    "rank4_probe_observation_present": key == rank4_key,
                    "rank4_probe_formal_state_update_rows": 0,
                    "rank4_probe_gradient_update_rows": 0,
                    "rank4_probe_incumbent_update_rows": 0,
                },
                "plan_regime_calibration": {
                    "contract": "tpcc_v42_exact_point_plan_regime_io_calibration_v1",
                    "validated_plan": plan,
                    "calibration_domain": calibration_domain,
                    "historical_resource": historical_resource,
                    "historical_resource_score": historical_score,
                    "representative_audit": representative_audit,
                    "corrected_resource": copy.deepcopy(predicted_resource),
                    "trusted_plan_drift": (
                        copy.deepcopy(trusted_drift)
                        if calibration_domain
                        == "trusted_only_exact_point_plus_plan_drift"
                        else None
                    ),
                    "probe_plan_offset": (
                        copy.deepcopy(probe_plan_offsets.get(plan))
                        if "probe_only" in calibration_domain
                        else None
                    ),
                    "probe_rows_used_for_trusted_calibration": 0,
                    "rank4_probe_used_for_formal_state": False,
                    "rank4_probe_used_for_gradient": False,
                    "rank4_probe_used_for_incumbent": False,
                    "trusted_eligible": trusted_eligible,
                },
            }
        )
        confidence = copy.deepcopy(candidate.get("confidence") or {})
        confidence.update(
            {
                "contract": "tpcc_v42_role_separated_exact_plan_confidence_v1",
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

    trusted = sorted(
        [item for item in calibrated if item["trusted_eligible_v42"] is True],
        key=lambda item: (-float(item["formal_lcb_score"]), str(item["exact_point_key"])),
    )[:3]
    if len(trusted) != 3:
        raise RuntimeError(f"TPC-C v42 has only {len(trusted)} trusted candidates")
    trusted_keys = {str(item["exact_point_key"]) for item in trusted}
    probe_pool = [
        item
        for item in calibrated
        if str(item["exact_point_key"]) not in trusted_keys
        and item["trusted_eligible_v42"] is not True
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
                "probe_kind": None if role == "trusted" else "v42_role_separated_maximin",
                "probe_reason": None if role == "trusted" else "not_admitted_by_v42_trusted_only_gate",
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
        raise RuntimeError("TPC-C v42 fresh 3T2P admission failed")
    result = copy.deepcopy(source)
    result.update(
        {
            "schema_version": "tpcc_v42_exact_plan_io_recalibration_validation_v1",
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
            "v42_build_contract": {
                "contract": BUILD_CONTRACT,
                "sealed_v40_authority": copy.deepcopy(authority["pins"]),
                "source_gate_failures": sorted(EXPECTED_FAILED_GATES),
                "threshold_changed": False,
                "score_mae_max": SCORE_MAE_MAX,
                "resource_mae_pp_max": copy.deepcopy(RESOURCE_MAE_PP_MAX),
                "io_calibration": "exact_point_then_role_separated_plan_regime_offset",
                "trusted_plan_anchor_ranks": [1, 2, 3],
                "trusted_plan_probe_rows_used": 0,
                "rank4_probe_calibration_domain": "probe_only",
                "current_probe_exact_points_replayed": False,
                "rank4_probe_formal_state_update_rows": 0,
                "rank4_probe_gradient_update_rows": 0,
                "rank4_probe_incumbent_update_rows": 0,
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
    print(
        json.dumps(
            {"output": str(args.output.resolve()), "workload_calls_added": 0},
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
