#!/usr/bin/env python3
"""Module."""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common_outer_loop import (  # noqa: E402
    acceptance_tier_for_miss_count,
    binary_shrink_parameter_ranges,
    cap_real_replay_top_k,
    decide_optimistic_replay_pruning,
    decide_outer_acceptance,
    decide_validation_acceptance,
    prune_ranked_candidates_for_replay,
    select_plan_diverse_representatives,
)
from resource_utility import ResourceUtilityCalculator  # noqa: E402


@dataclass(frozen=True)
class Range:
    min_value: float
    max_value: float
    transform: str = "linear"
    step: Optional[float] = None
    values: Optional[Tuple[float, ...]] = None


def test_global_real_replay_top_k_cap() -> None:
    assert cap_real_replay_top_k(1) == 1
    assert cap_real_replay_top_k(5) == 5
    assert cap_real_replay_top_k(16) == 5
    assert cap_real_replay_top_k(20) == 5
    assert cap_real_replay_top_k(0) == 5
    assert cap_real_replay_top_k(20, available_count=3) == 3


def test_acceptance_truth_table_and_current_incumbent() -> None:
    incumbent_resources = {"cpu": 45.0, "memory": 90.0, "io": 50.0}
    candidate_resources = {"cpu": 48.0, "memory": 89.0, "io": 55.0}
    accepted = decide_outer_acceptance(
        incumbent_db_qps=100.0,
        candidate_db_qps=101.0,
        incumbent_target_utilization=90.0,
        candidate_target_utilization=89.0,
        target_resource="memory",
        incumbent_resource_utilization=incumbent_resources,
        candidate_resource_utilization=candidate_resources,
        non_target_bottleneck_threshold=80.0,
        hard_gate_passed=True,
    )
    assert accepted.accepted

    cases = [
        (False, 101.0, 89.0, "hard_gate_failed"),
        (True, 100.0, 89.0, "db_qps_not_strictly_higher"),
        (True, None, 89.0, "missing_or_nonfinite_db_qps"),
        (True, 101.0, math.nan, "missing_or_nonfinite_target_utilization"),
    ]
    for gate, qps, util, reason in cases:
        decision = decide_outer_acceptance(
            incumbent_db_qps=100.0,
            candidate_db_qps=qps,
            incumbent_target_utilization=90.0,
            candidate_target_utilization=util,
            target_resource="memory",
            incumbent_resource_utilization=incumbent_resources,
            candidate_resource_utilization=candidate_resources,
            non_target_bottleneck_threshold=80.0,
            hard_gate_passed=gate,
        )
        assert not decision.accepted
        assert decision.reason == reason


    relative_to_current = decide_outer_acceptance(
        incumbent_db_qps=100.0,
        candidate_db_qps=99.0,
        incumbent_target_utilization=80.0,
        candidate_target_utilization=79.0,
        target_resource="memory",
        incumbent_resource_utilization={"cpu": 40.0, "memory": 80.0, "io": 50.0},
        candidate_resource_utilization={"cpu": 45.0, "memory": 79.0, "io": 55.0},
        non_target_bottleneck_threshold=80.0,
        hard_gate_passed=True,
    )
    assert not relative_to_current.accepted


def test_non_target_resource_cannot_become_new_bottleneck() -> None:
    incumbent = {
        "db_qps": 100.0,
        "utilization_pct": {"cpu": 45.0, "memory": 90.0, "io": 50.0},
    }
    candidate = {
        "db_qps": 101.0,
        "utilization_pct": {"cpu": 80.0, "memory": 89.0, "io": 55.0},
    }
    rejected = decide_validation_acceptance(
        incumbent,
        candidate,
        target_resource="memory",
        hard_gate_passed=True,
    )
    assert not rejected.accepted
    assert rejected.reason == "non_target_resource_became_bottleneck"
    assert rejected.non_target_bottleneck_resources == ["cpu"]
    assert rejected.candidate_non_target_bottlenecks == ["cpu"]

    candidate["utilization_pct"]["cpu"] = 79.999
    accepted = decide_validation_acceptance(
        incumbent,
        candidate,
        target_resource="memory",
        hard_gate_passed=True,
    )
    assert accepted.accepted


