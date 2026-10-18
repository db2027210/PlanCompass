#!/usr/bin/env python3
"""Generate a fail-closed JOB successor Top-5 with EXPLAIN-only expansion.

The five real diagnostic observations are calibration anchors.  A generated
neighbor is trusted only when its exact workload plan fingerprint matches the
anchor that supplied its real CPU/IO/memory calibration.  A new plan
fingerprint is retained only as an uncertain probe.  This command performs no
workload replay and never writes formal optimizer state.
"""

from __future__ import annotations

import argparse
import copy
import difflib
import hashlib
import itertools
import json
import math
from pathlib import Path
import re
import statistics
import subprocess
import sys
from typing import Any, Iterable, Mapping


RESOURCES = ("cpu", "io", "memory")
RESOURCE_WEIGHTS = {"cpu": 1.0, "io": 3.0, "memory": 1.0}
CURRENT_ROUND_GRADIENT_CONTRACT = "job_current_round_directional_secant_v1"
ZERO_GRADIENT_EPSILON = 1e-12


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number} is not a JSON object")
        values.append(value)
    return values


def incumbent_query_latency(
    point_result_path: Path,
) -> tuple[dict[str, float], dict[str, str], dict[str, Any]]:
    """Load mean per-query latency from the accepted incumbent replay."""
    point = load_json(point_result_path)
    measurement = point.get("measurement") or {}
    execution = point.get("measurement_execution") or {}
    if (
        point.get("status") != "complete"
        or measurement.get("correctness_passed") is not True
        or int(measurement.get("exit_code", -1)) != 0
    ):
        raise ValueError("incumbent point result is not a clean replay")
    query_log_path = Path(str(execution.get("query_log_path") or "")).resolve()
    expected_sha = str(execution.get("query_log_sha256") or "")
    if (
        not query_log_path.is_file()
        or not expected_sha
        or file_sha256(query_log_path) != expected_sha
    ):
        raise ValueError("incumbent query log is missing or SHA-mismatched")
    grouped: dict[str, list[float]] = {}
    for row in load_jsonl(query_log_path):
        if row.get("event") != "query_complete" or row.get("success") is not True:
            continue
        query_id = str(row.get("query_id") or "")
        latency_ms = float(row.get("latency_ms") or 0.0)
        if not query_id or not math.isfinite(latency_ms) or latency_ms <= 0.0:
            raise ValueError("incumbent query log has invalid latency evidence")
        grouped.setdefault(query_id, []).append(latency_ms)
    if not grouped:
        raise ValueError("incumbent query log has no successful latency evidence")
    means = {
        query_id: statistics.fmean(values)
        for query_id, values in grouped.items()
    }
    configured_ids = [str(value) for value in execution.get("configured_query_ids") or []]
    configured_files = [
        Path(str(value)).resolve()
        for value in execution.get("configured_query_files") or []
    ]
    if (
        len(configured_ids) != len(configured_files)
        or set(configured_ids) != set(means)
        or any(not path.is_file() for path in configured_files)
    ):
        raise ValueError("incumbent configured SQL/query-log coverage mismatch")
    source_sql = {
        query_id: path.read_text(encoding="utf-8")
        for query_id, path in zip(configured_ids, configured_files)
    }
    return means, source_sql, {
        "point_result_path": str(point_result_path.resolve()),
        "point_result_sha256": file_sha256(point_result_path),
        "query_log_path": str(query_log_path),
        "query_log_sha256": expected_sha,
        "query_ids": sorted(means),
        "configured_query_files": {
            query_id: str(path)
            for query_id, path in zip(configured_ids, configured_files)
        },
        "latency_statistic": "arithmetic_mean_of_successful_query_complete_ms",
        "qps_used": False,
    }


def normalized_sql_shape(sql: str) -> str:
    """Normalize literals while preserving relational SQL structure."""
    value = str(sql)
    value = re.sub(r"/\*.*?\*/", " ", value, flags=re.S)
    value = re.sub(r"--[^\n]*", " ", value)
    value = re.sub(r"'(?:''|[^'])*'", " ? ", value)
    value = re.sub(r"\$\d+", " ? ", value)
    value = re.sub(
        r"(?<![A-Za-z_])[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?![A-Za-z_])",
        " ? ",
        value,
    )
    value = re.sub(r"\s+", " ", value.lower()).strip(" ;")
    return value


def bind_latency_by_sql_shape(
    queries: list[dict[str, Any]],
    named_latency: Mapping[str, float],
    source_sql: Mapping[str, str],
) -> list[dict[str, Any]]:
    """Create a fail-closed bijection from pg_stat queryid to JOB query name."""
    query_shapes = [normalized_sql_shape(str(item["query"])) for item in queries]
    source_ids = sorted(source_sql)
    source_shapes = {
        query_id: normalized_sql_shape(source_sql[query_id])
        for query_id in source_ids
    }
    similarities = [
        {
            query_id: difflib.SequenceMatcher(
                None, query_shape, source_shapes[query_id], autojunk=False
            ).ratio()
            for query_id in source_ids
        }
        for query_shape in query_shapes
    ]
    best_assignment = max(
        itertools.permutations(source_ids),
        key=lambda assignment: sum(
            similarities[index][query_id]
            for index, query_id in enumerate(assignment)
        ),
    )
    audit: list[dict[str, Any]] = []
    for index, query_id in enumerate(best_assignment):
        score = similarities[index][query_id]
        alternatives = sorted(
            (
                value
                for candidate_id, value in similarities[index].items()
                if candidate_id != query_id
            ),
            reverse=True,
        )
        gap = score - (alternatives[0] if alternatives else 0.0)
        if score < 0.70 or gap < 0.05:
            raise ValueError(
                "incumbent SQL-shape mapping is ambiguous: "
                f"queryid={queries[index]['queryid']} job_id={query_id} "
                f"score={score:.6f} gap={gap:.6f}"
            )
        queries[index]["mean_latency_ms"] = float(named_latency[query_id])
        audit.append(
            {
                "pg_stat_queryid": str(queries[index]["queryid"]),
                "job_query_id": query_id,
                "shape_similarity": score,
                "runner_up_gap": gap,
                "mean_latency_ms": float(named_latency[query_id]),
            }
        )
    return audit


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def candidate_params(event: Mapping[str, Any]) -> Mapping[str, Any]:
    binding = event.get("candidate_binding") or {}
    params = binding.get("candidate_params") or event.get("candidate_params")
    if not isinstance(params, Mapping) or not params:
        raise ValueError("accepted event has no candidate parameter vector")
    return params


