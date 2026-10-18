#!/usr/bin/env python3
"""Build TPC-C v39 from the high-scoring v38 probe regime, with no replay."""

from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
from statistics import median
from typing import Any, Dict, Iterable, Mapping, Sequence

try:
    import build_tpcc_unique_exact_validation_v6 as v6
    import build_tpcc_v37_exact_drift_validation as v37
except ModuleNotFoundError:
    from tools import build_tpcc_unique_exact_validation_v6 as v6
    from tools import build_tpcc_v37_exact_drift_validation as v37


CAP = 5
RESOURCES = ("cpu", "io", "memory")
SELECTION_CONTRACT = "tpcc_v39_probe_discovered_plan_regime_trusted3_probe2_v1"
BUILD_CONTRACT = "tpcc_v39_zero_call_probe_regime_calibration_v1"
REGIME_CONTRACT = "tpcc_v39_current_clean_trusted_plus_probe_residual_regime_v1"
SCORE_MAE_MAX = 0.05
RESOURCE_MAE_PP_MAX = {"cpu": 5.0, "io": 5.0, "memory": 2.0}


def _load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def _current_pairs(
    selection: Mapping[str, Any],
    ledger: Mapping[str, Any],
) -> list[tuple[Dict[str, Any], Dict[str, Any]]]:
    return v6._validate_clean_batch(selection, ledger, "v38_current_clean5")


def _validate_failed_gate(
    gate: Mapping[str, Any],
    selection_path: Path,
    ledger_path: Path,
    summary_path: Path,
) -> None:
    checks = (gate.get("quality_gate") or {}).get("checks") or {}
    failed = sorted(name for name, passed in checks.items() if passed is not True)
    if (
        gate.get("passed") is not False
        or gate.get("formal_start_authorized") is not False
        or failed != ["actual_best_in_predicted_top3"]
        or int((gate.get("quality_gate") or {}).get("actual_best_predicted_rank", -1)) != 5
        or int((gate.get("accounting") or {}).get("physical_calls", -1)) != 5
        or int((gate.get("accounting") or {}).get("valid_replays", -1)) != 5
        or int((gate.get("accounting") or {}).get("technical_failures", -1)) != 0
        or (gate.get("state_isolation") or {}).get("passed") is not True
        or int(gate.get("accepted_count_delta", -1)) != 0
        or gate.get("selection_sha256") != v6._sha256(selection_path)
        or gate.get("ledger_sha256") != v6._sha256(ledger_path)
        or gate.get("summary_sha256") != v6._sha256(summary_path)
    ):
        raise RuntimeError("v38 gate is not the sealed clean5 rank5-only miss")


def _regime_calibrations(
    pairs: Sequence[tuple[Mapping[str, Any], Mapping[str, Any]]],
) -> Dict[str, Dict[str, Any]]:
    grouped: Dict[str, list[tuple[Mapping[str, Any], Mapping[str, Any]]]] = {}
    for candidate, observation in pairs:
        plan = str(candidate["workload_plan_fingerprint"])
        grouped.setdefault(plan, []).append((candidate, observation))
    result: Dict[str, Dict[str, Any]] = {}
    for plan, rows in grouped.items():
        residuals = {
            name: [
                float(observation["actual_resource"][name])
                - float(candidate["predicted_resource"][name])
                for candidate, observation in rows
            ]
            for name in RESOURCES
        }
        offset = {name: median(values) for name, values in residuals.items()}
        score_errors = []
        resource_errors = {name: [] for name in RESOURCES}
        for candidate, observation in rows:
            corrected = {
                name: max(0.0, float(candidate["predicted_resource"][name]) + offset[name])
                for name in RESOURCES
            }
            score_errors.append(
                abs(v6._score(corrected) - float(observation["actual_score"]))
            )
            for name in RESOURCES:
                resource_errors[name].append(
                    abs(corrected[name] - float(observation["actual_resource"][name]))
                    / v6.repeat_v2.CAPABILITY[name]
                    * 100.0
                )
        roles = [str(candidate.get("selection_role")) for candidate, _ in rows]
        score_mae = sum(score_errors) / len(score_errors)
        resource_mae = {
            name: sum(values) / len(values) for name, values in resource_errors.items()
        }
        result[plan] = {
            "contract": REGIME_CONTRACT,
            "plan": plan,
            "current_anchor_count": len(rows),
            "current_trusted_anchor_count": roles.count("trusted"),
            "current_probe_anchor_count": roles.count("uncertain_probe"),
            "anchor_ranks": [int(observation["rank"]) for _, observation in rows],
            "resource_offset": offset,
            "resource_residuals": residuals,
            "score_absolute_errors_after_offset": score_errors,
            "score_mae_after_offset": score_mae,
            "score_max_absolute_error_after_offset": max(score_errors),
            "resource_mae_pp_after_offset": resource_mae,
            "validated_for_trusted_transfer": bool(
                len(rows) >= 2
                and roles.count("trusted") >= 1
                and roles.count("uncertain_probe") >= 1
                and score_mae <= SCORE_MAE_MAX
                and all(
                    resource_mae[name] <= RESOURCE_MAE_PP_MAX[name]
                    for name in RESOURCES
                )
            ),
            "probe_rows_used_for_regime_discovery": roles.count("uncertain_probe"),
            "probe_rows_used_for_gradient_incumbent_accepted": 0,
        }
    return result


