#!/usr/bin/env python3
"""Deterministic current-interval plan coverage for MySQL Algorithm C.

This is the MySQL counterpart of PostgreSQL Algorithm C's QPS-blind
``deterministic_global_coverage_vectors`` stage.  It generates sparse and
low-discrepancy points only from the current formal interval, scores them with
the frozen MySQL experience/plan-physics model, and never consumes QPS.
"""

from __future__ import annotations

import copy
import itertools
import json
import math
from typing import Any, Mapping, Sequence

from scripts import run_mysql_four_scene_algorithm_c_v2 as alg


SCHEMA_VERSION = "mysql_algorithm_c_current_interval_plan_coverage_v1"
DEFAULT_SAMPLE_COUNT = 256
FRACTIONS = (0.20, 0.35, 0.65, 0.80)
PAIR_FRACTIONS = (0.25, 0.75)
PRIMES = (2, 3, 5, 7, 11, 13, 17, 19)


def _value_at_fraction(low: float, high: float, fraction: float) -> float:
    if low <= 0.0 or high <= 0.0 or high < low:
        raise ValueError(f"invalid positive log range: {low}, {high}")
    bounded = min(1.0, max(0.0, float(fraction)))
    return float(f"{math.exp(math.log(low) + bounded * (math.log(high) - math.log(low))):.12g}")


def _radical_inverse(index: int, base: int) -> float:
    inverse = 1.0 / float(base)
    factor = inverse
    result = 0.0
    value = int(index)
    while value > 0:
        result += float(value % base) * factor
        value //= base
        factor *= inverse
    return result


def deterministic_current_interval_vectors(
    incumbent: Mapping[str, float],
    ranges: Mapping[str, Any],
    active: Sequence[str],
    *,
    sample_count: int = DEFAULT_SAMPLE_COUNT,
) -> list[dict[str, float]]:
    """Return the PG-aligned sparse plus Halton coverage design."""
    if sample_count < 5:
        raise ValueError("coverage design requires at least five points")
    names = [str(name) for name in active]
    if len(names) > len(PRIMES):
        raise ValueError("coverage design has insufficient Halton bases")
    center = {name: float(value) for name, value in incumbent.items()}
    center_key = alg.optimizer_params_key(center)
    vectors: list[dict[str, float]] = []
    signatures: set[str] = set()

    def add(fractions: Mapping[str, float]) -> None:
        params = copy.deepcopy(center)
        for name, fraction in fractions.items():
            bounds = ranges[name]
            params[name] = _value_at_fraction(
                float(bounds["lower"]), float(bounds["upper"]), fraction,
            )
        signature = alg.optimizer_params_key(params)
        if signature == center_key or signature in signatures:
            return
        signatures.add(signature)
        vectors.append(params)

    for name in names:
        for fraction in FRACTIONS:
            add({name: fraction})
    for left_index, left in enumerate(names):
        for right in names[left_index + 1:]:
            for left_fraction, right_fraction in itertools.product(
                PAIR_FRACTIONS, repeat=2,
            ):
                add({left: left_fraction, right: right_fraction})

    index = 1
    while len(vectors) < sample_count:
        add({
            name: _radical_inverse(index, PRIMES[position])
            for position, name in enumerate(names)
        })
        index += 1
        if index > sample_count * 20:
            raise RuntimeError("deterministic coverage design failed to fill")
    return vectors[:sample_count]


def candidate_rankings(
    scene: str,
    incumbent: Mapping[str, float],
    ranges: Mapping[str, Any],
    resources: Mapping[str, float],
    *,
    freeze: Mapping[str, Any],
    worker: Mapping[str, Any],
    sample_count: int = DEFAULT_SAMPLE_COUNT,
) -> list[dict[str, Any]]:
    """Score deterministic coverage points with frozen MySQL plan physics."""
    document = alg.load(alg.TABLE_ROOT / alg.TABLES[scene])
    active = [str(name) for name in document["parameter_space"]["active_params"]]
    table = document["table"]
    target = alg.TARGET[scene]
    vectors = deterministic_current_interval_vectors(
        incumbent, ranges, active, sample_count=sample_count,
    )
    rows: list[dict[str, Any]] = []
    for probe_index, params in enumerate(vectors, 1):
        predicted = {
            name: float(resources[name]) for name in ("cpu", "memory", "io")
        }
        changed: list[str] = []
        events: list[dict[str, Any]] = []
        fractions: dict[str, float] = {}
        for name in active:
            before = float(incumbent[name])
            after = float(params[name])
            bounds = ranges[name]
            low = float(bounds["lower"])
            high = float(bounds["upper"])
            span = math.log(high) - math.log(low)
            fraction = 0.0 if span <= 0.0 else (math.log(after) - math.log(low)) / span
            fractions[name] = fraction
            if math.isclose(after, before, rel_tol=1e-12, abs_tol=1e-15):
                continue
            changed.append(name)
            log_delta = math.log(after / before)
            for resource in predicted:
                predicted[resource] = max(
                    0.000001,
                    predicted[resource] + float(table[name][resource]) * log_delta,
                )
            events.append({
                "parameter": name,
                "before": before,
                "after": after,
                "gradient_effect": float(table[name][target]),
                "direction_mode": "deterministic_current_interval_coverage",
                "boundary_fallback_applied": False,
            })
        rows.append({
            "subset_id": "coverage_" + "+".join(changed),
            "active_subset": changed,
            "candidate_step_fraction": max(
                abs(fractions[name] - 0.5) * 2.0 for name in active
            ),
            "candidate_direction_mode": "deterministic_current_interval_coverage",
            "coverage_probe_index": probe_index,
            "coverage_fractions": fractions,
            "optimized_params": copy.deepcopy(params),
            "experience_prediction": predicted,
            "gradient_events": events,
            "global_plan_coverage_rescue": True,
            "global_plan_coverage_contract": SCHEMA_VERSION,
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
            row["optimizer_cost_readback_tolerance"] = {
                "mysql_column_type": "FLOAT",
                "relative": alg.MYSQL_FLOAT_READBACK_REL_TOL,
                "absolute": alg.MYSQL_FLOAT_READBACK_ABS_TOL,
            }
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
            non_targets = [
                name for name in ("cpu", "memory", "io") if name != target
            ]
            raw_score = (
                float(resources[target]) - float(predicted[target])
                + 0.25 * sum(
                    float(predicted[name]) - float(resources[name])
                    for name in non_targets
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
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        key = alg.optimizer_params_key(row["optimized_params"])
        if key in seen:
            continue
        seen.add(key)
        row["raw_coverage_vector_count"] = len(vectors)
        row["unique_parameter_point_count"] = len(rows)
        row["equivalent_subset_ids"] = [row["subset_id"]]
        row["equivalent_active_subsets"] = [row["active_subset"]]
        row["equivalent_subset_candidate_scores"] = [{
            "subset_id": row["subset_id"],
            "candidate_score": row["candidate_score"],
        }]
        row["equivalent_subset_count"] = 1
        row["duplicate_parameter_points_explained_once"] = True
        row["equivalent_subset_representative_policy"] = "coverage_score_then_probe_index"
        unique.append(row)
    for rank, row in enumerate(unique, 1):
        row["rank"] = rank
    return unique


def audit_document() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "sample_count": DEFAULT_SAMPLE_COUNT,
        "sparse_fractions": list(FRACTIONS),
        "pair_fractions": list(PAIR_FRACTIONS),
        "low_discrepancy_design": "halton",
        "current_formal_interval_only": True,
        "qps_used_for_generation_or_ranking": False,
        "external_algorithm_evidence_used": False,
    }
