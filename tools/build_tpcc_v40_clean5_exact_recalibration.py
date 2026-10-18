#!/usr/bin/env python3
"""Build a TPC-C v40 diagnostic only from a SHA-sealed v39 clean5 batch.

This program deliberately has no partial-batch mode.  It never changes a gate
threshold and never promotes directly to formal; it absorbs the three trusted
v39 exact observations, recomputes exact-point calibration/ranking, and emits
a fresh state-neutral 3-trusted/2-probe diagnostic Top-5.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
from statistics import median
from typing import Any, Dict

try:
    import build_tpcc_unique_exact_validation_v6 as v6
    import build_tpcc_v37_exact_drift_validation as exact_builder
    import build_tpcc_v39_plan_regime_absorption as v39
except ModuleNotFoundError:
    from tools import build_tpcc_unique_exact_validation_v6 as v6
    from tools import build_tpcc_v37_exact_drift_validation as exact_builder
    from tools import build_tpcc_v39_plan_regime_absorption as v39


CAP = 5
BUILD_CONTRACT = "tpcc_v40_sha_sealed_clean5_exact_recalibration_v1"


def _load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def _require_sha(path: Path, expected: str, label: str) -> None:
    if not path.is_file() or v6._sha256(path) != expected:
        raise RuntimeError(f"TPC-C v40 sealed SHA mismatch: {label}")


def validate_clean5_authority(
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
    successful = ledger.get("successful") or []
    failures = ledger.get("technical_failures") or []
    quality = gate.get("quality_gate") or {}
    checks = quality.get("checks") or {}
    failed = sorted(name for name, passed in checks.items() if passed is not True)
    if (
        (selection.get("v39_plan_regime_absorption_contract") or {}).get("contract")
        != "tpcc_v39_clean_probe_plan_regime_absorption_v1"
        or int(ledger.get("real_workload_invocation_count", -1)) != CAP
        or len(successful) != CAP
        or {int(item["rank"]) for item in successful} != {1, 2, 3, 4, 5}
        or failures
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
        or failed != ["trusted_score_mae_within_gate"]
        or (gate.get("v39_algorithm_gate") or {}).get("passed") is not True
        or int(formal.get("accepted_count", -1)) != 0
        or int(formal.get("formal_physical_call_count", -1)) != 0
        or int(formal.get("formal_valid_replay_count", -1)) != 0
        or int(formal.get("formal_technical_failure_count", -1)) != 0
    ):
        raise RuntimeError("TPC-C v40 requires sealed v39 clean5 with only score-MAE failure")
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


def build(**kwargs: Any) -> Dict[str, Any]:
    authority = validate_clean5_authority(**kwargs)
    selection_path = Path(kwargs["selection_path"])
    ledger_path = Path(kwargs["ledger_path"])
    source = authority["selection"]
    pool, descriptors = v6.reconstruct_exact_pool(
        source,
        source_selection_path=selection_path,
        source_ledger_path=ledger_path,
    )
    current_ledger_sha = v6._sha256(ledger_path)
    evidence = exact_builder._role_evidence(descriptors, current_ledger_sha)
    historical = exact_builder._historical_fingerprint_ids(descriptors)
    ranges = source.get("candidate_param_ranges") or {}
    source_by_key = {
        v6._point_key(v6._params(item), str(item["workload_plan_fingerprint"])): item
        for item in source["top5"]
    }
    observations_by_rank = {
        int(item["rank"]): item for item in authority["ledger"]["successful"]
    }
    promoted_key = str(
        (source.get("v39_plan_regime_absorption_contract") or {}).get(
            "absorbed_probe_exact_point_key"
        )
    )
    promotion = copy.deepcopy(
        (source.get("v39_plan_regime_absorption_contract") or {}).get(
            "promotion_certificate"
        )
        or {}
    )
    all_spans = [
        max(float(row["actual_score"]) for row in rows)
        - min(float(row["actual_score"]) for row in rows)
        for rows in evidence.values()
        if len(rows) >= 3
    ]
    empirical_noise = float(median(all_spans))
    promotion_noise_bound = min(
        empirical_noise,
        float(promotion.get("empirical_noise_bound", empirical_noise)),
    )
    calibrated = []
    for raw in pool:
        candidate = copy.deepcopy(raw)
        key = str(candidate["exact_point_key"])
        rows = list(evidence.get(key) or [])
        if len(rows) < 2:
            continue
        trusted_rows = [row for row in rows if row["trusted_training_eligible"] is True]
        probe_rows = [row for row in rows if row["trusted_training_eligible"] is not True]
        resource, score, representative_audit, recent = v39._recent_representative(rows)
        recent_scores = [float(row["actual_score"]) for row in recent]
        utilization = {
            name: resource[name] / v6.repeat_v2.CAPABILITY[name]
            for name in v6.RESOURCES
        }
        fingerprint = v6._make_fingerprint(candidate, resource, utilization)
        if fingerprint["id"] in historical or exact_builder._parameter_corner(candidate, ranges):
            continue
        current = source_by_key.get(key)
        current_trusted = bool(
            current
            and current.get("selection_role") == "trusted"
            and current.get("trusted") is True
        )
        current_score_residual = None
        if current_trusted:
            observed = observations_by_rank[int(current["rank"])]
            current_score_residual = abs(
                float(current["predicted_score"]) - float(observed["actual_score"])
            )
        loo = exact_builder._loo_metrics(trusted_rows)
        historical_loo_gate = bool(
            len(trusted_rows) >= exact_builder.MIN_TRUSTED_OBSERVATIONS
            and loo.get("defined") is True
            and float(loo["score_mae"]) <= exact_builder.SCORE_LOO_MAE_MAX
            and all(
                float(loo["resource_mae_pp"][name])
                <= exact_builder.RESOURCE_LOO_MAE_PP_MAX[name]
                for name in v6.RESOURCES
            )
        )
        certificate_continuation = bool(
            key == promoted_key
            and promotion.get("passed") is True
            and current_trusted
            and current_score_residual is not None
            and current_score_residual <= promotion_noise_bound
        )
        current_clean_residual_gate = bool(
            current_trusted
            and current_score_residual is not None
            and current_score_residual <= exact_builder.SCORE_LOO_MAE_MAX
            and (authority["gate"].get("quality_gate") or {}).get("checks", {}).get(
                "trusted_resource_no_order_of_magnitude_error"
            )
            is True
        )
        trusted_eligible = bool(
            certificate_continuation or current_clean_residual_gate or historical_loo_gate
        )
        candidate.update(
            {
                "candidate_score": score,
                "candidate_score_recomputed": score,
                "predicted_score": score,
                "final_score": score,
                "formal_lcb_score": min(recent_scores),
                "robust_acceptance_score": min(recent_scores),
                "predicted_score_interval": {
                    "lower": min(recent_scores),
                    "center": score,
                    "upper": max(recent_scores),
                    "source": "two_most_recent_clean_exact_observations_v40",
                },
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
                "trusted_eligible_v40": trusted_eligible,
                "prediction_source": "v39_clean5_absorbed_recent_exact_v40",
                "repeat_prediction_source": "v39_clean5_absorbed_recent_exact_v40",
                "source_provenance": {
                    "contract": "tpcc_v40_sha_sealed_role_separated_exact_evidence_v1",
                    "exact_point_key": key,
                    "trusted_observations": copy.deepcopy(trusted_rows),
                    "probe_observations_excluded_from_trusted": copy.deepcopy(probe_rows),
                    "all_clean_observations": copy.deepcopy(rows),
                },
                "plan_regime_calibration": {
                    "contract": "tpcc_v40_recent_exact_clean5_recalibration_v1",
                    "validated_plan": candidate["workload_plan_fingerprint"],
                    "recent_exact_observations": copy.deepcopy(recent),
                    "recent_representative_audit": representative_audit,
                    "current_clean_trusted": current_trusted,
                    "current_score_residual": current_score_residual,
                    "historical_loo_gate": historical_loo_gate,
                    "certificate_continuation": certificate_continuation,
                    "current_clean_residual_gate": current_clean_residual_gate,
                    "probe_rows_used_for_formal_state_update": 0,
                    "trusted_eligible": trusted_eligible,
                },
            }
        )
        confidence = copy.deepcopy(candidate.get("confidence") or {})
        confidence.update(
            {
                "contract": "tpcc_v40_clean5_recent_exact_confidence_v1",
                "measurement_count": len(rows),
                "trusted_measurement_count": len(trusted_rows),
                "probe_measurement_count": len(probe_rows),
                "recent_actual_scores": recent_scores,
                "score_lower": min(recent_scores),
                "score_center": score,
                "score_upper": max(recent_scores),
                "historical_trusted_loo": loo,
                "empirical_noise_bound": empirical_noise,
                "promotion_continuation_noise_bound": promotion_noise_bound,
                "trusted_order": "descending_recent_exact_lcb",
                "trusted_eligible": trusted_eligible,
            }
        )
        candidate["confidence"] = confidence
        calibrated.append(candidate)

    trusted = sorted(
        [item for item in calibrated if item["trusted_eligible_v40"] is True],
        key=lambda item: (-float(item["formal_lcb_score"]), str(item["exact_point_key"])),
    )[:3]
    if len(trusted) != 3:
        raise RuntimeError(f"TPC-C v40 has only {len(trusted)} trusted candidates")
    trusted_keys = {str(item["exact_point_key"]) for item in trusted}
    probe_pool = [
        item for item in calibrated
        if str(item["exact_point_key"]) not in trusted_keys
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
                "probe_kind": None if role == "trusted" else "v40_role_separated_maximin",
                "probe_reason": None if role == "trusted" else "not_admitted_by_v40_trust_gate",
            }
        )
        item["plan_regime_calibration"]["trusted_eligible"] = role == "trusted"

    identifiers = [str(item["three_level_fingerprint"]["id"]) for item in top5]
    roles = [item.get("selection_role") for item in top5]
    if (
        len(top5) != CAP
        or roles != ["trusted", "trusted", "trusted", "uncertain_probe", "uncertain_probe"]
        or len(set(identifiers)) != CAP
        or bool(set(identifiers) & historical)
        or any(item.get("boundary_corner") is True for item in top5)
    ):
        raise RuntimeError("TPC-C v40 fresh 3T2P admission failed")
    result = copy.deepcopy(source)
    result.update(
        {
            "schema_version": "tpcc_v40_clean5_exact_recalibration_validation_v1",
            "diagnostic_only": True,
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
            "v40_build_contract": {
                "contract": BUILD_CONTRACT,
                "sealed_authority": copy.deepcopy(authority["pins"]),
                "source_gate_only_failure": "trusted_score_mae_within_gate",
                "threshold_changed": False,
                "current_clean_trusted_rows_absorbed": 3,
                "calibration": "two_most_recent_clean_exact_observations",
                "trusted_admission": (
                    "certificate_continuation_or_current_clean_score_residual_"
                    "or_historical_trusted_loo"
                ),
                "score_residual_limit": exact_builder.SCORE_LOO_MAE_MAX,
                "empirical_noise_bound": empirical_noise,
                "promotion_continuation_noise_bound": promotion_noise_bound,
                "trusted_order": "descending_recent_exact_lcb",
                "direct_formal_promotion": False,
                "five_new_noncorner_fingerprints": True,
                "roles": roles,
                "fingerprint_ids": identifiers,
                "state_neutral": True,
                "physical_workload_calls_added": 0,
                "manual_candidate_selection": False,
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
