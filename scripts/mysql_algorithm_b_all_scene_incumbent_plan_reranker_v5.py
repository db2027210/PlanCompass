#!/usr/bin/env python3
"""Use C-consistent incumbent-relative plan scoring for all B scenes.

Algorithm B keeps its independent fresh model and inner grid score.  Starting
with Outer 13, this outer-only reranker combines three global grid candidates
with two one-coordinate incumbent-neighborhood candidates.  Every candidate
is scored from the common plan-enhanced resource prediction using Algorithm
C's incumbent-relative, confidence-adjusted resource formula.  QPS is absent
from probe generation, prediction, gradient, and score.
"""

from __future__ import annotations

import copy
import math
from pathlib import Path
from typing import Any, Mapping

from scripts import (
    mysql_algorithm_b_tpch_full_suite_plan_ranker_fresh_v25_global_local as v4,
)


v3 = v4.v3
base = v4.base
EFFECTIVE_FROM_OUTER = 13
GLOBAL_TOP5_QUOTA = 3
LOCAL_TOP5_QUOTA = 2
LOCAL_FRACTIONS = v4.LOCAL_FRACTIONS
BASE_RANK_SCENE_OVERRIDE = None


def incumbent_relative_plan_score(
    *, target: str, incumbent_resources: Mapping[str, float],
    predicted_resources: Mapping[str, float], confidence: float,
) -> dict[str, float]:
    """Return the exact resource-only score formula used by Algorithm C."""
    if target not in {"cpu", "memory", "io"}:
        raise ValueError("unknown target resource")
    if not 0.0 < float(confidence) <= 1.0:
        raise ValueError("plan confidence must be in (0, 1]")
    incumbent = {
        name: float(incumbent_resources[name])
        for name in ("cpu", "memory", "io")
    }
    predicted = {
        name: float(predicted_resources[name])
        for name in ("cpu", "memory", "io")
    }
    if not all(math.isfinite(value) for value in [*incumbent.values(), *predicted.values()]):
        raise ValueError("plan resource score requires finite resources")
    non_targets = [name for name in ("cpu", "memory", "io") if name != target]
    raw_score = (
        incumbent[target] - predicted[target]
        + 0.25 * sum(
            predicted[name] - incumbent[name] for name in non_targets
        )
    )
    adjusted = base.alg.confidence_adjusted_score(raw_score, float(confidence))
    return {
        "raw_score": float(raw_score),
        "confidence": float(confidence),
        "score": float(adjusted),
    }


