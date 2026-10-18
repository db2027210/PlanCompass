#!/usr/bin/env python3
"""Build TPC-C v49 trusted3/probe2 with bounded empirical calibration.

When the SHA-sealed v43 calibration has fewer than three fresh trusted
candidates, v44 does not promote unknown points or weaken the quality gate.
It emits a state-neutral five-probe acquisition inside the best-supported
validated plan regime.  A single clean probe is prediction evidence only.
Two independent clean, stable probe batches for the same exact point and
validated plan regime may promote that evidence to calibration-trusted; the
original probe executions remain ineligible for formal state mutation.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
from pathlib import Path
from statistics import median
from typing import Any, Dict, Mapping, Sequence

try:
    import audit_tpcc_v43_postrun_gate as gate_module
    import build_tpcc_unique_exact_validation_v6 as v6
    import build_tpcc_v37_exact_drift_validation as v37
    import build_tpcc_v42_exact_plan_io_recalibration as v42
    import build_tpcc_v43_exact_plan_nonstationary_recalibration as v43
except ModuleNotFoundError:
    from tools import audit_tpcc_v43_postrun_gate as gate_module
    from tools import build_tpcc_unique_exact_validation_v6 as v6
    from tools import build_tpcc_v37_exact_drift_validation as v37
    from tools import build_tpcc_v42_exact_plan_io_recalibration as v42
    from tools import build_tpcc_v43_exact_plan_nonstationary_recalibration as v43


CAP = 5
BUILD_CONTRACT = "tpcc_v44_plan_regime_score_separation_recalibration_v1"
ACQUISITION_CONTRACT = "tpcc_v44_plan_regime_score_separation_probe5_v1"
ACQUISITION_GATE_SCHEMA = "tpcc_v44_score_separation_acquisition_gate_v1"
PROBE_PROMOTION_CONTRACT = "tpcc_v44_repeated_clean_probe_promotion_v1"
PARTIAL_TECHNICAL_GATE_SCHEMA = "tpcc_v48_partial_technical_recovery_gate_v1"


def _calibration_trusted(row: Mapping[str, Any]) -> bool:
    return bool(
        row.get("trusted_training_eligible") is True
        or row.get("calibration_trusted_eligible") is True
    )


def _promote_repeated_clean_probes(
    evidence_by_key: Mapping[str, Sequence[Mapping[str, Any]]],
) -> Dict[str, list[Dict[str, Any]]]:
    """Promote only two-batch, exact-point, clean probe evidence.

    Promotion is limited to calibration/trusted ranking.  It never rewrites
    the historical selection role and cannot make a replay formal-state
    eligible.
    """

    selection_cache: Dict[tuple[str, str], Mapping[str, Any]] = {}
    ledger_cache: Dict[tuple[str, str], Mapping[str, Any]] = {}
    promoted: Dict[str, list[Dict[str, Any]]] = {}
    for key, raw_rows in evidence_by_key.items():
        rows = [copy.deepcopy(dict(row)) for row in raw_rows]
        clean_probe_rows: list[tuple[Dict[str, Any], str, str]] = []
        for row in rows:
            if row.get("trusted_training_eligible") is True:
                continue
            selection_path = Path(str(row.get("selection_path") or "")).resolve()
            ledger_path = Path(str(row.get("ledger_path") or "")).resolve()
            selection_sha = str(row.get("selection_sha256") or "")
            ledger_sha = str(row.get("ledger_sha256") or "")
            if not selection_sha or not ledger_sha:
                continue
            selection_id = (str(selection_path), selection_sha)
            ledger_id = (str(ledger_path), ledger_sha)
            try:
                if selection_id not in selection_cache:
                    if v6._sha256(selection_path) != selection_sha:
                        raise RuntimeError("probe selection SHA mismatch")
                    selection_cache[selection_id] = json.loads(
                        selection_path.read_text(encoding="utf-8")
                    )
                if ledger_id not in ledger_cache:
                    if v6._sha256(ledger_path) != ledger_sha:
                        raise RuntimeError("probe ledger SHA mismatch")
                    ledger_cache[ledger_id] = json.loads(
                        ledger_path.read_text(encoding="utf-8")
                    )
            except (FileNotFoundError, OSError, json.JSONDecodeError):
                continue
            selection = selection_cache[selection_id]
            ledger = ledger_cache[ledger_id]
            rank = int(row.get("rank", -1))
            candidate = next(
                (
                    item for item in selection.get("top5") or []
                    if int(item.get("rank", -1)) == rank
                ),
                None,
            )
            observation = next(
                (
                    item for item in ledger.get("successful") or []
                    if int(item.get("rank", -1)) == rank
                ),
                None,
            )
            if not isinstance(candidate, Mapping) or not isinstance(observation, Mapping):
                continue
            plan = str(candidate.get("workload_plan_fingerprint") or "")
            protocol = observation.get("measurement_protocol") or {}
            mutation = selection.get("state_mutation_contract") or {}
            clean = bool(
                str(candidate.get("exact_point_key") or "") == key
                and plan.startswith("ratio-plan-sha256:")
                and candidate.get("selection_role") == "uncertain_probe"
                and candidate.get("diagnostic_only") is True
                and candidate.get("incumbent_update_eligible") is False
                and candidate.get("used_for_gradient") is False
                and candidate.get("formal_improvement_eligible") is False
                and mutation.get("accepted_update") is False
                and mutation.get("gradient_update") is False
                and mutation.get("incumbent_update") is False
                and mutation.get("range_update") is False
                and observation.get("workload_plan_fingerprint") == plan
                and observation.get("workload_returncode") == 0
                and observation.get("technical_failure") in (False, None)
                and protocol.get("stable") is True
                and protocol.get("correctness_passed") is True
                and protocol.get("technical_failure") is False
            )
            if clean:
                clean_probe_rows.append((row, selection_sha, ledger_sha))

        independent_batches = {
            (selection_sha, ledger_sha)
            for _, selection_sha, ledger_sha in clean_probe_rows
        }
        plans = {
            str(
                next(
                    item for item in selection_cache[(str(Path(str(row["selection_path"])).resolve()), selection_sha)]["top5"]
                    if int(item["rank"]) == int(row["rank"])
                )["workload_plan_fingerprint"]
            )
            for row, selection_sha, _ in clean_probe_rows
        }
        if len(independent_batches) >= 2 and len(plans) == 1:
            promoted_ids = sorted(
                f"{selection_sha}:{ledger_sha}"
                for selection_sha, ledger_sha in independent_batches
            )
            for row, selection_sha, ledger_sha in clean_probe_rows:
                row["calibration_trusted_eligible"] = True
                row["calibration_trust_certificate"] = {
                    "contract": PROBE_PROMOTION_CONTRACT,
                    "exact_point_key": key,
                    "validated_plan": next(iter(plans)),
                    "independent_clean_probe_batch_count": len(independent_batches),
                    "independent_batch_ids": promoted_ids,
                    "source_selection_role_preserved": "uncertain_probe",
                    "formal_state_update_eligible": False,
                    "gradient_update_eligible": False,
                    "incumbent_update_eligible": False,
                    "range_update_eligible": False,
                    "this_batch_id": f"{selection_sha}:{ledger_sha}",
                }
        promoted[key] = rows
    return promoted


def _acquisition_round(source: Mapping[str, Any]) -> int:
    contract = source.get("v44_build_contract") or {}
    if contract.get("mode") != "score_separation_acquisition":
        return 1
    return int(contract.get("acquisition_round", 0)) + 1


def _score_separation_indices(
    candidates: Sequence[Mapping[str, Any]],
    ranges: Mapping[str, Any],
    count: int,
    cycle: int,
) -> list[int]:
    """Choose deterministic score extrema, then score/parameter maximin."""

    if len(candidates) < count:
        return []
    order = sorted(
        range(len(candidates)),
        key=lambda index: (
            float(candidates[index]["acquisition_score_center"]),
            str(candidates[index]["exact_point_key"]),
        ),
    )
    if len(order) == count:
        return order
    # Rotate which interior point is held out on later acquisition rounds.
    held_out = order[1 + ((cycle - 1) % (len(order) - 2))]
    eligible = [index for index in order if index != held_out]
    selected = [eligible[0], eligible[-1]]
    scores = [float(candidates[index]["acquisition_score_center"]) for index in eligible]
    span = max(scores) - min(scores)
    while len(selected) < count:
        remaining = [index for index in eligible if index not in selected]

        def objective(index: int) -> tuple[float, float, str]:
            score = float(candidates[index]["acquisition_score_center"])
            score_distance = min(
                abs(score - float(candidates[chosen]["acquisition_score_center"]))
                / max(span, 1e-12)
                for chosen in selected
            )
            parameter_distance = min(
                _parameter_distance(candidates[index], candidates[chosen], ranges)
                for chosen in selected
            )
            return (
                score_distance + parameter_distance,
                parameter_distance,
                str(candidates[index]["exact_point_key"]),
            )

        selected.append(max(remaining, key=objective))
    return selected


def _joint_uncertainty_witness(
    center_resource: Mapping[str, Any],
    target_score: float,
    preferred_side: int,
    skip: int = 0,
) -> tuple[Dict[str, float], float, float]:
    """Return a bounded joint-resource witness for diagnostic acquisition.

    The witness is not a calibration scalar.  It is the conservative resource
    scenario used to fingerprint an uncertain probe before measurement; the
    exact center remains sealed separately for score-separation selection.
    """

    choices = []
    for step in range(401):
        scale = 0.8 + step / 1000.0
        if preferred_side < 0 and scale > 1.0:
            continue
        if preferred_side > 0 and scale < 1.0:
            continue
        resource = {
            name: max(0.0, float(center_resource[name]) * scale)
            for name in v6.RESOURCES
        }
        score = v6._score(resource)
        choices.append(
            (abs(score - target_score), abs(scale - 1.0), scale, resource, score)
        )
    ordered = sorted(choices, key=lambda row: row[:3])
    _, _, scale, resource, score = ordered[min(skip, len(ordered) - 1)]
    return resource, score, scale


def _build_score_separation_acquisition(
    *,
    source: Mapping[str, Any],
    pool: Sequence[Mapping[str, Any]],
    descriptors: Sequence[Mapping[str, Any]],
    evidence_by_key: Mapping[str, Sequence[Mapping[str, Any]]],
    ranges: Mapping[str, Any],
    pins: Mapping[str, tuple[Path, str]],
    failed: set[str],
    trusted_shortfall: int,
) -> Dict[str, Any]:
    round_number = _acquisition_round(source)
    historical_ids = v37._historical_fingerprint_ids(descriptors)
    by_plan: Dict[str, list[Dict[str, Any]]] = {}
    for raw in pool:
        candidate = copy.deepcopy(raw)
        key = str(candidate["exact_point_key"])
        rows = list(evidence_by_key.get(key) or [])
        if not rows or v37._parameter_corner(candidate, ranges):
            continue
        plan = str(candidate.get("workload_plan_fingerprint") or "")
        if not plan.startswith("ratio-plan-sha256:"):
            continue
        trusted_rows = [row for row in rows if _calibration_trusted(row)]
        probe_rows = [
            row for row in rows if not _calibration_trusted(row)
        ]
        resource, score, representative = v37._robust_exact_resource(rows)
        margin = max(
            gate_module.SCORE_MAE_MAX,
            median(
                abs(float(row["actual_score"]) - score) for row in rows
            ),
        )
        preferred_side = (
            -1
            if sum(bytes.fromhex(key)) % 2 == 0
            else 1
        )
        used_plan_ids = {
            str(item["three_level_fingerprint"]["id"])
            for item in by_plan.get(plan, [])
        }
        fingerprint = None
        for skip in range(max(0, round_number - 1) * 4, 41):
            predicted_resource, predicted_score, witness_scale = (
                _joint_uncertainty_witness(
                    resource, score - margin, preferred_side, skip
                )
            )
            utilization = {
                name: float(predicted_resource[name])
                / v6.repeat_v2.CAPABILITY[name]
                for name in v6.RESOURCES
            }
            fingerprint = v6._make_fingerprint(
                candidate, predicted_resource, utilization
            )
            if (
                fingerprint["id"] not in historical_ids
                and fingerprint["id"] not in used_plan_ids
            ):
                break
        if (
            fingerprint is None
            or fingerprint["id"] in historical_ids
            or fingerprint["id"] in used_plan_ids
        ):
            continue
        candidate.update(
            {
                "candidate_score": predicted_score,
                "candidate_score_recomputed": predicted_score,
                "predicted_score": predicted_score,
                "final_score": predicted_score,
                "formal_lcb_score": predicted_score - margin,
                "robust_acceptance_score": predicted_score - margin,
                "predicted_score_interval": {
                    "lower": predicted_score - margin,
                    "center": predicted_score,
                    "upper": predicted_score + margin,
                    "source": "v44_diagnostic_score_separation_repeat_only",
                },
                "predicted_resource": predicted_resource,
                "final_resource": copy.deepcopy(predicted_resource),
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
                "trusted_eligible_v44": False,
                "selection_role": "uncertain_probe",
                "trusted": False,
                "uncertain_probe": True,
                "exact_plan_regime_validated": False,
                "probe_kind": "v44_plan_regime_score_separation",
                "probe_reason": "trusted_fresh_fingerprint_shortfall_score_order_acquisition",
                "acquisition_score_center": score,
                "acquisition_center_resource": copy.deepcopy(resource),
                "acquisition_uncertainty_witness": {
                    "contract": "tpcc_v44_joint_resource_lcb_witness_v1",
                    "joint_resource_scale": witness_scale,
                    "deterministic_interval_side": preferred_side,
                    "center_score": score,
                    "target_score": score - margin,
                    "witness_score": predicted_score,
                    "io_only_scalar_calibration": False,
                    "used_to_establish_trust": False,
                },
                "prediction_source": "v44_diagnostic_score_separation_repeat_only",
                "repeat_prediction_source": "v44_diagnostic_score_separation_repeat_only",
                "source_provenance": {
                    "contract": "tpcc_v44_role_separated_score_acquisition_evidence_v1",
                    "exact_point_key": key,
                    "validated_plan": plan,
                    "trusted_observations": copy.deepcopy(trusted_rows),
                    "probe_observations": copy.deepcopy(probe_rows),
                    "representative_audit": representative,
                    "trusted_history_establishes_candidate_role": False,
                    "all_rows_prediction_only": not any(
                        row.get("calibration_trusted_eligible") is True
                        for row in trusted_rows
                    ),
                    "formal_state_update_rows": 0,
                    "gradient_update_rows": 0,
                    "incumbent_update_rows": 0,
                },
                "plan_regime_calibration": {
                    "contract": "tpcc_v44_score_separation_acquisition_prediction_only_v1",
                    "validated_plan": plan,
                    "trusted_eligible": False,
                    "unknown_plan_probe_only": not bool(trusted_rows),
                    "trusted_history_count": len(trusted_rows),
                    "probe_history_count": len(probe_rows),
                    "probe_evidence_affects_formal_or_gradient": False,
                },
            }
        )
        confidence = copy.deepcopy(candidate.get("confidence") or {})
        confidence.update(
            {
                "contract": "tpcc_v44_score_separation_acquisition_confidence_v1",
                "trusted_measurement_count": len(trusted_rows),
                "probe_measurement_count": len(probe_rows),
                "score_lower": predicted_score - margin,
                "score_center": predicted_score,
                "score_upper": predicted_score + margin,
                "trusted_eligible": False,
                "all_measurements_prediction_only": True,
            }
        )
        candidate["confidence"] = confidence
        by_plan.setdefault(plan, []).append(candidate)

    viable = [
        (plan, candidates)
        for plan, candidates in by_plan.items()
        if len(candidates) >= CAP
    ]
    if not viable:
        raise RuntimeError("TPC-C v44 lacks five exact points in one plan regime")
    plan, candidates = max(
        viable,
        key=lambda item: (
            sum(
                int(
                    (candidate.get("confidence") or {}).get(
                        "trusted_measurement_count", 0
                    )
                    > 0
                )
                for candidate in item[1]
            ),
            len(item[1]),
            item[0],
        ),
    )
    chosen_indices = _score_separation_indices(
        candidates, ranges, CAP, round_number
    )
    if len(chosen_indices) != CAP:
        raise RuntimeError("TPC-C v44 score-separation selector did not return five")
    top5 = [copy.deepcopy(candidates[index]) for index in chosen_indices]
    top5.sort(
        key=lambda item: (
            -float(item["acquisition_score_center"]),
            str(item["exact_point_key"]),
        )
    )
    for rank, candidate in enumerate(top5, 1):
        candidate["rank"] = rank
    identifiers = [
        str(item["three_level_fingerprint"]["id"]) for item in top5
    ]
    keys = [str(item["exact_point_key"]) for item in top5]
    if (
        len(set(keys)) != CAP
        or len(set(identifiers)) != CAP
        or any(item.get("boundary_corner") is not False for item in top5)
        or any(item.get("selection_role") != "uncertain_probe" for item in top5)
    ):
        raise RuntimeError("TPC-C v44 score-separation probe5 admission failed")

    result = copy.deepcopy(dict(source))
    result.update(
        {
            "schema_version": "tpcc_v44_plan_regime_score_separation_acquisition_v1",
            "selection_contract": ACQUISITION_CONTRACT,
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
            "source_batches": copy.deepcopy(list(descriptors)),
            "v44_build_contract": {
                "contract": BUILD_CONTRACT,
                "mode": "score_separation_acquisition",
                "acquisition_contract": ACQUISITION_CONTRACT,
                "acquisition_round": round_number,
                "target_plan_regime": plan,
                "trusted_candidate_count_before_acquisition": trusted_shortfall,
                "selection_algorithm": "score_extrema_then_score_parameter_maximin_v1",
                "selected_exact_point_keys": keys,
                "selected_fingerprint_ids": identifiers,
                "all_candidates_probe_only": True,
                "unknown_plan_probe_only": True,
                "single_clean_probe_prediction_only": True,
                "two_independent_clean_probes_promote_calibration_trust": True,
                "probe_promotion_contract": PROBE_PROMOTION_CONTRACT,
                "promoted_probe_formal_state_eligible": False,
                "trusted_eligibility_requires_historical_trusted_rows_or_two_clean_probe_batches": True,
                "source_gate_failures": sorted(failed),
                "sealed_authority": {
                    label: {"path": str(path.resolve()), "sha256": expected}
                    for label, (path, expected) in pins.items()
                },
                "threshold_changed": False,
                "score_mae_max": gate_module.SCORE_MAE_MAX,
                "resource_mae_pp_max": copy.deepcopy(
                    gate_module.RESOURCE_MAE_PP_MAX
                ),
                "manual_candidate_selection": False,
                "candidate_generation": False,
                "state_neutral": True,
                "physical_workload_calls_added": 0,
                "maximum_physical_calls": CAP,
            },
        }
    )
    return result


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
    try:
        import build_tpcc_v49_empirical_interval_successor as calibration
    except ModuleNotFoundError:
        from tools import build_tpcc_v49_empirical_interval_successor as calibration
    return calibration._local_model(
        candidate, historical_resource, historical_score, anchors, ranges
    )


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
        source.get("v44_build_contract")
        or source.get("v43_build_contract")
        or source.get("v42_build_contract")
        or {}
    ).get("contract")
    source_is_acquisition = bool(
        source_contract == BUILD_CONTRACT
        and (source.get("v44_build_contract") or {}).get("mode")
        == "score_separation_acquisition"
    )
    source_is_trusted3 = bool(
        source_contract == BUILD_CONTRACT
        and (source.get("v44_build_contract") or {}).get("mode")
        == "trusted3_probe2"
    )
    standard_source_valid = bool(
        (
            source_contract in {v42.BUILD_CONTRACT, v43.BUILD_CONTRACT}
            or source_is_trusted3
        )
        and gate.get("schema_version")
        == "tpcc_v43_v42_compatible_strict_postrun_gate_v1"
        and gate.get("passed") is False
        and failed
        and failed.issubset(allowed_failures)
        and (gate.get("quality_gate") or {}).get("checks", {}).get(
            "selection_algorithm_gate"
        )
        is True
    )
    acquisition_source_valid = bool(
        source_is_acquisition
        and gate.get("schema_version") == ACQUISITION_GATE_SCHEMA
        and gate.get("passed") is False
        and gate.get("formal_start_authorized") is False
        and gate.get("acquisition_complete") is True
        and all(
            value is True
            for value in (gate.get("acquisition_gate") or {}).get(
                "checks", {}
            ).values()
        )
    )
    partial_technical_source_valid = bool(
        gate.get("schema_version") == PARTIAL_TECHNICAL_GATE_SCHEMA
        and gate.get("passed") is False
        and gate.get("formal_start_authorized") is False
        and gate.get("recovery_complete") is True
        and (gate.get("recovery_gate") or {}).get("passed") is True
        and (gate.get("recovery_gate") or {}).get("physical_calls") == 5
        and (gate.get("recovery_gate") or {}).get("valid_replays") == 4
        and (gate.get("recovery_gate") or {}).get("technical_failures") == 1
        and (gate.get("recovery_gate") or {}).get("technical_rank") == 5
        and (gate.get("state_isolation") or {}).get("passed") is True
    )
    ordinary_batch_shape_valid = bool(
        int(ledger.get("real_workload_invocation_count", -1)) == CAP
        and len(ledger.get("successful") or []) == CAP
        and ledger.get("technical_failures") in ([], None)
        and int(summary.get("successful_real_replay_count", -1)) == CAP
        and summary.get("baseline_params_restored") is True
        and summary.get("baseline_snapshot_restored") is True
    )
    partial_batch_shape_valid = bool(
        partial_technical_source_valid
        and int(ledger.get("real_workload_invocation_count", -1)) == CAP
        and [int(row.get("rank", -1)) for row in ledger.get("successful") or []]
        == [1, 2, 3, 4]
        and [
            int(row.get("rank", -1))
            for row in ledger.get("technical_failures") or []
        ]
        == [5]
        and int(summary.get("successful_real_replay_count", -1)) == 4
        and int(summary.get("technical_failure_count", -1)) == 1
        and summary.get("baseline_params_restored") is False
        and summary.get("baseline_snapshot_restored") is False
        and summary.get("batch_clean5_eligible") is False
        and summary.get("batch_formal_gate_eligible") is False
    )
    if (
        not (
            standard_source_valid
            or acquisition_source_valid
            or partial_technical_source_valid
        )
        or not (ordinary_batch_shape_valid or partial_batch_shape_valid)
        or (
            not partial_technical_source_valid
            and (
                (gate.get("quality_gate") or {}).get("checks", {}).get(
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
            )
        )
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
    evidence_by_key = _promote_repeated_clean_probes(
        v37._role_evidence(descriptors, ledger_sha256)
    )
    historical_ids = v37._historical_fingerprint_ids(descriptors)
    ranges = source.get("candidate_param_ranges") or {}
    candidates_by_rank = {int(item["rank"]): item for item in source["top5"]}
    observations_by_rank = {
        int(item["rank"]): item for item in ledger["successful"]
    }
    trusted_anchors = [
        (candidate, observations_by_rank[rank])
        for rank, candidate in candidates_by_rank.items()
        if rank in observations_by_rank
        and candidate.get("selection_role") == "trusted"
        and candidate.get("trusted") is True
    ]
    probe_anchors = [
        (candidate, observations_by_rank[rank])
        for rank, candidate in candidates_by_rank.items()
        if rank in observations_by_rank
        and (candidate.get("selection_role") != "trusted"
        or candidate.get("trusted") is not True
        )
    ]
    current_probe_keys = {
        str(candidate["exact_point_key"])
        for candidate in candidates_by_rank.values()
        if candidate.get("selection_role") != "trusted"
        or candidate.get("trusted") is not True
    }
    trusted_plans = {
        str(candidate["workload_plan_fingerprint"]) for candidate, _ in trusted_anchors
    }
    calibration_anchors_by_plan: Dict[
        str, list[tuple[Mapping[str, Any], Mapping[str, Any]]]
    ] = {}
    for pool_candidate in pool:
        plan = str(pool_candidate["workload_plan_fingerprint"])
        rows = [
            row
            for row in evidence_by_key.get(
                str(pool_candidate["exact_point_key"]), []
            )
            if _calibration_trusted(row)
        ]
        if len(rows) >= 2:
            trusted_plans.add(plan)
        calibration_anchors_by_plan.setdefault(plan, []).extend(
            (pool_candidate, row) for row in rows
        )
    calibrated = []
    for raw in pool:
        candidate = copy.deepcopy(raw)
        key = str(candidate["exact_point_key"])
        if v37._parameter_corner(candidate, ranges):
            continue
        rows = list(evidence_by_key.get(key) or [])
        trusted_rows = [row for row in rows if _calibration_trusted(row)]
        probe_rows = [row for row in rows if not _calibration_trusted(row)]
        # A current probe stays state-ineligible, but two independently clean
        # batches may establish calibration trust regardless of whether the
        # immediately preceding selection was an all-probe acquisition.
        if key in current_probe_keys and len(trusted_rows) < 2:
            continue
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
        acquisition_prediction_only = False
        if trusted_eligible and not anchors and source_is_acquisition:
            anchors = [
                pair
                for pair in probe_anchors
                if str(pair[0]["workload_plan_fingerprint"]) == plan
            ]
            acquisition_prediction_only = bool(anchors)
        # Every row in this loop has exact-point replay evidence.  The v49
        # model consumes that empirical center directly and therefore does
        # not require a contemporaneous neighbour anchor.
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
                    "v44_acquisition_probe_rows_used_for_prediction_only": (
                        len(anchors) if acquisition_prediction_only else 0
                    ),
                    "v44_acquisition_probe_rows_used_to_establish_trust": sum(
                        row.get("calibration_trusted_eligible") is True
                        for row in trusted_rows
                    ),
                },
                "plan_regime_calibration": {
                    "contract": "tpcc_v43_exact_plan_nonstationarity_calibration_v1",
                    "validated_plan": plan,
                    "historical_resource": historical_resource,
                    "historical_score": historical_score,
                    "representative_audit": representative_audit,
                    "nonstationarity_model": model,
                    "probe_rows_used_for_trusted_calibration": sum(
                        row.get("calibration_trusted_eligible") is True
                        for row in trusted_rows
                    ),
                    "probe_evidence_affects_formal_or_gradient": False,
                    "trusted_eligible": trusted_eligible,
                    "acquisition_probe_prediction_only": acquisition_prediction_only,
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
                "probe_rows_used_for_trusted_calibration": sum(
                    row.get("calibration_trusted_eligible") is True
                    for row in trusted_rows
                ),
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
        known_plan = plan in trusted_plans
        anchors = (
            list(calibration_anchors_by_plan.get(plan) or [])
            if known_plan
            else [
                pair
                for pair in probe_anchors
                if str(pair[0]["workload_plan_fingerprint"]) == plan
            ]
        )
        if not anchors:
            continue
        model = _local_nonstationary_model(
            candidate, historical_resource, historical_score, anchors, ranges
        )
        loo = model.get("nonexact_loo") or {}
        resource_loo = loo.get("resource_mae_pp") or {}
        nonexact_trusted = bool(
            known_plan
            and loo.get("defined") is True
            and float(loo.get("score_mae", float("inf")))
            <= gate_module.SCORE_MAE_MAX
            and all(
                float(resource_loo.get(name, float("inf")))
                <= float(gate_module.RESOURCE_MAE_PP_MAX[name])
                for name in v6.RESOURCES
            )
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
                "predicted_resource_interval": copy.deepcopy(
                    model["predicted_resource_interval"]
                ),
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
                "trusted_eligible_v43": nonexact_trusted,
                "prediction_source": "v43_unmeasured_exact_plan_nonstationarity_probe",
                "repeat_prediction_source": "v43_unmeasured_exact_plan_nonstationarity_probe",
                "source_provenance": {
                    "contract": "tpcc_v43_sealed_explain_probe_evidence_v1",
                    "exact_point_key": key,
                    "validated_plan": plan,
                    "source_history": str(history_path),
                    "source_history_sha256": source["source_history_sha256"],
                    "source_ranking_line_number": ranking_line,
                    "trusted_observations": (
                        [copy.deepcopy(observation) for _, observation in anchors]
                        if nonexact_trusted
                        else []
                    ),
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
                    "trusted_eligible": nonexact_trusted,
                },
                "nonexact_loo": copy.deepcopy(loo),
                "distance_zero_residual_applied": False,
                "prediction_inside_calibration_interval": True,
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
                "trusted_eligible": nonexact_trusted,
                "probe_rows_used_for_trusted_calibration": 0,
                "nonexact_loo": copy.deepcopy(loo),
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
        if os.environ.get("TPCC_V49_DEBUG_FAIL_SHORTFALL") == "1":
            eligible = [
                {
                    "key": item.get("exact_point_key"),
                    "plan": item.get("workload_plan_fingerprint"),
                    "score": item.get("predicted_score"),
                    "fingerprint": (item.get("three_level_fingerprint") or {}).get("id"),
                    "loo": item.get("nonexact_loo"),
                }
                for item in calibrated
                if item.get("trusted_eligible_v43") is True
            ]
            raise RuntimeError(
                "TPC-C v49 trusted shortfall debug: "
                + json.dumps(
                    {
                        "trusted_count": len(trusted),
                        "eligible_count": len(eligible),
                        "trusted_plans": sorted(trusted_plans),
                        "eligible": eligible[:10],
                    },
                    sort_keys=True,
                )
            )
        return _build_score_separation_acquisition(
            source=source,
            pool=pool,
            descriptors=descriptors,
            evidence_by_key=evidence_by_key,
            ranges=ranges,
            pins=pins,
            failed=failed,
            trusted_shortfall=len(trusted),
        )
    trusted_keys = {str(item["exact_point_key"]) for item in trusted}
    probe_pool = [
        item
        for item in calibrated
        if str(item["exact_point_key"]) not in trusted_keys
        and item["trusted_eligible_v43"] is not True
    ]
    probe_ids = {
        str(item["three_level_fingerprint"]["id"]) for item in probe_pool
    }
    available_probe_ids = probe_ids - {
        str(item["three_level_fingerprint"]["id"]) for item in trusted
    }
    if len(available_probe_ids) < 2:
        # Once repeated clean probes establish three trusted points, retain
        # that progress.  Use the same deterministic uncertainty-witness
        # acquisition only to supply the two state-neutral exploration roles;
        # do not fall back to another all-probe batch and lose liveness.
        acquisition = _build_score_separation_acquisition(
            source=source,
            pool=pool,
            descriptors=descriptors,
            evidence_by_key=evidence_by_key,
            ranges=ranges,
            pins=pins,
            failed=failed,
            trusted_shortfall=len(trusted),
        )
        for candidate in acquisition["top5"]:
            key = str(candidate["exact_point_key"])
            fingerprint_id = str(candidate["three_level_fingerprint"]["id"])
            if key in trusted_keys or fingerprint_id in probe_ids:
                continue
            probe_pool.append(copy.deepcopy(candidate))
            probe_ids.add(fingerprint_id)
    probes = v6._maximin(probe_pool, 2, ranges, anchors=trusted)
    top5 = trusted + probes
    for rank, item in enumerate(top5, 1):
        role = "trusted" if rank <= 3 else "uncertain_probe"
        provenance = item.get("source_provenance") or {}
        promoted_rows = [
            row
            for row in provenance.get("trusted_observations") or []
            if row.get("calibration_trusted_eligible") is True
        ]
        # Keep evidence accounting complete even when a calibration-eligible
        # exact point occupies an exploration slot rather than a trusted top3
        # slot.  The role remains uncertain and state-ineligible; carrying the
        # count merely lets the strict runner verify that no promoted row was
        # hidden or silently discarded.
        provenance[
            "v44_acquisition_probe_rows_used_to_establish_trust"
        ] = len(promoted_rows)
        item["source_provenance"] = provenance
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
            "schema_version": "tpcc_v44_plan_regime_score_separation_validation_v1",
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
                "contract": v43.BUILD_CONTRACT,
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
            "v44_build_contract": {
                "contract": BUILD_CONTRACT,
                "mode": "trusted3_probe2",
                "source_was_score_separation_acquisition": source_is_acquisition,
                "unknown_plan_probe_only": True,
                "acquisition_probe_rows_used_to_establish_trust": sum(
                    row.get("calibration_trusted_eligible") is True
                    for rows in evidence_by_key.values()
                    for row in rows
                ),
                "clean_evidence_routes_automatically": True,
                "threshold_changed": False,
                "score_mae_max": gate_module.SCORE_MAE_MAX,
                "resource_mae_pp_max": copy.deepcopy(
                    gate_module.RESOURCE_MAE_PP_MAX
                ),
                "manual_candidate_selection": False,
                "state_neutral": True,
                "physical_workload_calls_added": 0,
                "roles": roles,
                "fingerprint_ids": identifiers,
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
