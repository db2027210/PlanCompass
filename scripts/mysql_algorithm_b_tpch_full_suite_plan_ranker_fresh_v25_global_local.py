#!/usr/bin/env python3
"""Fresh v25 incumbent-neighborhood probes for TPC-H plan reranking.

The resource-only grid can rank only extreme parameter points in its global
Top-64 even after real Top-5 resource observations are learned.  For TPC-H,
those extremes repeatedly produced incomplete Q1--Q22 measurements while the
current accepted incumbent remained viable.  This release retains the global
shortlist and adds deterministic one-coordinate trust-region probes around the
incumbent before the same Q1--Q22 resource-only plan-risk rerank.  QPS is not
used for probe generation, resource prediction, gradient, or score.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping

from scripts import mysql_algorithm_b_tpch_full_suite_plan_ranker_fresh_v25_risk as v3


v1 = v3.v1
base = v1.base
TPCH_SCENE = v1.TPCH_SCENE
EFFECTIVE_FROM_OUTER = 1
LOCAL_FRACTIONS = (0.25, 0.5)
LOCAL_TOP5_QUOTA = 2


def local_probe_params(
    params: Mapping[str, float], ranges: Mapping[str, Any], active: list[str],
) -> list[dict[str, Any]]:
    """Return deterministic one-coordinate probes inside the current range."""
    rows: list[dict[str, Any]] = []
    for name in active:
        bounds = dict(ranges[name])
        lower = float(bounds["lower"])
        upper = float(bounds["upper"])
        current = float(params[name])
        if not lower <= current <= upper:
            raise RuntimeError(f"TPC-H incumbent outside current range: {name}")
        for direction, boundary in (("lower", lower), ("upper", upper)):
            for fraction in LOCAL_FRACTIONS:
                value = current + fraction * (boundary - current)
                candidate = {
                    str(key): float(raw) for key, raw in params.items()
                }
                candidate[name] = float(value)
                rows.append({
                    "subset_id": (
                        f"tpch_incumbent_local_{name}_{direction}_{fraction:g}"
                    ),
                    "active_subset": [name],
                    "optimized_params": candidate,
                    "normalized_step_fraction": float(fraction),
                    "direction": direction,
                })
    return rows


def select_global_and_local_top_k(
    rows: list[dict[str, Any]], *, top_k: int = base.FIXED_TOP_K,
    local_quota: int = LOCAL_TOP5_QUOTA,
) -> list[dict[str, Any]]:
    """Keep both search scales in Top-K using only resource/plan ordering."""
    if top_k <= 0 or local_quota <= 0 or local_quota >= top_k:
        raise ValueError("TPC-H global/local Top-K quota is invalid")
    if len(rows) < top_k:
        raise ValueError("TPC-H global/local Top-K cardinality differs")

    # Asking v3 for the complete ordered list applies exactly the same soft
    # plan-risk and negative-evidence ordering used by the predecessor.
    ordered = v3.select_plan_reranked_top_k(rows, top_k=len(rows))
    local = [
        row for row in ordered
        if row.get("incumbent_local_trust_region_probe") is True
    ]
    global_rows = [
        row for row in ordered
        if row.get("incumbent_local_trust_region_probe") is not True
    ]
    if len(local) < local_quota or len(global_rows) < top_k - local_quota:
        raise RuntimeError(
            "TPC-H global/local Top-K cannot satisfy frozen 3+2 composition"
        )
    chosen_ids = {
        id(row)
        for row in [
            *global_rows[: top_k - local_quota],
            *local[:local_quota],
        ]
    }
    selected = [row for row in ordered if id(row) in chosen_ids]
    if len(selected) != top_k:
        raise RuntimeError("TPC-H global/local Top-K selection differs")
    for rank, row in enumerate(selected, 1):
        row["rank"] = rank
        row["global_local_top5_quota"] = {
            "global": top_k - local_quota,
            "incumbent_local": local_quota,
            "qps_used": False,
            "candidate_hard_pruning": False,
        }
    return selected


def rank_scene(**kwargs: Any) -> dict[str, Any]:
    scene = str(kwargs["scene"])
    outer = int(kwargs["round_index"])
    result = v3.rank_scene(**kwargs)
    if scene != TPCH_SCENE or outer < EFFECTIVE_FROM_OUTER:
        return result

    params = dict(kwargs["params"])
    ranges = dict(kwargs["ranges"])
    active = list(kwargs["active"])
    model = kwargs["model"]
    objective = kwargs["objective"]
    worker = base.worker_with_plan_log(
        kwargs["worker"], Path(kwargs["plan_client_log"]),
    )
    replayed = set(kwargs["replayed"])
    soft_negative_counts = dict(kwargs["soft_negative_counts"])
    incumbent_resources = dict(kwargs["incumbent_resources"])
    existing = list(result["plan_rerank_shortlist"])
    seen = {
        base.alg.optimizer_params_key(row["optimized_params"])
        for row in existing
    }
    incumbent_key = base.alg.optimizer_params_key(params)

    probes = []
    for row in local_probe_params(params, ranges, active):
        key = base.alg.optimizer_params_key(row["optimized_params"])
        if key == incumbent_key or key in replayed or key in seen:
            continue
        seen.add(key)
        prediction = model.predict({"scene": scene}, row["optimized_params"])
        row["estimated_resources"] = {
            name: float(prediction["resource"][name])
            for name in ("cpu", "memory", "io")
        }
        row["estimated_score"] = float(prediction["score"])
        row["soft_negative_evidence_count"] = int(
            soft_negative_counts.get(key, 0)
        )
        probes.append(row)

    base.alg.apply_costs(worker, params)
    baseline_plans = v3.full_suite_capture_scene_plans(
        base.alg.load(base.FREEZE), scene, worker,
    )
    relation_stats = base.alg.relation_stats(worker)
    executor_parameters = base.alg.executor_parameters(worker)
    plan_rows: list[dict[str, Any]] = []
    try:
        for offset, probe in enumerate(probes, 1):
            optimized = dict(probe["optimized_params"])
            readback = base.alg.apply_costs(worker, optimized)
            candidate_plans = v3.full_suite_capture_scene_plans(
                base.alg.load(base.FREEZE), scene, worker,
            )
            enhanced = base.alg.enhance_resource_prediction(
                incumbent_resources=incumbent_resources,
                experience_prediction=probe["estimated_resources"],
                baseline_plans=baseline_plans,
                candidate_plans=candidate_plans,
                relation_stats=relation_stats,
                executor_parameters=executor_parameters,
                concurrency=base.alg.SCENE_CONCURRENCY[scene],
                cache_profile=base.alg.PLAN_CACHE_PROFILE,
            )
            plan_rows.append({
                "experience_rank_before_full_plan": len(existing) + offset,
                "subset_id": probe["subset_id"],
                "active_subset": list(probe["active_subset"]),
                "optimized_params": optimized,
                "estimated_resources": copy.deepcopy(
                    probe["estimated_resources"]
                ),
                "estimated_score": float(probe["estimated_score"]),
                "full_suite_plan_resource_score": v1._resource_score(
                    objective, enhanced["predicted_resources"],
                ),
                "effective_optimizer_cost_readback": readback,
                "qps_used_for_resource_prediction_or_score": False,
                "hard_pruned": False,
                "soft_negative_evidence_count": int(
                    probe["soft_negative_evidence_count"]
                ),
                "full_plan_evidence_required_before_real_replay": True,
                "incumbent_local_trust_region_probe": True,
                "normalized_step_fraction": probe["normalized_step_fraction"],
                "direction": probe["direction"],
                "resource_model": {
                    **enhanced,
                    "kind": (
                        "algorithm_b_online_model_tpch_local_trust_plan_rerank_v4"
                    ),
                    "qps_used_for_resource_prediction_or_score": False,
                    "candidate_hard_pruning": False,
                    "complete_full_plan_evidence": True,
                    "tpch_query_plan_count": 22,
                },
            })
    finally:
        base.alg.apply_costs(worker, params)

    combined = existing + plan_rows
    selected = select_global_and_local_top_k(combined)
    result.update({
        "plan_rerank_shortlist": combined,
        "plan_rerank_shortlist_size": len(combined),
        "selected_top5": selected,
        "tpch_incumbent_local_trust_region_release": {
            "effective_from_outer": EFFECTIVE_FROM_OUTER,
            "probe_count": len(plan_rows),
            "fractions": list(LOCAL_FRACTIONS),
            "top5_global_quota": base.FIXED_TOP_K - LOCAL_TOP5_QUOTA,
            "top5_local_quota": LOCAL_TOP5_QUOTA,
            "one_coordinate_at_a_time": True,
            "qps_used": False,
            "inner_candidate_score_modified": False,
            "global_shortlist_retained": True,
        },
    })
    return result


def install() -> None:
    v3.install()
    v1.base.alg.capture_scene_plans = v3.full_suite_capture_scene_plans
    v1.base.rank_scene = rank_scene


__all__ = [
    "EFFECTIVE_FROM_OUTER",
    "LOCAL_FRACTIONS",
    "LOCAL_TOP5_QUOTA",
    "install",
    "local_probe_params",
    "rank_scene",
    "select_global_and_local_top_k",
]
