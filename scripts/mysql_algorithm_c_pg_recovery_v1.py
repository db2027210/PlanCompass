#!/usr/bin/env python3
"""PG-equivalent recovery actions for the MySQL Algorithm C controller.

The outer controller owns state transitions.  This module only builds and
scores deterministic C-only candidates for the two recovery paths that are
part of PostgreSQL's current Algorithm C policy:

* one measured safe/unsafe Outer bracket refinement before a due shrink; and
* the finite incumbent-centred numeric window ladder after empty admissions.

QPS is used only to choose an already measured Outer bracket.  It is never an
input to candidate scoring.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from typing import Any, Mapping, Sequence

from scripts import mysql_algorithm_c_global_coverage_v1 as coverage
from scripts import mysql_outer_rejection_cycle_v3 as rejection_cycle
from scripts import run_mysql_four_scene_algorithm_c_v2 as alg


POLICY_ID = "mysql_algorithm_c_pg_recovery_v1"
OUTER_BOUNDARY_CONTRACT = "algorithm_c_outer_feasibility_boundary_refinement_v2"
NUMERIC_REFINEMENT_CONTRACT = (
    "algorithm_c_progressive_centered_low_discrepancy_numeric_refinement_v2"
)
NUMERIC_RADII = (0.05, 0.10, 0.20, 0.40, 0.70, 1.00)
MAXIMUM_EMPTY_NUMERIC_WINDOWS = len(NUMERIC_RADII)


def advance_outer_decision(
    *, accepted: bool, consecutive_rejections: int,
    ranges: Mapping[str, Mapping[str, Any]],
    initial_ranges: Mapping[str, Mapping[str, Any]],
    incumbent_params: Mapping[str, Any], active: Sequence[str],
    observations: Sequence[Mapping[str, Any]],
    boundary_attempts_since_shrink: int,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    """Apply PG's optional bracket batch before the due two-miss bisection."""
    del initial_ranges  # Keep the frozen domain explicit in the public contract.
    pair = outer_boundary_pair(observations, ranges=ranges, active=active)
    if (
        not accepted
        and int(consecutive_rejections) == 1
        and int(boundary_attempts_since_shrink) < 1
        and pair is not None
    ):
        return copy.deepcopy(dict(ranges)), {
            "policy_id": POLICY_ID,
            "accepted": False,
            "consecutive_rejections_before": 1,
            "consecutive_rejections_after": 2,
            "forced_incumbent_shrink": False,
            "accepted_round_credit": 0,
            "next_outer_expansion": "outer_boundary_refinement",
            "incumbent_changed_by_cycle": False,
            "measured_outer_bracket_attempted_before_due_shrink": True,
            "outer_boundary_pair": pair,
        }, True
    updated, progress = rejection_cycle.advance_rejection_cycle(
        accepted=accepted,
        consecutive_rejections=int(consecutive_rejections),
        ranges=ranges,
        incumbent_params=incumbent_params,
        active_params=active,
    )
    return updated, progress, False


def _sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _inside(
    params: Mapping[str, Any], ranges: Mapping[str, Mapping[str, Any]],
    active: Sequence[str],
) -> bool:
    try:
        return all(
            float(ranges[name]["lower"]) <= float(params[name])
            <= float(ranges[name]["upper"])
            for name in active
        )
    except (KeyError, TypeError, ValueError):
        return False