def test_non_target_resource_evidence_is_fail_closed() -> None:
    incumbent = {
        "db_qps": 100.0,
        "utilization_pct": {"cpu": 45.0, "memory": 90.0, "io": 50.0},
    }
    candidate = {
        "db_qps": 101.0,
        "utilization_pct": {"cpu": 45.0, "memory": 89.0},
    }
    missing = decide_validation_acceptance(
        incumbent,
        candidate,
        target_resource="memory",
        hard_gate_passed=True,
    )
    assert not missing.accepted
    assert missing.reason == "missing_or_nonfinite_non_target_utilization"

    for nonfinite in (float("nan"), float("inf"), -float("inf")):
        candidate["utilization_pct"]["io"] = nonfinite
        invalid_candidate = decide_validation_acceptance(
            incumbent,
            candidate,
            target_resource="memory",
            hard_gate_passed=True,
        )
        assert not invalid_candidate.accepted
        assert (
            invalid_candidate.reason
            == "missing_or_nonfinite_non_target_utilization"
        )

    incumbent["utilization_pct"]["io"] = 80.0
    candidate["utilization_pct"]["io"] = 50.0
    invalid_incumbent = decide_validation_acceptance(
        incumbent,
        candidate,
        target_resource="memory",
        hard_gate_passed=True,
    )
    assert not invalid_incumbent.accepted
    assert invalid_incumbent.reason == "incumbent_non_target_resource_is_bottleneck"


def test_transform_aware_binary_shrink() -> None:
    ranges = {
        "linear_p": Range(0.0, 100.0, "linear", step=5.0),
        "random_page_cost": Range(
            1.0,
            100.0,
            "log",
            values=(1.0, 10.0, 100.0),
        ),
        "inactive": Range(0.0, 8.0),
    }
    updated, details = binary_shrink_parameter_ranges(
        ranges,
        {"linear_p": 75.0, "random_page_cost": 5.0, "inactive": 7.0},
        ["linear_p", "random_page_cost"],
    )
    assert updated["linear_p"].min_value == 50.0
    assert updated["linear_p"].max_value == 100.0
    assert updated["linear_p"].step == 5.0
    assert updated["random_page_cost"].min_value == 1.0
    assert updated["random_page_cost"].max_value == 10.0
    assert updated["random_page_cost"].values == (1.0, 10.0, 100.0)
    assert updated["inactive"] == ranges["inactive"]
    assert details["random_page_cost"]["midpoint"] == 10.0


def _band_target_calculator() -> ResourceUtilityCalculator:
    return ResourceUtilityCalculator(
        score_mode="band_target",
        resource_weights={"cpu": 1.0, "memory": 1.0, "io": 3.0},
    )


def test_optimistic_pruning_uses_multidimensional_utility_box() -> None:
    calculator = _band_target_calculator()
    low_box = decide_optimistic_replay_pruning(
        incumbent_db_qps=100.0,
        incumbent_target_utilization=90.0,
        predicted_db_qps=1.0,
        db_qps_upper_error=0.0,
        predicted_target_utilization=99.0,
        target_utilization_lower_error=0.0,
        calibration_ready=True,
        calibration_scope_matches=True,
        simultaneous_coverage_guaranteed=True,
        predicted_score=1000.0,
        score_upper_error=1000.0,
        best_score=4.0,
        predicted_resource={"cpu": 20.0, "memory": 20.0, "io": 20.0},
        resource_error_bounds={
            "cpu": {"error_lower": 1.0, "error_upper": 1.0},
            "memory": {"error_lower": 1.0, "error_upper": 1.0},
            "io": {"error_lower": 1.0, "error_upper": 1.0},
        },
        capability={"cpu": 100.0, "memory": 100.0, "io": 100.0},
        utility_calculator=calculator,
    )
    assert low_box.pruned
    assert low_box.reason == "utility_upper_not_strictly_above_current_best"
    assert math.isclose(low_box.optimistic_score_upper or 0.0, 3.0425751479289938)

    assert low_box.optimistic_score_upper != 2000.0

    reaches_sweet_spots = decide_optimistic_replay_pruning(
        incumbent_db_qps=100.0,
        incumbent_target_utilization=90.0,
        predicted_db_qps=None,
        db_qps_upper_error=None,
        predicted_target_utilization=None,
        target_utilization_lower_error=None,
        calibration_ready=True,
        calibration_scope_matches=True,
        simultaneous_coverage_guaranteed=True,
        best_score=4.9,
        predicted_resource={"cpu": 65.0, "memory": 70.0, "io": 50.0},
        resource_error_bounds={"cpu": 1.0, "memory": 1.0, "io": 1.0},
        capability={"cpu": 100.0, "memory": 100.0, "io": 100.0},
        utility_calculator=calculator,
    )
    assert not reaches_sweet_spots.pruned
    assert reaches_sweet_spots.reason == "utility_upper_can_beat_current_best"
    assert reaches_sweet_spots.optimistic_score_upper == 5.0

    missing_box = decide_optimistic_replay_pruning(
        incumbent_db_qps=100.0,
        incumbent_target_utilization=90.0,
        predicted_db_qps=None,
        db_qps_upper_error=None,
        predicted_target_utilization=None,
        target_utilization_lower_error=None,
        calibration_ready=True,
        calibration_scope_matches=True,
        simultaneous_coverage_guaranteed=True,
        best_score=4.0,
        utility_calculator=calculator,
    )
    assert not missing_box.pruned
    assert not missing_box.bounds_usable


