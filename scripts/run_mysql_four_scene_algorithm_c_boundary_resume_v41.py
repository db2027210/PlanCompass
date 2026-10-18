#!/usr/bin/env python3
"""Algorithm C runtime aligned with the current PostgreSQL escape flow.

This successor keeps MySQL measurement and prediction, but fixes the outer
search regression in the v32 client-primary chain:

* a rejected local Top-10 is followed by deterministic, QPS-blind coverage of
  the current interval in two Top-5 physical batches (at most ten points);
* coverage runs on every locally exhausted outer round, before the rejection
  is allowed to contribute to the two-rejection shrink cycle;
* plan-basin rescue never reopens the original parameter domain after a
  shrink; it remains inside the current formal interval;
* Algorithm A evidence is neither read nor used for generation or ranking.

The implementation is generated from the effective v40 source so all v36,
v39, and v40 correctness fixes remain present.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v40 as v40


PG_LATEST_OUTER_FLOW_SCHEMA = "mysql_algorithm_c_pg_latest_outer_flow_v1"
LOCAL_REAL_REPLAY_LIMIT = 10
GLOBAL_COVERAGE_REAL_REPLAY_LIMIT = 10
GLOBAL_COVERAGE_SAMPLE_COUNT = 256


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
    """Compose two PG Top-5 admissions without changing ordinary Top-K."""

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
        selected_plans = {
            pg_outer.full_plan_fingerprint(row) for row in selected
        }
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
        "admitted_count": min(
            len(accepted), GLOBAL_COVERAGE_REAL_REPLAY_LIMIT
        ),
        "partial_top_k": len(accepted) < GLOBAL_COVERAGE_REAL_REPLAY_LIMIT,
        "top_k_is_upper_bound": True,
        "physical_batch_limit": 5,
        "physical_batch_count_limit": 2,
        "duplicate_plan_padding_allowed": False,
        "baseline_padding_allowed": False,
        "sub_batch_admissions": batch_audits,
    }


def _replay_coverage_top10(**kwargs: Any):
    """Replay current-interval coverage as at most two semantic Top-5 batches."""

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
        aggregate_evaluations,
        aggregate_companions,
        aggregate_technical,
        aggregate_accepted,
        state,
    )


_source = v40._source


def _replace_once(old: str, new: str, label: str) -> None:
    global _source
    if _source.count(old) != 1:
        raise RuntimeError(f"Algorithm C v40 {label} boundary differs")
    _source = _source.replace(old, new)


# PostgreSQL's current controller performs global current-interval coverage
# after every exhausted local Top-10, not only after a prior rejected round.
_replace_once(
    '''                if int(state["consecutive_failed_rounds"][scene]) != 1:
                    continue
''',
    '''                # Current PG flow: exhaust QPS-blind current-interval
                # coverage before *this* no-improvement round is finalized.
''',
    "coverage trigger",
)

# PostgreSQL currently allows two plan-distinct Top-5 coverage batches.
_replace_once(
    '''                admitted, admission = pg_outer.admit_partial_topk(
                    unseen,
                    incumbent_params=params_by_scene[scene],
                    previously_accepted_signatures=set(
                        state["previously_accepted_parameter_signatures"][scene]
                    ),
                )
''',
    '''                admitted, admission = _admit_coverage_top10(
                    unseen,
                    incumbent_params=params_by_scene[scene],
                    previously_accepted_signatures=set(
                        state["previously_accepted_parameter_signatures"][scene]
                    ),
                )
''',
    "coverage Top-10 admission",
)

_replace_once(
    '''                ) = _replay_pg_expansion_waves(
                    batch_kind="current_interval_plan_coverage",
''',
    '''                ) = _replay_coverage_top10(
                    batch_kind="current_interval_plan_coverage",
''',
    "coverage Top-10 replay",
)

# The latest PG code treats the current interval as the only formal domain.
# Keep the legacy branch structure for compatibility but remove its original-
# range reopen and give it the truthful semantic batch name.
_replace_once(
    "                    ranges=initial_ranges_by_scene[scene],\n",
    "                    ranges=ranges_by_scene[scene],\n",
    "current-interval basin range",
)
_source = _source.replace(
    '"full_range_plan_basin_escape"',
    '"current_interval_plan_basin_escape"',
)

# v39's first-accepting-batch ordering must recognize both coverage batches.
_replace_once(
    '''                    "current_interval_plan_coverage": 2,
                    "current_interval_plan_basin_escape": 3,
''',
    '''                    "current_interval_plan_coverage": 2,
                    "current_interval_plan_coverage_001": 2,
                    "current_interval_plan_coverage_002": 2,
                    "current_interval_plan_basin_escape": 3,
''',
    "coverage semantic priority",
)

_replace_once(
    '''        raise RuntimeError("outer-runtime optimization authority differs")
''',
    '''        raise RuntimeError(
            "outer-runtime optimization authority differs: "
            + json.dumps({
                "optimization_path": str(OPTIMIZATION_CONTRACT),
                "status": optimization.get("status"),
                "formal_scope": optimization.get("formal_scope"),
                "cross_engine_comparison_prohibited": optimization.get(
                    "cross_engine_comparison_prohibited"
                ),
                "pg_search_outer": pg_search_outer,
                "rejection_policy_runtime": rejection_cycle.POLICY_ID,
                "optimized_reset": optimization.get("optimized_reset"),
                "reset_source": str(reset_source),
                "reset_source_sha256_actual": alg.sha(reset_source),
                "reset_source_sha256_bound": bindings.get(
                    "reset_source_sha256"
                ),
                "algorithm_source": str(algorithm_source),
                "algorithm_source_sha256_actual": alg.sha(algorithm_source),
                "algorithm_source_sha256_bound": bindings.get(
                    "algorithm_source_sha256"
                ),
            }, sort_keys=True)
        )
''',
    "optimization diagnostic",
)

exec(compile(_source, str(Path(__file__).resolve()), "exec"), globals())
