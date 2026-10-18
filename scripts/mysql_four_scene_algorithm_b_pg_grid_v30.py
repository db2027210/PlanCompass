#!/usr/bin/env python3
"""MySQL Algorithm B inner loop aligned with the current PostgreSQL B grid.

This module changes candidate generation only.  It deliberately returns rows
in the shape consumed by the current MySQL Algorithm C outer controller, so
real Top-5 replay, acceptance tiers, rejection memory, interval shrink, and
convergence remain owned by that controller.

The search contract mirrors ``resource_adaptive/shell/run_pg_algorithm_b_scene.py``:

* deterministic 15-point coordinate grids;
* at most 20 coordinate passes, stopping after two passes without improvement;
* exhaustive interaction probes of size 2--4;
* three representative values per interaction dimension and no artificial cap.

Only MySQL optimizer-cost parameters are legal candidate dimensions.  QPS is
not read by the estimator and does not participate in candidate scoring.
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import sys
import threading
import time
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
PG_FRAMEWORK_ROOT = Path("/home/anonymous/program/resource_adaptive")
if str(ROOT) in sys.path:
    sys.path.remove(str(ROOT))
sys.path.insert(0, str(ROOT))
if str(PG_FRAMEWORK_ROOT) not in sys.path:
    sys.path.append(str(PG_FRAMEWORK_ROOT))

from double_loop_grid_optimizer import DoubleLoopGridOptimizer, GridInnerLoopConfig
from double_loop_search_common import OuterLoopConfig, ParameterRange
from scripts import run_mysql_four_scene_algorithm_c_v2 as alg


METHOD = "mysql_algorithm_b_pg_coordinate_grid_v30"
SEARCH_CONTRACT = "coordinate_grid15_plus_exhaustive_interactions_2_to_4_v1"
GRID_POINTS = 15
MAX_PASSES = 20
NO_IMPROVEMENT_PATIENCE = 2
COMBINATION_MIN_SIZE = 2
COMBINATION_MAX_SIZE = 4
COMBINATION_VALUES_PER_PARAMETER = 3
COMBINATION_MAX_CANDIDATES = 0
AUDIT_CANDIDATE_LIMIT = 64
REAL_TOP_K = 5

# This is the complete MySQL candidate universe.  PostgreSQL GUC names and
# arbitrary model features are rejected before any MySQL or workload access.
MYSQL_OPTIMIZER_COST_PARAMETERS = tuple(alg.DEFAULTS)

_round_lock = threading.Lock()
_round_by_scene = {scene: 0 for scene in alg.SCENES}


def search_contract() -> dict[str, Any]:
    """Return the immutable, machine-checkable inner-loop policy."""
    return {
        "method": METHOD,
        "search_contract": SEARCH_CONTRACT,
        "grid_points": GRID_POINTS,
        "max_coordinate_passes": MAX_PASSES,
        "no_improvement_patience": NO_IMPROVEMENT_PATIENCE,
        "interaction_min_size": COMBINATION_MIN_SIZE,
        "interaction_max_size": COMBINATION_MAX_SIZE,
        "interaction_values_per_parameter": COMBINATION_VALUES_PER_PARAMETER,
        "interaction_candidate_cap": COMBINATION_MAX_CANDIDATES,
        "audit_candidate_limit": AUDIT_CANDIDATE_LIMIT,
        "real_top_k_limit": REAL_TOP_K,
        "top_k_is_upper_bound": True,
        "partial_top_k_allowed": True,
        "duplicate_plan_padding_allowed": False,
        "baseline_padding_allowed": False,
        "candidate_parameter_universe": list(MYSQL_OPTIMIZER_COST_PARAMETERS),
        "qps_used_for_prediction_or_score": False,
        "physical_workload_invocations": 0,
    }


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def _load_table(scene: str) -> dict[str, Any]:
    if scene not in alg.SCENES:
        raise RuntimeError(f"unsupported MySQL scene: {scene}")
    return alg.load(alg.TABLE_ROOT / alg.TABLES[scene])


def _params_key(params: Mapping[str, Any]) -> str:
    return json.dumps(
        {str(name): float(value) for name, value in sorted(params.items())},
        sort_keys=True,
        separators=(",", ":"),
    )


def _same_params(one: Mapping[str, Any], two: Mapping[str, Any]) -> bool:
    return _params_key(one) == _params_key(two)


def _raw_resource_score(
    *, target: str, incumbent: Mapping[str, float], candidate: Mapping[str, float],
) -> float:
    """Use the current MySQL C candidate-score formula, without QPS."""
    non_targets = [name for name in ("cpu", "memory", "io") if name != target]
    return float(incumbent[target]) - float(candidate[target]) + 0.25 * sum(
        float(candidate[name]) - float(incumbent[name]) for name in non_targets
    )


def _validated_inputs(
    scene: str,
    incumbent: Mapping[str, float],
    ranges: Mapping[str, Any],
    resources: Mapping[str, float],
) -> tuple[
    dict[str, Any], list[str], dict[str, float],
    dict[str, ParameterRange], dict[str, float],
]:
    table_document = _load_table(scene)
    parameter_space = _mapping(
        table_document.get("parameter_space"), f"{scene} parameter space",
    )
    active = [str(name) for name in parameter_space.get("active_params") or []]
    table = _mapping(table_document.get("table"), f"{scene} experience table")
    allowed = set(MYSQL_OPTIMIZER_COST_PARAMETERS)
    supplied_params = {str(name): float(value) for name, value in incumbent.items()}
    supplied_ranges = _mapping(ranges, f"{scene} ranges")
    if (
        not active
        or len(active) != len(set(active))
        or any(name not in allowed for name in active)
        or any(name not in allowed for name in supplied_params)
        or set(supplied_ranges) != set(active)
        or set(table) != set(active)
        or any(name not in supplied_params for name in active)
    ):
        raise RuntimeError(
            f"{scene} candidate parameters are not the frozen MySQL-only set"
        )

    typed_ranges: dict[str, ParameterRange] = {}
    for priority, name in enumerate(active, 1):
        row = _mapping(supplied_ranges[name], f"{scene} {name} range")
        lower = float(row["lower"])
        upper = float(row["upper"])
        anchor = supplied_params[name]
        if (
            not all(math.isfinite(value) for value in (lower, upper, anchor))
            or lower <= 0.0
            or upper < lower
            or not lower <= anchor <= upper
            or row.get("transform", "log") != "log"
        ):
            raise RuntimeError(f"{scene} {name} positive log range differs")
        typed_ranges[name] = ParameterRange(
            lower,
            upper,
            discrete_count=GRID_POINTS,
            priority=priority,
            transform="log",
        )

    baseline_resources = {
        name: float(resources[name]) for name in ("cpu", "memory", "io")
    }
    if any(
        not math.isfinite(value) or value < 0.0
        for value in baseline_resources.values()
    ):
        raise RuntimeError(f"{scene} incumbent resource means differ")
    return table, active, supplied_params, typed_ranges, baseline_resources


def _plan_diverse_order(rows: Sequence[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Match PG B: first representative of every full plan, then refinements."""
    representatives: list[dict[str, Any]] = []
    refinements: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        plan = str((row.get("resource_model") or {}).get("candidate_plan_sha256") or "")
        if plan and plan not in seen:
            seen.add(plan)
            representatives.append(row)
        else:
            refinements.append(row)
    return representatives + refinements, len(seen)