def local_probe_params(
    *, scene: str, params: Mapping[str, float], ranges: Mapping[str, Any],
    active: list[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name in active:
        lower = float(ranges[name]["lower"])
        upper = float(ranges[name]["upper"])
        current = float(params[name])
        if not lower <= current <= upper:
            raise RuntimeError(f"{scene} incumbent outside current range: {name}")
        for direction, boundary in (("lower", lower), ("upper", upper)):
            for fraction in LOCAL_FRACTIONS:
                candidate = {
                    str(key): float(value) for key, value in params.items()
                }
                candidate[name] = current + fraction * (boundary - current)
                rows.append({
                    "subset_id": (
                        f"{scene}_incumbent_local_{name}_{direction}_{fraction:g}"
                    ),
                    "active_subset": [name],
                    "optimized_params": candidate,
                    "normalized_step_fraction": float(fraction),
                    "direction": direction,
                    "incumbent_local_trust_region_probe": True,
                })
    return rows


def _sort_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        int(row.get("soft_negative_evidence_count") or 0),
        -float(row["incumbent_relative_plan_resource_score"]),
        int(row.get("experience_rank_before_full_plan") or row.get("rank") or 0),
        base.alg.optimizer_params_key(row["optimized_params"]),
    )


def select_global_and_local_top5(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    global_rows = sorted(
        [row for row in rows if row.get("incumbent_local_trust_region_probe") is not True],
        key=_sort_key,
    )
    local_rows = sorted(
        [row for row in rows if row.get("incumbent_local_trust_region_probe") is True],
        key=_sort_key,
    )
    if len(global_rows) < GLOBAL_TOP5_QUOTA or len(local_rows) < LOCAL_TOP5_QUOTA:
        raise RuntimeError("all-scene global/local Top-5 cardinality differs")
    selected = sorted(
        [
            *global_rows[:GLOBAL_TOP5_QUOTA],
            *local_rows[:LOCAL_TOP5_QUOTA],
        ],
        key=_sort_key,
    )
    for rank, row in enumerate(selected, 1):
        row["rank"] = rank
        row["global_local_top5_quota"] = {
            "global": GLOBAL_TOP5_QUOTA,
            "incumbent_local": LOCAL_TOP5_QUOTA,
            "qps_used": False,
            "candidate_hard_pruning": False,
        }
    return selected


def _apply_common_score(
    rows: list[dict[str, Any]], *, scene: str,
    incumbent_resources: Mapping[str, float],
) -> None:
    target = str(base.alg.TARGET[scene])
    for row in rows:
        model = dict(row.get("resource_model") or {})
        details = incumbent_relative_plan_score(
            target=target,
            incumbent_resources=incumbent_resources,
            predicted_resources=dict(model["predicted_resources"]),
            confidence=float(model["confidence"]),
        )
        row["incumbent_relative_plan_resource_score_before_confidence"] = details[
            "raw_score"
        ]
        row["incumbent_relative_plan_confidence"] = details["confidence"]
        row["incumbent_relative_plan_resource_score"] = details["score"]
        row["incumbent_relative_plan_score_formula"] = (
            "algorithm_c_target_drop_plus_0.25_non_target_delta_"
            "confidence_adjusted_v1"
        )
        row["qps_used_for_incumbent_relative_plan_score"] = False


def rank_scene(**kwargs: Any) -> dict[str, Any]:
    scene = str(kwargs["scene"])
    ranker = (
        BASE_RANK_SCENE_OVERRIDE
        if scene != v4.TPCH_SCENE and BASE_RANK_SCENE_OVERRIDE is not None
        else v4.rank_scene
    )
    result = ranker(**kwargs)
    outer = int(kwargs["round_index"])
    if outer < EFFECTIVE_FROM_OUTER:
        return result

    params = dict(kwargs["params"])
    ranges = dict(kwargs["ranges"])
    active = list(kwargs["active"])
    incumbent_resources = dict(kwargs["incumbent_resources"])
    replayed = set(kwargs["replayed"])
    soft_negative_counts = dict(kwargs["soft_negative_counts"])
    model = kwargs["model"]

    if scene == v4.TPCH_SCENE:
        rows = copy.deepcopy(list(result["plan_rerank_shortlist"]))
    else:
        rows = copy.deepcopy(list(result["selected_top5"]))
        for row in rows:
            row.setdefault("experience_rank_before_full_plan", int(row["rank"]))
            row.setdefault("incumbent_local_trust_region_probe", False)

    seen = {base.alg.optimizer_params_key(row["optimized_params"]) for row in rows}
    incumbent_key = base.alg.optimizer_params_key(params)
    worker = base.worker_with_plan_log(
        kwargs["worker"], Path(kwargs["plan_client_log"]),
    )
    local_rows: list[dict[str, Any]] = []
    for row in local_probe_params(
        scene=scene, params=params, ranges=ranges, active=active,
    ):
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
        local_rows.append(row)

    # TPC-H v4 already captured its local pool.  Other scenes need the same
    # common plan evidence for each newly introduced local candidate.
    if scene != v4.TPCH_SCENE:
        base.alg.apply_costs(worker, params)
        baseline_plans = v3.full_suite_capture_scene_plans(
            base.alg.load(base.FREEZE), scene, worker,
        )
        relation_stats = base.alg.relation_stats(worker)
        executor_parameters = base.alg.executor_parameters(worker)
        try:
            for offset, row in enumerate(local_rows, 1):
                readback = base.alg.apply_costs(worker, row["optimized_params"])
                candidate_plans = v3.full_suite_capture_scene_plans(
                    base.alg.load(base.FREEZE), scene, worker,
                )
                enhanced = base.alg.enhance_resource_prediction(
                    incumbent_resources=incumbent_resources,
                    experience_prediction=row["estimated_resources"],
                    baseline_plans=baseline_plans,
                    candidate_plans=candidate_plans,
                    relation_stats=relation_stats,
                    executor_parameters=executor_parameters,
                    concurrency=base.alg.SCENE_CONCURRENCY[scene],
                    cache_profile=base.alg.PLAN_CACHE_PROFILE,
                )
                row.update({
                    "experience_rank_before_full_plan": len(rows) + offset,
                    "effective_optimizer_cost_readback": readback,
                    "full_plan_evidence_required_before_real_replay": True,
                    "qps_used_for_resource_prediction_or_score": False,
                    "hard_pruned": False,
                    "resource_model": {
                        **enhanced,
                        "kind": (
                            "algorithm_b_online_model_all_scene_incumbent_"
                            "plan_rerank_v5"
                        ),
                        "qps_used_for_resource_prediction_or_score": False,
                        "candidate_hard_pruning": False,
                        "complete_full_plan_evidence": True,
                    },
                })
        finally:
            base.alg.apply_costs(worker, params)
        rows.extend(local_rows)

    _apply_common_score(
        rows, scene=scene, incumbent_resources=incumbent_resources,
    )
    selected = select_global_and_local_top5(rows)
    result.update({
        "plan_rerank_shortlist": rows,
        "plan_rerank_shortlist_size": len(rows),
        "selected_top5": selected,
        "all_scene_incumbent_plan_rerank_release": {
            "effective_from_outer": EFFECTIVE_FROM_OUTER,
            "score_formula": (
                "algorithm_c_target_drop_plus_0.25_non_target_delta_"
                "confidence_adjusted_v1"
            ),
            "plan_resource_estimator_shared_with_algorithm_c": True,
            "inner_candidate_score_modified": False,
            "qps_used": False,
            "candidate_hard_pruning": False,
            "top5_global_quota": GLOBAL_TOP5_QUOTA,
            "top5_local_quota": LOCAL_TOP5_QUOTA,
            "local_probe_fractions": list(LOCAL_FRACTIONS),
        },
    })
    return result


def install() -> None:
    v4.install()
    v4.v1.base.rank_scene = rank_scene


__all__ = [
    "EFFECTIVE_FROM_OUTER",
    "GLOBAL_TOP5_QUOTA",
    "LOCAL_TOP5_QUOTA",
    "incumbent_relative_plan_score",
    "install",
    "local_probe_params",
    "rank_scene",
    "select_global_and_local_top5",
]
