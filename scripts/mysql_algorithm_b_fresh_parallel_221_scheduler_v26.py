#!/usr/bin/env python3
"""True two-worker ``[1,2], [3,4], [5]`` Algorithm-B scheduler."""

from __future__ import annotations

import copy
from pathlib import Path
import re
from typing import Any, Mapping

from scripts import mysql_algorithm_b_fresh_parallel_221_scheduler_v25 as predecessor


alg = predecessor.alg
runtime = predecessor.runtime
SCENES = tuple(predecessor.SCENES)
FORMAL_CANDIDATE_BATCHES = [[1, 2], [3, 4], [5]]
RESTORE_HISTORICAL_OUTER1_RANKS = False
_ORIGINAL_ASSIGNMENTS = predecessor._ORIGINAL_ASSIGNMENTS
_ORIGINAL_MEASURE = predecessor._ORIGINAL_MEASURE
_pending: dict[str, Any] = {}
_rank_cache: dict[tuple[int, int], dict[str, Any]] = {}


def _sync_predecessor_helpers() -> None:
    predecessor.SCENES = tuple(SCENES)
    predecessor.FORMAL_CANDIDATE_BATCHES = copy.deepcopy(FORMAL_CANDIDATE_BATCHES)
    predecessor._ORIGINAL_ASSIGNMENTS = _ORIGINAL_ASSIGNMENTS
    predecessor._ORIGINAL_MEASURE = _ORIGINAL_MEASURE


def _build_assignments_221(**kwargs: Any):
    """Build two physical ranks for positions 1/3 and one rank for 5."""
    global _pending
    _sync_predecessor_helpers()
    rankings = kwargs["rankings"]
    rank = int(kwargs["rank"])
    calibration = bool(rankings) and all(
        rows and "calibration_id" in rows[0] for rows in rankings.values()
    )
    position = (rank - 1) % 5 + 1
    if calibration or position in {2, 4, 5}:
        assignments, companions = _ORIGINAL_ASSIGNMENTS(**kwargs)
        _pending = {
            "rank": rank, "ranks": (rank,), "active_scenes": set(rankings),
            "assignments": copy.deepcopy(assignments),
            "companions": set(companions),
            "slot_ranks": {worker_id: rank for worker_id in assignments},
            "calibration": calibration,
        }
        return assignments, companions
    ranks = (rank, rank + 1)
    assignments, companions, slot_ranks = predecessor._dual_assignments(
        rankings=rankings,
        params_by_scene=kwargs["params_by_scene"],
        workers=kwargs["workers"],
        ranks=ranks,
    )
    _pending = {
        "rank": rank, "ranks": ranks, "active_scenes": set(rankings),
        "assignments": copy.deepcopy(assignments),
        "companions": set(companions), "slot_ranks": slot_ranks,
        "calibration": False,
    }
    return assignments, companions


def _measure_221(**kwargs: Any):
    _sync_predecessor_helpers()
    output = Path(kwargs["output"])
    match = re.search(r"outer_(\d+)/top5_batch_(\d+)$", str(output))
    if match is None or _pending.get("calibration") is True:
        return _ORIGINAL_MEASURE(**kwargs)
    outer = int(match.group(1))
    rank = int(match.group(2))
    state = kwargs["state"]
    state_path = Path(kwargs["state_path"])
    stage_prefix = str(kwargs["stage_prefix"])
    position = (rank - 1) % 5 + 1

    if position in {2, 4}:
        key = (outer, rank)
        if key not in _rank_cache:
            raise RuntimeError(f"parallel wave cache absent for outer {outer} rank {rank}")
        return predecessor._materialize_cached_rank(
            output=output, cached=_rank_cache.pop(key), state_path=state_path,
            state=state, stage_prefix=stage_prefix,
        )

    measured, state = _ORIGINAL_MEASURE(**kwargs)
    active_scenes = set(_pending["active_scenes"])
    if position in {1, 3}:
        ranks = tuple(int(value) for value in _pending["ranks"])
        if ranks != (rank, rank + 1):
            raise RuntimeError("two-worker parallel candidate wave identity differs")
        documents = predecessor._split_physical_wave(
            output=output, measured=measured, ranks=ranks,
            active_scenes=active_scenes,
            slot_ranks=_pending["slot_ranks"],
        )
        _rank_cache[(outer, rank + 1)] = {
            "document": documents[rank + 1],
        }
        state["formal_candidate_batches"] = copy.deepcopy(FORMAL_CANDIDATE_BATCHES)
        state["parallel_221_last_physical_wave"] = list(ranks)
        alg.atomic(state_path, state)
        return copy.deepcopy(dict(documents[rank]["measurements"])), state

    filtered = predecessor._filter_single_rank(
        output=output, measured=measured, rank=rank,
        active_scenes=active_scenes, slot_ranks=_pending["slot_ranks"],
    )
    return filtered, state