def merge_candidate_rankings(
    pools: Sequence[Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    """Merge C-compatible pools without losing Algorithm B plan diversity.

    The C controller calls this hook before plan-history admission, including
    when it asks for boundary supplementation.  Using C's gradient-pool merge
    here would re-sort B rows by score alone and silently undo PG B's
    distinct-plan-first ordering.
    """
    best_by_params: dict[str, dict[str, Any]] = {}
    for pool in pools:
        for raw in pool:
            row = copy.deepcopy(dict(raw))
            key = _params_key(_mapping(
                row.get("optimized_params"), "candidate optimized params",
            ))
            previous = best_by_params.get(key)
            if previous is None or float(row["candidate_score"]) > float(
                previous["candidate_score"]
            ):
                best_by_params[key] = row
    ranked = sorted(best_by_params.values(), key=lambda row: (
        -float(row["candidate_score"]),
        str(row.get("subset_id") or ""),
        _params_key(row["optimized_params"]),
    ))
    ordered, _distinct_plan_count = _plan_diverse_order(ranked)
    for rank, row in enumerate(ordered, 1):
        row["rank"] = rank
    return ordered


def candidate_rankings(
    scene: str,
    incumbent: Mapping[str, float],
    ranges: Mapping[str, Any],
    resources: Mapping[str, float],
    *,
    freeze: Mapping[str, Any],
    worker: Mapping[str, Any],
    step_fraction: float = 0.08,
    direction_mode: str = "gradient",
) -> list[dict[str, Any]]:
    """Generate B rankings in the exact row shape expected by MySQL C.

    ``step_fraction`` and ``direction_mode`` are accepted only for interface
    compatibility with C's boundary-supplementation hook.  B has neither eta
    expansion nor a gradient direction; every call executes the same complete
    deterministic grid contract.
    """
    if not math.isfinite(step_fraction) or step_fraction <= 0.0:
        raise ValueError("positive finite compatibility step_fraction required")
    _ = direction_mode
    table, active, start, typed_ranges, baseline = _validated_inputs(
        scene, incumbent, ranges, resources,
    )
    target = alg.TARGET[scene]

    def estimate(_sql_set: Any, params: dict[str, float]) -> dict[str, Any]:
        predicted = dict(baseline)
        for name in active:
            before = float(start[name])
            after = float(params[name])
            if before <= 0.0 or after <= 0.0:
                raise RuntimeError("positive MySQL optimizer costs required")
            log_delta = math.log(after / before)
            effects = _mapping(table[name], f"{scene} {name} effects")
            for resource in ("cpu", "memory", "io"):
                predicted[resource] = max(
                    1e-9,
                    predicted[resource] + float(effects[resource]) * log_delta,
                )
        score = _raw_resource_score(
            target=target, incumbent=baseline, candidate=predicted,
        )
        return {
            "resource": predicted,
            "score": score,
            "estimated_score": score,
            "model": {
                "kind": "mysql_algorithm_b_experience_grid_prior_v30",
                "qps_read": False,
                "candidate_hard_pruning": False,
            },
        }

    def no_real_replay(_sql_set: Any, _params: dict[str, float]) -> dict[str, Any]:
        raise RuntimeError("Algorithm B inner grid cannot run a physical workload")

    optimizer = DoubleLoopGridOptimizer(
        estimate,
        no_real_replay,
        {"cpu": 1.0, "memory": 1.0, "io": 1.0},
        grid_config=GridInnerLoopConfig(
            max_workers=8,
            max_passes=MAX_PASSES,
            no_improvement_patience=NO_IMPROVEMENT_PATIENCE,
            combination_probe_enabled=True,
            combination_min_size=COMBINATION_MIN_SIZE,
            combination_max_size=COMBINATION_MAX_SIZE,
            combination_values_per_param=COMBINATION_VALUES_PER_PARAMETER,
            combination_max_candidates=COMBINATION_MAX_CANDIDATES,
        ),
        outer_config=OuterLoopConfig(
            max_rounds=1,
            real_validation_top_k=REAL_TOP_K,
            topk_controller="fixed",
            adaptive_topk_min_k=REAL_TOP_K,
            target_resource=target,
        ),
        verbose=False,
    )
    with _round_lock:
        _round_by_scene[scene] += 1
        round_index = _round_by_scene[scene]
    points, grid_audit = optimizer._grid_small_loop(
        {"contract": "read_only_full_workload_plan_set"},
        copy.deepcopy(start),
        active,
        typed_ranges,
        round_index=round_index,
        iteration_writer=None,
    )
    ranked_points = [
        point for point in optimizer._deduplicate_and_rank(points)
        if not point.failure_reason and not _same_params(point.params, start)
    ]
    if not ranked_points:
        return []

    # MySQL optimizer costs are global to one instance.  Plan capture therefore
    # stays serial on the scene's representative data plane, even though the
    # pure resource-estimation grid above is evaluated concurrently.
    alg.apply_costs(worker, start)
    baseline_plans = alg.capture_scene_plans(freeze, scene, worker)
    relation_stats = alg.relation_stats(worker)
    executor_parameters = alg.executor_parameters(worker)
    audited: list[dict[str, Any]] = []
    try:
        for point in ranked_points[:AUDIT_CANDIDATE_LIMIT]:
            params = {name: float(value) for name, value in point.params.items()}
            readback = alg.apply_costs(worker, params)
            candidate_plans = alg.capture_scene_plans(freeze, scene, worker)
            enhanced = alg.enhance_resource_prediction(
                incumbent_resources=baseline,
                experience_prediction=point.estimated_resource,
                baseline_plans=baseline_plans,
                candidate_plans=candidate_plans,
                relation_stats=relation_stats,
                executor_parameters=executor_parameters,
                concurrency=alg.SCENE_CONCURRENCY[scene],
                cache_profile=alg.PLAN_CACHE_PROFILE,
            )
            predicted = {
                name: float(enhanced["predicted_resources"][name])
                for name in ("cpu", "memory", "io")
            }
            raw_score = _raw_resource_score(
                target=target, incumbent=baseline, candidate=predicted,
            )
            audited.append({
                "subset_id": str(point.subset_id),
                "active_subset": list(point.active_subset),
                "optimized_params": params,
                "experience_prediction": copy.deepcopy(point.estimated_resource),
                "predicted_resources": predicted,
                "candidate_score_before_confidence": raw_score,
                "candidate_score": alg.confidence_adjusted_score(
                    raw_score, float(enhanced["confidence"]),
                ),
                "candidate_step_fraction": 0.0,
                "candidate_direction_mode": "algorithm_b_coordinate_grid",
                "effective_optimizer_cost_readback": readback,
                "optimizer_cost_readback_tolerance": {
                    "mysql_column_type": "FLOAT",
                    "relative": alg.MYSQL_FLOAT_READBACK_REL_TOL,
                    "absolute": alg.MYSQL_FLOAT_READBACK_ABS_TOL,
                },
                "resource_model": {
                    **copy.deepcopy(enhanced),
                    "kind": "mysql_algorithm_b_pg_grid_plus_contextual_plan_physics_v30",
                    "grid_estimated_resources": copy.deepcopy(point.estimated_resource),
                    "grid_estimated_score": float(point.estimated_score),
                    "qps_used_for_resource_prediction_or_score": False,
                    "candidate_hard_pruning": False,
                    "complete_full_plan_evidence": True,
                },
                "algorithm_b_grid_audit": copy.deepcopy(grid_audit),
                "algorithm_b_search_contract": SEARCH_CONTRACT,
                "algorithm_b_non_gradient_search": True,
                "qps_used_for_generation_or_ranking": False,
            })
    finally:
        alg.apply_costs(worker, start)

    audited.sort(key=lambda row: (
        -float(row["candidate_score"]),
        str(row["subset_id"]),
        _params_key(row["optimized_params"]),
    ))
    ordered, distinct_plan_count = _plan_diverse_order(audited)
    plan_counts: dict[str, int] = {}
    for row in ordered:
        plan = str((row.get("resource_model") or {}).get("candidate_plan_sha256") or "")
        plan_counts[plan] = plan_counts.get(plan, 0) + 1
    for rank, row in enumerate(ordered, 1):
        plan = str((row.get("resource_model") or {}).get("candidate_plan_sha256") or "")
        row["rank"] = rank
        row["numeric_refinement"] = bool(plan and plan_counts.get(plan, 0) > 1)
        row["offline_grid_candidate_count"] = len(ranked_points)
        row["audited_candidate_count"] = len(audited)
        row["offline_distinct_plan_count"] = distinct_plan_count
    return ordered


def rank_scene(
    *,
    scene: str,
    params: Mapping[str, float],
    ranges: Mapping[str, Any],
    resources: Mapping[str, float],
    freeze: Mapping[str, Any],
    worker: Mapping[str, Any],
    step_fraction: float = 0.08,
) -> dict[str, Any]:
    """Drop-in replacement for the MySQL C scene-ranking hook."""
    started = time.monotonic()
    rankings = candidate_rankings(
        scene,
        params,
        ranges,
        resources,
        freeze=freeze,
        worker=worker,
        step_fraction=step_fraction,
    )
    return {
        "scene": scene,
        "representative_worker_id": worker["worker_id"],
        "candidate_step_fraction": None,
        "elapsed_seconds": time.monotonic() - started,
        "rankings": rankings,
        "algorithm_b_inner_loop_contract": search_contract(),
    }


__all__ = [
    "MYSQL_OPTIMIZER_COST_PARAMETERS",
    "candidate_rankings",
    "merge_candidate_rankings",
    "rank_scene",
    "search_contract",
]