def record_outer_observations(
    observations: Sequence[Mapping[str, Any]], *, rows: Sequence[Mapping[str, Any]],
    target: str, active: Sequence[str], outer_attempt: int,
) -> list[dict[str, Any]]:
    """Append de-duplicated measured Outer endpoints in the PG categories."""
    result = [copy.deepcopy(dict(row)) for row in observations]
    known = {str(row.get("observation_id") or "") for row in result}
    for row in rows:
        decision = row.get("outer_decision")
        ranking = row.get("candidate_ranking")
        params = row.get("params")
        resources = row.get("resource_means")
        if not all(isinstance(value, Mapping) for value in (
            decision, ranking, params, resources,
        )):
            continue
        if any(name not in params for name in active):
            continue
        try:
            qps_gain = float(decision["qps_gate_gain"])
            required_gain = float(decision.get("minimum_db_qps_gain_ratio", 0.0))
            target_drop = float(decision["target_drop_vs_reference"])
            utilization = {
                name: 100.0 * float(resources[name])
                for name in ("cpu", "memory", "io") if name != target
            }
        except (KeyError, TypeError, ValueError):
            continue
        if not all(math.isfinite(value) for value in (
            qps_gain, required_gain, target_drop, *utilization.values(),
        )):
            continue
        non_target_safe = not bool(
            decision.get("candidate_non_target_bottlenecks")
        ) and decision.get("hard_gate_passed") is True
        target_down = target_drop > 0.0
        resource_feasible = non_target_safe and target_down
        qps_passed = qps_gain > required_gain
        if resource_feasible and qps_passed:
            category = "resource_feasible_qps_passed"
        elif resource_feasible and 0.0 < qps_gain < required_gain:
            category = "safe_positive_qps_shortfall"
        elif qps_passed and not non_target_safe:
            category = "qps_pass_resource_unsafe"
        elif resource_feasible and not qps_passed:
            category = "resource_feasible_qps_below_gate"
        elif qps_gain > 0.0 and not resource_feasible:
            category = "positive_qps_resource_infeasible"
        else:
            category = "other_outer_observation"
        params_copy = {name: float(params[name]) for name in active}
        observation_id = _sha({
            "outer_attempt": int(outer_attempt),
            "params": params_copy,
            "rank": int(row.get("rank") or ranking.get("rank") or 0),
            "worker_id": str(row.get("worker_id") or ""),
        })
        if observation_id in known:
            continue
        known.add(observation_id)
        result.append({
            "contract": OUTER_BOUNDARY_CONTRACT,
            "observation_id": observation_id,
            "category": category,
            "outer_attempt": int(outer_attempt),
            "params": params_copy,
            "parameter_identity": alg.optimizer_params_key(params_copy),
            "qps_gate_gain": qps_gain,
            "required_qps_gain": required_gain,
            "qps_passed": qps_passed,
            "non_target_safe": non_target_safe,
            "target_down": target_down,
            "resource_feasible": resource_feasible,
            "non_target_utilization_pct": utilization,
            "non_target_threshold_pct": 80.0,
            "effective_non_target_threshold_pct": {
                name: 80.0 for name in utilization
            },
            "non_target_unsafe_excess_pct": max(
                [value - 80.0 for value in utilization.values()] or [0.0]
            ),
            "qps_used_for_inner_scoring": False,
            "qps_used_for_outer_interval_control": True,
            "external_baseline_information_used": False,
        })
    return result[-128:]


def _distance(
    left: Mapping[str, Any], right: Mapping[str, Any],
    ranges: Mapping[str, Mapping[str, Any]], active: Sequence[str],
) -> float:
    squared = []
    for name in active:
        low = math.log(float(ranges[name]["lower"]))
        high = math.log(float(ranges[name]["upper"]))
        span = max(high - low, 1e-12)
        squared.append(
            ((math.log(float(right[name])) - math.log(float(left[name]))) / span) ** 2
        )
    return math.sqrt(sum(squared))


