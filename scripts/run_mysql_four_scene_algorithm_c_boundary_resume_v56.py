#!/usr/bin/env python3
"""Complete PG Algorithm C recovery and interval state-machine parity.

Adds the two PostgreSQL recovery actions that v55 did not implement: one
measured safe/unsafe Outer-boundary refinement before a due two-miss shrink,
and the finite progressive numeric-window ladder after empty admissions.
"""

from __future__ import annotations

from pathlib import Path

from scripts import mysql_algorithm_c_pg_recovery_v1 as pg_recovery
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v55 as v55


scene_checkpoint = v55.scene_checkpoint
_source = v55._source


def _replace_once(old: str, new: str, label: str) -> None:
    global _source
    if _source.count(old) != 1:
        raise RuntimeError(f"Algorithm C v56 {label} boundary differs")
    _source = _source.replace(old, new)


_replace_once(
    "from scripts import mysql_algorithm_c_global_coverage_v1 as global_coverage",
    "from scripts import mysql_algorithm_c_global_coverage_v1 as global_coverage\n"
    "from scripts import mysql_algorithm_c_pg_recovery_v1 as pg_recovery",
    "recovery import",
)

_replace_once(
    '''        "consecutive_failed_rounds": copy.deepcopy(
            reconstructed["consecutive_failed_rounds"]
        ),''',
    '''        "consecutive_failed_rounds": copy.deepcopy(
            reconstructed["consecutive_failed_rounds"]
        ),
        "algorithm_c_outer_boundary_observations": {
            scene: [] for scene in SCENES
        },
        "algorithm_c_outer_boundary_generated_ids": {
            scene: [] for scene in SCENES
        },
        "algorithm_c_outer_boundary_attempts_since_shrink": {
            scene: 0 for scene in SCENES
        },
        "algorithm_c_outer_boundary_pending": {
            scene: False for scene in SCENES
        },
        "algorithm_c_empty_recovery_stage": {
            scene: 0 for scene in SCENES
        },
        "algorithm_c_empty_numeric_windows": {
            scene: 0 for scene in SCENES
        },''',
    "state initialization",
)

_replace_once(
    '''                    if int(state["consecutive_failed_rounds"][scene]) != 1:
                        continue
                    queue = state.setdefault(''',
    '''                    if (
                        int(state["consecutive_failed_rounds"][scene]) != 1
                        and int(state["algorithm_c_empty_recovery_stage"][scene]) != 1
                    ):
                        continue
                    queue = state.setdefault(''',
    "first-miss region trigger",
)

_replace_once(
    '''                    if int(state["consecutive_failed_rounds"][scene]) != 1:
                        continue
                    coverage_rows = global_coverage.candidate_rankings(''',
    '''                    if (
                        int(state["consecutive_failed_rounds"][scene]) != 1
                        and int(state["algorithm_c_empty_recovery_stage"][scene]) != 1
                    ):
                        continue
                    coverage_rows = global_coverage.candidate_rankings(''',
    "first-miss generator trigger",
)

