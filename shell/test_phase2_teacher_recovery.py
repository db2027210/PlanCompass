#!/usr/bin/env python3
"""Regression checks for Phase2 teacher recovery auditing."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from phase2_teacher_recovery import (
    COST_PARAM_NAMES,
    audit_candidate_rankings,
    audit_teacher_range_containment,
    validate_blind_recovery_inputs,
)


RANGES = {
    "seq_page_cost": {"min_value": 0.0001, "max_value": 100.0},
    "random_page_cost": {"min_value": 0.0001, "max_value": 20.0},
    "cpu_tuple_cost": {"min_value": 0.000001, "max_value": 1.0},
    "cpu_index_tuple_cost": {"min_value": 0.000001, "max_value": 1.0},
    "cpu_operator_cost": {"min_value": 0.000001, "max_value": 1.0},
    "parallel_setup_cost": {"min_value": 0.0, "max_value": 10000.0},
    "parallel_tuple_cost": {"min_value": 0.000001, "max_value": 10.0},
}

TEACHER = {
    "seq_page_cost": 87.5000125,
    "random_page_cost": 2.5000875,
    "cpu_tuple_cost": 0.625000375,
    "cpu_index_tuple_cost": 0.875000125,
    "cpu_operator_cost": 0.375000625,
    "parallel_setup_cost": 8750.0,
    "parallel_tuple_cost": 1.250000875,
}


def test_natural_rank_and_containment() -> None:
    rankings = [
        {"rank": 1, "params": {name: RANGES[name]["min_value"] for name in COST_PARAM_NAMES}},
        {"rank": 7, "params": dict(TEACHER), "source": "candidate_pair_probe"},
    ]
    audit = audit_candidate_rankings(rankings, TEACHER, RANGES, top_k=15)
    assert audit["exact_match"] is True, audit
    assert audit["within_top_k"] is True, audit
    assert audit["closest"]["rank"] == 7, audit

    right_left_ranges = {
        "seq_page_cost": {"min_value": 50.00005, "max_value": 100.0},
        "random_page_cost": {"min_value": 0.0001, "max_value": 10.00005},
        "cpu_tuple_cost": {"min_value": 0.5000005, "max_value": 1.0},
        "cpu_index_tuple_cost": {"min_value": 0.5000005, "max_value": 1.0},
        "cpu_operator_cost": {"min_value": 0.000001, "max_value": 0.5000005},
        "parallel_setup_cost": {"min_value": 5000.0, "max_value": 10000.0},
        "parallel_tuple_cost": {"min_value": 0.000001, "max_value": 5.0000005},
    }
    containment = audit_teacher_range_containment(right_left_ranges, TEACHER)
    assert containment["contained"] is True, containment

    right_left_ranges["random_page_cost"] = {"min_value": 10.00005, "max_value": 20.0}
    containment = audit_teacher_range_containment(right_left_ranges, TEACHER)
    assert containment["contained"] is False, containment
    assert "random_page_cost" in containment["outside_params"], containment


def test_answer_leakage_guard() -> None:
    valid = validate_blind_recovery_inputs(
        TEACHER,
        RANGES,
        teacher_label_excluded_from_training=True,
        forced_seed_records=[],
    )
    assert valid["passed"] is True, valid

    leaked = validate_blind_recovery_inputs(
        TEACHER,
        RANGES,
        teacher_label_excluded_from_training=True,
        forced_seed_records=[{"params": dict(TEACHER)}],
    )
    assert leaked["passed"] is False, leaked
    assert leaked["matching_forced_seed_indices"] == [0], leaked

    unrelated_seed = validate_blind_recovery_inputs(
        TEACHER,
        RANGES,
        teacher_label_excluded_from_training=True,
        forced_seed_records=[
            {"params": {name: RANGES[name]["min_value"] for name in COST_PARAM_NAMES}}
        ],
    )
    assert unrelated_seed["passed"] is False, unrelated_seed
    assert unrelated_seed["forced_seed_record_count"] == 1, unrelated_seed


def main() -> int:
    test_natural_rank_and_containment()
    test_answer_leakage_guard()
    print("phase2 teacher recovery tests passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