def outer_boundary_pair(
    observations: Sequence[Mapping[str, Any]], *,
    ranges: Mapping[str, Mapping[str, Any]], active: Sequence[str],
) -> dict[str, Any] | None:
    safe_categories = {
        "safe_positive_qps_shortfall",
        "resource_feasible_qps_below_gate",
        "resource_feasible_qps_passed",
    }
    unsafe_categories = {
        "qps_pass_resource_unsafe",
        "positive_qps_resource_infeasible",
    }
    safe_rows = [row for row in observations if row.get("category") in safe_categories]
    unsafe_rows = [
        row for row in observations
        if row.get("category") in unsafe_categories
        and row.get("non_target_safe") is False
    ]
    candidates: list[tuple[tuple[float, ...], dict[str, Any]]] = []
    for safe in safe_rows:
        for unsafe in unsafe_rows:
            try:
                if float(unsafe["qps_gate_gain"]) <= float(safe["qps_gate_gain"]):
                    continue
                safe_params = safe["params"]
                unsafe_params = unsafe["params"]
                if not _inside(safe_params, ranges, active) or not _inside(
                    unsafe_params, ranges, active
                ):
                    continue
                distance = _distance(safe_params, unsafe_params, ranges, active)
                shortfall = max(
                    0.0,
                    float(safe["required_qps_gain"]) - float(safe["qps_gate_gain"]),
                )
                excess = max(0.0, float(unsafe["non_target_unsafe_excess_pct"]) / 100.0)
            except (KeyError, TypeError, ValueError):
                continue
            pair = {
                "safe": copy.deepcopy(dict(safe)),
                "unsafe": copy.deepcopy(dict(unsafe)),
                "normalized_parameter_distance": distance,
                "safe_qps_shortfall": shortfall,
                "unsafe_resource_excess_fraction": excess,
            }
            candidates.append(((-float(unsafe["qps_gate_gain"]), excess, distance, shortfall), pair))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    result = candidates[0][1]
    result["pair_id"] = _sha({
        "safe": result["safe"]["observation_id"],
        "unsafe": result["unsafe"]["observation_id"],
    })
    return result


def outer_boundary_vectors(
    pair: Mapping[str, Any], *, active: Sequence[str], limit: int,
    generated_ids: Sequence[str],
) -> tuple[list[dict[str, float]], list[str], dict[str, Any]]:
    fractions: list[float] = []
    safe = pair["safe"]
    unsafe = pair["unsafe"]
    boundary_fractions: list[float] = []
    for name, unsafe_value in unsafe["non_target_utilization_pct"].items():
        safe_value = safe["non_target_utilization_pct"].get(name)
        threshold = unsafe["effective_non_target_threshold_pct"].get(name, 80.0)
        try:
            span = float(unsafe_value) - float(safe_value)
            fraction = (float(threshold) - float(safe_value)) / span
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        if span > 0.0 and 0.0 < fraction < 1.0:
            boundary_fractions.append(fraction)
    if boundary_fractions:
        boundary = min(boundary_fractions)
        fractions.extend(
            min(0.999, max(0.001, boundary * multiplier))
            for multiplier in (0.50, 0.75, 0.90, 0.98, 1.02)
        )
    fractions.extend((1.0 / 3.0, 2.0 / 3.0, 0.5))
    for level in range(2, 14):
        denominator = 2 ** level
        fractions.extend(numerator / denominator for numerator in range(1, denominator, 2))
    generated = set(str(value) for value in generated_ids)
    vectors: list[dict[str, float]] = []
    rows: list[dict[str, Any]] = []
    for fraction in fractions:
        params = {
            name: math.exp(
                math.log(float(safe["params"][name]))
                + fraction * (
                    math.log(float(unsafe["params"][name]))
                    - math.log(float(safe["params"][name]))
                )
            )
            for name in active
        }
        identity = alg.optimizer_params_key(params)
        key = _sha({"pair_id": pair["pair_id"], "parameter_identity": identity})
        if key in generated:
            continue
        generated.add(key)
        vectors.append(params)
        rows.append({
            "parameter_identity": identity,
            "fraction_from_safe_to_unsafe": fraction,
            "generated_key": key,
        })
        if len(vectors) >= max(1, int(limit)):
            break
    return vectors, sorted(generated), {
        "contract": OUTER_BOUNDARY_CONTRACT,
        "pair_id": pair["pair_id"],
        "vectors": rows,
        "qps_used_for_inner_scoring": False,
        "qps_used_for_outer_interval_control": True,
    }


def _radical_inverse(index: int, base: int) -> float:
    factor = 1.0 / base
    result = 0.0
    while index:
        result += (index % base) * factor
        index //= base
        factor /= base
    return result


