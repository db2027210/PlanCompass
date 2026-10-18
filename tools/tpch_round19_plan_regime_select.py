#!/usr/bin/env python3
"""Build a fail-closed TPC-H Top-5 using exact validated plan regimes."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple


RESOURCES: Tuple[str, ...] = ("cpu", "io", "memory")
CAPABILITY = {
    "cpu": 46.0,
    "io": 36.0,
    "memory": 1.6298145055770874,
}
TRUSTED_SLOTS = 3
PROBE_SLOTS = 2
LOG_TRANSFORM_PARAMS = {
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "cpu_tuple_cost",
    "parallel_tuple_cost",
    "random_page_cost",
    "seq_page_cost",
}
NON_CORNER_MARGIN = 0.05


def configure_capability(config: Mapping[str, Any]) -> None:
    raw = config.get("capability") or {}
    if set(raw) != set(RESOURCES):
        raise ValueError("config capability must contain cpu/io/memory exactly")
    configured = {
        name: finite(raw[name], f"capability.{name}") for name in RESOURCES
    }
    if any(value <= 0.0 for value in configured.values()):
        raise ValueError("all configured capabilities must be positive")
    CAPABILITY.clear()
    CAPABILITY.update(configured)


def load_json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def load_analysis(path: Path) -> Mapping[str, Any]:
    if path.suffix != ".jsonl":
        loaded = load_json(path)
        ranked = loaded.get("ranked_candidates") or []
        result = copy.deepcopy(dict(loaded))
    else:
        matches = [
            event
            for event in load_jsonl(path)
            if event.get("event") == "experience_table_all_subset_ranking"
        ]
        if len(matches) != 1:
            raise ValueError(
                f"expected one ranking event in {path}, found {len(matches)}"
            )
        event = matches[0]
        ranked = event.get("candidate_rankings") or []
        result = {
            "source_history": str(path),
            "calibration_binding": copy.deepcopy(
                event.get("calibration_binding")
            ),
        }
    if not isinstance(ranked, list) or not ranked:
        raise ValueError("ranking event has no candidates")
    normalized = []
    for raw in ranked:
        candidate = copy.deepcopy(dict(raw))
        if candidate.get("candidate_params") is None:
            candidate["candidate_params"] = copy.deepcopy(
                candidate.get("optimized_params")
            )
        if candidate.get("candidate_score") is None:
            candidate["candidate_score"] = candidate.get("final_score")
        if candidate.get("predicted_resource") is None:
            candidate["predicted_resource"] = copy.deepcopy(
                candidate.get("final_resource")
            )
        if candidate.get("feature_ratio") is None:
            metadata = candidate.get("final_estimation_metadata") or {}
            candidate["feature_ratio"] = copy.deepcopy(
                metadata.get("feature_ratios")
            )
        normalized.append(candidate)
    result["ranked_candidates"] = normalized
    return result


def load_jsonl(path: Path) -> List[Mapping[str, Any]]:
    records: List[Mapping[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, Mapping):
                raise ValueError(f"{path}:{line_number} must contain an object")
            records.append(value)
    return records


def finite(value: Any, label: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def canonical_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def fingerprint_id(candidate: Mapping[str, Any]) -> str:
    value = (candidate.get("three_level_fingerprint") or {}).get("id")
    if not isinstance(value, str) or not value.startswith("three-level-sha256:"):
        raise ValueError("candidate is missing a valid three-level fingerprint")
    return value


def plan_level(candidate: Mapping[str, Any]) -> str:
    value = (candidate.get("three_level_fingerprint") or {}).get("plan_level")
    if not isinstance(value, str) or not value.startswith("ratio-plan-sha256:"):
        raise ValueError("candidate is missing a validated ratio plan fingerprint")
    return value


def parameter_key(candidate: Mapping[str, Any]) -> str:
    params = candidate.get("candidate_params") or candidate.get("optimized_params")
    if not isinstance(params, Mapping) or not params:
        raise ValueError("candidate is missing parameter values")
    normalized = {str(name): finite(value, f"params.{name}") for name, value in params.items()}
    return canonical_sha256(normalized)


def structural_plan_regime_id(candidate: Mapping[str, Any]) -> str:
    metadata = candidate.get("final_estimation_metadata") or {}
    query_features = metadata.get("query_features") or []
    if not isinstance(query_features, list) or not query_features:
        raise ValueError("candidate is missing exact per-query plan structures")
    structures = []
    for row in query_features:
        if not isinstance(row, Mapping):
            raise ValueError("query feature row must be an object")
        queryid = str(row.get("queryid") or "")
        plan = str(row.get("plan_fingerprint") or "")
        if not queryid or not plan.startswith("exact-structure-sha256:"):
            raise ValueError("query feature lacks exact structural plan fingerprint")
        structures.append({"queryid": queryid, "plan_fingerprint": plan})
    structures.sort(key=lambda item: (item["queryid"], item["plan_fingerprint"]))
    if len({item["queryid"] for item in structures}) != len(structures):
        raise ValueError("candidate has duplicate query IDs in structural plan data")
    return "exact-workload-structure-sha256:" + canonical_sha256(
        {"query_structures": structures}
    )


def exact_plan_regime_id(candidate: Mapping[str, Any]) -> str:
    """Bind trust to both the ratio-level and exact per-query structures."""
    payload = {
        "ratio_plan_level": plan_level(candidate),
        "structural_plan_regime_id": structural_plan_regime_id(candidate),
    }
    return "exact-plan-regime-sha256:" + canonical_sha256(payload)


def band_score(utilization: float, weight: float, target: bool) -> float:
    target_min, target_max, hard = ((0.85, 0.90, 0.95) if target else (0.30, 0.50, 0.90))
    if target:
        if utilization < target_min:
            gap = (target_min - utilization) / target_min
            return weight * (1.0 - gap * gap)
        if utilization <= target_max:
            position = (utilization - target_min) / (target_max - target_min)
            return weight * (1.0 - 0.2 * position)
        span = max(hard - target_max, 0.05)
        gap = (utilization - target_max) / span
        hard_excess = max(0.0, utilization - hard) / span
        return weight * (0.8 - gap * gap - 4.0 * hard_excess)
    if utilization < target_min:
        gap = (target_min - utilization) / target_min
        return weight * 0.8 * (1.0 - gap * gap)
    if utilization <= target_max:
        position = (utilization - target_min) / (target_max - target_min)
        return weight * (0.8 + 0.2 * position)
    span = max(hard - target_max, 0.05)
    gap = (utilization - target_max) / span
    hard_excess = max(0.0, utilization - hard) / span
    return weight * (1.0 - gap * gap - 4.0 * hard_excess)


def score_resource(resource: Mapping[str, Any]) -> Tuple[float, Dict[str, float]]:
    utilization = {
        name: finite(resource[name], f"resource.{name}") / CAPABILITY[name]
        for name in RESOURCES
    }
    score = sum(
        band_score(utilization[name], weight, name == "memory")
        for name, weight in (("cpu", 1.0), ("io", 3.0), ("memory", 1.0))
    )
    return score, utilization


def validated_regimes(
    events: Iterable[Mapping[str, Any]],
    analysis: Mapping[str, Any],
) -> Tuple[Dict[str, Dict[str, Any]], set[str], set[str]]:
    raw_candidates = analysis.get("ranked_candidates") or []
    by_params: Dict[str, Mapping[str, Any]] = {}
    for raw in raw_candidates:
        key = parameter_key(raw)
        if key in by_params:
            raise ValueError(f"duplicate parameter vector in analysis: {key}")
        by_params[key] = raw

    evidence_by_regime: Dict[str, List[Dict[str, Any]]] = {}
    historical_ids: set[str] = set()
    historical_param_keys: set[str] = set()
    for source_order, event in enumerate(events):
        event_name = event.get("event")
        if event_name not in {
            "tpch_gradient_real_replay_complete",
            "tpch_formal_real_replay_complete",
            "tpch_diagnostic_real_replay_complete",
        }:
            continue
        if event.get("technical_failure") is not False:
            continue
        if event_name == "tpch_gradient_real_replay_complete":
            protocol = event.get("measurement_protocol") or {}
            if (
                protocol.get("correctness_passed") is not True
                or protocol.get("stable") is not True
            ):
                continue
        elif event.get("valid_replay") is not True:
            continue

        event_param_key = parameter_key(event)
        raw = by_params.get(event_param_key)
        if raw is None:
            raise ValueError(
                "validated replay parameter vector is absent from source analysis: "
                f"{event_param_key}"
            )
        regime = exact_plan_regime_id(raw)
        structural_regime = structural_plan_regime_id(raw)
        predicted = (
            raw.get("predicted_resource")
            or raw.get("final_resource")
            or {}
        )
        event_predicted = event.get("predicted_resource") or {}
        resource_formula_audit = (
            (event.get("three_level_fingerprint") or {}).get(
                "resource_formula_audit"
            )
            or {}
        )
        actual = event.get("actual_resource") or {}
        scalar = {}
        calibration_input = {}
        for name in RESOURCES:
            raw_predicted_value = predicted.get(name)
            if raw_predicted_value is None:
                raw_predicted_value = (
                    resource_formula_audit.get(name) or {}
                ).get("precalibration_resource")
            if raw_predicted_value is None:
                raw_predicted_value = event_predicted.get(name)
            predicted_value = finite(
                raw_predicted_value, f"{regime}.predicted.{name}"
            )
            actual_value = finite(actual[name], f"{regime}.actual.{name}")
            if predicted_value <= 0.0 or actual_value < 0.0:
                raise ValueError(
                    f"invalid resource calibration for {regime}.{name}"
                )
            scalar[name] = actual_value / predicted_value
            calibration_input[name] = predicted_value
        evidence_by_regime.setdefault(regime, []).append(
            {
                "event": event_name,
                "source_rank": int(event["rank"]),
                "source_fingerprint": fingerprint_id(event),
                "source_raw_fingerprint": fingerprint_id(raw),
                "source_parameter_key": event_param_key,
                "ratio_plan_level": plan_level(raw),
                "structural_plan_regime_id": structural_regime,
                "predicted_resource": {
                    name: finite(calibration_input[name], name)
                    for name in RESOURCES
                },
                "calibration_input_resource": calibration_input,
                "actual_resource": {
                    name: finite(actual[name], name) for name in RESOURCES
                },
                "scalar_by_resource": scalar,
                "actual_score": finite(
                    event["actual_score"], f"{regime}.actual_score"
                ),
                "evidence_source_order": source_order,
                "timestamp_epoch": (
                    finite(event["timestamp_epoch"], "timestamp_epoch")
                    if event.get("timestamp_epoch") is not None
                    else None
                ),
            }
        )
        historical_ids.add(fingerprint_id(event))
        historical_ids.add(fingerprint_id(raw))
        historical_param_keys.add(event_param_key)

    regimes: Dict[str, Dict[str, Any]] = {}
    for regime, evidence in evidence_by_regime.items():
        ordered_evidence = sorted(
            evidence,
            key=lambda item: (
                item["timestamp_epoch"] is not None,
                (
                    float(item["timestamp_epoch"])
                    if item["timestamp_epoch"] is not None
                    else float(item["evidence_source_order"])
                ),
                int(item["evidence_source_order"]),
            ),
        )
        latest = ordered_evidence[-1]
        median_scalar = {
            name: statistics.median(
                item["scalar_by_resource"][name]
                for item in ordered_evidence
            )
            for name in RESOURCES
        }
        scalar = {
            name: float(latest["scalar_by_resource"][name])
            for name in RESOURCES
        }
        if any(
            not math.isfinite(value) or not (0.0 <= value <= 1e12)
            for value in scalar.values()
        ):
            raise ValueError(f"invalid exact structural regime scalar: {regime}")
        regimes[regime] = {
            "exact_plan_regime_id": regime,
            "structural_plan_regime_ids": sorted(
                {
                    str(item["structural_plan_regime_id"])
                    for item in evidence
                }
            ),
            "evidence_count": len(evidence),
            "scalar_by_resource": scalar,
            "historical_median_scalar_by_resource": median_scalar,
            "latest_epoch_actual_score": float(latest["actual_score"]),
            "actual_score_median": float(latest["actual_score"]),
            "latest_epoch_evidence": copy.deepcopy(latest),
            "maximum_latest_to_historical_scalar_factor": max(
                max(
                    scalar[name] / max(median_scalar[name], 1e-15),
                    median_scalar[name] / max(scalar[name], 1e-15),
                )
                for name in RESOURCES
            ),
            "evidence": ordered_evidence,
            "evidence_contract": (
                "exact_workload_plan_regime_latest_measurement_epoch_v2"
            ),
        }
    return regimes, historical_ids, historical_param_keys


def recalibrated_fingerprint(
    candidate: Mapping[str, Any],
    predicted_resource: Mapping[str, float],
    calibration: Mapping[str, Any],
) -> Dict[str, Any]:
    old = candidate.get("three_level_fingerprint") or {}
    feature_level = old.get("feature_ratio_level")
    if not isinstance(feature_level, Mapping) or not feature_level:
        raise ValueError("trusted candidate is missing feature-ratio level")
    resource_level = {
        name: int(round(predicted_resource[name] / CAPABILITY[name] / 0.005))
        for name in RESOURCES
    }
    payload = {
        "plan": plan_level(candidate),
        "feature_ratio_q005": dict(feature_level),
        "resource_utilization_q005": resource_level,
    }
    audit = {}
    old_predicted = candidate.get("predicted_resource") or {}
    for name in RESOURCES:
        before = finite(old_predicted[name], f"precalibration.{name}")
        scalar = finite(calibration["scalar_by_resource"][name], f"scalar.{name}")
        expected = before * scalar
        observed = predicted_resource[name]
        error = abs(expected - observed)
        audit[name] = {
            "precalibration_resource": before,
            "exact_plan_regime_scalar": scalar,
            "expected_resource": expected,
            "observed_resource": observed,
            "absolute_error": error,
            "allowed_error": max(1e-12, 1e-12 * max(abs(expected), abs(observed))),
        }
        if error > audit[name]["allowed_error"]:
            raise ValueError(f"calibrated resource audit failed for {name}")
    return {
        "contract": "plan_feature_resource_three_level_v1",
        "id": "three-level-sha256:" + canonical_sha256(payload),
        "valid": True,
        "validation_errors": [],
        "plan_level": plan_level(candidate),
        "feature_ratio_level": dict(feature_level),
        "predicted_resource_level": resource_level,
        "parameter_fallback_used": False,
        "resource_formula_audit": audit,
        "post_prediction_calibration": "exact_validated_plan_regime_v1",
    }


def normalized_vector(candidate: Mapping[str, Any]) -> List[float]:
    params = candidate.get("candidate_params") or {}
    values = [
        math.log(max(finite(params["cpu_index_tuple_cost"], "cpu_index_tuple_cost"), 1e-12)),
        math.log(max(finite(params["cpu_operator_cost"], "cpu_operator_cost"), 1e-12)),
        math.log(max(finite(params["cpu_tuple_cost"], "cpu_tuple_cost"), 1e-12)),
        math.log1p(max(finite(params["parallel_setup_cost"], "parallel_setup_cost"), 0.0)),
        math.log(max(finite(params["parallel_tuple_cost"], "parallel_tuple_cost"), 1e-12)),
        math.log(max(finite(params["random_page_cost"], "random_page_cost"), 1e-12)),
        math.log(max(finite(params["seq_page_cost"], "seq_page_cost"), 1e-12)),
    ]
    feature = candidate.get("feature_ratio") or {}
    resource = candidate.get("predicted_resource") or {}
    values.extend(math.log(max(finite(feature[name], f"feature.{name}"), 1e-12)) for name in RESOURCES)
    values.extend(math.log(max(finite(resource[name], f"resource.{name}"), 1e-12)) for name in RESOURCES)
    return values


def distance(left: Mapping[str, Any], right: Mapping[str, Any]) -> float:
    left_vector = normalized_vector(left)
    right_vector = normalized_vector(right)
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(left_vector, right_vector)))


def is_non_corner(
    candidate: Mapping[str, Any],
    param_ranges: Mapping[str, Any],
) -> bool:
    if (
        candidate.get("boundary_plateau") is True
        or candidate.get("boundary_corner") is True
    ):
        return False
    provenance = " ".join(
        str(candidate.get(field) or "")
        for field in ("probe_kind", "candidate_origin", "source_provenance")
    ).lower()
    if "boundary_corner" in provenance or "parameter_boundary_corner" in provenance:
        return False
    params = candidate.get("candidate_params") or candidate.get("optimized_params")
    if not isinstance(params, Mapping):
        return False
    initial = candidate.get("initial_params") or {}
    changed_names = [
        name
        for name in param_ranges
        if name in initial
        and not math.isclose(
            finite(params[name], f"params.{name}"),
            finite(initial[name], f"initial_params.{name}"),
            rel_tol=1e-12,
            abs_tol=1e-15,
        )
    ]
    if not changed_names:
        return False
    for name in changed_names:
        bounds = param_ranges[name]
        lower = finite(bounds["min_value"], f"range.{name}.min")
        upper = finite(bounds["max_value"], f"range.{name}.max")
        value = finite(params[name], f"params.{name}")
        if not lower < upper:
            return False
        if name in LOG_TRANSFORM_PARAMS:
            if lower <= 0.0 or value <= 0.0:
                return False
            fraction = (math.log(value) - math.log(lower)) / (
                math.log(upper) - math.log(lower)
            )
        else:
            fraction = (value - lower) / (upper - lower)
        if not NON_CORNER_MARGIN <= fraction <= 1.0 - NON_CORNER_MARGIN:
            return False
    return True


def maximin_probes(
    pool: Sequence[Mapping[str, Any]],
    selected: Sequence[Mapping[str, Any]],
    count: int,
) -> List[Mapping[str, Any]]:
    remaining = list(pool)
    anchors = list(selected)
    chosen: List[Mapping[str, Any]] = []
    if not anchors and remaining:
        first = max(
            remaining,
            key=lambda item: (
                finite(item["candidate_score"], "candidate_score"),
                fingerprint_id(item),
            ),
        )
        chosen.append(first)
        anchors.append(first)
        remaining.remove(first)
    while remaining and len(chosen) < count:
        winner = max(
            remaining,
            key=lambda item: (
                min(distance(item, anchor) for anchor in anchors),
                fingerprint_id(item),
            ),
        )
        chosen.append(winner)
        anchors.append(winner)
        remaining.remove(winner)
    if len(chosen) != count:
        raise ValueError(f"only selected {len(chosen)} uncertain probes, need {count}")
    return chosen


def validate_formal_state(
    state: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    minimum_predicted_improvement: float,
) -> Dict[str, Any]:
    if (
        state.get("schema_version") != "tpch_exact_plan_regime_formal_state_v1"
        or state.get("status") != "complete"
        or state.get("baseline_restored") is not True
        or int(state.get("technical_failure_count", -1)) != 0
    ):
        raise ValueError("formal continuation state is not clean")
    accepted_count = int(state.get("accepted_count", -1))
    if accepted_count < 0 or accepted_count >= 10:
        raise ValueError("formal accepted_count must be in [0, 9]")
    incumbent_score = finite(state.get("incumbent_score"), "incumbent_score")
    ranges = state.get("range_state") or {}
    if not isinstance(ranges, Mapping) or not ranges:
        raise ValueError("formal state is missing range_state")
    for rank, candidate in enumerate(candidates, 1):
        params = candidate.get("candidate_params") or {}
        if set(params) != set(ranges):
            raise ValueError(f"rank {rank} parameter/range set mismatch")
        for name, bounds in ranges.items():
            value = finite(params[name], f"rank{rank}.{name}")
            minimum = finite(bounds["min_value"], f"range.{name}.min")
            maximum = finite(bounds["max_value"], f"range.{name}.max")
            if value < minimum - 1e-15 or value > maximum + 1e-15:
                raise ValueError(
                    f"rank {rank} parameter {name} is outside formal range"
                )
    trusted_scores = [
        finite(candidate["candidate_score"], "candidate_score")
        for candidate in candidates
        if candidate.get("selection_role") == "trusted"
    ]
    best_predicted = max(trusted_scores)
    required = incumbent_score + minimum_predicted_improvement
    if best_predicted <= required:
        raise ValueError(
            "formal Top-5 has no positive predicted improvement: "
            f"{best_predicted} <= {required}"
        )
    return {
        "accepted_count": accepted_count,
        "incumbent_score": incumbent_score,
        "best_trusted_predicted_score": best_predicted,
        "minimum_predicted_improvement": minimum_predicted_improvement,
        "expected_improvement": best_predicted - incumbent_score,
    }


def build_discovery_selection(
    analysis: Mapping[str, Any],
    regimes: Mapping[str, Mapping[str, Any]],
    *,
    historical_ids: set[str],
    historical_param_keys: set[str],
    param_ranges: Mapping[str, Any],
    ranking_sha256: str,
    events_sha256: str,
) -> Dict[str, Any]:
    by_structural_regime: Dict[str, List[Mapping[str, Any]]] = {}
    discovery_filter_audit = {
        "historical_fingerprint": 0,
        "historical_parameter_vector": 0,
        "corner": 0,
        "already_validated_composite_regime": 0,
        "eligible": 0,
    }
    for raw in analysis.get("ranked_candidates") or []:
        if fingerprint_id(raw) in historical_ids:
            discovery_filter_audit["historical_fingerprint"] += 1
            continue
        if parameter_key(raw) in historical_param_keys:
            discovery_filter_audit["historical_parameter_vector"] += 1
            continue
        if not is_non_corner(raw, param_ranges):
            discovery_filter_audit["corner"] += 1
            continue
        structural_regime = structural_plan_regime_id(raw)
        if exact_plan_regime_id(raw) in regimes:
            discovery_filter_audit["already_validated_composite_regime"] += 1
            continue
        discovery_filter_audit["eligible"] += 1
        by_structural_regime.setdefault(structural_regime, []).append(raw)
    representatives = [
        max(
            candidates,
            key=lambda item: (
                finite(item["candidate_score"], "candidate_score"),
                fingerprint_id(item),
            ),
        )
        for candidates in by_structural_regime.values()
    ]
    if len(representatives) < 5:
        raise ValueError(
            "insufficient novel non-corner unknown structural regimes: "
            f"{len(representatives)} < 5; audit="
            f"{json.dumps(discovery_filter_audit, sort_keys=True)}"
        )
    probes = maximin_probes(representatives, [], 5)
    selected: List[Dict[str, Any]] = []
    for rank, raw in enumerate(probes, 1):
        item = copy.deepcopy(raw)
        _, utilization = score_resource(item["predicted_resource"])
        item.update(
            {
                "predicted_rank": int(item.get("rank", rank)),
                "rank": rank,
                "acquisition_role": "uncertain_probe",
                "selection_role": "probe",
                "selection_basis": (
                    "unknown_exact_structural_regime_noncorner_maximin"
                ),
                "structural_plan_regime_id": structural_plan_regime_id(item),
                "exact_plan_regime_id": exact_plan_regime_id(item),
                "candidate_trust_confidence": 0.0,
                "candidate_trust_basis": "unknown_plan_regime_probe_only",
                "excluded_from_spearman": True,
                "eligible_for_best_candidate": False,
                "incumbent_update_eligible": False,
                "used_for_inner_incumbent_update": False,
                "diagnostic_only": True,
                "accepted_update_allowed": False,
                "incumbent_update_allowed": False,
                "gradient_update_allowed": False,
                "range_update_allowed": False,
                "qps_used_for_inner_scoring": False,
                "utilization": utilization,
                "boundary_corner": False,
                "actual_parameter_noncorner_audit": {
                    "reason": "actual_parameter_space_noncorner",
                    "margin_fraction": NON_CORNER_MARGIN,
                },
            }
        )
        selected.append(item)
    identifiers = [fingerprint_id(item) for item in selected]
    plans = [item["structural_plan_regime_id"] for item in selected]
    if (
        len(set(identifiers)) != 5
        or len(set(plans)) != 5
        or any(identifier in historical_ids for identifier in identifiers)
    ):
        raise ValueError("discovery Top-5 novelty or regime diversity failed")
    required_fields = (
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
    )
    for item in selected:
        missing = [field for field in required_fields if field not in item]
        if missing:
            raise ValueError(
                f"discovery candidate {item['rank']} missing fields: {missing}"
            )
    return {
        "selection_contract": (
            "tpch_q1_completion_risk_probe5_discovery_v1"
        ),
        "calibration_contract": (
            "unknown_structural_regime_probe_only_no_calibration"
        ),
        "trusted_slots": 0,
        "probe_slots": 5,
        "maximum_real_replay_invocations": 5,
        "serial_real_replay_required": True,
        "diagnostic_only": True,
        "formal_continuation": False,
        "accepted_update_allowed": False,
        "incumbent_update_allowed": False,
        "range_update_allowed": False,
        "gradient_update_allowed": False,
        "qps_used_for_inner_scoring": False,
        "technical_failures_count_toward_physical_cap": True,
        "technical_failures_count_as_valid_or_accepted": False,
        "unknown_plan_regime_trusted_allowed": False,
        "non_corner_margin": NON_CORNER_MARGIN,
        "discovery_filter_audit": discovery_filter_audit,
        "source_evidence": {
            "ranking_sha256": ranking_sha256,
            "events_sha256": events_sha256,
        },
        "validated_plan_regimes": copy.deepcopy(regimes),
        "historical_excluded_identifier_count": len(historical_ids),
        "historical_excluded_parameter_count": len(historical_param_keys),
        "novel_unique_fingerprint_count": 5,
        "novel_unique_structural_regime_count": 5,
        "top5": selected,
        "ranked_candidates": selected,
    }


def build_selection(
    analysis: Mapping[str, Any],
    regimes: Mapping[str, Mapping[str, Any]],
    *,
    historical_ids: set[str],
    historical_param_keys: set[str],
    ranking_sha256: str,
    events_sha256: str,
    param_ranges: Mapping[str, Any],
    formal_state: Mapping[str, Any] | None = None,
    minimum_predicted_improvement: float = 0.0,
) -> Dict[str, Any]:
    raw_candidates = analysis.get("ranked_candidates") or []
    if not isinstance(raw_candidates, list) or len(raw_candidates) < 5:
        raise ValueError("ranking analysis has fewer than five candidates")
    trusted_pool: List[Dict[str, Any]] = []
    unknown_pool: List[Mapping[str, Any]] = []
    for raw in raw_candidates:
        if not isinstance(raw, Mapping):
            raise ValueError("ranked candidate must be an object")
        if not is_non_corner(raw, param_ranges):
            continue
        if parameter_key(raw) in historical_param_keys:
            continue
        structural_regime = structural_plan_regime_id(raw)
        exact_regime = exact_plan_regime_id(raw)
        if exact_regime not in regimes:
            if fingerprint_id(raw) not in historical_ids:
                unknown_pool.append(raw)
            continue
        # A same-regime EXPLAIN-only neighbor can legitimately have the same
        # uncalibrated three-level fingerprint as its calibration anchor:
        # exact structure and cost-free features are intentionally identical.
        # Its post-regime-calibration fingerprint is recomputed below and must
        # still be novel.  Parameter equality remains strictly excluded above.
        calibration = regimes[exact_regime]
        candidate = copy.deepcopy(raw)
        original = candidate.get("predicted_resource") or {}
        corrected = {
            name: finite(
                original[name], f"{exact_regime}.predicted.{name}"
            )
            * finite(
                calibration["scalar_by_resource"][name],
                f"{exact_regime}.scalar.{name}",
            )
            for name in RESOURCES
        }
        corrected_score, utilization = score_resource(corrected)
        candidate.update({
            "precalibration_predicted_resource": copy.deepcopy(original),
            "predicted_resource": corrected,
            "final_resource": copy.deepcopy(corrected),
            "regime_adjusted_predicted_resource": copy.deepcopy(corrected),
            "candidate_score": corrected_score,
            "final_score": corrected_score,
            "regime_adjusted_score": corrected_score,
            "selection_score": finite(
                calibration["latest_epoch_actual_score"],
                f"{exact_regime}.latest_epoch_actual_score",
            ),
            "utilization": utilization,
            "plan_regime_id": exact_regime,
            "exact_plan_regime_id": exact_regime,
            "structural_plan_regime_id": structural_regime,
            "regime_calibration": copy.deepcopy(calibration),
            "candidate_trust_confidence": 1.0,
            "candidate_trust_basis": (
                "exact_validated_plan_regime_latest_measurement_epoch"
            ),
            "three_level_fingerprint": recalibrated_fingerprint(candidate, corrected, calibration),
        })
        trusted_pool.append(candidate)
    trusted_by_regime: Dict[str, Dict[str, Any]] = {}
    for candidate in trusted_pool:
        regime = str(candidate["exact_plan_regime_id"])
        incumbent = trusted_by_regime.get(regime)
        if incumbent is None or (
            finite(candidate["candidate_score"], "candidate_score"),
            fingerprint_id(candidate),
        ) > (
            finite(incumbent["candidate_score"], "candidate_score"),
            fingerprint_id(incumbent),
        ):
            trusted_by_regime[regime] = candidate
    if len(trusted_by_regime) < TRUSTED_SLOTS:
        raise ValueError(
            "insufficient distinct validated exact structural regimes: "
            f"{len(trusted_by_regime)} < {TRUSTED_SLOTS}"
        )
    trusted_representatives = list(trusted_by_regime.values())
    trusted_representatives.sort(
        key=lambda item: (
            -finite(item["candidate_score"], "candidate_score"),
            -finite(item["selection_score"], "selection_score"),
            fingerprint_id(item),
        )
    )
    trusted = trusted_representatives[:TRUSTED_SLOTS]
    probes = maximin_probes(unknown_pool, trusted, PROBE_SLOTS)
    selected: List[Dict[str, Any]] = []
    diagnostic_only = formal_state is None
    for replay_rank, raw in enumerate([*trusted, *probes], 1):
        item = copy.deepcopy(raw)
        trusted_role = replay_rank <= TRUSTED_SLOTS
        item["structural_plan_regime_id"] = structural_plan_regime_id(item)
        item["exact_plan_regime_id"] = exact_plan_regime_id(item)
        if not trusted_role:
            _, probe_utilization = score_resource(item["predicted_resource"])
            item["utilization"] = probe_utilization
        item.update({
            "predicted_rank": int(item.get("rank", replay_rank)),
            "rank": replay_rank,
            "acquisition_role": "trusted_validation" if trusted_role else "uncertain_probe",
            "selection_role": "trusted" if trusted_role else "probe",
            "selection_basis": (
                "exact_plan_regime_latest_measurement_epoch"
                if trusted_role
                else "unknown_plan_regime_parameter_resource_maximin"
            ),
            "candidate_trust_confidence": 1.0 if trusted_role else 0.0,
            "candidate_trust_basis": (
                "exact_validated_plan_regime_latest_measurement_epoch"
                if trusted_role
                else "unknown_plan_regime_probe_only"
            ),
            "excluded_from_spearman": not trusted_role,
            "eligible_for_best_candidate": trusted_role and not diagnostic_only,
            "incumbent_update_eligible": trusted_role and not diagnostic_only,
            "used_for_inner_incumbent_update": False,
            "diagnostic_only": diagnostic_only,
            "accepted_update_allowed": trusted_role and not diagnostic_only,
            "incumbent_update_allowed": trusted_role and not diagnostic_only,
            "gradient_update_allowed": False,
            "range_update_allowed": trusted_role and not diagnostic_only,
            "qps_used_for_inner_scoring": False,
            "boundary_corner": False,
            "boundary_plateau": False,
            "non_corner_verified": True,
        })
        selected.append(item)
    identifiers = [fingerprint_id(item) for item in selected]
    if len(set(identifiers)) != 5 or any(identifier in historical_ids for identifier in identifiers):
        raise ValueError("corrected Top-5 fingerprints are duplicated or not novel")
    required_fields = (
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
    )
    missing = {
        item["rank"]: [field for field in required_fields if field not in item]
        for item in selected
        if any(field not in item for field in required_fields)
    }
    if missing:
        raise ValueError(f"candidate audit fields missing: {missing}")
    formal_continuation = None
    if formal_state is not None:
        formal_continuation = validate_formal_state(
            formal_state,
            selected,
            minimum_predicted_improvement,
        )
    return {
        "selection_contract": (
            "exact_structural_plan_regime_trusted3_unknown_probe2_top5_v2"
        ),
        "calibration_contract": (
            "exact_workload_plan_regime_latest_measurement_epoch_v2"
        ),
        "trusted_slots": TRUSTED_SLOTS,
        "probe_slots": PROBE_SLOTS,
        "maximum_real_replay_invocations": 5,
        "serial_real_replay_required": True,
        "diagnostic_only": diagnostic_only,
        "accepted_update_allowed": not diagnostic_only,
        "incumbent_update_allowed": not diagnostic_only,
        "range_update_allowed": not diagnostic_only,
        "gradient_update_allowed": False,
        "qps_used_for_inner_scoring": False,
        "technical_failures_count_toward_physical_cap": True,
        "technical_failures_count_as_valid_or_accepted": False,
        "unknown_plan_regime_trusted_allowed": False,
        "source_evidence": {
            "ranking_sha256": ranking_sha256,
            "round18_events_sha256": events_sha256,
        },
        "validated_plan_regimes": copy.deepcopy(regimes),
        "historical_excluded_identifier_count": len(historical_ids),
        "historical_excluded_parameter_count": len(historical_param_keys),
        "novel_unique_fingerprint_count": 5,
        "novel_noncorner_fingerprint_count": 5,
        "distinct_trusted_structural_regime_count": len(
            {item["structural_plan_regime_id"] for item in trusted}
        ),
        "distinct_trusted_exact_plan_regime_count": len(
            {item["exact_plan_regime_id"] for item in trusted}
        ),
        "formal_expected_improvement_required": not diagnostic_only,
        "formal_continuation": (
            formal_continuation if formal_continuation is not None else False
        ),
        "top5": selected,
        "ranked_candidates": selected,
    }


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument(
        "--evidence-analysis",
        type=Path,
        help=(
            "analysis or history containing the original candidates for replay "
            "evidence; defaults to --analysis"
        ),
    )
    parser.add_argument(
        "--events",
        type=Path,
        action="append",
        required=True,
        help="validated diagnostic or formal replay event JSONL; repeatable",
    )
    parser.add_argument(
        "--mode",
        choices=("corrected", "discovery"),
        default="corrected",
    )
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="candidate-generation config and authoritative resource capability",
    )
    parser.add_argument(
        "--formal-state",
        type=Path,
        help="clean formal state; when supplied, produce a formal continuation",
    )
    parser.add_argument(
        "--minimum-predicted-improvement",
        type=float,
        default=0.0,
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = load_json(args.config)
    configure_capability(config)
    analysis = load_analysis(args.analysis)
    evidence_analysis_path = args.evidence_analysis or args.analysis
    evidence_analysis = load_analysis(evidence_analysis_path)
    events = [
        event
        for events_path in args.events
        for event in load_jsonl(events_path)
    ]
    regimes, historical_ids, historical_param_keys = validated_regimes(
        events, evidence_analysis
    )
    events_sha256 = canonical_sha256(
        {str(path): file_sha256(path) for path in args.events}
    )
    if args.mode == "discovery":
        if args.formal_state is not None:
            raise ValueError("discovery mode cannot produce a formal selection")
        param_ranges = config.get("param_ranges") or {}
        if not isinstance(param_ranges, Mapping) or not param_ranges:
            raise ValueError("discovery config is missing parameter ranges")
        selection = build_discovery_selection(
            analysis,
            regimes,
            historical_ids=historical_ids,
            historical_param_keys=historical_param_keys,
            param_ranges=param_ranges,
            ranking_sha256=file_sha256(args.analysis),
            events_sha256=events_sha256,
        )
    else:
        formal_state = (
            load_json(args.formal_state)
            if args.formal_state is not None
            else None
        )
        selection = build_selection(
            analysis,
            regimes,
            historical_ids=historical_ids,
            historical_param_keys=historical_param_keys,
            ranking_sha256=file_sha256(args.analysis),
            events_sha256=events_sha256,
            param_ranges=config.get("param_ranges") or {},
            formal_state=formal_state,
            minimum_predicted_improvement=args.minimum_predicted_improvement,
        )
    selection["source_evidence"]["evidence_analysis_sha256"] = file_sha256(
        evidence_analysis_path
    )
    args.output.write_text(
        json.dumps(selection, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "output": str(args.output),
        "mode": args.mode,
        "trusted_structural_regimes": [
            item["structural_plan_regime_id"]
            for item in selection["top5"]
            if item["selection_role"] == "trusted"
        ],
        "probe_structural_regimes": [
            item["structural_plan_regime_id"]
            for item in selection["top5"]
            if item["selection_role"] != "trusted"
        ],
        "fingerprints": [
            item["three_level_fingerprint"]["id"]
            for item in selection["top5"]
        ],
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
