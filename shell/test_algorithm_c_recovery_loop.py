#!/usr/bin/env python3
"""Exercise the actual adaptive loop with deterministic, database-free replays."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from algorithm_c_adaptive_search import AdaptiveSearchPolicy
from algorithm_c_execution_role import FORMAL
from shell import run_pg_algorithm_c_scene as base
from shell.run_pg_algorithm_c_budgeted_scene import BudgetedLaneController


def controller():
    c = BudgetedLaneController.__new__(BudgetedLaneController)
    c.adaptive_policy = AdaptiveSearchPolicy()
    c.execution_role = FORMAL
    c.target = "cpu"
    c.budget = None
    c.stop_requested = False
    c.policy = {}
    c.accepted_target = 7
    c.normalized_width_threshold = 0.01
    c.ranges = {name: base.ParameterRange(*limits) for name, limits in base.DEFAULT_RANGES.items()}
    c.state = {"round_index": 0, "canonical_baseline": {"resources": {"cpu": 4.0}, "db_qps": 100.0},
               "accepted": 0, "normalized_width": 1.0,
               "incumbent_params": {name: limits[0] for name, limits in base.DEFAULT_RANGES.items()},
               "consecutive_missed_outer_rounds": 0}
    c.trial = {
        "started_at_epoch_s": time.time(), "search_replays": 0,
        "search_valid_replays": 0, "search_technical_failures": 0,
        "baseline_wall_s": 0.0, "planning_s": 0.0, "replay_batch_wall_s": 0.0,
        "stagnation": 0, "region_queue": [], "active_region_mode": "gradient",
        "feasible_archive": {"best": {"params": {"p": 1.0},
                                      "observations": [{"label": "observed", "qps": 105.0}]}},
    }
    c.save_state = mock.Mock()
    c.log = mock.Mock()
    c.baseline = mock.Mock()
    c.verify_baseline_execution_domain = mock.Mock()
    c.restore_clean_baseline = mock.Mock(return_value={"passed": True})
    c.finish_trial = mock.Mock()
    c.accept = mock.Mock()
    c.select_outer_admitted_candidates = lambda rankings, **kw: (rankings[:kw["limit"]], {})
    actions = []

    def generate(round_index, **kwargs):
        action = c.trial["latest_adaptive_decision"]["action"]
        actions.append(action)
        return [{"optimized_params": {"p": round_index * 10 + n},
                 "three_level_fingerprint": {"plan_level": "same-plan"}}
                for n in range(10)]

    c.generate_rankings = generate
    c.generate_first_miss_rankings = generate
    c.generate_recovered_gradient_rankings = generate
    c.generate_global_plan_coverage_rankings = generate
    c.generate_dynamic_numeric_refinement_rankings = generate

    def replay(candidates, **kwargs):
        c.trial["search_replays"] += len(candidates)
        c.trial["search_valid_replays"] += len(candidates)
        return [{"status": "ok", "candidate_ranking": candidate} for candidate in candidates]

    c.replay_batch = replay
    c.evaluate_batch = mock.Mock(side_effect=lambda rows, **kw: (None, len(rows)))
    return c, actions


def main():
    c, actions = controller()
    c.run()
    assert actions == ["gradient_search", "first_miss_expansion"] * 7, actions
    c.finish_trial.assert_called_once_with("adaptive_convergence")
    assert c.trial["consecutive_rejected_batches"] == 0
    assert c.state["accepted"] == 0
    assert c.state["incumbent_centered_stagnation_refinement_count"] == 7
    assert c.state["normalized_width"] <= 0.01
    # Each miss is one full Top-5 batch (2+2+1 worker waves). A
    # no-acceptance ordinary/expansion pair spends ten distinct replays.
    assert c.trial["search_replays"] == 70
    assert c.trial["completed_search_waves"] == 42

    # A measured safe/unsafe bracket gets one physical batch before each
    # contraction. Rejected refinement cannot postpone contraction forever.
    bracketed, bracket_actions = controller()
    bracketed.outer_boundary_pair = mock.Mock(return_value={"pair_id": "measured"})
    original_numeric = bracketed.generate_dynamic_numeric_refinement_rankings
    def refine_bracket(round_index):
        bracketed.trial["outer_boundary_refinement_attempts_since_shrink"] = 1
        return original_numeric(round_index)
    bracketed.generate_dynamic_numeric_refinement_rankings = refine_bracket
    bracketed.run()
    assert bracket_actions == [
        "gradient_search", "first_miss_expansion", "frontier_numeric_refinement",
    ] * 7, bracket_actions
    assert bracketed.trial["search_replays"] == 105
    bracketed.finish_trial.assert_called_once_with("adaptive_convergence")

    # Pending exploration cannot delay a due shrink or precision termination.
    region, region_actions = controller()
    region.trial["region_queue"] = [{"optimistic_resource_gain": 0.25}]
    def revisit(_round):
        region.trial["region_queue"].pop()
        return True
    region.revisit_region = mock.Mock(side_effect=revisit)
    region.run()
    region.revisit_region.assert_not_called()
    assert region_actions == actions
    region.finish_trial.assert_called_once_with("adaptive_convergence")

    # Empty and invalid batches do not consume the two-rejection threshold.
    counting, _ = controller()
    rows = [{"status": "ok", "candidate_ranking": {}} for _ in range(5)]
    candidates = [row["candidate_ranking"] for row in rows]
    counting.record_adaptive_batch(candidates, rows, None)
    assert counting.trial["consecutive_rejected_batches"] == 1
    assert counting.trial["completed_search_waves"] == 3
    counting.record_adaptive_batch([], [], None)
    counting.record_adaptive_batch(candidates, rows[:-1], None)
    assert counting.trial["consecutive_rejected_batches"] == 1
    counting.record_adaptive_batch(candidates, rows, None, archive_improved=True)
    assert counting.trial["consecutive_no_progress_batches"] == 0
    assert counting.trial["consecutive_rejected_batches"] == 2

    # Accepting a new search center starts a fresh recovery epoch.
    accepted, _ = controller()
    accepted.ranges = {name: base.ParameterRange(*limits) for name, limits in base.DEFAULT_RANGES.items()}
    accepted.trial.update({"visited_regions": [], "recovery_stage": 3,
                           "stage_rejected_batches": 5, "consecutive_rejected_batches": 9,
                           "numeric_pending_rankings": [{"stale": True}]})
    def shrink_once(*args):
        accepted.ranges, _ = base.binary_shrink_parameter_ranges(
            accepted.ranges, accepted.state["incumbent_params"], base.ACTIVE_PARAMS,
        )
    with mock.patch.object(base.LaneController, "accept", side_effect=shrink_once):
        BudgetedLaneController.accept(accepted, {"normalized": {"resources": {"cpu": 3.0}},
                                               "raw": {"candidate_ranking": {}}}, 1)
    assert accepted.trial["recovery_stage"] == 0
    assert accepted.trial["consecutive_rejected_batches"] == 0
    assert accepted.trial["numeric_pending_rankings"] == []

    # A mixed valid/invalid batch must not reach semantic evaluation or accept.
    bad, _ = controller()
    bad.replay_batch = lambda candidates, **kw: [
        {"status": "ok" if i == 0 else "failed", "candidate_ranking": row}
        for i, row in enumerate(candidates)
    ]
    bad.run()
    bad.evaluate_batch.assert_not_called()
    bad.accept.assert_not_called()
    assert bad.trial.get("consecutive_rejected_batches", 0) == 0
    bad.finish_trial.assert_called_once_with("technical_batch_failure")

    # Back-to-back empty generators immediately advance all recovery tiers.
    empty, empty_actions = controller()
    def empty_gen(round_index, **kwargs):
        empty_actions.append(empty.trial["latest_adaptive_decision"]["action"])
        return []
    for method in ("generate_rankings", "generate_first_miss_rankings", "generate_recovered_gradient_rankings",
                   "generate_global_plan_coverage_rankings", "generate_dynamic_numeric_refinement_rankings"):
        setattr(empty, method, empty_gen)
    empty.run()
    assert empty_actions[:3] == ["gradient_search", "first_miss_expansion", "frontier_numeric_refinement"]
    assert empty.trial.get("consecutive_rejected_batches", 0) == 0
    assert empty.trial.get("completed_search_waves", 0) == 0
    assert empty_actions.count("frontier_numeric_refinement") == 6
    empty.finish_trial.assert_called_once_with("numeric_windows_exhausted_without_physical_evidence")

    # Admissible numeric candidates remain queued after only part of a pool is tested.
    numeric, _ = controller()
    numeric.ranges = {name: base.ParameterRange(*limits) for name, limits in base.DEFAULT_RANGES.items()}
    numeric.state["incumbent_params"] = {name: limits[0] for name, limits in base.DEFAULT_RANGES.items()}
    consumed = set()
    numeric.select_outer_admitted_candidates = lambda rows, **kw: (
        [row for row in rows if row["optimized_params"]["p"] not in consumed][:kw["limit"]], {}
    )
    numeric.outer_boundary_refinement_vectors = mock.Mock(return_value=([{"p": 1}], {"contract": "boundary"}))
    numeric.progressive_numeric_refinement_vectors = mock.Mock(
        return_value=([{"p": 2}, {"p": 3}, {"p": 4}], {"contract": "progressive"}))
    numeric.generate_global_plan_coverage_rankings = lambda *args, **kw: [
        {"optimized_params": row} for row in numeric._adaptive_coverage_override
    ]
    # Boundary generated a ranking, but exact admission rejects it: fallback is mandatory.
    consumed.add(1)
    first = BudgetedLaneController.generate_dynamic_numeric_refinement_rankings(numeric, 1)
    assert len(first) == 3
    assert numeric.trial["outer_boundary_refinement_attempts_since_shrink"] == 1
    numeric.progressive_numeric_refinement_vectors.assert_called_once()
    consumed.add(2)
    second = BudgetedLaneController.generate_dynamic_numeric_refinement_rankings(numeric, 2)
    assert [row["optimized_params"]["p"] for row in second] == [3, 4]
    numeric.progressive_numeric_refinement_vectors.assert_called_once()

    # Reliable partial gradient evidence reaches directional boundary recovery.
    gradient, _ = controller()
    step = base.IncompleteSubsetOptimizationError("partial", [], "unused")
    step.plan_space_saturation_evidence = True
    step.eligible_candidate_rankings = [{"p": 1}]
    boundary = base.IncompleteSubsetOptimizationError("partial boundary", [], "unused")
    boundary.plan_space_saturation_evidence = True
    boundary.eligible_candidate_rankings = [{"p": 2}]
    gradient.generate_rankings = mock.Mock(side_effect=step)
    gradient.boundary_inward_bisection_rankings = mock.Mock(side_effect=boundary)
    gradient.merge_exhausted_partial_topk = mock.Mock(return_value=[{"p": 1}, {"p": 2}])
    recovered = BudgetedLaneController.generate_recovered_gradient_rankings(gradient, 3)
    assert len(recovered) == 2
    gradient.boundary_inward_bisection_rankings.assert_called_once()
    gradient.merge_exhausted_partial_topk.assert_called_once_with([step, boundary], round_index=3)
    step.technical_candidate_generation_failure = True
    gradient.boundary_inward_bisection_rankings.reset_mock()
    try:
        BudgetedLaneController.generate_recovered_gradient_rankings(gradient, 4)
    except base.IncompleteSubsetOptimizationError:
        pass
    else:
        raise AssertionError("technical planning failure was treated as an exhausted pool")
    gradient.boundary_inward_bisection_rankings.assert_not_called()
    print("PASS: actual recovery loop, batch counts, partial failures, numeric backfill, gradient boundaries", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
