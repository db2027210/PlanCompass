#!/usr/bin/env python3
"""Algorithm C with one valid Top-5 per outer decision and actual-width stop.

The v45 measurement, isolation, and checkpoint code is retained. This new
version changes only the Algorithm C search/outer-loop boundaries; historical
v45 code and its sealed releases remain byte-for-byte unchanged.
"""

from __future__ import annotations

from pathlib import Path

from scripts import mysql_algorithm_c_interval_control_v1 as interval_control
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v45 as v45


LOCAL_REAL_REPLAY_LIMIT = 5
GLOBAL_COVERAGE_REAL_REPLAY_LIMIT = 5
GLOBAL_COVERAGE_SAMPLE_COUNT = v45.GLOBAL_COVERAGE_SAMPLE_COUNT
scene_checkpoint = v45.scene_checkpoint

_source = v45._source


def _replace_once(old: str, new: str, label: str) -> None:
    global _source
    if _source.count(old) != 1:
        raise RuntimeError(f"Algorithm C v46 {label} boundary differs")
    _source = _source.replace(old, new)


# B's frozen policy has a 0.1% QPS floor and used canonical-baseline QPS in
# early versions. C now follows PG's zero-noise-floor, last-accepted rule.
_replace_once(
    "from scripts import mysql_algorithm_b_pg_outer_policy_v31 as pg_outer",
    "from scripts import mysql_algorithm_c_pg_outer_policy_v1 as pg_outer",
    "outer acceptance policy",
)
_replace_once(
    "parser = argparse.ArgumentParser(description=__doc__)",
    '''parser = argparse.ArgumentParser(
        description="MySQL Algorithm C v120: complete Top-5 batches and actual interval precision"
    )''',
    "command help description",
)

# Keep all four scenes independent; the 100-round value is historical
# accounting only. A generator exhaustion is an incomplete result, never a
# precision-converged result.
_replace_once(
    "if candidate_space_exhausted or outer_attempt >= MAXIMUM_OUTER_ATTEMPTS:",
    "if candidate_space_exhausted:",
    "fixed outer-round stop",
)
_replace_once(
    '''"maximum_outer_attempts_exhausted": (
                        outer_attempt >= MAXIMUM_OUTER_ATTEMPTS
                    ),''',
    '''"maximum_outer_attempts_exhausted": False,''',
    "fixed round termination audit",
)
_replace_once(
    '''"target_accepted_rounds_per_scene": TARGET_ACCEPTED_ROUNDS,
            "maximum_rejected_outer_attempts_per_scene": None,
            "maximum_outer_attempts": MAXIMUM_OUTER_ATTEMPTS,''',
    '''"target_accepted_rounds_per_scene": None,
            "maximum_rejected_outer_attempts_per_scene": None,
            "maximum_outer_attempts": None,
            "normal_stop_uses_actual_interval_width": True,''',
    "runtime budget manifest",
)

# The first batch is ordinary C resource ranking. A complete valid rejection
# sets the existing per-scene miss counter to one. The next round uses the
# current-interval sparse/Halton plan coverage plus fresh trusted-gradient
# identities, with one physical Top-5 batch. The incumbent remains the anchor.
_replace_once(
    '''                ranking_started = time.monotonic()
                with ThreadPoolExecutor(''',
    '''                for scene in active_scenes:
                    if int(state["consecutive_failed_rounds"][scene]) != 1:
                        continue
                    queue = state.setdefault(
                        "algorithm_c_retained_region_queue", {}
                    ).setdefault(scene, [])
                    expanded, remaining, selected_region = (
                        interval_control.take_retained_region(
                            queue, ranges_by_scene[scene],
                            initial_ranges_by_scene[scene],
                            params_by_scene[scene], active_by_scene[scene],
                        )
                    )
                    if selected_region is not None:
                        parent_ranges = copy.deepcopy(ranges_by_scene[scene])
                        state.setdefault(
                            "algorithm_c_first_miss_parent_ranges", {}
                        )[scene] = parent_ranges
                        state["algorithm_c_retained_region_queue"][scene] = remaining
                        ranges_by_scene[scene] = expanded
                        state["ranges_by_scene"][scene] = copy.deepcopy(expanded)
                        state.setdefault(
                            "algorithm_c_first_miss_region_audit", {}
                        )[scene] = {
                            "outer_attempt": outer_attempt,
                            "selected_region": selected_region,
                            "parent_ranges": parent_ranges,
                            "expanded_ranges": copy.deepcopy(expanded),
                            "qps_used_for_selection": False,
                        }
                        alg.atomic(state_path, state)
                ranking_started = time.monotonic()
                with ThreadPoolExecutor(''',
    "retained region reopen",
)

_replace_once(
    '''                ranking_elapsed += sum(
                    float(row.get("candidate_supplementation_elapsed_seconds", 0.0))
                    for row in ranking_rows.values()
                )
            ranking_document = {''',
    '''                ranking_elapsed += sum(
                    float(row.get("candidate_supplementation_elapsed_seconds", 0.0))
                    for row in ranking_rows.values()
                )
                for scene in active_scenes:
                    if int(state["consecutive_failed_rounds"][scene]) != 1:
                        continue
                    coverage_rows = global_coverage.candidate_rankings(
                        scene,
                        params_by_scene[scene],
                        ranges_by_scene[scene],
                        incumbents[scene]["resource_means"],
                        freeze=freeze,
                        worker=workers[representatives[scene]],
                    )
                    rankings[scene], expansion_audit = (
                        interval_control.select_expanded_top5(
                            coverage_rankings=coverage_rows,
                            local_rankings=all_rankings[scene],
                            incumbent_params=params_by_scene[scene],
                            replayed_signatures=replayed[scene],
                            accepted_signatures=set(state[
                                "previously_accepted_parameter_signatures"
                            ][scene]),
                            policy=pg_outer,
                            optimizer_params_key=alg.optimizer_params_key,
                        )
                    )
                    replay_dedup[scene]["first_miss_expansion"] = expansion_audit
            ranking_document = {''',
    "first-miss candidate expansion",
)

