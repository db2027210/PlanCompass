#!/usr/bin/env python3
"""Algorithm C successor preserving PG semantic-batch precedence.

The predecessor performs lane-paired incumbent revalidation only after it has
physically replayed every fallback/coverage batch that appeared necessary
under the raw cross-lane comparison.  Revalidation can make the original
Top-5 acceptable.  In that case PostgreSQL semantics require the original
Top-5 to win and all later expansion batches to be diagnostic-only evidence.

This successor tags every replay with its semantic batch, selects the first
batch containing an accepted point with effective MySQL parameter movement,
and prevents later batches from mutating replay history or the incumbent.
It also computes terminal status over active scenes only.
"""

from __future__ import annotations

from pathlib import Path


PREDECESSOR = Path(__file__).resolve().with_name(
    "run_mysql_four_scene_algorithm_c_boundary_resume_v35.py"
)
_source = PREDECESSOR.read_text(encoding="utf-8")


def _replace_once(old: str, new: str, label: str) -> None:
    global _source
    if _source.count(old) != 1:
        raise RuntimeError(f"Algorithm C v35 {label} boundary differs")
    _source = _source.replace(old, new)


# Retain the v36 cached no-move correction.
_replace_once(
    '''                    row["cached_valid_replay_redecision"] = True
                    cached_rows.append(row)
''',
    '''                    try:
                        pg_outer.moved_shrink_subset(
                            incumbent_params=params_by_scene[scene],
                            candidate_ranking=_mapping(
                                candidate, "cached movement candidate ranking",
                            ),
                            active_params=active_by_scene[scene],
                        )
                    except RuntimeError as error:
                        if str(error) != (
                            "accepted candidate has no moved declared parameter"
                        ):
                            raise
                        row["cached_valid_replay_redecision"] = False
                        row["cached_redecision_skipped_before_physical"] = True
                        row["cached_redecision_skip_reason"] = (
                            "no_moved_declared_parameter_relative_to_current_incumbent"
                        )
                        state.setdefault(
                            "cached_no_move_prephysical_exclusions", {}
                        ).setdefault(scene, 0)
                        state["cached_no_move_prephysical_exclusions"][scene] += 1
                        continue
                    row["cached_valid_replay_redecision"] = True
                    row["semantic_batch_kind"] = "prephysical_cached_valid_replay"
                    cached_rows.append(row)
''',
    "cached-row",
)

# Expansion rows carry the batch identity supplied by the caller.
_replace_once(
    '''            row["candidate_ranking"] = copy.deepcopy(
                candidate_sets[scene][int(row["rank"]) - 1]
            )
            evaluations[scene].append(row)
''',
    '''            row["candidate_ranking"] = copy.deepcopy(
                candidate_sets[scene][int(row["rank"]) - 1]
            )
            row["semantic_batch_kind"] = batch_kind
            evaluations[scene].append(row)
''',
    "expansion-row tag",
)

# Tag the valid sibling retained from a partially failed wave.
_replace_once(
    '''                            row["candidate_ranking"] = copy.deepcopy(
                                rankings[scene][int(row["rank"]) - 1]
                            )
                            evaluations[scene].append(row)
''',
    '''                            row["candidate_ranking"] = copy.deepcopy(
                                rankings[scene][int(row["rank"]) - 1]
                            )
                            row["semantic_batch_kind"] = "base_top5"
                            evaluations[scene].append(row)
''',
    "partial base-row tag",
)

# Tag the ordinary successful base Top-5 replay path.
_replace_once(
    '''                        row["candidate_ranking"] = copy.deepcopy(
                            rankings[scene][int(row["rank"]) - 1]
                        )
                        evaluations[scene].append(row)
''',
    '''                        row["candidate_ranking"] = copy.deepcopy(
                            rankings[scene][int(row["rank"]) - 1]
                        )
                        row["semantic_batch_kind"] = "base_top5"
                        evaluations[scene].append(row)
''',
    "base-row tag",
)

_replace_once(
    '''                state["full_plan_replay_history_v2"][scene] = (
''',
    '''                # Lane pairing can reverse the raw decision that caused
                # later PG expansion batches to be replayed.  Recompute the
                # first accepting semantic batch after normalization.  Later
                # batches remain SHA-bound diagnostics but cannot mutate state.
                semantic_batch_priority = {
                    "prephysical_cached_valid_replay": -1,
                    "base_top5": 0,
                    "fallback_plan_change": 1,
                    "current_interval_plan_coverage": 2,
                    "full_range_plan_basin_escape": 3,
                }
                accepting_moved_batch_kinds: set[str] = set()
                for row, _decision in eligible:
                    try:
                        pg_outer.moved_shrink_subset(
                            incumbent_params=params_by_scene[scene],
                            candidate_ranking=_mapping(
                                row["candidate_ranking"],
                                "semantic-batch movement candidate ranking",
                            ),
                            active_params=active_by_scene[scene],
                        )
                    except RuntimeError as error:
                        if str(error) != (
                            "accepted candidate has no moved declared parameter"
                        ):
                            raise
                        continue
                    accepting_moved_batch_kinds.add(str(
                        row.get("semantic_batch_kind") or "base_top5"
                    ))
                selected_semantic_batch_kind = None
                if accepting_moved_batch_kinds:
                    selected_semantic_batch_kind = min(
                        accepting_moved_batch_kinds,
                        key=lambda name: semantic_batch_priority[name],
                    )
                    eligible = [
                        (row, decision) for row, decision in eligible
                        if str(row.get("semantic_batch_kind") or "base_top5")
                        == selected_semantic_batch_kind
                    ]
                    for row in rows:
                        row["semantic_state_eligible"] = (
                            str(row.get("semantic_batch_kind") or "base_top5")
                            == selected_semantic_batch_kind
                        )
                        if not row["semantic_state_eligible"]:
                            row["semantic_state_exclusion_reason"] = (
                                "later_batch_after_first_paired_acceptance"
                            )
                else:
                    for row in rows:
                        row["semantic_state_eligible"] = True

                state["full_plan_replay_history_v2"][scene] = (
''',
    "post-pair semantic selection",
)

_replace_once(
    '''                            row for row in rows
                            if row.get("cached_valid_replay_redecision") is not True
''',
    '''                            row for row in rows
                            if row.get("cached_valid_replay_redecision") is not True
                            and row.get("semantic_state_eligible", True) is True
''',
    "plan-history filter",
)

_replace_once(
    '''                for row in rows:
                    if row.get("cached_valid_replay_redecision") is True:
                        continue
                    signature = alg.optimizer_params_key(row["params"])
''',
    '''                for row in rows:
                    if row.get("cached_valid_replay_redecision") is True:
                        continue
                    if row.get("semantic_state_eligible", True) is not True:
                        continue
                    signature = alg.optimizer_params_key(row["params"])
''',
    "replay-archive filter",
)

_replace_once(
    '''                    "no_move_accepted_candidates_excluded": sum(
''',
    '''                    "selected_semantic_batch_kind": (
                        selected_semantic_batch_kind
                    ),
                    "pg_first_accepting_semantic_batch_preserved": True,
                    "later_semantic_batches_grant_no_state_credit": True,
                    "no_move_accepted_candidates_excluded": sum(
''',
    "decision audit",
)

_replace_once(
    '''                "COMPLETE" if all(state["converged"].values()) else "NON_CONVERGED"
''',
    '''                "COMPLETE"
                if all(state["converged"][scene] for scene in active_scenes)
                else "NON_CONVERGED"
''',
    "active-scene completion",
)

exec(compile(_source, str(Path(__file__).resolve()), "exec"), globals())
