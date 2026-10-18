from __future__ import annotations

import math

from scripts.mysql_outer_rejection_cycle_v1 import (
    advance_rejection_cycle,
    convergence_document,
    maximum_normalized_width,
    shrink_ranges_around_incumbent,
)


INITIAL = {
    "a": {"lower": 1.0, "upper": 100.0, "metadata": "kept"},
    "b": {"lower": 10.0, "upper": 1000.0},
}


def test_first_rejection_expands_without_shrinking_or_credit() -> None:
    ranges, audit = advance_rejection_cycle(
        accepted=False,
        consecutive_rejections=0,
        ranges=INITIAL,
        incumbent_params={"a": 10.0, "b": 100.0},
        active_params=["a", "b"],
    )
    assert ranges == INITIAL
    assert audit["forced_incumbent_shrink"] is False
    assert audit["consecutive_rejections_after"] == 1
    assert audit["accepted_round_credit"] == 0
    assert audit["next_outer_expansion"] == "predicted_points_and_plan_tree"


def test_second_rejection_halves_log_width_around_unchanged_incumbent() -> None:
    ranges, audit = advance_rejection_cycle(
        accepted=False,
        consecutive_rejections=1,
        ranges=INITIAL,
        incumbent_params={"a": 10.0, "b": 100.0},
        active_params=["a", "b"],
    )
    assert audit["forced_incumbent_shrink"] is True
    assert audit["consecutive_rejections_after"] == 0
    assert audit["accepted_round_credit"] == 0
    assert audit["incumbent_changed_by_cycle"] is False
    assert ranges["a"]["metadata"] == "kept"
    assert math.isclose(
        maximum_normalized_width(
            ranges=ranges, initial_ranges=INITIAL, active_params=["a", "b"]
        ),
        0.5,
        rel_tol=1e-12,
    )


def test_boundary_anchor_shifts_window_but_keeps_full_half_width() -> None:
    ranges, details = shrink_ranges_around_incumbent(
        ranges=INITIAL,
        incumbent_params={"a": 1.0, "b": 1000.0},
        active_params=["a", "b"],
    )
    assert math.isclose(ranges["a"]["lower"], 1.0, rel_tol=1e-12)
    assert math.isclose(ranges["a"]["upper"], 10.0, rel_tol=1e-12)
    assert math.isclose(ranges["b"]["lower"], 100.0, rel_tol=1e-12)
    assert math.isclose(ranges["b"]["upper"], 1000.0, rel_tol=1e-12)
    assert math.isclose(details["a"]["log_width_ratio"], 0.5, rel_tol=1e-12)
    assert math.isclose(details["b"]["log_width_ratio"], 0.5, rel_tol=1e-12)


def test_acceptance_resets_rejection_cycle_without_forced_shrink() -> None:
    ranges, audit = advance_rejection_cycle(
        accepted=True,
        consecutive_rejections=1,
        ranges=INITIAL,
        incumbent_params={"a": 10.0, "b": 100.0},
        active_params=["a", "b"],
    )
    assert ranges == INITIAL
    assert audit["consecutive_rejections_after"] == 0
    assert audit["forced_incumbent_shrink"] is False
    assert audit["accepted_round_credit"] == 1


def test_width_converges_after_seven_binary_shrinks() -> None:
    ranges = INITIAL
    for _ in range(7):
        ranges, _ = shrink_ranges_around_incumbent(
            ranges=ranges,
            incumbent_params={"a": 10.0, "b": 100.0},
            active_params=["a", "b"],
        )
    document = convergence_document(
        ranges=ranges, initial_ranges=INITIAL, active_params=["a", "b"]
    )
    assert document["normalized_width"] <= 0.01
    assert document["converged"] is True
