#!/usr/bin/env python3
"""Algorithm C v43 with self-contained PG coverage helpers."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v1 as checkpoint_v1
from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v2 as checkpoint_v2
from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v3 as checkpoint_v3
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v42 as v42


LOCAL_REAL_REPLAY_LIMIT = v42.v41.LOCAL_REAL_REPLAY_LIMIT
GLOBAL_COVERAGE_REAL_REPLAY_LIMIT = v42.v41.GLOBAL_COVERAGE_REAL_REPLAY_LIMIT
GLOBAL_COVERAGE_SAMPLE_COUNT = v42.v41.GLOBAL_COVERAGE_SAMPLE_COUNT


def _mapping_local(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def _admit_coverage_top10(
    rankings: Sequence[Mapping[str, Any]],
    *,
    incumbent_params: Mapping[str, Any],
    previously_accepted_signatures: set[str] | None = None,
    hard_pruned_plan_fingerprints: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = [copy.deepcopy(dict(row)) for row in rankings]
    accepted: list[dict[str, Any]] = []
    batch_audits: list[dict[str, Any]] = []
    remaining = rows
    pruned_plans = set(hard_pruned_plan_fingerprints or set())
    for batch_index in (1, 2):
        selected, audit = pg_outer.admit_partial_topk(
            remaining,
            incumbent_params=incumbent_params,
            previously_accepted_signatures=previously_accepted_signatures,
            hard_pruned_plan_fingerprints=pruned_plans,
        )
        batch_audits.append({"batch_index": batch_index, **audit})
        if not selected:
            break
        accepted.extend(selected)
        selected_params = {
            alg.optimizer_params_key(
                _mapping_local(row.get("optimized_params"), "coverage params")
            )
            for row in selected
        }
        selected_plans = {pg_outer.full_plan_fingerprint(row) for row in selected}
        pruned_plans.update(selected_plans)
        remaining = [
            row for row in remaining
            if alg.optimizer_params_key(
                _mapping_local(row.get("optimized_params"), "coverage params")
            ) not in selected_params
            and pg_outer.full_plan_fingerprint(row) not in selected_plans
        ]
    for rank, row in enumerate(accepted, 1):
        row["pg_outer_admission_rank"] = rank
    return accepted[:GLOBAL_COVERAGE_REAL_REPLAY_LIMIT], {
        "top_k_limit": GLOBAL_COVERAGE_REAL_REPLAY_LIMIT,
        "admitted_count": min(len(accepted), GLOBAL_COVERAGE_REAL_REPLAY_LIMIT),
        "partial_top_k": len(accepted) < GLOBAL_COVERAGE_REAL_REPLAY_LIMIT,
        "top_k_is_upper_bound": True,
        "physical_batch_limit": 5,
        "physical_batch_count_limit": 2,
        "duplicate_plan_padding_allowed": False,
        "baseline_padding_allowed": False,
        "sub_batch_admissions": batch_audits,
    }


def _replay_coverage_top10(**kwargs: Any):
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


class _CheckpointVerifier:
    @staticmethod
    def verify_or_standard(path: Path) -> dict[str, Any]:
        value = checkpoint_v3._load(path)
        if value.get("schema_version") == checkpoint_v3.SCHEMA:
            return checkpoint_v3.verify_checkpoint(path)
        if value.get("schema_version") == checkpoint_v2.SCHEMA:
            return checkpoint_v2.verify_checkpoint(path)
        if value.get("schema_version") == checkpoint_v1.SCHEMA:
            return checkpoint_v1.verify_checkpoint(path)
        return checkpoint_v2._STANDARD_VERIFY(path)


scene_checkpoint = _CheckpointVerifier()
_source = v42._source
exec(compile(_source, str(Path(__file__).resolve()), "exec"), globals())