def progressive_numeric_vectors(
    *, incumbent: Mapping[str, Any], ranges: Mapping[str, Mapping[str, Any]],
    active: Sequence[str], window_index: int, count: int,
) -> tuple[list[dict[str, float]], dict[str, Any]]:
    radius = NUMERIC_RADII[min(int(window_index), len(NUMERIC_RADII) - 1)]
    primes = (2, 3, 5, 7, 11, 13, 17)
    centers = {
        name: (
            math.log(float(incumbent[name]) / float(ranges[name]["lower"]))
            / max(math.log(float(ranges[name]["upper"]) / float(ranges[name]["lower"])), 1e-12)
        )
        for name in active
    }
    vectors: list[dict[str, float]] = []
    seen: set[str] = {alg.optimizer_params_key(incumbent)}

    def add(fractions: Mapping[str, float]) -> None:
        params = {name: float(incumbent[name]) for name in active}
        for name in active:
            fraction = min(1.0, max(0.0, float(fractions.get(name, centers[name]))))
            lower = float(ranges[name]["lower"])
            upper = float(ranges[name]["upper"])
            low = math.log(lower)
            high = math.log(upper)
            params[name] = min(
                upper,
                max(lower, math.exp(low + fraction * (high - low))),
            )
        signature = alg.optimizer_params_key(params)
        if signature not in seen:
            seen.add(signature)
            vectors.append(params)

    axis_slots = 2 * len(active)
    for offset in range(min(count, axis_slots)):
        slot = (int(window_index) * count + offset) % axis_slots
        name = active[slot // 2]
        direction = -1.0 if slot % 2 == 0 else 1.0
        add({name: centers[name] + direction * radius})
    index = 1 + int(window_index) * max(1, count)
    attempts = 0
    while len(vectors) < count and attempts < max(1024, count * 128):
        add({
            name: centers[name] + radius * (2.0 * _radical_inverse(index, prime) - 1.0)
            for name, prime in zip(active, primes)
        })
        index += 1
        attempts += 1
    return vectors, {
        "contract": NUMERIC_REFINEMENT_CONTRACT,
        "window_index": int(window_index),
        "normalized_radius": radius,
        "vector_count": len(vectors),
        "qps_used_for_inner_scoring": False,
        "qps_used_for_outer_interval_control": False,
    }


def score_vectors(
    *, scene: str, vectors: Sequence[Mapping[str, Any]],
    incumbent: Mapping[str, Any], ranges: Mapping[str, Mapping[str, Any]],
    resources: Mapping[str, Any], freeze: Mapping[str, Any], worker: Mapping[str, Any],
    direction_mode: str,
) -> list[dict[str, Any]]:
    """Use the frozen MySQL C plan/resource model to rank explicit vectors."""
    document = alg.load(alg.TABLE_ROOT / alg.TABLES[scene])
    active = [str(name) for name in document["parameter_space"]["active_params"]]
    table = document["table"]
    target = alg.TARGET[scene]
    rows: list[dict[str, Any]] = []
    for probe_index, raw_params in enumerate(vectors, 1):
        params = {name: float(raw_params[name]) for name in active}
        predicted = {name: float(resources[name]) for name in ("cpu", "memory", "io")}
        changed: list[str] = []
        events: list[dict[str, Any]] = []
        fractions: dict[str, float] = {}
        for name in active:
            before, after = float(incumbent[name]), float(params[name])
            low, high = float(ranges[name]["lower"]), float(ranges[name]["upper"])
            span = math.log(high) - math.log(low)
            fractions[name] = 0.0 if span <= 0.0 else (math.log(after) - math.log(low)) / span
            if math.isclose(after, before, rel_tol=1e-12, abs_tol=1e-15):
                continue
            changed.append(name)
            delta = math.log(after / before)
            for resource in predicted:
                predicted[resource] = max(
                    0.000001, predicted[resource] + float(table[name][resource]) * delta,
                )
            events.append({
                "parameter": name,
                "before": before,
                "after": after,
                "gradient_effect": float(table[name][target]),
                "direction_mode": direction_mode,
                "boundary_fallback_applied": False,
            })
        if not changed:
            continue
        rows.append({
            "subset_id": direction_mode + "_" + "+".join(changed),
            "active_subset": changed,
            "candidate_step_fraction": max(abs(fractions[name] - 0.5) * 2.0 for name in active),
            "candidate_direction_mode": direction_mode,
            "coverage_probe_index": probe_index,
            "coverage_fractions": fractions,
            "optimized_params": params,
            "experience_prediction": predicted,
            "gradient_events": events,
            "global_plan_coverage_rescue": True,
            "global_plan_coverage_contract": coverage.SCHEMA_VERSION,
            "pg_recovery_contract": POLICY_ID,
            "qps_used_for_generation_or_ranking": False,
            "external_algorithm_evidence_used": False,
        })
    alg.apply_costs(worker, incumbent)
    baseline_plans = alg.capture_scene_plans(freeze, scene, worker)
    stats = alg.relation_stats(worker)
    executor = alg.executor_parameters(worker)
    try:
        for row in rows:
            effective = alg.apply_costs(worker, row["optimized_params"])
            candidate_plans = alg.capture_scene_plans(freeze, scene, worker)
            row["effective_optimizer_cost_readback"] = dict(effective)
            enhanced = alg.enhance_resource_prediction(
                incumbent_resources=resources,
                experience_prediction=row["experience_prediction"],
                baseline_plans=baseline_plans,
                candidate_plans=candidate_plans,
                relation_stats=stats,
                executor_parameters=executor,
                concurrency=alg.SCENE_CONCURRENCY[scene],
                cache_profile=alg.PLAN_CACHE_PROFILE,
            )
            predicted = enhanced["predicted_resources"]
            non_targets = [name for name in ("cpu", "memory", "io") if name != target]
            raw_score = (
                float(resources[target]) - float(predicted[target])
                + 0.25 * sum(
                    float(predicted[name]) - float(resources[name]) for name in non_targets
                )
            )
            row["predicted_resources"] = predicted
            row["candidate_score_before_confidence"] = raw_score
            row["candidate_score"] = alg.confidence_adjusted_score(
                raw_score, float(enhanced["confidence"]),
            )
            row["resource_model"] = enhanced
    finally:
        alg.apply_costs(worker, incumbent)
    rows.sort(key=lambda row: (
        -float(row["candidate_score"]),
        int(row["coverage_probe_index"]),
        alg.optimizer_params_key(row["optimized_params"]),
    ))
    for rank, row in enumerate(rows, 1):
        row["rank"] = rank
        row["raw_coverage_vector_count"] = len(vectors)
        row["unique_parameter_point_count"] = len(rows)
        row["equivalent_subset_ids"] = [row["subset_id"]]
        row["equivalent_active_subsets"] = [row["active_subset"]]
        row["equivalent_subset_candidate_scores"] = [{
            "subset_id": row["subset_id"], "candidate_score": row["candidate_score"],
        }]
        row["equivalent_subset_count"] = 1
        row["duplicate_parameter_points_explained_once"] = True
    return rows


def policy_contract() -> dict[str, Any]:
    return {
        "policy_id": POLICY_ID,
        "outer_boundary_refinement_contract": OUTER_BOUNDARY_CONTRACT,
        "maximum_outer_bracket_attempts_per_interval": 1,
        "numeric_refinement_contract": NUMERIC_REFINEMENT_CONTRACT,
        "numeric_window_radii": list(NUMERIC_RADII),
        "empty_admission_counts_as_physical_miss": False,
        "numeric_windows_exhausted_is_convergence": False,
        "qps_used_for_inner_scoring": False,
    }


__all__ = [
    "MAXIMUM_EMPTY_NUMERIC_WINDOWS", "NUMERIC_RADII", "POLICY_ID",
    "advance_outer_decision", "outer_boundary_pair", "outer_boundary_vectors",
    "policy_contract",
    "progressive_numeric_vectors", "record_outer_observations", "score_vectors",
]