def params_match(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> bool:
    if set(left) != set(right):
        return False
    return all(
        math.isclose(
            float(left[name]),
            float(right[name]),
            rel_tol=5e-6,
            abs_tol=max(5e-12, abs(float(right[name])) * 5e-12),
        )
        for name in left
    )


def incumbent_evidence(
    formal_state: Mapping[str, Any],
    event_sets: Iterable[Iterable[Mapping[str, Any]]],
) -> Mapping[str, Any]:
    incumbent_params = formal_state.get("incumbent_params")
    incumbent_score = formal_state.get("incumbent_score")
    if not isinstance(incumbent_params, Mapping) or not incumbent_params:
        raise ValueError("formal state has no incumbent_params")
    if incumbent_score is None or not math.isfinite(float(incumbent_score)):
        raise ValueError("formal state has no finite incumbent_score")
    matches: list[Mapping[str, Any]] = []
    for events in event_sets:
        for event in events:
            if (
                event.get("event") != "job_round8_candidate_replay_complete"
                or event.get("accepted") is not True
                or event.get("technical_failure") is not False
            ):
                continue
            if not params_match(candidate_params(event), incumbent_params):
                continue
            actual_score = event.get("actual_score")
            if actual_score is None or not math.isclose(
                float(actual_score),
                float(incumbent_score),
                rel_tol=1e-12,
                abs_tol=1e-12,
            ):
                continue
            actual_resource = event.get("actual_resource")
            if not isinstance(actual_resource, Mapping) or any(
                name not in actual_resource for name in RESOURCES
            ):
                raise ValueError(
                    "matching incumbent event has incomplete actual_resource"
                )
            matches.append(event)
    if not matches:
        raise ValueError(
            "fail closed: no clean accepted event exactly matches formal "
            "incumbent params and score"
        )
    evidence = matches[-1]
    plan = (
        ((evidence.get("candidate_binding") or {}).get(
            "three_level_fingerprint"
        ) or {}).get("plan_level")
        or ((evidence.get("three_level_fingerprint") or {}).get("plan_level"))
    )
    if not isinstance(plan, str) or not plan.startswith("ratio-plan-sha256:"):
        raise ValueError("matching incumbent event has no exact plan level")
    return evidence


def existing_fingerprint_ids(payloads: Iterable[dict[str, Any]]) -> set[str]:
    result: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            fingerprint = value.get("three_level_fingerprint")
            if isinstance(fingerprint, dict):
                fingerprint_id = fingerprint.get("id")
                if isinstance(fingerprint_id, str) and fingerprint_id:
                    result.add(fingerprint_id)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for payload in payloads:
        visit(payload)
    return result


def raw_three_level(
    plan: str,
    feature_ratio: Mapping[str, float],
    resource: Mapping[str, float],
    capability: Mapping[str, float],
) -> dict[str, Any]:
    feature_level = {
        name: int(round(float(feature_ratio[name]) / 0.005))
        for name in RESOURCES
    }
    resource_level = {
        name: int(
            round(
                (float(resource[name]) / float(capability[name])) / 0.005
            )
        )
        for name in RESOURCES
    }
    identity = {
        "plan": plan,
        "feature_ratio_q005": feature_level,
        "resource_utilization_q005": resource_level,
    }
    return {
        "contract": "plan_feature_resource_three_level_v1",
        "id": "three-level-sha256:" + canonical_sha256(identity),
        "valid": True,
        "validation_errors": [],
        "plan_level": plan,
        "feature_ratio_level": feature_level,
        "predicted_resource_level": resource_level,
        "parameter_fallback_used": False,
    }


def query_set(
    capture: Mapping[str, Any],
    baseline_features: Mapping[str, Any],
) -> list[dict[str, Any]]:
    replay_count = {
        str(item["queryid"]): float(item.get("replay_count") or 1.0)
        for item in baseline_features.get("query_features") or []
    }
    queries: dict[str, dict[str, Any]] = {}
    for item in capture.get("items") or []:
        queryid = str(item.get("queryid"))
        query = str(item.get("query") or "").strip()
        if not queryid or not query:
            raise ValueError("baseline plan capture has an incomplete query")
        queries.setdefault(
            queryid,
            {
                "queryid": queryid,
                "query": query,
                "replay_count": replay_count.get(queryid, 1.0),
            },
        )
    if set(queries) != set(replay_count):
        raise ValueError(
            "baseline capture/query-feature coverage mismatch: "
            f"capture={len(queries)} features={len(replay_count)}"
        )
    return [queries[name] for name in replay_count]


def plan_regime(candidate: Mapping[str, Any]) -> str:
    metadata = candidate.get("final_estimation_metadata") or {}
    plan = (
        metadata.get("workload_plan_fingerprint")
        or candidate.get("workload_plan_fingerprint")
        or (candidate.get("three_level_fingerprint") or {}).get("plan_level")
    )
    if not isinstance(plan, str) or not plan.startswith("ratio-plan-sha256:"):
        raise ValueError("candidate has no exact ratio-plan workload fingerprint")
    return plan


def score_resource(
    resource: Mapping[str, float],
    capability: Mapping[str, float],
) -> tuple[float, dict[str, float]]:
    utilization = {
        name: float(resource[name]) / float(capability[name])
        for name in RESOURCES
    }

    score = sum(
        resource_band_score(
            utilization[name],
            RESOURCE_WEIGHTS[name],
            name == "memory",
        )
        for name in RESOURCES
    )
    return score, utilization


def resource_band_score(value: float, weight: float, target: bool) -> float:
    """Return one resource's contribution to the JOB utility score."""
    low, high, hard = ((0.85, 0.90, 0.95) if target else (0.30, 0.50, 0.90))
    if target:
        if value < low:
            return weight * (1.0 - ((low - value) / low) ** 2)
        if value <= high:
            return weight * (1.0 - 0.2 * (value - low) / (high - low))
        span = max(hard - high, 0.05)
        gap = (value - high) / span
        excess = max(0.0, value - hard) / span
        return weight * (0.8 - gap * gap - 4.0 * excess)
    if value < low:
        return weight * 0.8 * (1.0 - ((low - value) / low) ** 2)
    if value <= high:
        return weight * (0.8 + 0.2 * (value - low) / (high - low))
    span = max(hard - high, 0.05)
    gap = (value - high) / span
    excess = max(0.0, value - hard) / span
    return weight * (1.0 - gap * gap - 4.0 * excess)


def resource_score_slope(resource: str, utilization: float) -> float:
    """Numerically differentiate the exact score function used this round."""
    epsilon = 1e-6
    lower = max(0.0, float(utilization) - epsilon)
    upper = float(utilization) + epsilon
    if upper <= lower:
        return 0.0
    weight = RESOURCE_WEIGHTS[resource]
    target = resource == "memory"
    return (
        resource_band_score(upper, weight, target)
        - resource_band_score(lower, weight, target)
    ) / (upper - lower)


def refresh_current_round_gradient_fields(
    candidate: dict[str, Any],
    *,
    incumbent_params: Mapping[str, float],
    incumbent_model_resource: Mapping[str, float],
    capability: Mapping[str, float],
    model_confidence: float,
    generation_eta: float,
) -> None:
    """Replace inherited gradient metadata with a current-round secant.

    A multi-parameter candidate provides one directional observation rather than
    independent coordinate observations.  The resource-utilization displacement
    is therefore assigned to parameter log-coordinates with the minimum-norm
    directional secant.  This makes every published ``T`` and ``g_j`` a function
    only of the current incumbent and this candidate's current model prediction.
    It also permits a strict zero-gradient/non-zero-step fail-closed check.
    """
    params = candidate.get("candidate_params") or {}
    predicted = candidate.get("predicted_resource") or {}
    if set(params) != set(incumbent_params):
        raise ValueError("candidate/current incumbent parameter coverage mismatch")
    if any(name not in predicted for name in RESOURCES):
        raise ValueError("candidate has incomplete current-round resource prediction")
    confidence = float(model_confidence)
    if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
        raise ValueError("current-round model confidence must be in [0, 1]")

    coordinates: dict[str, float] = {}
    for name in sorted(incumbent_params):
        before = float(incumbent_params[name])
        after = float(params[name])
        if before > 0.0 and after > 0.0:
            coordinates[name] = math.log(after / before)
        else:
            coordinates[name] = after - before
    squared_norm = sum(value * value for value in coordinates.values())
    eta = math.sqrt(squared_norm)
    utilization = {
        resource: float(predicted[resource]) / float(capability[resource])
        for resource in RESOURCES
    }
    incumbent_utilization = {
        resource: float(incumbent_model_resource[resource])
        / float(capability[resource])
        for resource in RESOURCES
    }
    slopes = {
        resource: resource_score_slope(resource, utilization[resource])
        for resource in RESOURCES
    }
    sensitivities: dict[str, dict[str, float]] = {}
    components: dict[str, dict[str, dict[str, Any]]] = {}
    gradients: dict[str, float] = {}
    dscore_du: dict[str, dict[str, float]] = {}
    for parameter, coordinate in coordinates.items():
        sensitivities[parameter] = {}
        components[parameter] = {}
        dscore_du[parameter] = copy.deepcopy(slopes)
        gradient = 0.0
        for resource in RESOURCES:
            sensitivity = (
                (utilization[resource] - incumbent_utilization[resource])
                * coordinate
                / squared_norm
                if squared_norm > 0.0
                else 0.0
            )
            contribution = slopes[resource] * sensitivity * confidence
            sensitivities[parameter][resource] = sensitivity
            components[parameter][resource] = {
                "predicted_resource": float(predicted[resource]),
                "capability": float(capability[resource]),
                "utilization": utilization[resource],
                "dScore/dU": slopes[resource],
                "effective_dScore/dU": slopes[resource],
                "T": sensitivity,
                "confidence": confidence,
                "g_component": contribution,
                "phase": "current_round_directional_secant",
                "pressure_scale": 1.0,
            }
            gradient += contribution
        gradients[parameter] = gradient

    moved = squared_norm > ZERO_GRADIENT_EPSILON ** 2
    all_zero = all(abs(value) <= ZERO_GRADIENT_EPSILON for value in gradients.values())
    if moved and all_zero:
        raise ValueError(
            "fail closed: current-round g_j is all zero but delta_param is non-zero"
        )
    candidate.update(
        {
            "dScore/dU": dscore_du,
            "T": sensitivities,
            "confidence": {
                parameter: confidence for parameter in sorted(coordinates)
            },
            "g_j": gradients,
            "gradient_components": components,
            "gradient_contract": CURRENT_ROUND_GRADIENT_CONTRACT,
            "gradient_source": "current_incumbent_and_current_candidate_model",
            "gradient_zero_guard_passed": True,
            "generation_eta": float(generation_eta),
            "eta": eta,
            "delta_param": delta_payload(params, incumbent_params),
        }
    )


def current_round_gradient_fields_valid(candidate: Mapping[str, Any]) -> bool:
    """Validate the published current-round gradient and zero-step contract."""
    params = candidate.get("candidate_params")
    gradients = candidate.get("g_j")
    deltas = candidate.get("delta_param")
    components = candidate.get("gradient_components")
    if (
        candidate.get("gradient_contract") != CURRENT_ROUND_GRADIENT_CONTRACT
        or candidate.get("gradient_source")
        != "current_incumbent_and_current_candidate_model"
        or candidate.get("gradient_zero_guard_passed") is not True
        or not isinstance(params, Mapping)
        or not isinstance(gradients, Mapping)
        or not isinstance(deltas, Mapping)
        or not isinstance(components, Mapping)
        or set(gradients) != set(params)
        or set(deltas) != set(params)
        or set(components) != set(params)
    ):
        return False
    try:
        parsed_gradients = {name: float(gradients[name]) for name in params}
        parsed_deltas = {
            name: float((deltas[name] or {}).get("absolute")) for name in params
        }
    except (TypeError, ValueError, AttributeError):
        return False
    if any(
        not math.isfinite(value)
        for value in (*parsed_gradients.values(), *parsed_deltas.values())
    ):
        return False
    for parameter in params:
        resource_components = components.get(parameter)
        if not isinstance(resource_components, Mapping) or set(resource_components) != set(
            RESOURCES
        ):
            return False
        if any(
            not isinstance(values, Mapping)
            or values.get("phase") != "current_round_directional_secant"
            for values in resource_components.values()
        ):
            return False
    all_zero = all(
        abs(value) <= ZERO_GRADIENT_EPSILON
        for value in parsed_gradients.values()
    )
    moved = any(
        abs(value) > ZERO_GRADIENT_EPSILON for value in parsed_deltas.values()
    )
    return not (all_zero and moved)


def measurement_relative_half_width(
    event: Mapping[str, Any],
) -> dict[str, float]:
    """Derive a conservative resource interval from clean stable windows."""
    protocol = event.get("measurement_protocol") or {}
    stability = protocol.get("resource_stability") or {}
    blocks = stability.get("cross_window_blocks") or []
    latest = blocks[-1] if blocks else {}
    fields = latest.get("fields") or []
    by_field = {
        str(item.get("field")): float(
            item.get("coefficient_of_variation") or 0.0
        )
        for item in fields
        if isinstance(item, Mapping)
    }
    result = {}
    for name in RESOURCES:
        coefficient = by_field.get(f"{name}_utilization", 0.0)
        if not math.isfinite(coefficient) or coefficient < 0.0:
            coefficient = 0.0
        # One percent is the minimum uncertainty for a single real replay.
        # Twice the cross-window CV is used when the stable run itself shows
        # more variation; the interval remains bounded but never optimistic.
        result[name] = min(0.50, max(0.01, 2.0 * coefficient))
    return result


def conservative_score_interval(
    predicted: Mapping[str, float],
    relative_half_width: Mapping[str, float],
    capability: Mapping[str, float],
) -> tuple[dict[str, dict[str, float]], dict[str, Any]]:
    """Evaluate score extrema at box endpoints and utility breakpoints."""
    interval = {}
    values_by_resource = {}
    for name in RESOURCES:
        value = float(predicted[name])
        width = float(relative_half_width[name])
        lower = max(0.0, value * (1.0 - width))
        upper = value * (1.0 + width)
        interval[name] = {
            "lower": lower,
            "median": value,
            "upper": upper,
            "relative_half_width": width,
        }
        values = {lower, value, upper}
        target = (
            (0.85, 0.90, 0.95)
            if name == "memory"
            else (0.30, 0.50, 0.90)
        )
        for utilization in target:
            breakpoint = utilization * float(capability[name])
            if lower <= breakpoint <= upper:
                values.add(breakpoint)
        values_by_resource[name] = sorted(values)
    evaluated = []
    for combination in itertools.product(
        *(values_by_resource[name] for name in RESOURCES)
    ):
        resource = {
            name: float(combination[index])
            for index, name in enumerate(RESOURCES)
        }
        score, _utilization = score_resource(resource, capability)
        evaluated.append((score, resource))
    lower_score, lower_witness = min(
        evaluated,
        key=lambda item: item[0],
    )
    upper_score, upper_witness = max(
        evaluated,
        key=lambda item: item[0],
    )
    median_score, _ = score_resource(predicted, capability)
    return interval, {
        "lower": float(lower_score),
        "median": float(median_score),
        "upper": float(upper_score),
        "lower_witness_resource": lower_witness,
        "upper_witness_resource": upper_witness,
        "evaluated_point_count": len(evaluated),
        "method": "resource_box_band_breakpoints_v1",
    }


def calibrated_resource(
    raw_resource: Mapping[str, float],
    anchor_predicted: Mapping[str, float],
    anchor_actual: Mapping[str, float],
) -> dict[str, float]:
    result: dict[str, float] = {}
    for name in RESOURCES:
        predicted = float(anchor_predicted[name])
        if predicted <= 0.0:
            raise ValueError(f"anchor predicted {name} is not positive")
        result[name] = (
            float(anchor_actual[name]) * float(raw_resource[name]) / predicted
        )
    return result


def candidate_vectors(
    anchor: Mapping[str, float],
    initial: Mapping[str, float],
    ranges: Mapping[str, Mapping[str, float]],
) -> list[tuple[str, float, dict[str, float]]]:
    vectors: list[tuple[str, float, dict[str, float]]] = []

    def clipped_point(values: Mapping[str, float]) -> dict[str, float]:
        point: dict[str, float] = {}
        for name, value in values.items():
            low = float(ranges[name]["min_value"])
            high = float(ranges[name]["max_value"])
            if high <= low:
                point[name] = low
                continue
            epsilon = max((high - low) * 1e-9, 1e-15)
            point[name] = min(
                high - epsilon,
                max(low + epsilon, float(value)),
            )
        return point

    for factor in (0.98, 1.02, 0.95, 1.05, 0.90, 1.10, 0.80, 1.20):
        params: dict[str, float] = {}
        for name, raw in anchor.items():
            baseline = float(initial[name])
            proposed = baseline + (float(raw) - baseline) * factor
            low = float(ranges[name]["min_value"])
            high = float(ranges[name]["max_value"])
            if high <= low:
                params[name] = low
                continue
            epsilon = max((high - low) * 1e-9, 1e-15)
            params[name] = min(high - epsilon, max(low + epsilon, proposed))
        vectors.append(("gradient_ray", factor, params))
    for name in sorted(anchor):
        low = float(ranges[name]["min_value"])
        high = float(ranges[name]["max_value"])
        if high <= low:
            continue
        for factor in (0.95, 1.05, 0.90, 1.10, 0.80, 1.20):
            params: dict[str, float] = {}
            for key, value in anchor.items():
                key_low = float(ranges[key]["min_value"])
                key_high = float(ranges[key]["max_value"])
                if key_high <= key_low:
                    params[key] = key_low
                    continue
                key_epsilon = max(
                    (key_high - key_low) * 1e-9,
                    1e-15,
                )
                params[key] = min(
                    key_high - key_epsilon,
                    max(key_low + key_epsilon, float(value)),
                )
            epsilon = max((high - low) * 1e-9, 1e-15)
            params[name] = min(
                high - epsilon,
                max(low + epsilon, float(anchor[name]) * factor),
            )
            vectors.append((f"coordinate:{name}", factor, params))

    # The formal JOB range can collapse to only two live dimensions.  Ray and
    # single-coordinate moves then repeatedly hit the same feature bucket even
    # though an exact-plan neighborhood still has unexplored interior points.
    # Explore that neighborhood deterministically with small paired offsets,
    # followed by a low-discrepancy interior design.  These are EXPLAIN-only
    # points; the real replay cap is unaffected.
    free_names = [
        name
        for name in sorted(anchor)
        if float(ranges[name]["max_value"])
        > float(ranges[name]["min_value"])
    ]
    if len(free_names) >= 2:
        first, second = free_names[:2]
        local_fractions = (
            0.002,
            0.005,
            0.01,
            0.02,
            0.04,
            0.08,
            0.15,
            0.25,
        )
        for fraction in local_fractions:
            for first_sign, second_sign in (
                (-1.0, -1.0),
                (-1.0, 1.0),
                (1.0, -1.0),
                (1.0, 1.0),
            ):
                values = dict(anchor)
                values[first] = float(anchor[first]) + first_sign * fraction * (
                    float(ranges[first]["max_value"])
                    - float(ranges[first]["min_value"])
                )
                values[second] = float(anchor[second]) + second_sign * fraction * (
                    float(ranges[second]["max_value"])
                    - float(ranges[second]["min_value"])
                )
                vectors.append(
                    (
                        f"paired_local:{first}:{second}",
                        fraction,
                        clipped_point(values),
                    )
                )

        def halton(index: int, base: int) -> float:
            value = 0.0
            denominator = 1.0
            current = index
            while current:
                current, remainder = divmod(current, base)
                denominator *= base
                value += remainder / denominator
            return value

        for index in range(1, 49):
            values = dict(anchor)
            for name, base in zip(free_names, (2, 3, 5, 7, 11, 13, 17)):
                low = float(ranges[name]["min_value"])
                high = float(ranges[name]["max_value"])
                # Keep every live dimension strictly away from its corners.
                fraction = 0.02 + 0.96 * halton(index, base)
                values[name] = low + fraction * (high - low)
            vectors.append(
                (
                    "halton_interior",
                    float(index),
                    clipped_point(values),
                )
            )

    deduplicated: list[tuple[str, float, dict[str, float]]] = []
    seen_points: set[tuple[tuple[str, float], ...]] = set()
    for direction, eta, params in vectors:
        key = tuple(
            (name, round(float(value), 15))
            for name, value in sorted(params.items())
        )
        if key in seen_points:
            continue
        seen_points.add(key)
        deduplicated.append((direction, eta, params))
    return deduplicated


def delta_payload(
    params: Mapping[str, float],
    initial: Mapping[str, float],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, after in params.items():
        before = float(initial[name])
        value = float(after)
        result[name] = {
            "before": before,
            "proposed_before_clip": value,
            "after_clip": value,
            "absolute": value - before,
            "relative": (value - before) / before if before != 0.0 else None,
            "clipped": False,
        }
    return result


def normalized_parameter_distance(
    params: Mapping[str, float],
    incumbent: Mapping[str, float],
    ranges: Mapping[str, Mapping[str, float]],
) -> float:
    distances: list[float] = []
    for name, raw_after in params.items():
        after = float(raw_after)
        before = float(incumbent[name])
        low = float(ranges[name]["min_value"])
        high = float(ranges[name]["max_value"])
        if low > 0.0 and before > 0.0 and after > 0.0:
            distances.append(abs(math.log(after / before)))
        else:
            distances.append(abs(after - before) / max(high - low, 1e-15))
    return max(distances, default=0.0)


def normalized_candidate_distance(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    ranges: Mapping[str, Mapping[str, float]],
    capability: Mapping[str, float],
) -> float:
    """Return a deterministic maximin distance across all fingerprint levels."""
    parameter_distances: list[float] = []
    left_params = candidate_params(left)
    right_params = candidate_params(right)
    for name in sorted(left_params):
        low = float(ranges[name]["min_value"])
        high = float(ranges[name]["max_value"])
        scale = max(high - low, abs(float(left_params[name])), 1e-15)
        parameter_distances.append(
            abs(float(left_params[name]) - float(right_params[name])) / scale
        )

    feature_distances = [
        abs(
            float(left["feature_ratio"][name])
            - float(right["feature_ratio"][name])
        )
        / max(
            abs(float(left["feature_ratio"][name])),
            abs(float(right["feature_ratio"][name])),
            1e-15,
        )
        for name in RESOURCES
    ]
    resource_distances = [
        abs(
            float(left["predicted_resource"][name])
            - float(right["predicted_resource"][name])
        )
        / max(float(capability[name]), 1e-15)
        for name in RESOURCES
    ]
    plan_distance = (
        1.0
        if plan_regime(left) != plan_regime(right)
        else 0.0
    )
    components = [
        plan_distance,
        *parameter_distances,
        *feature_distances,
        *resource_distances,
    ]
    return math.sqrt(sum(value * value for value in components))


def maximin_candidates(
    candidates: list[dict[str, Any]],
    count: int,
    ranges: Mapping[str, Mapping[str, float]],
    capability: Mapping[str, float],
) -> list[dict[str, Any]]:
    """Select diverse probes without using workload outcomes or manual ranks."""
    if len(candidates) < count:
        raise ValueError("not enough candidates for maximin selection")
    ordered = sorted(
        candidates,
        key=lambda item: (
            -float(item["candidate_score"]),
            str(item["three_level_fingerprint"]["id"]),
        ),
    )
    selected = [ordered.pop(0)]
    while len(selected) < count:
        next_item = max(
            ordered,
            key=lambda item: (
                min(
                    normalized_candidate_distance(
                        item, chosen, ranges, capability
                    )
                    for chosen in selected
                ),
                float(item["candidate_score"]),
                str(item["three_level_fingerprint"]["id"]),
            ),
        )
        selected.append(next_item)
        ordered.remove(next_item)
    return selected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", required=True, type=Path)
    parser.add_argument("--evidence-top5", required=True, type=Path)
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--formal-state", required=True, type=Path)
    parser.add_argument(
        "--incumbent-events",
        action="append",
        required=True,
        type=Path,
        help=(
            "formal event JSONL containing the accepted event that exactly "
            "matches the authoritative incumbent; repeatable"
        ),
    )
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--capture", required=True, type=Path)
    parser.add_argument(
        "--incumbent-point-result",
        required=True,
        type=Path,
        help="clean accepted-incumbent point_result.json with per-query latency",
    )
    parser.add_argument("--code-root", required=True, type=Path)
    parser.add_argument("--exclude", action="append", default=[], type=Path)
    parser.add_argument(
        "--mode",
        choices=("diagnostic", "formal"),
        default="diagnostic",
    )
    parser.add_argument(
        "--minimum-predicted-improvement",
        type=float,
        default=0.005,
    )
    parser.add_argument(
        "--force-probe-discovery",
        action="store_true",
        help=(
            "in diagnostic mode, select five maximin unknown-regime probes "
            "even when three calibrated trusted candidates exist"
        ),
    )
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.force_probe_discovery and args.mode != "diagnostic":
        raise ValueError(
            "--force-probe-discovery is only valid in diagnostic mode"
        )

    sys.path.insert(0, str(args.code_root))
    from ratio_scale_resource_estimator import RatioScaleResourceEstimator

    history = load_json(args.history)
    evidence = load_json(args.evidence_top5)
    events = load_jsonl(args.events)
    formal_state = load_json(args.formal_state)
    incumbent_event_sets = [
        load_jsonl(path) for path in args.incumbent_events
    ]
    incumbent_event = incumbent_evidence(formal_state, incumbent_event_sets)
    config = load_json(args.config)
    capture = load_json(args.capture)
    anchors = evidence.get("top5") or []
    if len(anchors) != 5:
        raise ValueError("evidence Top-5 must contain exactly five candidates")
    results = {
        int(item["rank"]): item
        for item in events
        if item.get("event") == "job_round8_candidate_replay_complete"
        and not item.get("technical_failure")
    }
    if set(results) != {1, 2, 3, 4, 5}:
        raise ValueError("events must contain five clean diagnostic observations")

    raw_candidates = history.get("ranked_candidates") or []
    if not raw_candidates:
        raise ValueError("history has no ranked_candidates")
    reference = raw_candidates[0]
    baseline_metadata = reference["final_estimation_metadata"]
    baseline_features = baseline_metadata["baseline_features"]
    queries = query_set(capture, baseline_features)
    query_latency, source_sql, query_latency_audit = incumbent_query_latency(
        args.incumbent_point_result
    )
    query_latency_audit["sql_shape_binding"] = bind_latency_by_sql_shape(
        queries,
        query_latency,
        source_sql,
    )
    incumbent_params = formal_state["incumbent_params"]
    initial = {
        name: float(incumbent_params[name])
        for name in config["initial_params"]
    }
    if set(initial) != set(incumbent_params):
        raise ValueError(
            "formal incumbent parameter names do not match optimizer config"
        )
    capability = {
        name: float(value) for name, value in config["capability"].items()
    }
    formal_ranges = formal_state.get("range_by_parameter")
    if not isinstance(formal_ranges, Mapping) or set(formal_ranges) != set(initial):
        raise ValueError("formal state has no complete range_by_parameter")
    active_ranges = {
        name: {
            "min_value": float(formal_ranges[name]["min"]),
            "max_value": float(formal_ranges[name]["max"]),
        }
        for name in initial
    }
    for name, bounds in active_ranges.items():
        if (
            bounds["min_value"] > bounds["max_value"]
            or float(initial[name]) < bounds["min_value"] - 1e-15
            or float(initial[name]) > bounds["max_value"] + 1e-15
        ):
            raise ValueError(f"formal range is invalid for {name}")
    baseline_real = {
        name: float(incumbent_event["actual_resource"][name])
        for name in RESOURCES
    }
    postgres = config["postgres"]

    def psql_query(sql: str) -> str:
        # node02's system psql is linked against a pre-SCRAM libpq.  Use the
        # PostgreSQL container's psql exactly like the sealed replay runner.
        # The SQL contains SET LOCAL plus EXPLAIN only; it does not run the
        # benchmark or mutate persistent optimizer state.
        completed = subprocess.run(
            [
                "docker",
                "exec",
                "ra_pg5_job_node02_v1_outercontrol",
                "psql",
                "-X",
                "-A",
                "-t",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                str(postgres["user"]),
                "-d",
                str(postgres["dbname"]),
                "-c",
                sql,
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip())
        return completed.stdout

    estimator = RatioScaleResourceEstimator(
        pg_host=str(postgres["host"]),
        pg_port=str(postgres["port"]),
        pg_user=str(postgres["user"]),
        pg_dbname=str(postgres["dbname"]),
        estimator_cfg=config["metrics"]["plan_estimator"],
        plan_capture_modes=["generic"],
        custom_plan_params={},
        psql_query_func=psql_query,
        cache_hit_rate=float(
            baseline_metadata.get("cache_hit_rate") or 0.85
        ),
        verbose=False,
    )
    original_collect_query_plans = estimator._inner._collect_query_plans

    def collect_query_plans_with_latency(
        query_records: Sequence[Mapping[str, Any]],
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Preserve sealed per-query latency across the EXPLAIN adapter boundary."""
        latency_by_queryid = {
            str(item["queryid"]): float(item["mean_latency_ms"])
            for item in query_records
        }
        replay_count_by_queryid = {
            str(item["queryid"]): float(item.get("replay_count") or 1.0)
            for item in query_records
        }
        explained = original_collect_query_plans(query_records, **kwargs)
        observed_queryids = [str(item.get("queryid") or "") for item in explained]
        if (
            len(observed_queryids) != len(latency_by_queryid)
            or len(set(observed_queryids)) != len(observed_queryids)
            or set(observed_queryids) != set(latency_by_queryid)
        ):
            raise RuntimeError(
                "EXPLAIN adapter queryid coverage differs from sealed latency "
                f"evidence: observed={observed_queryids} "
                f"expected={sorted(latency_by_queryid)}"
            )
        for item in explained:
            queryid = str(item["queryid"])
            item["mean_latency_ms"] = latency_by_queryid[queryid]
            item["replay_count"] = replay_count_by_queryid[queryid]
        return explained

    estimator._inner._collect_query_plans = collect_query_plans_with_latency
    calibration = estimator.calibrate(queries, initial, baseline_real)
    live_baseline_plan = str(
        estimator.last_metadata.get("workload_plan_fingerprint") or ""
    )
    (
        incumbent_model_resource,
        _incumbent_stats,
        incumbent_model_metadata,
    ) = estimator.estimate_with_metadata(queries, initial)
    live_baseline_plan = str(
        incumbent_model_metadata.get("workload_plan_fingerprint") or ""
    )
    expected_baseline_plan = str(
        (
            (incumbent_event.get("candidate_binding") or {}).get(
                "three_level_fingerprint"
            )
            or incumbent_event.get("three_level_fingerprint")
            or {}
        )["plan_level"]
    )
    if live_baseline_plan != expected_baseline_plan:
        raise RuntimeError(
            "live EXPLAIN baseline plan does not match sealed analysis: "
            f"live={live_baseline_plan} expected={expected_baseline_plan}"
        )

    excluded_payloads = [evidence, *[load_json(path) for path in args.exclude]]
    seen = existing_fingerprint_ids(excluded_payloads)
    generated: list[dict[str, Any]] = []
    audit: list[dict[str, Any]] = []
    incumbent_anchor = copy.deepcopy(reference)
    incumbent_anchor.update(
        {
            "rank": 0,
            "candidate_params": copy.deepcopy(initial),
            "optimized_params": copy.deepcopy(initial),
            "predicted_resource": {
                name: float(incumbent_model_resource[name])
                for name in RESOURCES
            },
            "final_resource": {
                name: float(incumbent_model_resource[name])
                for name in RESOURCES
            },
            "workload_plan_fingerprint": live_baseline_plan,
            "final_estimation_metadata": incumbent_model_metadata,
            "three_level_fingerprint": {
                "plan_level": live_baseline_plan,
            },
        }
    )
    anchor_contexts = [
        {
            "anchor": incumbent_anchor,
            "rank": 0,
            "actual_resource": baseline_real,
            "anchor_predicted": {
                name: float(incumbent_model_resource[name])
                for name in RESOURCES
            },
            "incumbent_anchor": True,
            "evidence_event": incumbent_event,
        }
    ]
    for anchor in anchors:
        rank = int(anchor["rank"])
        actual_resource = results[rank].get("actual_resource")
        if not isinstance(actual_resource, dict):
            raise ValueError(f"rank {rank} has no actual_resource")
        anchor_contexts.append(
            {
                "anchor": anchor,
                "rank": rank,
                "actual_resource": actual_resource,
                "anchor_predicted": anchor["predicted_resource"],
                "incumbent_anchor": False,
                "evidence_event": results[rank],
            }
        )

    for context in anchor_contexts:
        anchor = context["anchor"]
        rank = int(context["rank"])
        anchor_plan = plan_regime(anchor)
        actual_resource = context["actual_resource"]
        anchor_predicted = context["anchor_predicted"]
        is_incumbent_anchor = bool(context["incumbent_anchor"])
        evidence_event = context["evidence_event"]
        attempts: list[dict[str, Any]] = []
        trusted_found_count = 0
        probe_found_count = 0
        for direction, eta, params in candidate_vectors(
            anchor["candidate_params"], initial, active_ranges
        ):
            parameter_distance = normalized_parameter_distance(
                params,
                initial,
                active_ranges,
            )
            if parameter_distance <= 1e-6:
                continue
            raw_resource, _stats, metadata = estimator.estimate_with_metadata(
                queries, params
            )
            observed_plan = str(metadata["workload_plan_fingerprint"])
            same_regime = observed_plan == anchor_plan
            # A validated exact plan regime is modeled by its real observation.
            # Ratio scaling inside the same plan ID proved unstable when the
            # anchor's old memory prediction was near zero: harmless raw-model
            # changes were amplified by 3x-17x.  Keep the trusted prediction
            # piecewise constant at the exact-regime empirical anchor.  A plan
            # transition remains an uncertain probe and retains the raw model.
            relative_ratio = {
                name: (
                    float(raw_resource[name])
                    / float(anchor_predicted[name])
                )
                for name in RESOURCES
            }
            relative_model_valid = all(
                math.isfinite(value) and 0.5 <= value <= 2.0
                for value in relative_ratio.values()
            )
            if same_regime and is_incumbent_anchor and relative_model_valid:
                predicted = {
                    name: (
                        float(actual_resource[name]) * relative_ratio[name]
                    )
                    for name in RESOURCES
                }
                prediction_contract = (
                    "exact_plan_regime_incumbent_relative_anchor_v1"
                )
            elif same_regime:
                predicted = {
                    name: float(actual_resource[name])
                    for name in RESOURCES
                }
                prediction_contract = "exact_plan_regime_empirical_anchor_v1"
            else:
                predicted = {
                    name: float(raw_resource[name]) for name in RESOURCES
                }
                prediction_contract = "unknown_plan_regime_raw_probe_v1"
            feature_ratio = {
                name: float(metadata["feature_ratios"][name])
                for name in RESOURCES
            }
            fingerprint = raw_three_level(
                observed_plan, feature_ratio, predicted, capability
            )
            novel = fingerprint["id"] not in seen
            score, utilization = score_resource(predicted, capability)
            if same_regime:
                measurement_width = measurement_relative_half_width(
                    evidence_event
                )
                # Exact-plan calibration removes the regime discontinuity but
                # does not make an extrapolated neighbor deterministic.  Add a
                # bounded distance term so a farther point cannot inherit the
                # same confidence as its measured anchor.
                relative_half_width = {
                    name: min(
                        0.50,
                        float(measurement_width[name])
                        + min(0.25, 0.05 * parameter_distance),
                    )
                    for name in RESOURCES
                }
            else:
                # Unknown regimes have no empirical resource calibration and
                # therefore remain discovery probes with deliberately broad
                # intervals.  Their point estimate never admits formal work.
                relative_half_width = {name: 1.0 for name in RESOURCES}
            (
                predicted_resource_interval,
                predicted_score_interval,
            ) = conservative_score_interval(
                predicted,
                relative_half_width,
                capability,
            )
            formal_lcb_score = float(predicted_score_interval["lower"])
            incumbent_score = float(formal_state["incumbent_score"])
            predicted_improvement_lower_bound = (
                formal_lcb_score - incumbent_score
            )
            formal_improvement_eligible = bool(
                same_regime
                and formal_lcb_score
                > incumbent_score + args.minimum_predicted_improvement
            )
            feasible_prediction = (
                math.isfinite(score)
                and score >= 0.0
                and all(
                    math.isfinite(value) and 0.0 <= value <= 1.20
                    for value in utilization.values()
                )
            )
            attempts.append(
                {
                    "direction": direction,
                    "eta": eta,
                    "same_exact_regime": same_regime,
                    "plan_level": observed_plan,
                    "three_level_fingerprint_id": fingerprint["id"],
                    "novel": novel,
                    "feasible_prediction": feasible_prediction,
                    "relative_model_valid": relative_model_valid,
                    "relative_ratio": relative_ratio,
                    "parameter_distance": parameter_distance,
                    "prediction_contract": prediction_contract,
                    "candidate_score": score,
                    "formal_lcb_score": formal_lcb_score,
                    "predicted_improvement_lower_bound": (
                        predicted_improvement_lower_bound
                    ),
                    "formal_improvement_eligible": (
                        formal_improvement_eligible
                    ),
                    "utilization": utilization,
                }
            )
            if not novel:
                continue
            role = "trusted" if same_regime else "probe"
            if role == "trusted" and not feasible_prediction:
                continue
            if role == "trusted" and trusted_found_count >= 3:
                continue
            if role == "probe" and probe_found_count >= 1:
                continue
            candidate = copy.deepcopy(anchor)
            candidate_pool_binding = copy.deepcopy(
                anchor.get("candidate_pool_binding") or {}
            )
            candidate_pool_binding["candidate_id"] = fingerprint["id"]
            if not candidate_pool_binding.get("source_sha256"):
                candidate_pool_binding.update(
                    {
                        "candidate_rank": rank,
                        "source_contract": (
                            "ranking_analysis.json."
                            "derived_exact_regime_successor"
                        ),
                        "source_path": str(args.history),
                        "source_sha256": file_sha256(args.history),
                    }
                )
            candidate.update(
                {
                    "rank": 0,
                    "selection_role": role,
                    "initial_params": copy.deepcopy(initial),
                    "candidate_params": params,
                    "optimized_params": copy.deepcopy(params),
                    "feature_ratio": feature_ratio,
                    "predicted_resource": predicted,
                    "predicted_resource_interval": (
                        predicted_resource_interval
                    ),
                    "predicted_score_interval": predicted_score_interval,
                    "final_resource": copy.deepcopy(predicted),
                    "utilization": utilization,
                    "candidate_score": score,
                    "calibrated_candidate_score": score,
                    "selection_score": score,
                    "final_score": score,
                    "formal_lcb_score": formal_lcb_score,
                    "formal_admission_score": formal_lcb_score,
                    "predicted_improvement_lower_bound": (
                        predicted_improvement_lower_bound
                    ),
                    "formal_improvement_eligible": (
                        formal_improvement_eligible
                    ),
                    "three_level_fingerprint": fingerprint,
                    "candidate_plan_signature": fingerprint["id"],
                    "candidate_pool_binding": candidate_pool_binding,
                    "workload_plan_fingerprint": observed_plan,
                    "final_estimation_metadata": metadata,
                    "candidate_origin": (
                        "job_exact_regime_empirical_anchor_explain_neighbor"
                        if same_regime
                        else "job_unknown_regime_explain_transition_probe"
                    ),
                    "diagnostic_fields_complete": True,
                    "resource_prediction_contract": (
                        prediction_contract
                    ),
                    "probe_kind": (
                        "same_exact_regime_successor"
                        if same_regime
                        else "first_unknown_exact_regime_probe"
                    ),
                    "source_calibration_anchor_rank": rank,
                    "source_is_formal_incumbent_anchor": is_incumbent_anchor,
                    "source_calibration_actual_resource": actual_resource,
                    "diagnostic_only": args.mode == "diagnostic",
                    "incumbent_update_eligible": (
                        same_regime and args.mode == "formal"
                    ),
                    "used_for_gradient": (
                        same_regime and args.mode == "formal"
                    ),
                    "used_for_inner_incumbent_update": (
                        same_regime and args.mode == "formal"
                    ),
                    "accepted": False,
                    "gradient_update_applied": False,
                    "incumbent_update_applied": False,
                    "range_update_applied": False,
                    "qps_used_for_inner_scoring": False,
                    "boundary_corner": False,
                    "boundary_plateau": False,
                }
            )
            try:
                refresh_current_round_gradient_fields(
                    candidate,
                    incumbent_params=initial,
                    incumbent_model_resource=incumbent_model_resource,
                    capability=capability,
                    model_confidence=1.0 if same_regime else 0.35,
                    generation_eta=eta,
                )
            except ValueError as error:
                attempts[-1]["gradient_rejected"] = True
                attempts[-1]["gradient_rejection_reason"] = str(error)
                continue
            candidate.pop("diagnostic_snapshot", None)
            generated.append(candidate)
            seen.add(fingerprint["id"])
            if same_regime:
                trusted_found_count += 1
            else:
                probe_found_count += 1
            if (
                args.force_probe_discovery
                and probe_found_count >= 1
            ) or (
                not args.force_probe_discovery
                and trusted_found_count >= 3
                and probe_found_count >= 1
            ):
                break
        audit.append(
            {
                "anchor_rank": rank,
                "anchor_plan": anchor_plan,
                "incumbent_anchor": is_incumbent_anchor,
                "attempts": attempts,
                "trusted_successor_count": trusted_found_count,
                "uncertain_probe_count": probe_found_count,
            }
        )
        progress_path = args.output.with_suffix(
            args.output.suffix + ".progress.json"
        )
        progress_path.write_text(
            json.dumps(
                {
                    "status": "explain_only_generation_in_progress",
                    "new_workload_calls": 0,
                    "completed_anchor_count": len(audit),
                    "generated_trusted_count": sum(
                        item["selection_role"] == "trusted"
                        for item in generated
                    ),
                    "generated_probe_count": sum(
                        item["selection_role"] == "probe"
                        for item in generated
                    ),
                    "last_anchor": audit[-1]["anchor_rank"],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        generated_trusted_count = sum(
            item["selection_role"] == "trusted" for item in generated
        )
        generated_probe_count = sum(
            item["selection_role"] == "probe" for item in generated
        )
        if (
            args.force_probe_discovery
            and generated_probe_count >= 5
        ) or (
            not args.force_probe_discovery
            and generated_trusted_count >= 3
            and generated_probe_count >= 2
        ):
            break

    trusted = sorted(
        (
            item
            for item in generated
            if item["selection_role"] == "trusted"
        ),
        key=lambda item: (
            float(item["formal_lcb_score"]),
            float(item["candidate_score"]),
            str(item["three_level_fingerprint"]["id"]),
        ),
        reverse=True,
    )
    probes = sorted(
        (
            item
            for item in generated
            if item["selection_role"] == "probe"
        ),
        key=lambda item: float(item["candidate_score"]),
        reverse=True,
    )
    incumbent_score = float(formal_state["incumbent_score"])
    diagnostic_probe_discovery = args.mode == "diagnostic" and (
        args.force_probe_discovery or len(trusted) < 3
    )
    if diagnostic_probe_discovery:
        if len(probes) < 5:
            raise RuntimeError(
                "fail closed: diagnostic discovery pool has "
                f"trusted={len(trusted)}, probes={len(probes)}; need 5 probes"
            )
        top5 = maximin_candidates(probes, 5, active_ranges, capability)
        trusted_count = 0
        probe_count = 5
        best_predicted_score = None
    else:
        if len(trusted) < 3 or len(probes) < 2:
            failure_audit_path = args.output.with_suffix(
                args.output.suffix + ".failure_audit.json"
            )
            failure_audit_path.write_text(
                json.dumps(
                    {
                        "status": "fail_closed",
                        "reason": "insufficient_trusted_probe_pool",
                        "new_workload_calls": 0,
                        "trusted_count": len(trusted),
                        "probe_count": len(probes),
                        "generated_candidate_count": len(generated),
                        "anchor_summary": [
                            {
                                "anchor_rank": item["anchor_rank"],
                                "anchor_plan": item["anchor_plan"],
                                "incumbent_anchor": item["incumbent_anchor"],
                                "attempt_count": len(item["attempts"]),
                                "trusted_successor_count": (
                                    item["trusted_successor_count"]
                                ),
                                "uncertain_probe_count": (
                                    item["uncertain_probe_count"]
                                ),
                            }
                            for item in audit
                        ],
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            raise RuntimeError(
                "fail closed: successor pool has "
                f"trusted={len(trusted)}, probes={len(probes)}; need 3+2"
            )
        best_predicted_score = float(trusted[0]["candidate_score"])
        best_formal_lcb_score = float(trusted[0]["formal_lcb_score"])
        if (
            args.mode == "formal"
            and best_formal_lcb_score
            <= incumbent_score + args.minimum_predicted_improvement
        ):
            failure_audit_path = args.output.with_suffix(
                args.output.suffix + ".failure_audit.json"
            )
            failure_audit_path.write_text(
                json.dumps(
                    {
                        "status": "fail_closed",
                        "reason": "no_positive_trusted_improvement",
                        "new_workload_calls": 0,
                        "trusted_count": len(trusted),
                        "probe_count": len(probes),
                        "generated_candidate_count": len(generated),
                        "best_trusted_predicted_score": (
                            best_predicted_score
                        ),
                        "best_trusted_formal_lcb_score": (
                            best_formal_lcb_score
                        ),
                        "incumbent_score": incumbent_score,
                        "minimum_predicted_improvement": (
                            args.minimum_predicted_improvement
                        ),
                        "required_score": (
                            incumbent_score
                            + args.minimum_predicted_improvement
                        ),
                        "recommended_next_mode": (
                            "diagnostic_force_probe_discovery"
                        ),
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            raise RuntimeError(
                "fail closed: no trusted candidate has sufficient positive "
                "lower-confidence-bound improvement: "
                f"{best_formal_lcb_score} <= "
                f"{incumbent_score + args.minimum_predicted_improvement}"
            )
        top5 = [*trusted[:3], *probes[:2]]
        trusted_count = 3
        probe_count = 2
    for rank, candidate in enumerate(top5, 1):
        candidate["rank"] = rank
    fingerprint_ids = {
        item["three_level_fingerprint"]["id"] for item in top5
    }
    if len(fingerprint_ids) != 5:
        raise RuntimeError("fail closed: Top-5 fingerprints are not distinct")

    output = {
        "selection_contract": "job_round8_robust_calibrated_trusted_probe_top5_v1",
        "successor_generation_contract": "job_exact_regime_successor_top5_v1",
        "diagnostic_only": args.mode == "diagnostic",
        "diagnostic_role_evaluation_only": (
            args.mode == "diagnostic" and not diagnostic_probe_discovery
        ),
        "explain_only": True,
        "new_workload_calls": 0,
        "trusted_count": trusted_count,
        "probe_count": probe_count,
        "accepted_update_allowed": args.mode == "formal",
        "incumbent_update_allowed": args.mode == "formal",
        "range_update_allowed": args.mode == "formal",
        "gradient_update_allowed": False,
        "formal_update_flags": {
            "accepted": args.mode == "formal",
            "incumbent": args.mode == "formal",
            "range": args.mode == "formal",
            "gradient": False,
        },
        "probe_updates_gradient": False,
        "probe_updates_incumbent": False,
        "qps_used_for_inner_scoring": False,
        "baseline_structural_regime": live_baseline_plan,
        "baseline_calibration": calibration,
        "formal_acquisition": {
            "mode": args.mode,
            "diagnostic_probe_discovery": diagnostic_probe_discovery,
            "incumbent_score": incumbent_score,
            "best_trusted_predicted_score": best_predicted_score,
            "best_trusted_formal_lcb_score": (
                float(trusted[0]["formal_lcb_score"])
                if trusted and not diagnostic_probe_discovery
                else None
            ),
            "score_field": "predicted_score_interval.lower",
            "minimum_predicted_improvement": (
                args.minimum_predicted_improvement
            ),
            "expected_improvement": (
                best_predicted_score - incumbent_score
                if best_predicted_score is not None
                else None
            ),
            "predicted_improvement_lower_bound": (
                float(trusted[0]["formal_lcb_score"]) - incumbent_score
                if trusted and not diagnostic_probe_discovery
                else None
            ),
            "near_noop_distance_threshold": 1e-6,
        },
        "authoritative_formal_state": {
            "path": str(args.formal_state),
            "sha256": file_sha256(args.formal_state),
            "accepted_count": int(formal_state["accepted_count"]),
            "incumbent_score": float(formal_state["incumbent_score"]),
            "incumbent_params": copy.deepcopy(initial),
            "range_by_parameter": copy.deepcopy(
                formal_state.get("range_by_parameter") or {}
            ),
        },
        "incumbent_calibration_evidence": {
            "source_paths": [
                str(path) for path in args.incumbent_events
            ],
            "source_sha256": {
                str(path): file_sha256(path)
                for path in args.incumbent_events
            },
            "actual_score": float(incumbent_event["actual_score"]),
            "actual_resource": copy.deepcopy(
                incumbent_event["actual_resource"]
            ),
            "candidate_params": copy.deepcopy(
                candidate_params(incumbent_event)
            ),
            "plan_level": expected_baseline_plan,
            "query_latency": query_latency_audit,
        },
        "neighbor_audit": audit,
        "candidate_generation_ranges": active_ranges,
        "generated_candidate_count": len(generated),
        "novel_top5_fingerprint_count": len(fingerprint_ids),
        "top5": top5,
        "ranked_candidates": [
            *top5,
            *[
                item
                for item in [*trusted, *probes]
                if item not in top5
            ],
        ],
    }
    args.output.write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "generated_candidate_count": len(generated),
                "trusted_candidate_count": len(trusted),
                "probe_candidate_count": len(probes),
                "top5_fingerprints": sorted(fingerprint_ids),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