_replace_once(
    '''                    replay_dedup[scene]["first_miss_expansion"] = expansion_audit
            ranking_document = {''',
    '''                    replay_dedup[scene]["first_miss_expansion"] = expansion_audit

                # PG recovery actions have priority over an ordinary ranking
                # batch.  They still pass through the frozen C-only plan and
                # resource model; QPS does not enter their score.
                for scene in active_scenes:
                    recovery_mode = None
                    recovery_audit = None
                    vectors = []
                    if state["algorithm_c_outer_boundary_pending"][scene]:
                        pair = pg_recovery.outer_boundary_pair(
                            state["algorithm_c_outer_boundary_observations"][scene],
                            ranges=ranges_by_scene[scene],
                            active=active_by_scene[scene],
                        )
                        if pair is None:
                            raise RuntimeError(
                                "scheduled Algorithm C Outer boundary bracket disappeared"
                            )
                        vectors, generated, recovery_audit = (
                            pg_recovery.outer_boundary_vectors(
                                pair,
                                active=active_by_scene[scene],
                                limit=FIXED_TOP_K,
                                generated_ids=state[
                                    "algorithm_c_outer_boundary_generated_ids"
                                ][scene],
                            )
                        )
                        state["algorithm_c_outer_boundary_generated_ids"][scene] = generated
                        state["algorithm_c_outer_boundary_attempts_since_shrink"][scene] += 1
                        recovery_mode = "outer_boundary_refinement"
                    elif int(state["algorithm_c_empty_recovery_stage"][scene]) >= 2:
                        window_index = int(
                            state["algorithm_c_empty_numeric_windows"][scene]
                        )
                        vectors, recovery_audit = (
                            pg_recovery.progressive_numeric_vectors(
                                incumbent=params_by_scene[scene],
                                ranges=ranges_by_scene[scene],
                                active=active_by_scene[scene],
                                window_index=window_index,
                                count=max(
                                    4 * INDEPENDENT_SCENE_WORKER_COUNT,
                                    2 * FIXED_TOP_K,
                                    2 * len(active_by_scene[scene])
                                    + 2 * INDEPENDENT_SCENE_WORKER_COUNT,
                                ),
                            )
                        )
                        recovery_mode = "progressive_numeric_refinement"
                    if recovery_mode is not None:
                        scored = pg_recovery.score_vectors(
                            scene=scene,
                            vectors=vectors,
                            incumbent=params_by_scene[scene],
                            ranges=ranges_by_scene[scene],
                            resources=incumbents[scene]["resource_means"],
                            freeze=freeze,
                            worker=workers[representatives[scene]],
                            direction_mode=recovery_mode,
                        )
                        selected, admission = pg_outer.admit_partial_topk(
                            scored,
                            incumbent_params=params_by_scene[scene],
                            previously_accepted_signatures=set(state[
                                "previously_accepted_parameter_signatures"
                            ][scene]),
                        )
                        rankings[scene] = selected
                        all_rankings[scene] = scored
                        for rank, row in enumerate(rankings[scene], 1):
                            row["rank"] = rank
                            row["algorithm_c_pg_recovery_mode"] = recovery_mode
                        replay_dedup[scene]["pg_recovery"] = {
                            "contract": pg_recovery.POLICY_ID,
                            "mode": recovery_mode,
                            "generation": recovery_audit,
                            "admission": admission,
                            "qps_used_for_generation_or_ranking": False,
                        }
                alg.atomic(state_path, state)
            ranking_document = {''',
    "recovery candidate generation",
)

_replace_once(
    '''                    state.setdefault("candidate_space_exhausted", {})[scene] = True
                    alg.atomic(
                        output / f"outer_{outer_attempt:03d}" / f"{scene}.decision.json",
                        {
                            "schema_version": "mysql_four_scene_algorithm_c_outer_decision_boundary_resume_v2",
                            "status": "CANDIDATE_SPACE_EXHAUSTED_CLEAN",
                            "scene": scene,
                            "outer_attempt": outer_attempt,
                            "accepted": None,
                            "evaluations": [],
                            "accepted_rounds": state["accepted_rounds"][scene],
                            "rejected_outer_attempts": state["rejected_outer_attempts"][scene],
                            "formal_evidence_eligible": not args.interference_override_pilot,
                            "accepted_state_credit_increment": 0,
                            "rejected_outer_attempt_increment": 0,
                            "acceptance_tier_mutation": False,
                            "convergence_mutation": False,
                            "replay_history_mutation": False,
                            "pruning_evidence_eligible": False,
                            "semantic_state_unchanged": True,
                        },
                    )
                    continue''',
    '''                    recovery_stage = int(
                        state["algorithm_c_empty_recovery_stage"][scene]
                    )
                    if recovery_stage == 0:
                        state["algorithm_c_empty_recovery_stage"][scene] = 1
                        next_action = "first_miss_expansion"
                    elif recovery_stage == 1:
                        state["algorithm_c_empty_recovery_stage"][scene] = 2
                        next_action = "progressive_numeric_refinement"
                    else:
                        state["algorithm_c_empty_numeric_windows"][scene] += 1
                        next_action = "progressive_numeric_refinement"
                        if (
                            state["algorithm_c_empty_numeric_windows"][scene]
                            >= pg_recovery.MAXIMUM_EMPTY_NUMERIC_WINDOWS
                        ):
                            raise RuntimeError(
                                "numeric_windows_exhausted_without_physical_evidence"
                            )
                    alg.atomic(
                        output / f"outer_{outer_attempt:03d}" / f"{scene}.decision.json",
                        {
                            "schema_version": "mysql_four_scene_algorithm_c_outer_decision_pg_recovery_v1",
                            "status": "EMPTY_ADMISSION_RECOVERY",
                            "scene": scene,
                            "outer_attempt": outer_attempt,
                            "accepted": None,
                            "evaluations": [],
                            "accepted_rounds": state["accepted_rounds"][scene],
                            "rejected_outer_attempts": state["rejected_outer_attempts"][scene],
                            "accepted_state_credit_increment": 0,
                            "rejected_outer_attempt_increment": 0,
                            "physical_miss_increment": 0,
                            "next_action": next_action,
                            "empty_numeric_windows": state[
                                "algorithm_c_empty_numeric_windows"
                            ][scene],
                            "convergence_mutation": False,
                            "semantic_state_unchanged": True,
                        },
                    )
                    continue''',
    "empty admission recovery",
)

