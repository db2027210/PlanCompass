#!/usr/bin/env python3
"""Select B's real Top-5 from a supported, diverse trust region.

The independent Algorithm-B grid and its resource-only inner score remain
unchanged.  This outer replay policy removes the predecessor's mandatory
three-global/two-local composition.  Four distinct incumbent-neighbourhood
directions are reserved first; the fifth replay may be a grid point only when
that point is already close to dynamic real support.  Unsupported grid points
remain in the ranked audit pool and are never hard-pruned.

Neighbourhood probes move 8% or 16% of the logarithmic distance from the
current incumbent to one parameter boundary.  QPS is absent from generation,
prediction, ranking, and selection.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from scripts import mysql_algorithm_b_all_scene_incumbent_plan_reranker_v8 as v8


base = v8.base
v4 = v8.v4
EFFECTIVE_FROM_OUTER = 3
LOCAL_LOG_FRACTIONS = (0.08, 0.16)
MINIMUM_DIVERSE_LOCAL_DIRECTIONS = 4
MAXIMUM_GLOBAL_TOP5 = 1
TRUSTED_GLOBAL_SUPPORT_WEIGHT = 0.5


def local_probe_params(
    *, scene: str, params: Mapping[str, float], ranges: Mapping[str, Any],
    active: list[str],
) -> list[dict[str, Any]]:
    """Generate deterministic one-coordinate probes in normalized log space."""
    rows: list[dict[str, Any]] = []
    for name in active:
        lower = float(ranges[name]["lower"])
        upper = float(ranges[name]["upper"])
        current = float(params[name])
        if min(lower, upper, current) <= 0.0 or not lower <= current <= upper:
            raise RuntimeError(f"{scene} incumbent/range differs: {name}")
        for direction, boundary in (("lower", lower), ("upper", upper)):
            for fraction in LOCAL_LOG_FRACTIONS:
                value = math.exp(
                    math.log(current)
                    + fraction * (math.log(boundary) - math.log(current))
                )
                candidate = {
                    str(key): float(raw) for key, raw in params.items()
                }
                candidate[name] = float(f"{value:.12g}")
                rows.append({
                    "subset_id": (
                        f"{scene}_incumbent_log_local_"
                        f"{name}_{direction}_{fraction:g}"
                    ),
                    "active_subset": [name],
                    "optimized_params": candidate,
                    "normalized_step_fraction": float(fraction),
                    "direction": direction,
                    "incumbent_local_trust_region_probe": True,
                    "incumbent_log_trust_region_probe": True,
                })
    return rows


def tpch_local_probe_params(
    params: Mapping[str, float], ranges: Mapping[str, Any], active: list[str],
) -> list[dict[str, Any]]:
    return local_probe_params(
        scene=v4.TPCH_SCENE, params=params, ranges=ranges, active=active,
    )


def _subset_key(row: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(sorted(str(name) for name in row.get("active_subset") or []))


def select_trust_aware_diverse_top5(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Choose 4 diverse locals plus one local or already-supported global."""
    local_rows = sorted(
        [
            row for row in rows
            if row.get("incumbent_local_trust_region_probe") is True
        ],
        key=v8._sort_key,
    )
    global_rows = sorted(
        [
            row for row in rows
            if row.get("incumbent_local_trust_region_probe") is not True
        ],
        key=v8._sort_key,
    )
    if len(local_rows) < 5:
        raise RuntimeError("trust-diverse Top-5 requires five local probes")

    selected: list[dict[str, Any]] = []
    selected_ids: set[int] = set()
    selected_subsets: set[tuple[str, ...]] = set()
    for row in local_rows:
        subset = _subset_key(row)
        if not subset or subset in selected_subsets:
            continue
        selected.append(row)
        selected_ids.add(id(row))
        selected_subsets.add(subset)
        if len(selected) == MINIMUM_DIVERSE_LOCAL_DIRECTIONS:
            break
    if len(selected) != MINIMUM_DIVERSE_LOCAL_DIRECTIONS:
        raise RuntimeError("trust-diverse Top-5 lacks four local directions")

    remaining_local = [row for row in local_rows if id(row) not in selected_ids]
    trusted_global = [
        row for row in global_rows
        if float(row.get("dynamic_support_weight", 0.0))
        >= TRUSTED_GLOBAL_SUPPORT_WEIGHT
    ]
    fifth_pool = sorted([*remaining_local, *trusted_global], key=v8._sort_key)
    if not fifth_pool:
        raise RuntimeError("trust-diverse Top-5 lacks a fifth supported point")
    selected.append(fifth_pool[0])
    selected = sorted(selected, key=v8._sort_key)

    global_selected = sum(
        row.get("incumbent_local_trust_region_probe") is not True
        for row in selected
    )
    if len(selected) != 5 or global_selected > MAXIMUM_GLOBAL_TOP5:
        raise RuntimeError("trust-diverse Top-5 composition differs")
    for rank, row in enumerate(selected, 1):
        row["rank"] = rank
        row["trust_diverse_top5_policy"] = {
            "minimum_distinct_local_directions": (
                MINIMUM_DIVERSE_LOCAL_DIRECTIONS
            ),
            "global_minimum": 0,
            "global_maximum": MAXIMUM_GLOBAL_TOP5,
            "trusted_global_minimum_dynamic_support_weight": (
                TRUSTED_GLOBAL_SUPPORT_WEIGHT
            ),
            "selected_global_count": global_selected,
            "selected_local_count": 5 - global_selected,
            "unsupported_candidates_retained_in_audit_pool": True,
            "candidate_hard_pruning": False,
            "qps_used": False,
        }
        row["global_local_top5_quota"] = {
            "global_minimum": 0,
            "global_maximum": MAXIMUM_GLOBAL_TOP5,
            "incumbent_local_minimum": MINIMUM_DIVERSE_LOCAL_DIRECTIONS,
            "qps_used": False,
            "candidate_hard_pruning": False,
        }
    return selected


