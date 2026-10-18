#!/usr/bin/env python3
"""Behavior tests for interval-driven Algorithm C outer control."""

from __future__ import annotations

from algorithm_c_adaptive_search import (
    AdaptiveSearchEvidence, AdaptiveSearchPolicy, adaptive_policy_audit, adaptive_search_decision,
)


def evidence(**overrides):
    values = dict(
        completed_waves=3, attempted_search_replays=8, valid_search_replays=8,
        active_wall_s=1200.0, consecutive_no_progress_waves=0,
        novel_plan_fingerprints=1, duplicate_plan_fraction=0.25,
        normalized_resource_gain=0.03, maximum_model_uncertainty=0.1,
        pending_region_count=0, best_pending_region_optimistic_gain=0.0,
        outer_qps_accepted=False, latest_valid_search_replays=2,
    )
    values.update(overrides)
    return AdaptiveSearchEvidence(**values)


def main() -> int:
    policy = AdaptiveSearchPolicy()
    audit = adaptive_policy_audit(policy)
    assert not audit["normal_stop_uses_fixed_replay_cap"]
    assert not audit["normal_stop_uses_fixed_wall_cap"]
    assert not audit["fixed_operational_replay_or_wall_guard_enabled"]
    assert not audit["pending_regions_or_numeric_candidates_veto_convergence"]
    ordinary = adaptive_search_decision(policy, evidence())
    assert ordinary.action == "gradient_search"
    assert ordinary.requested_candidates == 5
    expanded = adaptive_search_decision(policy, evidence(
        consecutive_rejected_batches=1,
    ))
    assert expanded.action == "first_miss_expansion"
    assert expanded.requested_candidates == 5
    # Diversity, resource gains and remaining queues cannot postpone bisection.
    busy = dict(consecutive_rejected_batches=2, novel_plan_fingerprints=20,
                normalized_resource_gain=0.5, pending_region_count=100,
                pending_numeric_candidate_count=100, maximum_model_uncertainty=1.0)
    for finalist in (True, False):
        decision = adaptive_search_decision(policy, evidence(**busy, has_feasible_finalist=finalist))
        assert decision.action == "shrink_incumbent" and not decision.converged
        done = adaptive_search_decision(policy, evidence(
            **busy, has_feasible_finalist=finalist, normalized_interval_width=0.01,
        ))
        assert done.converged and done.action == "finish_search"
    for replays, wall in ((256, 172800.0), (10000, 9999999.0)):
        decision = adaptive_search_decision(policy, evidence(
            attempted_search_replays=replays, active_wall_s=wall,
        ))
        assert decision.action == "gradient_search" and not decision.converged
    assert adaptive_search_decision(policy, evidence(
        normalized_interval_width=0.010001,
    )).action == "gradient_search"
    assert adaptive_search_decision(policy, evidence(
        technical_failure_count=1, normalized_interval_width=0.001,
    )).action == "operational_safety_stop_invalid"
    assert adaptive_search_decision(policy, evidence(
        consecutive_empty_admissions=1,
    )).action == "first_miss_expansion"
    assert adaptive_search_decision(policy, evidence(
        consecutive_empty_admissions=2, recovery_stage=1,
    )).action == "frontier_numeric_refinement"
    invalid = adaptive_search_decision(policy, evidence(
        recovery_stage=3, consecutive_empty_numeric_windows=6,
    ))
    assert not invalid.converged and not invalid.valid_for_quality_or_efficiency_claim
    assert adaptive_search_decision(policy, evidence(
        pending_outer_confirmation_count=1,
    )).action == "gradient_search"
    print("PASS: interval precision, first-miss exploration, two-miss shrink, no replay/wall cutoff", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