_replace_once(
    '''                eligible: list[tuple[dict[str, Any], dict[str, Any]]] = []
                ordered_rows = sorted(rows, key=lambda item: item["rank"])''',
    '''                state["algorithm_c_empty_recovery_stage"][scene] = 0
                state["algorithm_c_empty_numeric_windows"][scene] = 0
                eligible: list[tuple[dict[str, Any], dict[str, Any]]] = []
                ordered_rows = sorted(rows, key=lambda item: item["rank"])''',
    "nonempty recovery reset",
)

_replace_once(
    '''                state["full_plan_replay_history_v2"][scene] = (
                    record_plan_replay_outcomes(''',
    '''                state["algorithm_c_outer_boundary_observations"][scene] = (
                    pg_recovery.record_outer_observations(
                        state["algorithm_c_outer_boundary_observations"][scene],
                        rows=[
                            row for row in rows
                            if row.get("semantic_state_eligible", True) is True
                        ],
                        target=alg.TARGET[scene],
                        active=active_by_scene[scene],
                        outer_attempt=outer_attempt,
                    )
                )
                state["full_plan_replay_history_v2"][scene] = (
                    record_plan_replay_outcomes(''',
    "outer observation recording",
)

_replace_once(
    '''                    ranges_before_rejection = copy.deepcopy(ranges_by_scene[scene])
                    ranges_by_scene[scene], rejection_progress = (
                        rejection_cycle.advance_rejection_cycle(
                            accepted=accepted,
                            consecutive_rejections=int(
                                state["consecutive_failed_rounds"][scene]
                            ),
                            ranges=ranges_by_scene[scene],
                            incumbent_params=params_by_scene[scene],
                            active_params=active_by_scene[scene],
                        )
                    )''',
    '''                    ranges_before_rejection = copy.deepcopy(ranges_by_scene[scene])
                    prior_rejections = int(
                        state["consecutive_failed_rounds"][scene]
                    )
                    (
                        ranges_by_scene[scene], rejection_progress,
                        boundary_pending,
                    ) = pg_recovery.advance_outer_decision(
                        accepted=accepted,
                        consecutive_rejections=prior_rejections,
                        ranges=ranges_by_scene[scene],
                        initial_ranges=initial_ranges_by_scene[scene],
                        incumbent_params=params_by_scene[scene],
                        active=active_by_scene[scene],
                        observations=state[
                            "algorithm_c_outer_boundary_observations"
                        ][scene],
                        boundary_attempts_since_shrink=state[
                            "algorithm_c_outer_boundary_attempts_since_shrink"
                        ][scene],
                    )
                    state["algorithm_c_outer_boundary_pending"][scene] = (
                        boundary_pending
                    )''',
    "boundary refinement before due shrink",
)

_replace_once(
    '''                    if rejection_progress["forced_incumbent_shrink"]:
                        interval_control.require_binary_progress(''',
    '''                    if accepted or rejection_progress["forced_incumbent_shrink"]:
                        state[
                            "algorithm_c_outer_boundary_attempts_since_shrink"
                        ][scene] = 0
                        state["algorithm_c_outer_boundary_pending"][scene] = False
                    if rejection_progress["forced_incumbent_shrink"]:
                        interval_control.require_binary_progress(''',
    "boundary attempt reset",
)

exec(compile(_source, str(Path(__file__).resolve()), "exec"), globals())
