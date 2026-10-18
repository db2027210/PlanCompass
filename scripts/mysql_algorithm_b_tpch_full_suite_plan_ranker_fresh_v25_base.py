#!/usr/bin/env python3
"""Fresh v25 TPC-H full-suite plan-aware candidate ranking.

The predecessor records plan physics only after its experience model has
already selected the formal Top-5.  That makes the plan model audit-only and
cannot protect the real replay budget from Q9/Q22 plan regressions.  This
release keeps the same estimated grid, takes a deterministic Top-10
experience-model shortlist, captures Q1--Q22 for every shortlisted point, and
then selects the formal Top-5 by the resource-only plan-enhanced score.
"""

from __future__ import annotations

import copy
import math
import time
from pathlib import Path
from typing import Any, Mapping

from scripts import (
    run_mysql_four_scene_algorithm_b_independent_lane_failure_isolation_core_v18
    as base,
)


TPCH_SCENE = "tpch_sf4_memory"
TPCH_QUERY_IDS = tuple(range(1, 23))
PLAN_RERANK_SHORTLIST_SIZE = 10
EFFECTIVE_FROM_OUTER = 1
TPCH_QUALIFICATION_SQL_ROOT = base.ROOT / (
    "artifacts/mysql_tpch_retry21_collector_schema_renderer_successor_v1/"
    "54a1727673eeb53437b147003c97f8474c8668ca17db64c7ade4fe4f033b807f/"
    "target/0179951ba054c4f594900ff19b60e4dced5496f740eb036689b1630010bb6aaa/"
    "qualification/sql/tpch"
)
_ORIGINAL_RANK_SCENE = base.rank_scene
_ORIGINAL_CAPTURE_SCENE_PLANS = base.alg.capture_scene_plans


def tpch_full_suite_explains() -> dict[int, str]:
    """Load sealed literal-bound qualification SQL and render EXPLAIN SQL."""
    result: dict[int, str] = {}
    for query_id in TPCH_QUERY_IDS:
        path = TPCH_QUALIFICATION_SQL_ROOT / f"Q{query_id}.sql"
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"sealed TPC-H plan SQL absent: {path}")
        sql = path.read_text(encoding="utf-8").strip()
        if query_id == 15:
            sql = """WITH revenue0 AS (
                SELECT l_suppkey AS supplier_no,
                       SUM(l_extendedprice*(1-l_discount)) AS total_revenue
                FROM lineitem
                WHERE l_shipdate >= DATE '1993-11-01'
                  AND l_shipdate < DATE_ADD(DATE '1993-11-01', INTERVAL 3 MONTH)
                GROUP BY l_suppkey
            )
            SELECT s_suppkey,s_name,s_address,s_phone,total_revenue
            FROM supplier JOIN revenue0 ON s_suppkey=supplier_no
            WHERE total_revenue=(SELECT MAX(total_revenue) FROM revenue0)
            ORDER BY s_suppkey"""
        else:
            if sql.count(";") != 1 or not sql.endswith(";"):
                raise RuntimeError(f"TPC-H Q{query_id} qualification SQL differs")
            sql = sql[:-1].rstrip()
        result[query_id] = "EXPLAIN FORMAT=JSON " + sql
    return result


def full_suite_capture_scene_plans(
    freeze: Mapping[str, Any], scene: str, worker: Mapping[str, Any],
) -> dict[str, str]:
    """Capture all 22 TPC-H plans; retain predecessor behavior elsewhere."""
    if scene != TPCH_SCENE:
        return _ORIGINAL_CAPTURE_SCENE_PLANS(freeze, scene, worker)
    explains = tpch_full_suite_explains()
    plans = base.alg.explain(
        worker, [explains[query_id] for query_id in TPCH_QUERY_IDS],
    )
    if len(plans) != len(TPCH_QUERY_IDS):
        raise RuntimeError("TPC-H Q1-Q22 plan cardinality differs")
    return {
        f"tpch_{query_id:03d}": plan
        for query_id, plan in zip(TPCH_QUERY_IDS, plans, strict=True)
    }


def _resource_score(
    objective: Any, predicted: Mapping[str, float],
) -> float:
    resource = base.measurement_from_payload(
        predicted,
        {
            "resource": dict(predicted),
            "db_qps": float(objective.baseline.db_qps),
        },
    ).as_resource()
    score = float(objective.score(resource))
    if not math.isfinite(score):
        raise RuntimeError("TPC-H plan-enhanced Resource Score is non-finite")
    return score


