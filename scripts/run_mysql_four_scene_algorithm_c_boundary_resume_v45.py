#!/usr/bin/env python3
"""Algorithm C v110 complete-lifecycle recovery runtime."""

from __future__ import annotations

import copy
from typing import Any, Mapping

from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v44 as v44


LOCAL_REAL_REPLAY_LIMIT = v44.LOCAL_REAL_REPLAY_LIMIT
GLOBAL_COVERAGE_REAL_REPLAY_LIMIT = v44.GLOBAL_COVERAGE_REAL_REPLAY_LIMIT
GLOBAL_COVERAGE_SAMPLE_COUNT = v44.GLOBAL_COVERAGE_SAMPLE_COUNT
_admit_coverage_top10 = v44._admit_coverage_top10
scene_checkpoint = v44.scene_checkpoint


def _mapping_local(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def _replay_coverage_top10(**kwargs: Any):
    """Replay coverage through this module's active runtime globals.

    v44 aliased the helper from v43.  Python functions retain their defining
    module globals, so that alias silently re-entered v43 where independent
    scene/no-lease flags were false and the target CPU scope became ``None``.
    Defining the helper here keeps coverage waves in the same runtime as the
    ordinary Top-5 and expansion waves.
    """
    candidate_sets = _mapping_local(kwargs["candidate_sets"], "candidate sets")
    aggregate_evaluations: dict[str, list[dict[str, Any]]] = {
        scene: [] for scene in candidate_sets
    }
    aggregate_companions: list[dict[str, Any]] = []
    aggregate_technical: list[dict[str, Any]] = []
    aggregate_accepted: dict[str, dict[str, Any]] = {}
    state = kwargs["state"]
    for batch_index, offset in ((1, 0), (2, 5)):
        active: dict[str, list[dict[str, Any]]] = {}
        for scene, raw_rows in candidate_sets.items():
            if scene in aggregate_accepted:
                continue
            rows = [copy.deepcopy(dict(row)) for row in raw_rows[offset:offset + 5]]
            for rank, row in enumerate(rows, 1):
                row["coverage_global_rank"] = offset + rank
                row["rank"] = rank
            if rows:
                active[scene] = rows
        if not active:
            continue
        call = dict(kwargs)
        call["candidate_sets"] = active
        call["batch_kind"] = f"current_interval_plan_coverage_{batch_index:03d}"
        evaluations, companions, technical, accepted, state = (
            _replay_pg_expansion_waves(**call)
        )
        for scene, rows in evaluations.items():
            aggregate_evaluations.setdefault(scene, []).extend(rows)
        aggregate_companions.extend(companions)
        aggregate_technical.extend(technical)
        aggregate_accepted.update(accepted)
    return (
        aggregate_evaluations, aggregate_companions, aggregate_technical,
        aggregate_accepted, state,
    )


_source = v44._source
_old_wait = (
    'or float(contract.get("maximum_quiet_wait_while_held_seconds", 0)) > 60.0'
)
_new_wait = (
    'or float(contract.get("maximum_quiet_wait_while_held_seconds", 0)) > 300.0'
)
if _source.count(_old_wait) != 1:
    raise RuntimeError("Algorithm C v44 quiet-wait validation boundary differs")
_source = _source.replace(_old_wait, _new_wait)

exec(compile(_source, str(__file__), "exec"), globals())
