#!/usr/bin/env python3
"""PostgreSQL-equivalent exhaustive L/R candidate construction for MySQL A."""

from __future__ import annotations

import copy
import itertools
import math
from typing import Any, Mapping, Sequence


METHOD = "exhaustive_binary_factorial_algorithm_a_v2"
SEARCH_CONTRACT = "lane_local_complete_lr_cartesian_physical_attempt_v2"


def interval_pair_points(low: float, high: float) -> tuple[float, float]:
    """Match PostgreSQL Algorithm A's log/linear quarter-point rule."""

    low = float(low)
    high = float(high)
    if not math.isfinite(low) or not math.isfinite(high) or low < 0 or high <= low:
        raise ValueError(f"invalid Algorithm A interval: {low}..{high}")
    if low > 0:
        log_low = math.log(low)
        log_width = math.log(high) - log_low
        return (
            math.exp(log_low + 0.25 * log_width),
            math.exp(log_low + 0.75 * log_width),
        )
    width = high - low
    return low + 0.25 * width, low + 0.75 * width


def full_factorial_parameter_vectors(
    ranges: Mapping[str, Mapping[str, Any]],
    active_parameters: Sequence[str],
    incumbent: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return the complete deterministic lane-local MySQL L/R product."""

    names = tuple(str(name) for name in active_parameters)
    if not names or len(set(names)) != len(names):
        raise ValueError("Algorithm A needs unique active parameters")
    if any(name not in ranges or name not in incumbent for name in names):
        raise KeyError("Algorithm A range/incumbent is missing an active parameter")
    levels: dict[str, tuple[float, float]] = {}
    for name in names:
        specification = ranges[name]
        if not isinstance(specification, Mapping):
            raise TypeError(f"Algorithm A range must be an object: {name}")
        levels[name] = interval_pair_points(
            float(specification["lower"]), float(specification["upper"])
        )

    rows: list[dict[str, Any]] = []
    for mask, sides in enumerate(itertools.product((0, 1), repeat=len(names))):
        params = {str(name): float(value) for name, value in incumbent.items()}
        labels: dict[str, str] = {}
        for name, side in zip(names, sides, strict=True):
            # Match the MySQL cost-table precision boundary used by B/C.
            params[name] = float(f"{levels[name][side]:.12g}")
            labels[name] = "R" if side else "L"
        rows.append({
            "rank": mask + 1,
            "algorithm_a_factor_mask": mask,
            "algorithm_a_factor_levels": labels,
            "active_subset": list(names),
            "optimized_params": params,
            "candidate_origin": "algorithm_a_lane_local_full_factorial",
            "probe_kind": "complete_lr_cartesian_product",
            "prediction_used_for_selection": False,
            "prediction_used_for_ordering": False,
            "qps_used_for_inner_scoring": False,
        })

    expected = 1 << len(names)
    signatures = {
        tuple(format(float(row["optimized_params"][name]), ".17g") for name in names)
        for row in rows
    }
    if len(rows) != expected or len(signatures) != expected:
        raise RuntimeError("Algorithm A Cartesian product is incomplete or non-unique")
    return rows


def physical_waves(candidate_count: int, parallelism: int = 2) -> list[list[int]]:
    """Return global-rank waves without filtering, ordering, or truncation."""

    if candidate_count < 1 or parallelism < 1:
        raise ValueError("candidate count and parallelism must be positive")
    return [
        list(range(start, min(candidate_count, start + parallelism - 1) + 1))
        for start in range(1, candidate_count + 1, parallelism)
    ]


def attach_mysql_prediction_audit(
    scene: str,
    candidates: Sequence[Mapping[str, Any]],
    incumbent: Mapping[str, Any],
    incumbent_resources: Mapping[str, Any],
    *,
    freeze: Mapping[str, Any],
    worker: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Attach MySQL plan-physics estimates without selecting or ordering A.

    PostgreSQL Algorithm A computes a prediction for every factorial point but
    treats it as audit metadata.  This function preserves that flow while
    using the MySQL plan/resource estimator and MySQL optimizer-cost readback.
    Canonical factorial rank is never changed.
    """

    from scripts import run_mysql_four_scene_algorithm_c_v2 as mysql

    if scene not in mysql.SCENES:
        raise ValueError(f"unsupported MySQL Algorithm A scene: {scene}")
    table_document = mysql.load(mysql.TABLE_ROOT / mysql.TABLES[scene])
    table = table_document["table"]
    target = mysql.TARGET[scene]
    baseline_resources = {
        name: float(incumbent_resources[name])
        for name in ("cpu", "memory", "io")
    }
    baseline_params = {
        name: float(value) for name, value in incumbent.items()
    }
    mysql.apply_costs(worker, baseline_params)
    baseline_plans = mysql.capture_scene_plans(freeze, scene, worker)
    stats = mysql.relation_stats(worker)
    executor = mysql.executor_parameters(worker)
    audited: list[dict[str, Any]] = []
    try:
        for expected_rank, raw in enumerate(candidates, start=1):
            row = copy.deepcopy(dict(raw))
            if int(row.get("rank") or 0) != expected_rank:
                raise RuntimeError("Algorithm A candidate order is not canonical")
            params = {
                name: float(value)
                for name, value in (row.get("optimized_params") or {}).items()
            }
            experience = copy.deepcopy(baseline_resources)
            for name in row.get("active_subset") or ():
                before = float(baseline_params[name])
                after = float(params[name])
                if before <= 0.0 or after <= 0.0:
                    raise ValueError("MySQL Algorithm A requires positive log costs")
                log_delta = math.log(after / before)
                for resource in experience:
                    experience[resource] = max(
                        0.000001,
                        experience[resource]
                        + float(table[name][resource]) * log_delta,
                    )
            effective = mysql.apply_costs(worker, params)
            candidate_plans = mysql.capture_scene_plans(freeze, scene, worker)
            enhanced = mysql.enhance_resource_prediction(
                incumbent_resources=baseline_resources,
                experience_prediction=experience,
                baseline_plans=baseline_plans,
                candidate_plans=candidate_plans,
                relation_stats=stats,
                executor_parameters=executor,
                concurrency=mysql.SCENE_CONCURRENCY[scene],
                cache_profile=mysql.PLAN_CACHE_PROFILE,
            )
            predicted = enhanced["predicted_resources"]
            non_targets = [
                name for name in ("cpu", "memory", "io") if name != target
            ]
            raw_score = (
                baseline_resources[target] - float(predicted[target])
                + 0.25 * sum(
                    float(predicted[name]) - baseline_resources[name]
                    for name in non_targets
                )
            )
            row.update({
                "experience_prediction": experience,
                "predicted_resources": predicted,
                "candidate_score_before_confidence": raw_score,
                "candidate_score": mysql.confidence_adjusted_score(
                    raw_score, float(enhanced["confidence"])
                ),
                "resource_model": enhanced,
                "effective_optimizer_cost_readback": effective,
                "optimizer_cost_readback_tolerance": {
                    "mysql_column_type": "FLOAT",
                    "relative": mysql.MYSQL_FLOAT_READBACK_REL_TOL,
                    "absolute": mysql.MYSQL_FLOAT_READBACK_ABS_TOL,
                },
                "prediction_role": "mysql_plan_physics_audit_only",
                "prediction_used_for_selection": False,
                "prediction_used_for_ordering": False,
                "qps_used_for_inner_scoring": False,
            })
            audited.append(row)
    finally:
        mysql.apply_costs(worker, baseline_params)
    if [int(row["rank"]) for row in audited] != list(
        range(1, len(audited) + 1)
    ):
        raise RuntimeError("MySQL prediction audit changed Algorithm A order")
    return audited