def select_plan_reranked_top_k(
    rows: list[dict[str, Any]], *, top_k: int = base.FIXED_TOP_K,
) -> list[dict[str, Any]]:
    """Apply the frozen soft-negative then full-plan Resource Score order."""
    if top_k <= 0 or len(rows) < top_k:
        raise ValueError("plan-rerank Top-K cardinality differs")
    rows.sort(key=lambda row: (
        int(row["soft_negative_evidence_count"]),
        -float(row["full_suite_plan_resource_score"]),
        int(row["experience_rank_before_full_plan"]),
        base.alg.optimizer_params_key(row["optimized_params"]),
    ))
    selected = rows[:top_k]
    for rank, row in enumerate(selected, 1):
        row["rank"] = rank
    return selected


def rank_scene(
    *, scene: str, params: Mapping[str, float], ranges: Mapping[str, Any],
    active: list[str], model: Any, objective: Any,
    error_bounds: Mapping[str, Any],
    incumbent_resources: Mapping[str, float],
    worker: Mapping[str, Any], replayed: set[str], round_index: int,
    calibration_seed_points: list[Mapping[str, float]],
    plan_client_log: Path,
    soft_negative_counts: Mapping[str, int],
) -> dict[str, Any]:
    if scene != TPCH_SCENE or round_index < EFFECTIVE_FROM_OUTER:
        capture = base.alg.capture_scene_plans
        if scene == TPCH_SCENE:
            base.alg.capture_scene_plans = _ORIGINAL_CAPTURE_SCENE_PLANS
        try:
            return _ORIGINAL_RANK_SCENE(
                scene=scene, params=params, ranges=ranges, active=active,
                model=model, objective=objective, error_bounds=error_bounds,
                incumbent_resources=incumbent_resources, worker=worker,
                replayed=replayed, round_index=round_index,
                calibration_seed_points=calibration_seed_points,
                plan_client_log=plan_client_log,
                soft_negative_counts=soft_negative_counts,
            )
        finally:
            base.alg.capture_scene_plans = capture

    started = time.monotonic()
    worker = base.worker_with_plan_log(worker, plan_client_log)
    parameter_ranges = base.transformed_ranges(ranges, active)

    def no_real_replay(_sql_set: Any, _params: dict[str, float]) -> dict[str, Any]:
        raise RuntimeError("Algorithm B estimated ranking cannot perform real replay")

    optimizer = base.DoubleLoopGridOptimizer(
        model.predict, no_real_replay,
        {"cpu": 1.0, "memory": 1.0, "io": 1.0},
        candidate_seed_points=calibration_seed_points,
        grid_config=base.GridInnerLoopConfig(
            max_workers=8,
            max_passes=base.INNER_PASSES,
            no_improvement_patience=base.CONVERGENCE_PATIENCE,
            combination_probe_enabled=True,
            combination_min_size=base.COMBINATION_MIN_SIZE,
            combination_max_size=base.COMBINATION_MAX_SIZE,
            combination_values_per_param=base.COMBINATION_VALUES_PER_PARAMETER,
            combination_max_candidates=0,
        ),
        outer_config=base.OuterLoopConfig(
            max_rounds=base.TARGET_ACCEPTED_SHRINKS,
            real_validation_top_k=base.FIXED_TOP_K,
            real_validation_top_fraction=1.0,
            topk_controller="fixed",
            adaptive_topk_min_k=base.FIXED_TOP_K,
            adaptive_topk_batch_size=1,
            no_improvement_patience=0,
            epsilon=1e-4,
            target_resource=base.alg.TARGET[scene],
        ),
        estimated_score_func=objective.score,
        real_score_func=objective.score,
        resource_box_upper_bound_func=objective.optimistic_score_in_resource_box,
        initial_resource_error_bounds=dict(error_bounds),
        verbose=False,
    )
    points, grid_audit = optimizer._grid_small_loop(
        {"scene": scene}, dict(params), active, parameter_ranges,
        round_index=round_index, iteration_writer=None,
    )
    ranked = optimizer._deduplicate_and_rank(points)
    incumbent_key = base.alg.optimizer_params_key(params)
    usable = [
        point for point in ranked
        if base.alg.optimizer_params_key(point.params) != incumbent_key
        and base.alg.optimizer_params_key(point.params) not in replayed
    ]
    usable = [
        point for _, point in sorted(
            enumerate(usable),
            key=lambda pair: (
                int(soft_negative_counts.get(
                    base.alg.optimizer_params_key(pair[1].params), 0,
                )),
                pair[0],
            ),
        )
    ]
    if len(usable) < PLAN_RERANK_SHORTLIST_SIZE:
        raise RuntimeError("TPC-H independent B grid lacks plan-rerank Top-10")

    shortlist = usable[:PLAN_RERANK_SHORTLIST_SIZE]
    base.alg.apply_costs(worker, params)
    baseline_plans = full_suite_capture_scene_plans(
        base.alg.load(base.FREEZE), scene, worker,
    )
    relation_stats = base.alg.relation_stats(worker)
    executor_parameters = base.alg.executor_parameters(worker)
    plan_ranked: list[dict[str, Any]] = []
    try:
        for experience_rank, point in enumerate(shortlist, 1):
            optimized_params = {
                name: float(value) for name, value in point.params.items()
            }
            readback = base.alg.apply_costs(worker, optimized_params)
            candidate_plans = full_suite_capture_scene_plans(
                base.alg.load(base.FREEZE), scene, worker,
            )
            enhanced = base.alg.enhance_resource_prediction(
                incumbent_resources=incumbent_resources,
                experience_prediction={
                    name: float(point.estimated_resource[name])
                    for name in ("cpu", "memory", "io")
                },
                baseline_plans=baseline_plans,
                candidate_plans=candidate_plans,
                relation_stats=relation_stats,
                executor_parameters=executor_parameters,
                concurrency=base.alg.SCENE_CONCURRENCY[scene],
                cache_profile=base.alg.PLAN_CACHE_PROFILE,
            )
            plan_score = _resource_score(
                objective, enhanced["predicted_resources"],
            )
            plan_ranked.append({
                "experience_rank_before_full_plan": experience_rank,
                "subset_id": point.subset_id,
                "active_subset": list(point.active_subset),
                "optimized_params": optimized_params,
                "estimated_resources": {
                    name: float(point.estimated_resource[name])
                    for name in ("cpu", "memory", "io")
                },
                "estimated_score": float(point.estimated_score),
                "full_suite_plan_resource_score": plan_score,
                "effective_optimizer_cost_readback": readback,
                "qps_used_for_resource_prediction_or_score": False,
                "hard_pruned": False,
                "soft_negative_evidence_count": int(
                    soft_negative_counts.get(
                        base.alg.optimizer_params_key(point.params), 0,
                    )
                ),
                "full_plan_evidence_required_before_real_replay": True,
                "resource_model": {
                    **enhanced,
                    "kind": (
                        "algorithm_b_fresh_model_grid_with_tpch_q1_q22_plan_rerank_v1"
                    ),
                    "qps_used_for_resource_prediction_or_score": False,
                    "candidate_hard_pruning": False,
                    "complete_full_plan_evidence": True,
                    "tpch_query_plan_count": 22,
                    "plan_rerank_shortlist_size": PLAN_RERANK_SHORTLIST_SIZE,
                },
            })
    finally:
        base.alg.apply_costs(worker, params)

    selected = select_plan_reranked_top_k(plan_ranked)
    return {
        "scene": scene,
        "representative_worker_id": worker["worker_id"],
        "plan_client_log": str(plan_client_log),
        "elapsed_seconds": time.monotonic() - started,
        "grid_audit": copy.deepcopy(grid_audit),
        "candidate_count_before_top5": len(usable),
        "plan_rerank_shortlist_size": PLAN_RERANK_SHORTLIST_SIZE,
        "plan_rerank_shortlist": plan_ranked,
        "selected_top5": selected,
        "resource_error_bounds": copy.deepcopy(dict(error_bounds)),
        "qps_used_for_resource_prediction_gradient_or_score": False,
        "candidate_hard_pruning": False,
        "complete_plan_evidence_captured_before_real_replay": True,
        "tpch_full_suite_plan_rerank": True,
        "tpch_query_plan_count_per_candidate": 22,
    }


def install() -> None:
    base.alg.capture_scene_plans = full_suite_capture_scene_plans
    base.rank_scene = rank_scene