def rank_scene(**kwargs: Any) -> dict[str, Any]:
    result = v8.rank_scene(**kwargs)
    outer = int(kwargs["round_index"])
    if outer < EFFECTIVE_FROM_OUTER:
        return result
    selected = list(result["selected_top5"])
    global_count = sum(
        row.get("incumbent_local_trust_region_probe") is not True
        for row in selected
    )
    if len(selected) != 5 or global_count > MAXIMUM_GLOBAL_TOP5:
        raise RuntimeError("v9 real Top-5 trust composition differs")
    release = dict(result["all_scene_incumbent_plan_rerank_release"])
    release.update({
        "selection_policy": "trust_aware_diverse_local_first_v1",
        "top5_global_minimum": 0,
        "top5_global_maximum": MAXIMUM_GLOBAL_TOP5,
        "top5_global_selected": global_count,
        "top5_incumbent_local_minimum": MINIMUM_DIVERSE_LOCAL_DIRECTIONS,
        "local_probe_coordinate_space": "normalized_log",
        "local_probe_fractions": list(LOCAL_LOG_FRACTIONS),
        "trusted_global_minimum_dynamic_support_weight": (
            TRUSTED_GLOBAL_SUPPORT_WEIGHT
        ),
        "unsupported_candidates_retained_in_audit_pool": True,
        "inner_candidate_score_modified": False,
        "qps_used": False,
        "candidate_hard_pruning": False,
    })
    result["all_scene_incumbent_plan_rerank_release"] = release
    return result


def install() -> None:
    v8.EFFECTIVE_FROM_OUTER = EFFECTIVE_FROM_OUTER
    v8.LOCAL_FRACTIONS = LOCAL_LOG_FRACTIONS
    v8.local_probe_params = local_probe_params
    v8.select_global_and_local_top5 = select_trust_aware_diverse_top5
    v4.LOCAL_FRACTIONS = LOCAL_LOG_FRACTIONS
    v4.local_probe_params = tpch_local_probe_params
    v8.install()
    v4.v1.base.rank_scene = rank_scene


__all__ = [
    "EFFECTIVE_FROM_OUTER",
    "LOCAL_LOG_FRACTIONS",
    "MAXIMUM_GLOBAL_TOP5",
    "MINIMUM_DIVERSE_LOCAL_DIRECTIONS",
    "TRUSTED_GLOBAL_SUPPORT_WEIGHT",
    "install",
    "local_probe_params",
    "rank_scene",
    "select_trust_aware_diverse_top5",
    "tpch_local_probe_params",
]