def test_pruning_fail_open_and_does_not_consume_replay() -> None:
    records = [
        {"rank": 1, "optimized_params": {"p": 1.0}},
        {
            "rank": 2,
            "optimized_params": {"p": 2.0},
            "optimistic_replay_pruning_input": {
                "calibration_ready": True,
                "calibration_scope_matches": True,
                "simultaneous_coverage_guaranteed": True,
                "predicted_db_qps": 95.0,
                "db_qps_upper_error": 4.0,
                "predicted_target_utilization": 80.0,
                "target_utilization_lower_error": 2.0,
                "predicted_resource": {"cpu": 20.0, "memory": 20.0, "io": 20.0},
                "resource_error_bounds": {"cpu": 1.0, "memory": 1.0, "io": 1.0},
            },
        },
        {
            "rank": 3,
            "optimized_params": {"p": 3.0},
            "optimistic_replay_pruning_input": {
                "calibration_ready": False,
                "calibration_scope_matches": True,
                "simultaneous_coverage_guaranteed": True,
                "predicted_db_qps": 1.0,
                "db_qps_upper_error": 0.0,
                "predicted_target_utilization": 99.0,
                "target_utilization_lower_error": 0.0,
            },
        },
    ]
    kept, pruned, checks = prune_ranked_candidates_for_replay(
        records,
        incumbent_db_qps=100.0,
        incumbent_target_utilization=90.0,
        capability={"cpu": 100.0, "memory": 100.0, "io": 100.0},
        utility_calculator=_band_target_calculator(),
        best_score=4.0,
    )
    assert [record["rank"] for record in kept] == [1, 3]
    assert [record["rank"] for record in pruned] == [2]
    assert len(checks) == 3
    assert records[1].get("optimistic_replay_pruning_decision") is None


def test_plan_cluster_selects_one_best_representative_and_missing_fails_open() -> None:
    records = [
        {
            "rank": 1,
            "final_score": 0.80,
            "plan_similarity_cluster_id": "join_family_a",
            "optimistic_replay_pruning_input": {
                "predicted_score": 0.80,
                "score_upper_error": 0.05,
            },
        },
        {
            "rank": 2,
            "final_score": 0.79,
            "plan_similarity_cluster_id": "join_family_a",
            "optimistic_replay_pruning_input": {
                "predicted_score": 0.79,
                "score_upper_error": 0.20,
            },
        },
        {"rank": 3, "final_score": 0.70, "plan_fingerprint": {"node": "Seq"}},
        {"rank": 4, "final_score": 0.60},
        {"rank": 5, "final_score": 0.50},
    ]
    selected, suppressed, audit = select_plan_diverse_representatives(
        records,
        suppress_similar=True,
    )

    assert [record["rank"] for record in selected] == [2, 3, 4, 5]
    assert [record["rank"] for record in suppressed] == [1]
    assert audit["plan_cluster_count"] == 4
    assert audit["missing_plan_policy"] == "fail_open_unique_cluster"
    assert records[0].get("replay_plan_cluster_id") is None


def test_plan_cluster_prefers_validated_joint_direction_score() -> None:
    records = [
        {
            "rank": 1,
            "final_score": 0.70,
            "plan_similarity_cluster_id": "same_plan",
            "real_replay_direction_prediction": {"ranking_score": 0.90},
            "optimistic_replay_pruning_input": {"optimistic_score_upper": 0.75},
        },
        {
            "rank": 2,
            "final_score": 0.90,
            "plan_similarity_cluster_id": "same_plan",
            "real_replay_direction_prediction": {"ranking_score": 0.20},
            "optimistic_replay_pruning_input": {"optimistic_score_upper": 0.99},
        },
    ]
    selected, suppressed, audit = select_plan_diverse_representatives(
        records,
        suppress_similar=True,
    )
    assert [record["rank"] for record in selected] == [1]
    assert [record["rank"] for record in suppressed] == [2]
    assert "validated_joint_direction" in audit["selection_policy"]


