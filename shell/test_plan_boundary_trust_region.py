#!/usr/bin/env python3
"""Focused tests for plan-boundary-aware trust-region pure logic."""

from __future__ import annotations

import math
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from plan_boundary_trust_region import (  # noqa: E402
    BoundaryBracket,
    DirectionalPrior,
    ParameterRange,
    PlanObservation,
    RoundState,
    classify_transition,
    decide_real_candidate,
    denormalize_value,
    expanding_radii,
    finish_round,
    local_secant,
    maximum_ray_distance,
    nearest_plan_boundary,
    normalize_value,
    normalized_direction,
    point_on_ray,
    refine_boundary,
    select_cross_cluster_candidates,
)


RANGES = {
    "log_p": ParameterRange(1.0, 100.0, "log"),
    "linear_p": ParameterRange(0.0, 10.0, "linear"),
}


def test_normalized_coordinates_round_trip() -> None:
    assert abs(normalize_value(10.0, RANGES["log_p"]) - 0.5) < 1e-12
    assert abs(denormalize_value(0.5, RANGES["log_p"]) - 10.0) < 1e-12
    assert normalize_value(2.5, RANGES["linear_p"]) == 0.25
    assert denormalize_value(0.25, RANGES["linear_p"]) == 2.5


def test_global_prior_only_selects_direction() -> None:
    priors = {
        "log_p": DirectionalPrior({"memory": 1.0}, confidence=0.8),
        "linear_p": DirectionalPrior({"memory": -1.0}, confidence=0.6),
    }
    direction = normalized_direction(priors, target_resource="memory")
    assert direction["log_p"] < 0.0
    assert direction["linear_p"] > 0.0
    assert abs(sum(value * value for value in direction.values()) - 1.0) < 1e-12


def test_ray_and_expanding_radius_are_bounded() -> None:
    incumbent = {"log_p": 10.0, "linear_p": 5.0, "fixed": 3.0}
    direction = {"log_p": 1.0}
    maximum = maximum_ray_distance(incumbent, RANGES, direction)
    assert abs(maximum - 0.5) < 1e-12
    radii = expanding_radii(maximum, initial_radius=0.0625)
    assert radii == [0.0625, 0.125, 0.25, 0.5]
    endpoint = point_on_ray(incumbent, RANGES, direction, maximum)
    assert abs(endpoint["log_p"] - 100.0) < 1e-10
    assert endpoint["fixed"] == 3.0


def test_plateau_expands_until_first_exact_boundary() -> None:
    rows = [
        PlanObservation(distance=d, params={"p": d}, fingerprint=fp)
        for d, fp in [(0.0, "A"), (0.1, "A"), (0.2, "A"), (0.4, "B")]
    ]
    bracket = nearest_plan_boundary(rows)
    assert bracket is not None
    assert bracket.plateau.distance == 0.2
    assert bracket.crossed.distance == 0.4
    assert classify_transition(rows[0], rows[1]) == "plateau_expand_radius"


def test_binary_refinement_finds_stair_edge() -> None:
    low = PlanObservation(0.25, {"p": 0.25}, "A")
    high = PlanObservation(0.5, {"p": 0.5}, "B")

    def observe(distance: float) -> PlanObservation:
        return PlanObservation(distance, {"p": distance}, "A" if distance < 0.375 else "B")

    refined, added = refine_boundary(BoundaryBracket(low, high), observe, iterations=8)
    assert len(added) == 8
    assert refined.width == (0.5 - 0.25) / 256.0
    assert refined.plateau.distance < 0.375 <= refined.crossed.distance


def test_error_gate_rejects_false_gradient_and_accepts_secant() -> None:
    before = PlanObservation(
        0.1, {"p": 1.0}, "A", resources={"memory": 80.0}, error_bounds={"memory": 1.0}
    )
    drift = PlanObservation(
        0.2, {"p": 2.0}, "A", resources={"memory": 81.5}, error_bounds={"memory": 1.0}
    )
    weak_boundary = PlanObservation(
        0.3, {"p": 3.0}, "B", resources={"memory": 81.5}, error_bounds={"memory": 1.0}
    )
    strong_boundary = PlanObservation(
        0.4, {"p": 4.0}, "C", resources={"memory": 75.0}, error_bounds={"memory": 1.0}
    )
    assert classify_transition(before, drift) == "plateau_expand_radius"
    assert classify_transition(before, weak_boundary) == "plan_boundary_needs_calibration"
    assert classify_transition(before, strong_boundary) == "credible_plan_boundary"
    assert local_secant(before, weak_boundary, "memory") is None
    assert local_secant(before, strong_boundary, "memory") is not None


def test_exact_plan_cluster_dedup() -> None:
    candidates = [
        {"id": 1, "workload_plan_fingerprint": "A"},
        {"id": 2, "workload_plan_fingerprint": "B"},
        {"id": 3, "workload_plan_fingerprint": "B"},
        {"id": 4, "workload_plan_fingerprint": "C"},
    ]
    selected = select_cross_cluster_candidates(
        candidates, incumbent_fingerprint="A", limit=5
    )
    assert [candidate["id"] for candidate in selected] == [2, 4]


def test_real_gate_requires_qps_target_and_non_target_safety() -> None:
    accepted = decide_real_candidate(
        incumbent_qps=10.0,
        incumbent_utilization={"cpu": 50.0, "memory": 85.0, "io": 60.0},
        candidate_qps=10.1,
        candidate_utilization={"cpu": 55.0, "memory": 84.0, "io": 70.0},
        target_resource="memory",
        hard_gate_passed=True,
    )
    assert accepted.accepted
    rejected = decide_real_candidate(
        incumbent_qps=10.0,
        incumbent_utilization={"cpu": 50.0, "memory": 85.0, "io": 60.0},
        candidate_qps=10.1,
        candidate_utilization={"cpu": 81.0, "memory": 84.0, "io": 70.0},
        target_resource="memory",
        hard_gate_passed=True,
    )
    assert not rejected.accepted
    assert rejected.reason == "non_target_resource_at_or_above_limit"


def test_rejection_never_changes_incumbent_range_or_counter() -> None:
    state = RoundState(
        incumbent={"params": {"p": 2.0}},
        ranges={"p": ParameterRange(1.0, 4.0)},
        accepted_shrink_count=4,
    )
    rejected = decide_real_candidate(
        incumbent_qps=10.0,
        incumbent_utilization={"memory": 85.0, "cpu": 50.0},
        candidate_qps=9.9,
        candidate_utilization={"memory": 84.0, "cpu": 50.0},
        target_resource="memory",
        hard_gate_passed=True,
    )
    outcome = finish_round(state, [{"params": {"p": 3.0}}], [rejected])
    assert outcome.status == "round_rejected/no_feasible_candidate"
    assert outcome.state == state
    assert outcome.state.accepted_shrink_count == 4


def main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}", flush=True)
    print(f"PASS {len(tests)} plan-boundary trust-region tests", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
