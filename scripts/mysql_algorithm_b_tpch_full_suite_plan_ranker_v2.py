#!/usr/bin/env python3
"""TPC-H full-suite plan reranking with soft extreme-plan protection.

The v1 Top-10 shortlist could contain ten variants of the same catastrophic
plan family.  Its physics prediction deliberately capped directional changes
at roughly five percent, so a twelve-fold raw memory-work increase did not
meaningfully affect ordering.  This release expands only the read-only EXPLAIN
shortlist and applies an uncapped, resource-only *soft* plan-risk discount.
No candidate is hard-pruned and QPS remains absent from prediction and score.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

from scripts import mysql_algorithm_b_tpch_full_suite_plan_ranker_v1 as v1


PLAN_RERANK_SHORTLIST_SIZE = 64
EFFECTIVE_FROM_OUTER = 7
_PREDECESSOR_SHORTLIST_SIZE = v1.PLAN_RERANK_SHORTLIST_SIZE
_PREDECESSOR_SELECT = v1.select_plan_reranked_top_k
_PREDECESSOR_RANK_SCENE = v1.rank_scene
_PREDECESSOR_FULL_SUITE_CAPTURE = v1.full_suite_capture_scene_plans


def batched_explain(worker: dict[str, Any], sqls: list[str]) -> list[str]:
    """Capture a plan set in one pinned mysql client, with strict sentinels."""
    v1.base.alg.private_credential(worker)
    defaults = Path(str(worker["benchmark_defaults"]))
    runner = v1.base.alg.r0mod.PinnedRunner(
        worker, Path(worker["formal_client_log"]),
    )
    argv = [
        "mysql", f"--defaults-file={defaults}", "--batch", "--raw",
        "--skip-column-names", "--get-server-public-key",
        "--ssl-mode=DISABLED", "-h", "127.0.0.1", "-P",
        str(worker["host_port"]), str(worker["database"]),
    ]
    markers = [f"__RA_PLAN_END_{index:03d}__" for index in range(1, len(sqls) + 1)]
    payload_parts: list[str] = []
    for sql, marker in zip(sqls, markers, strict=True):
        payload_parts.extend([
            sql.rstrip().removesuffix(";") + ";\n",
            f"SELECT '{marker}';\n",
        ])
    completed = runner(
        argv, input="".join(payload_parts), env=dict(os.environ), text=True,
        capture_output=True, timeout=900, check=False,
    )
    if completed.returncode:
        error = (completed.stderr or "batched EXPLAIN failed").strip()[-1000:]
        raise RuntimeError(
            f"{worker['worker_id']} batched EXPLAIN failed: "
            f"rc={completed.returncode} stderr={error!r}"
        )
    plans: list[str] = []
    current: list[str] = []
    expected = 0
    for line in completed.stdout.splitlines():
        if expected < len(markers) and line.strip() == markers[expected]:
            plan = "\n".join(current).strip()
            if not plan:
                raise RuntimeError(
                    f"{worker['worker_id']} batched EXPLAIN #{expected + 1} empty"
                )
            plans.append(plan)
            current = []
            expected += 1
        else:
            current.append(line)
    if expected != len(markers) or any(line.strip() for line in current):
        raise RuntimeError(
            f"{worker['worker_id']} batched EXPLAIN sentinel cardinality differs: "
            f"expected={len(markers)} observed={expected}"
        )
    return plans


def full_suite_capture_scene_plans(
    freeze: dict[str, Any], scene: str, worker: dict[str, Any],
) -> dict[str, str]:
    if scene != v1.TPCH_SCENE:
        return _PREDECESSOR_FULL_SUITE_CAPTURE(freeze, scene, worker)
    explains = v1.tpch_full_suite_explains()
    plans = batched_explain(
        worker, [explains[query_id] for query_id in v1.TPCH_QUERY_IDS],
    )
    if len(plans) != len(v1.TPCH_QUERY_IDS):
        raise RuntimeError("TPC-H batched Q1-Q22 plan cardinality differs")
    return {
        f"tpch_{query_id:03d}": plan
        for query_id, plan in zip(v1.TPCH_QUERY_IDS, plans, strict=True)
    }


def _raw_plan_risk(row: dict[str, Any]) -> float:
    ratios = (row.get("resource_model") or {}).get("raw_plan_work_ratios") or {}
    finite = [
        max(1.0, float(ratios.get(name, 1.0)))
        for name in ("cpu", "memory", "io")
        if math.isfinite(float(ratios.get(name, 1.0)))
    ]
    return max(finite, default=1.0)


def select_plan_reranked_top_k(
    rows: list[dict[str, Any]], *, top_k: int = v1.base.FIXED_TOP_K,
) -> list[dict[str, Any]]:
    """Rank by soft negatives then resource score discounted by plan risk."""
    if top_k <= 0 or len(rows) < top_k:
        raise ValueError("plan-rerank Top-K cardinality differs")
    for row in rows:
        risk = _raw_plan_risk(row)
        score = float(row["full_suite_plan_resource_score"])
        # Square-root damping keeps the signal soft: an extreme candidate can
        # still win when its resource score is sufficiently better.
        row["raw_full_suite_plan_risk_ratio"] = risk
        row["plan_risk_soft_discount"] = math.sqrt(risk)
        row["plan_risk_adjusted_resource_score"] = score / math.sqrt(risk)
        row["candidate_hard_pruned_by_plan_risk"] = False
    rows.sort(key=lambda row: (
        int(row["soft_negative_evidence_count"]),
        -float(row["plan_risk_adjusted_resource_score"]),
        int(row["experience_rank_before_full_plan"]),
        v1.base.alg.optimizer_params_key(row["optimized_params"]),
    ))
    selected = rows[:top_k]
    for rank, row in enumerate(selected, 1):
        row["rank"] = rank
    return selected


def rank_scene(**kwargs: Any) -> dict[str, Any]:
    """Preserve v1 identity through Outer 6; activate v2 at Outer 7."""
    if kwargs.get("scene") != v1.TPCH_SCENE:
        return _PREDECESSOR_RANK_SCENE(**kwargs)
    use_v2 = int(kwargs["round_index"]) >= EFFECTIVE_FROM_OUTER
    old_size = v1.PLAN_RERANK_SHORTLIST_SIZE
    old_select = v1.select_plan_reranked_top_k
    old_capture = v1.full_suite_capture_scene_plans
    v1.PLAN_RERANK_SHORTLIST_SIZE = (
        PLAN_RERANK_SHORTLIST_SIZE if use_v2 else _PREDECESSOR_SHORTLIST_SIZE
    )
    v1.select_plan_reranked_top_k = (
        select_plan_reranked_top_k if use_v2 else _PREDECESSOR_SELECT
    )
    v1.full_suite_capture_scene_plans = (
        full_suite_capture_scene_plans if use_v2 else _PREDECESSOR_FULL_SUITE_CAPTURE
    )
    try:
        result = _PREDECESSOR_RANK_SCENE(**kwargs)
    finally:
        v1.PLAN_RERANK_SHORTLIST_SIZE = old_size
        v1.select_plan_reranked_top_k = old_select
        v1.full_suite_capture_scene_plans = old_capture
    result["tpch_soft_plan_risk_release"] = {
        "effective_from_outer": EFFECTIVE_FROM_OUTER,
        "applied": use_v2,
    }
    return result


def install() -> None:
    v1.install()
    v1.base.alg.capture_scene_plans = full_suite_capture_scene_plans
    v1.base.rank_scene = rank_scene


__all__ = [
    "PLAN_RERANK_SHORTLIST_SIZE",
    "EFFECTIVE_FROM_OUTER",
    "batched_explain",
    "full_suite_capture_scene_plans",
    "install",
    "rank_scene",
    "select_plan_reranked_top_k",
]