def test_plan_cluster_default_is_audit_only_without_suppression() -> None:
    records = [
        {
            "rank": 1,
            "final_score": 0.8,
            "plan_similarity_cluster_id": "same_plan",
        },
        {
            "rank": 2,
            "final_score": 0.7,
            "plan_similarity_cluster_id": "same_plan",
        },
    ]
    selected, suppressed, audit = select_plan_diverse_representatives(records)
    assert [record["rank"] for record in selected] == [1, 2]
    assert suppressed == []
    assert audit["plan_cluster_count"] == 1
    assert audit["representative_count"] == 2
    assert audit["plan_shape_pruning_enabled"] is False


def test_outer_qps_threshold_is_real_measurement_only() -> None:
    common = {
        "incumbent_db_qps": 100.0,
        "incumbent_target_utilization": 90.0,
        "candidate_target_utilization": 80.0,
        "target_resource": "memory",
        "incumbent_resource_utilization": {
            "cpu": 40.0,
            "memory": 90.0,
            "io": 40.0,
        },
        "candidate_resource_utilization": {
            "cpu": 45.0,
            "memory": 80.0,
            "io": 45.0,
        },
        "non_target_bottleneck_threshold": 80.0,
        "hard_gate_passed": True,
        "minimum_db_qps_improvement_ratio": 0.15,
    }
    below = decide_outer_acceptance(candidate_db_qps=114.9, **common)
    reached = decide_outer_acceptance(candidate_db_qps=115.0, **common)
    assert not below.accepted
    assert below.reason == "db_qps_improvement_below_minimum"
    assert reached.accepted
    assert abs(reached.observed_db_qps_improvement_ratio - 0.15) < 1e-12


def test_three_level_outer_acceptance_and_two_round_downgrade() -> None:
    assert [acceptance_tier_for_miss_count(value) for value in (0, 1, 2, 3, 4)] == [
        "strict",
        "strict",
        "secondary",
        "secondary",
        "fallback",
    ]
    common = {
        "incumbent_db_qps": 100.0,
        "candidate_db_qps": 101.0,
        "incumbent_target_utilization": 90.0,
        "target_resource": "memory",
        "incumbent_resource_utilization": {"cpu": 40.0, "memory": 90.0, "io": 50.0},
        "non_target_bottleneck_threshold": 80.0,
        "hard_gate_passed": True,
    }
    secondary_candidate = {
        **common,
        "candidate_target_utilization": 89.0,
        "candidate_resource_utilization": {"cpu": 39.0, "memory": 89.0, "io": 49.0},
    }
    strict_preference = decide_outer_acceptance(
        **secondary_candidate, required_acceptance_tier="strict"
    )
    assert strict_preference.accepted
    assert strict_preference.acceptance_tier == "secondary"
    assert strict_preference.required_acceptance_tier == "fallback"
    assert strict_preference.preferred_acceptance_tier == "strict"
    secondary_gate = decide_outer_acceptance(
        **secondary_candidate, required_acceptance_tier="secondary"
    )
    assert secondary_gate.accepted
    assert secondary_gate.reason == "accepted_secondary_qps_up_target_down"

    fallback_candidate = {
        **common,
        "candidate_target_utilization": 91.0,
        "candidate_resource_utilization": {"cpu": 39.0, "memory": 91.0, "io": 49.0},
    }
    secondary_preference = decide_outer_acceptance(
        **fallback_candidate, required_acceptance_tier="secondary"
    )
    assert secondary_preference.accepted
    assert secondary_preference.acceptance_tier == "fallback"
    assert secondary_preference.required_acceptance_tier == "fallback"
    assert secondary_preference.preferred_acceptance_tier == "secondary"
    fallback_gate = decide_outer_acceptance(
        **fallback_candidate, required_acceptance_tier="fallback"
    )
    assert fallback_gate.accepted
    assert not fallback_gate.accepted_escape
    assert fallback_gate.accepted_shrink
    assert fallback_gate.formal_state_mutation_allowed
    assert fallback_gate.reason == "accepted_fallback_qps_up"


if __name__ == "__main__":
    test_global_real_replay_top_k_cap()
    test_acceptance_truth_table_and_current_incumbent()
    test_transform_aware_binary_shrink()
    test_optimistic_pruning_uses_multidimensional_utility_box()
    test_pruning_fail_open_and_does_not_consume_replay()
    test_plan_cluster_selects_one_best_representative_and_missing_fails_open()
    test_plan_cluster_prefers_validated_joint_direction_score()
    test_plan_cluster_default_is_audit_only_without_suppression()
    test_outer_qps_threshold_is_real_measurement_only()
    test_three_level_outer_acceptance_and_two_round_downgrade()
    print("common_outer_loop tests passed", flush=True)