def _distance(
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
        denominator = span if span > 0 else max(abs(left_params[name]), 1.0)
        terms.append(((left_params[name] - right_params[name]) / denominator) ** 2)
    return math.sqrt(sum(terms) / max(len(terms), 1))


def _diverse_lcb(
    candidates: Iterable[Dict[str, Any]],
    count: int,
    ranges: Mapping[str, Any],
    anchors: Sequence[Dict[str, Any]] = (),
) -> list[Dict[str, Any]]:
    remaining = list(candidates)
    selected: list[Dict[str, Any]] = []
    while len(selected) < count:
        if not remaining:
            raise RuntimeError("v39 has insufficient candidates for deterministic acquisition")
        references = [*anchors, *selected]
        chosen = max(
            remaining,
            key=lambda item: (
                float(item["formal_lcb_score"]),
                min((_distance(item, ref, ranges) for ref in references), default=0.0),
                str(item["exact_point_key"]),
            ),
        )
        selected.append(chosen)
        remaining = [
            item for item in remaining if item["exact_point_key"] != chosen["exact_point_key"]
        ]
    return selected


def build(
    source_selection: Dict[str, Any],
    *,
    source_selection_path: Path,
    source_ledger_path: Path,
    source_summary_path: Path,
    source_gate_path: Path,
) -> Dict[str, Any]:
    ledger = _load(source_ledger_path)
    summary = _load(source_summary_path)
    gate = _load(source_gate_path)
    _validate_failed_gate(
        gate,
        source_selection_path,
        source_ledger_path,
        source_summary_path,
    )
    pairs = _current_pairs(source_selection, ledger)
    current_by_rank = {int(observation["rank"]): (candidate, observation) for candidate, observation in pairs}
    best_rank = max(
        current_by_rank,
        key=lambda rank: float(current_by_rank[rank][1]["actual_score"]),
    )
    best_candidate, best_observation = current_by_rank[best_rank]
    if best_rank != 5 or best_candidate.get("selection_role") != "uncertain_probe":
        raise RuntimeError("v38 empirical best is not the sealed rank5 probe")
    discovered_plan = str(best_candidate["workload_plan_fingerprint"])

    pool, descriptors = v6.reconstruct_exact_pool(
        source_selection,
        source_selection_path=source_selection_path,
        source_ledger_path=source_ledger_path,
    )
    historical_ids = v37._historical_fingerprint_ids(descriptors)
    calibrations = _regime_calibrations(pairs)
    discovered_calibration = calibrations.get(discovered_plan) or {}
    if discovered_calibration.get("validated_for_trusted_transfer") is not True:
        raise RuntimeError("rank5 probe regime did not pass transferable calibration gates")
    ranges = source_selection.get("candidate_param_ranges") or {}
    current_ledger_sha = v6._sha256(source_ledger_path)
    current_trusted_keys = {
        v6._point_key(
            v6._params(candidate), str(candidate["workload_plan_fingerprint"])
        )
        for candidate, _observation in pairs
        if candidate.get("selection_role") == "trusted"
        and candidate.get("trusted") is True
        and (candidate.get("confidence") or {}).get("trusted_eligible") is True
    }

    calibrated: list[Dict[str, Any]] = []
    for raw in pool:
        candidate = copy.deepcopy(raw)
        plan = str(candidate["workload_plan_fingerprint"])
        regime = calibrations.get(plan)
        offset = (
            dict(regime["resource_offset"])
            if regime is not None
            else {name: 0.0 for name in RESOURCES}
        )
        observations = candidate.get("exact_evidence") or []
        historical_observations = [
            item
            for item in observations
            if item.get("ledger_sha256") != current_ledger_sha
        ] or observations
        base_resource = {
            name: median(
                float(item["actual_resource"][name])
                for item in historical_observations
            )
            for name in RESOURCES
        }
        predicted_resource = {
            name: max(0.0, base_resource[name] + float(offset[name]))
            for name in RESOURCES
        }
        utilization = {
            name: predicted_resource[name] / v6.repeat_v2.CAPABILITY[name]
            for name in RESOURCES
        }
        score = v6._score(predicted_resource)
        observed_scores = [float(item["actual_score"]) for item in observations]
        empirical_median = median(observed_scores)
        empirical_mad = median(
            abs(value - empirical_median) for value in observed_scores
        )
        regime_error = (
            float(regime["score_max_absolute_error_after_offset"])
            if regime is not None
            else SCORE_MAE_MAX
        )
        margin = max(regime_error, 1.4826 * empirical_mad)
        fingerprint = v6._make_fingerprint(candidate, predicted_resource, utilization)
        discovered_regime_transfer = bool(
            plan == discovered_plan
            and discovered_calibration["validated_for_trusted_transfer"] is True
            and len(observations) >= 1
        )
        exact_trusted_control = str(candidate["exact_point_key"]) in current_trusted_keys
        trusted_transfer = bool(
            (discovered_regime_transfer or exact_trusted_control)
            and not v37._parameter_corner(candidate, ranges)
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
                    "source": "v38_probe_discovered_plan_regime_residual_v39",
                },
                "predicted_resource": predicted_resource,
                "final_resource": copy.deepcopy(predicted_resource),
                "uncalibrated_predicted_resource": base_resource,
                "utilization": utilization,
                "three_level_fingerprint": fingerprint,
                "diagnostic_only": True,
                "incumbent_update_eligible": False,
                "formal_improvement_eligible": False,
                "used_for_gradient": False,
                "used_for_inner_incumbent_update": False,
                "empirical_exact_score_median": empirical_median,
                "empirical_exact_score_mad": empirical_mad,
                "trusted_transfer_eligible_v39": trusted_transfer,
                "discovered_regime_transfer_eligible_v39": discovered_regime_transfer,
                "exact_trusted_control_eligible_v39": exact_trusted_control,
                "prediction_source": "v38_probe_discovered_plan_regime_residual_v39",
                "repeat_prediction_source": "v38_probe_discovered_plan_regime_residual_v39",
                "plan_regime_calibration": copy.deepcopy(regime),
            }
        )
        confidence = copy.deepcopy(candidate.get("confidence") or {})
        confidence.update(
            {
                "contract": "tpcc_v39_probe_regime_transfer_confidence_v1",
                "score_lower": score - margin,
                "score_center": score,
                "score_upper": score + margin,
                "empirical_exact_observation_count": len(observations),
                "empirical_exact_score_mad": empirical_mad,
                "regime_transfer_validated": trusted_transfer,
                "probe_evidence_used_for_regime_discovery_only": bool(
                    regime and int(regime["probe_rows_used_for_regime_discovery"]) > 0
                ),
                "probe_evidence_used_for_gradient_incumbent_accepted": 0,
            }
        )
        candidate["confidence"] = confidence
        calibrated.append(candidate)

    novel = [
        item
        for item in calibrated
        if str(item["three_level_fingerprint"]["id"]) not in historical_ids
        and not v37._parameter_corner(item, ranges)
    ]
    trusted_pool = [item for item in novel if item["trusted_transfer_eligible_v39"] is True]
    if len(trusted_pool) < 3:
        raise RuntimeError("v39 has fewer than three novel trusted regime candidates")
    discovered_trusted_pool = [
        item
        for item in trusted_pool
        if item["discovered_regime_transfer_eligible_v39"] is True
    ]
    exact_control_pool = [
        item
        for item in trusted_pool
        if item["exact_trusted_control_eligible_v39"] is True
        and item["workload_plan_fingerprint"] != discovered_plan
    ]
    if len(discovered_trusted_pool) < 2 or not exact_control_pool:
        raise RuntimeError("v39 lacks exploit-plus-control trusted acquisition coverage")
    stability_anchor = max(
        discovered_trusted_pool,
        key=lambda item: (
            float(item["empirical_exact_score_median"]),
            -float(item["empirical_exact_score_mad"]),
            str(item["exact_point_key"]),
        ),
    )
    regime_exploit = _diverse_lcb(
        (
            item
            for item in discovered_trusted_pool
            if item["exact_point_key"] != stability_anchor["exact_point_key"]
        ),
        1,
        ranges,
        anchors=[stability_anchor],
    )[0]
    trusted_control = _diverse_lcb(
        exact_control_pool,
        1,
        ranges,
        anchors=[stability_anchor, regime_exploit],
    )[0]
    trusted = [stability_anchor, regime_exploit, trusted_control]
    trusted_keys = {str(item["exact_point_key"]) for item in trusted}
    probe_pool = [
        item
        for item in novel
        if str(item["exact_point_key"]) not in trusted_keys
    ]
    probes = v6._maximin(probe_pool, 2, ranges, anchors=trusted)
    selected = trusted + probes
    for rank, candidate in enumerate(selected, 1):
        role = "trusted" if rank <= 3 else "uncertain_probe"
        candidate["rank"] = rank
        candidate["selection_role"] = role
        candidate["trusted"] = role == "trusted"
        candidate["uncertain_probe"] = role != "trusted"
        candidate["exact_plan_regime_validated"] = role == "trusted"
        candidate["probe_kind"] = (
            None if role == "trusted" else "v39_heldout_or_unvalidated_regime_maximin"
        )
        candidate["probe_reason"] = (
            None
            if role == "trusted"
            else "outside_trusted3_acquisition_cap_or_plan_regime_not_transfer_validated"
        )
        candidate["stability_acquisition"] = bool(
            rank == 1 and candidate["exact_point_key"] == stability_anchor["exact_point_key"]
        )

    ids = [str(item["three_level_fingerprint"]["id"]) for item in selected]
    exact_keys = [str(item["exact_point_key"]) for item in selected]
    roles = [str(item["selection_role"]) for item in selected]
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
        len(set(ids)) != CAP
        or len(set(exact_keys)) != CAP
        or set(ids) & historical_ids
        or roles != ["trusted", "trusted", "trusted", "uncertain_probe", "uncertain_probe"]
        or any(item.get("boundary_corner") is True for item in selected)
        or any(required_fields - set(item) for item in selected)
        or stability_anchor["workload_plan_fingerprint"] != discovered_plan
    ):
        raise RuntimeError("v39 Top-5 role/novelty/logging contract failed")

    output = copy.deepcopy(source_selection)
    output.update(
        {
            "schema_version": "tpcc_probe_regime_validation_v39",
            "selection_contract": SELECTION_CONTRACT,
            "diagnostic_only": True,
            "diagnostic_role_evaluation_only": True,
            "maximum_real_workload_invocations": CAP,
            "same_rank_retry_allowed": False,
            "technical_call_advances_to_next_unique_rank": True,
            "qps_role": "outer_audit_only_not_inner_score",
            "state_mutation_contract": {
                "accepted_update": False,
                "gradient_update": False,
                "incumbent_update": False,
                "range_update": False,
            },
            "top5": selected,
            "source_batches": descriptors,
            "v39_build_contract": {
                "contract": BUILD_CONTRACT,
                "source_selection_path": str(source_selection_path.resolve()),
                "source_selection_sha256": v6._sha256(source_selection_path),
                "source_ledger_path": str(source_ledger_path.resolve()),
                "source_ledger_sha256": v6._sha256(source_ledger_path),
                "source_summary_path": str(source_summary_path.resolve()),
                "source_summary_sha256": v6._sha256(source_summary_path),
                "source_gate_path": str(source_gate_path.resolve()),
                "source_gate_sha256": v6._sha256(source_gate_path),
                "source_gate_only_failure": "actual_best_in_predicted_top3",
                "v38_empirical_best_rank": best_rank,
                "v38_empirical_best_score": float(best_observation["actual_score"]),
                "probe_discovered_plan_regime": discovered_plan,
                "probe_discovered_exact_point_key": v6._point_key(
                    v6._params(best_candidate), discovered_plan
                ),
                "probe_regime_calibration": copy.deepcopy(discovered_calibration),
                "probe_rows_used_for_gradient_incumbent_accepted": 0,
                "stability_acquisition_policy": (
                    "automatic_empirical_median_best_in_validated_probe_regime"
                ),
                "stability_anchor_exact_point_key": stability_anchor["exact_point_key"],
                "stability_fingerprint_novelty_policy": (
                    "recalibrated_full_plan_feature_resource_id_must_not_exist_in_any_"
                    "sha_pinned_history"
                ),
                "historical_fingerprint_count": len(historical_ids),
                "five_new_noncorner_fingerprints": True,
                "fingerprint_ids": ids,
                "roles": roles,
                "state_neutral": True,
                "physical_workload_calls_added": 0,
            },
        }
    )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--gate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    selection = args.selection.resolve()
    result = build(
        _load(selection),
        source_selection_path=selection,
        source_ledger_path=args.ledger.resolve(),
        source_summary_path=args.summary.resolve(),
        source_gate_path=args.gate.resolve(),
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
                "stability_anchor_rank": 1,
                "new_workload_calls": 0,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