# The old runtime would keep replaying fallback and global coverage within
# the same outer decision. PG v7 treats five candidates as one semantic batch:
# miss one expands the following batch, and miss two immediately bisects.
begin = '            fallback_sets: dict[str, list[dict[str, Any]]] = {}\n'
end = '            alg.atomic(output / f"outer_{outer_attempt:03d}" / "companion_audit.json", {'
if _source.count(begin) != 1 or _source.count(end) != 1:
    raise RuntimeError("Algorithm C v46 post-Top-5 expansion boundary differs")
start_index = _source.index(begin)
end_index = _source.index(end, start_index)
_source = _source[:start_index] + '''            alg.atomic(
                output / f"outer_{outer_attempt:03d}" / "pg_outer_expansion_audit.json",
                {
                    "schema_version": "mysql_algorithm_c_interval_control_v1",
                    "status": "PASS",
                    "outer_attempt": outer_attempt,
                    "batch_unit": "one_complete_valid_top5",
                    "next_batch_expands_after_first_miss": True,
                    "coverage_before_second_miss_shrink": False,
                    "qps_used_for_generation_or_ranking": False,
                },
            )
''' + _source[end_index:]

_replace_once(
    '''            for scene in active_scenes:
                rows = evaluations[scene]
                if not rows:''',
    '''            for scene in active_scenes:
                rows = evaluations[scene]
                interval_control.require_complete_batch(
                    selected_count=len(rankings[scene]),
                    rows=rows,
                    technical_exclusions=technical_batch_exclusions,
                    physical_valid=pg_outer.physical_valid,
                )
                if not rows:''',
    "complete batch semantic credit",
)

_replace_once(
    '''                    params_by_scene[scene] = dict(chosen["params"])
                    incumbents[scene] = {''',
    '''                    prior_target_resources = copy.deepcopy(
                        incumbents[scene]["resource_means"]
                    )
                    params_by_scene[scene] = dict(chosen["params"])
                    incumbents[scene] = {''',
    "accepted resource reference",
)
_replace_once(
    '''                    ranges_by_scene[scene] = alg.shrink_ranges(
                        ranges_by_scene[scene], params_by_scene[scene],
                        shrink_active_parameters,
                    )''',
    '''                    ranges_before_accept = copy.deepcopy(ranges_by_scene[scene])
                    ranges_by_scene[scene] = alg.shrink_ranges(
                        ranges_by_scene[scene], params_by_scene[scene],
                        shrink_active_parameters,
                    )
                    interval_control.require_binary_progress(
                        ranges_before_accept, ranges_by_scene[scene],
                        active_by_scene[scene],
                    )
                    target_resource = alg.TARGET[scene]
                    predicted = selected_ranking.get("predicted_resources") or (
                        selected_ranking.get("experience_prediction") or {}
                    )
                    reference_resource = float(
                        prior_target_resources[target_resource]
                    )
                    resource_potential = (
                        max(0.0, 1.0 - float(predicted[target_resource]) /
                            reference_resource)
                        if isinstance(predicted, Mapping)
                        and target_resource in predicted
                        and reference_resource > 0 else 0.0
                    )
                    retained = interval_control.retained_complement_regions(
                        ranges_before_accept, ranges_by_scene[scene],
                        active_by_scene[scene],
                        resource_potential=resource_potential,
                    )
                    state.setdefault(
                        "algorithm_c_retained_region_queue", {}
                    ).setdefault(scene, []).extend(retained)
                    state.setdefault(
                        "algorithm_c_first_miss_parent_ranges", {}
                    ).pop(scene, None)''',
    "accepted interval progress",
)
_replace_once(
    '''                    ranges_by_scene[scene], rejection_progress = (
                        rejection_cycle.advance_rejection_cycle(''',
    '''                    parent_ranges = state.setdefault(
                        "algorithm_c_first_miss_parent_ranges", {}
                    ).pop(scene, None)
                    if parent_ranges is not None:
                        ranges_by_scene[scene] = parent_ranges
                    ranges_before_rejection = copy.deepcopy(ranges_by_scene[scene])
                    ranges_by_scene[scene], rejection_progress = (
                        rejection_cycle.advance_rejection_cycle(''',
    "rejection interval snapshot",
)
_replace_once(
    '''                    if rejection_progress["forced_incumbent_shrink"]:
                        replayed[scene].clear()''',
    '''                    if rejection_progress["forced_incumbent_shrink"]:
                        interval_control.require_binary_progress(
                            ranges_before_rejection, ranges_by_scene[scene],
                            active_by_scene[scene],
                        )
                        replayed[scene].clear()''',
    "second-miss strict progress",
)

exec(compile(_source, str(Path(__file__).resolve()), "exec"), globals())
