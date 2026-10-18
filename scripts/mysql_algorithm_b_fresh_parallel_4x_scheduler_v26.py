#!/usr/bin/env python3
"""Four-lane scheduler for Top4 B calibration and formal Top-5 waves."""

from __future__ import annotations

import copy
from pathlib import Path
import re
from typing import Any, Mapping

from scripts import mysql_algorithm_b_fresh_parallel_221_scheduler_v25 as base


SCENES: tuple[str, ...] = ()
_pending: dict[str, Any] = {}
_rank_cache: dict[tuple[Any, int], dict[str, Any]] = {}
_ORIGINAL_ASSIGNMENTS: Any = None
_ORIGINAL_MEASURE: Any = None


def _parallel_assignments(
    *, rankings: Mapping[str, list[dict[str, Any]]],
    params_by_scene: Mapping[str, Mapping[str, float]],
    workers: Mapping[str, Mapping[str, Any]], ranks: tuple[int, ...],
) -> tuple[dict[str, dict[str, Any]], set[str], dict[str, int]]:
    assignments: dict[str, dict[str, Any]] = {}
    companions: set[str] = set()
    slot_ranks: dict[str, int] = {}
    for scene in SCENES:
        scene_workers = sorted(
            worker_id for worker_id, worker in workers.items()
            if worker["scene"] == scene
        )
        if len(scene_workers) != 4 or not 1 <= len(ranks) <= 4:
            raise RuntimeError(f"{scene} frozen four-worker cardinality differs")
        rows = rankings.get(scene) or []
        for slot, worker_id in enumerate(scene_workers):
            if slot < len(ranks):
                rank = ranks[slot]
                if rank < 1 or rank > len(rows):
                    raise RuntimeError(f"{scene} candidate rank {rank} is absent")
                row = rows[rank - 1]
                slot_ranks[worker_id] = rank
                assignments[worker_id] = {
                    "rank": rank,
                    "params": copy.deepcopy(row["optimized_params"]),
                    "candidate_physical_plan_sha256": (
                        row.get("resource_model", {}).get(
                            "candidate_plan_sha256"
                        )
                    ),
                }
            else:
                slot_ranks[worker_id] = 0
                assignments[worker_id] = {
                    "rank": 0,
                    "params": copy.deepcopy(dict(params_by_scene[scene])),
                }
                companions.add(worker_id)
    return assignments, companions, slot_ranks


def _single_assignments(**kwargs: Any):
    assignments, companions, slot_ranks = _parallel_assignments(
        rankings=kwargs["rankings"],
        params_by_scene=kwargs["params_by_scene"],
        workers=kwargs["workers"],
        ranks=(int(kwargs["rank"]),),
    )
    # A serialized rank uses one candidate lane.  Remove the three incumbent
    # placeholders so a rank-5 wave performs only one physical replay.
    assignments = {
        worker_id: row for worker_id, row in assignments.items()
        if int(slot_ranks[worker_id]) > 0
    }
    companions = set()
    slot_ranks = {worker_id: rank for worker_id, rank in slot_ranks.items() if rank > 0}
    return assignments, companions, slot_ranks


def _build_assignments_221(**kwargs: Any):
    """Use 4-wide calibration waves and the frozen formal 4+1 waves."""
    global _pending
    rankings = kwargs["rankings"]
    rank = int(kwargs["rank"])
    calibration = bool(rankings) and all(
        rows and "calibration_id" in rows[0] for rows in rankings.values()
    )
    if calibration:
        group_start = ((rank - 1) // 4) * 4 + 1
        ranks = tuple(range(group_start, min(group_start + 4, 16)))
        if rank == group_start:
            assignments, companions, slot_ranks = _parallel_assignments(
                rankings=rankings, params_by_scene=kwargs["params_by_scene"],
                workers=kwargs["workers"], ranks=ranks,
            )
        else:
            assignments, companions, slot_ranks = _single_assignments(**kwargs)
    else:
        position = (rank - 1) % 5 + 1
        if position == 1:
            ranks = (rank, rank + 1, rank + 2, rank + 3)
            assignments, companions, slot_ranks = _parallel_assignments(
                rankings=rankings, params_by_scene=kwargs["params_by_scene"],
                workers=kwargs["workers"], ranks=ranks,
            )
        else:
            ranks = (rank,)
            assignments, companions, slot_ranks = _single_assignments(**kwargs)
    _pending = {
        "rank": rank, "ranks": ranks,
        "active_scenes": set(rankings),
        "assignments": copy.deepcopy(assignments),
        "companions": set(companions),
        "slot_ranks": slot_ranks,
        "calibration": calibration,
    }
    return assignments, companions


def _measure_221(**kwargs: Any):
    output = Path(kwargs["output"])
    calibration_match = re.search(r"fresh_calibration/wave_(\d+)$", str(output))
    formal_match = re.search(r"outer_(\d+)/top5_batch_(\d+)$", str(output))
    if calibration_match is None and formal_match is None:
        return _ORIGINAL_MEASURE(**kwargs)
    if calibration_match is not None:
        rank = int(calibration_match.group(1))
        cache_key: tuple[Any, int] = ("calibration", rank)
        group_start = ((rank - 1) // 4) * 4 + 1
        physical_start = rank == group_start
    else:
        assert formal_match is not None
        outer = int(formal_match.group(1))
        rank = int(formal_match.group(2))
        cache_key = (outer, rank)
        physical_start = ((rank - 1) % 5 + 1) == 1
    if not physical_start and cache_key in _rank_cache:
        return base._materialize_cached_rank(
            output=output, cached=_rank_cache.pop(cache_key),
            state_path=Path(kwargs["state_path"]), state=kwargs["state"],
            stage_prefix=str(kwargs["stage_prefix"]),
        )
    measured, state = _ORIGINAL_MEASURE(**kwargs)
    ranks = tuple(int(value) for value in _pending["ranks"])
    if len(ranks) > 1:
        documents = base._split_physical_wave(
            output=output, measured=measured, ranks=ranks,
            active_scenes=set(_pending["active_scenes"]),
            slot_ranks=_pending["slot_ranks"],
        )
        cache_prefix: Any = "calibration" if calibration_match is not None else outer
        for cached_rank in ranks[1:]:
            _rank_cache[(cache_prefix, cached_rank)] = {
                "document": documents[cached_rank]
            }
        state["four_lane_last_physical_wave"] = list(ranks)
        state["calibration_candidate_batches"] = [
            [1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12], [13, 14, 15]
        ]
        base.alg.atomic(Path(kwargs["state_path"]), state)
        return copy.deepcopy(dict(documents[rank]["measurements"])), state
    filtered = base._filter_single_rank(
        output=output, measured=measured, rank=rank,
        active_scenes=set(_pending["active_scenes"]),
        slot_ranks=_pending["slot_ranks"],
    )
    return filtered, state
