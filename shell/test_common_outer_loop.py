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
    advance_outer_shrink_progress,
    binary_shrink_parameter_ranges,
    decide_optimistic_replay_pruning,
    decide_outer_acceptance,
    prune_ranked_candidates_for_replay,
    sanitize_optimistic_pruning_metadata_tree,
)


@dataclass(frozen=True)
class Range:
    min_value: float
    max_value: float
    transform: str = "linear"
    step: Optional[float] = None
    values: Optional[Tuple[float, ...]] = None


def test_acceptance_truth_table_and_current_incumbent() -> None:
    incumbent_resources = {"cpu": 0.4, "memory": 0.9, "io": 0.3}
    candidate_resources = {"cpu": 0.5, "memory": 0.89, "io": 0.4}
    accepted = decide_outer_acceptance(
        incumbent_db_qps=100.0,
        candidate_db_qps=101.0,
        incumbent_target_utilization=90.0,
        candidate_target_utilization=89.0,
        target_resource="memory",
        incumbent_resource_utilization=incumbent_resources,
        candidate_resource_utilization=candidate_resources,
        non_target_bottleneck_threshold=0.8,
        hard_gate_passed=True,
    )
    assert accepted.accepted

    cases = [
        (False, 101.0, 89.0, "hard_gate_failed"),
        (True, 100.0, 89.0, "db_qps_not_strictly_higher"),
        (True, 101.0, 90.0, "target_bottleneck_not_strictly_lower"),
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
            non_target_bottleneck_threshold=0.8,
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
        incumbent_resource_utilization={"cpu": 0.4, "memory": 0.8, "io": 0.3},
        candidate_resource_utilization={"cpu": 0.5, "memory": 0.79, "io": 0.4},
        non_target_bottleneck_threshold=0.8,
        hard_gate_passed=True,
    )
    assert not relative_to_current.accepted


def test_non_target_resource_gate_is_strict_and_fail_closed() -> None:
    incumbent = {"cpu": 0.4, "memory": 0.9, "io": 0.3}
    candidate = {"cpu": 0.8, "memory": 0.89, "io": 0.4}
    rejected = decide_outer_acceptance(
        incumbent_db_qps=100.0,
        candidate_db_qps=101.0,
        incumbent_target_utilization=0.9,
        candidate_target_utilization=0.89,
        target_resource="memory",
        incumbent_resource_utilization=incumbent,
        candidate_resource_utilization=candidate,
        non_target_bottleneck_threshold=0.8,
        hard_gate_passed=True,
    )
    assert not rejected.accepted
    assert rejected.reason == "non_target_resource_became_bottleneck"
    assert rejected.non_target_bottleneck_resources == ["cpu"]

    candidate["cpu"] = 0.79999
    accepted = decide_outer_acceptance(
        incumbent_db_qps=100.0,
        candidate_db_qps=101.0,
        incumbent_target_utilization=0.9,
        candidate_target_utilization=0.89,
        target_resource="memory",
        incumbent_resource_utilization=incumbent,
        candidate_resource_utilization=candidate,
        non_target_bottleneck_threshold=0.8,
        hard_gate_passed=True,
    )
    assert accepted.accepted

    for invalid in (None, math.nan, math.inf, -math.inf):
        candidate["io"] = invalid
        invalid_decision = decide_outer_acceptance(
            incumbent_db_qps=100.0,
            candidate_db_qps=101.0,
            incumbent_target_utilization=0.9,
            candidate_target_utilization=0.89,
            target_resource="memory",
            incumbent_resource_utilization=incumbent,
            candidate_resource_utilization=candidate,
            non_target_bottleneck_threshold=0.8,
            hard_gate_passed=True,
        )
        assert not invalid_decision.accepted
        assert invalid_decision.reason == "missing_or_nonfinite_non_target_utilization"


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


def test_mysql_cost_defaults_to_log_transform() -> None:
    ranges = {
        "io_block_read_cost": Range(0.1, 10.0, ""),
        "memory_temptable_row_cost": Range(0.01, 1.0, ""),
    }
    updated, details = binary_shrink_parameter_ranges(
        ranges,
        {"io_block_read_cost": 0.5, "memory_temptable_row_cost": 0.5},
        list(ranges),
    )
    assert details["io_block_read_cost"]["transform"] == "log"
    assert details["io_block_read_cost"]["midpoint"] == 1.0
    assert updated["io_block_read_cost"].min_value == 0.1
    assert updated["io_block_read_cost"].max_value == 1.0
    assert details["memory_temptable_row_cost"]["midpoint"] == 0.1
    assert updated["memory_temptable_row_cost"].min_value == 0.1
    assert updated["memory_temptable_row_cost"].max_value == 1.0


def test_only_acceptance_shrinks_all_active_ranges_and_target_is_ten() -> None:
    ranges = {
        "io_block_read_cost": Range(0.1, 10.0, "log"),
        "row_evaluate_cost": Range(0.01, 1.0, "log"),
    }
    rejected = decide_outer_acceptance(
        incumbent_db_qps=100.0,
        candidate_db_qps=99.0,
        incumbent_target_utilization=90.0,
        candidate_target_utilization=80.0,
        target_resource="memory",
        incumbent_resource_utilization={"cpu": 0.4, "memory": 0.9, "io": 0.3},
        candidate_resource_utilization={"cpu": 0.5, "memory": 0.8, "io": 0.4},
        non_target_bottleneck_threshold=0.8,
        hard_gate_passed=True,
    )
    progress = advance_outer_shrink_progress(
        ranges=ranges,
        accepted_shrink_count=9,
        rejected_count=3,
        target_accepted_shrinks=10,
        decision=rejected,
        candidate_params={"io_block_read_cost": 5.0, "row_evaluate_cost": 0.5},
        active_params=list(ranges),
    )
    assert progress.accepted_shrink_count == 9
    assert progress.rejected_count == 4
    assert progress.ranges == ranges
    assert not progress.complete

    accepted = decide_outer_acceptance(
        incumbent_db_qps=100.0,
        candidate_db_qps=101.0,
        incumbent_target_utilization=90.0,
        candidate_target_utilization=89.0,
        target_resource="memory",
        incumbent_resource_utilization={"cpu": 0.4, "memory": 0.9, "io": 0.3},
        candidate_resource_utilization={"cpu": 0.5, "memory": 0.89, "io": 0.4},
        non_target_bottleneck_threshold=0.8,
        hard_gate_passed=True,
    )
    progress = advance_outer_shrink_progress(
        ranges=progress.ranges,
        accepted_shrink_count=progress.accepted_shrink_count,
        rejected_count=progress.rejected_count,
        target_accepted_shrinks=10,
        decision=accepted,
        candidate_params={"io_block_read_cost": 5.0, "row_evaluate_cost": 0.05},
        active_params=list(ranges),
    )
    assert progress.accepted_shrink_count == 10
    assert progress.rejected_count == 4
    assert set(progress.range_changes) == set(ranges)
    assert progress.complete


def _resource_pruning_input(**overrides):
    value = {
        "predicted_resources": {"cpu": 70.0, "memory": 75.0, "io": 60.0},
        "asymmetric_resource_errors": {
            "cpu": {"lower": 10.0, "upper": 15.0, "valid": True, "scope_matches": True},
            "memory": {"lower": 5.0, "upper": 10.0, "valid": True, "scope_matches": True},
            "io": {"lower": 20.0, "upper": 20.0, "valid": True, "scope_matches": True},
        },
        "calibration_ready": True,
        "calibration_scope_matches": True,
        "simultaneous_coverage_guaranteed": True,
        "error_bound_provenance": {
            "schema_version": "resource_error_bound_provenance_v1",
            "immutable": True,
            "artifact_sha256": "a" * 64,
            "resources": ["cpu", "memory", "io"],
            "simultaneous_coverage_guaranteed": True,
            "bindings": {
                "freeze_id": "freeze-test",
                "benchmark_id": "job",
                "scenario_id": "memory",
                "resource_envelope_sha256": "b" * 64,
                "domain_id": "job-memory-envelope-v1",
            },
        },
    }
    value.update(overrides)
    return value


def _expected_error_bound_bindings(**overrides):
    value = {
        "artifact_sha256": "a" * 64,
        "freeze_id": "freeze-test",
        "benchmark_id": "job",
        "scenario_id": "memory",
        "resource_envelope_sha256": "b" * 64,
        "domain_id": "job-memory-envelope-v1",
    }
    value.update(overrides)
    return value


def test_optimistic_pruning_maximizes_true_utility_over_joint_box() -> None:
    payload = _resource_pruning_input()
    decision = decide_optimistic_replay_pruning(
        **payload,
        capabilities={"cpu": 100.0, "memory": 100.0, "io": 100.0},
        utility_config={"score_mode": "band_target"},
        best_utility=3.0,
        expected_error_bound_bindings=_expected_error_bound_bindings(),
    )
    assert decision.pruned
    assert decision.reason == "utility_score_upper_not_above_current_best"
    assert decision.utilization_intervals == {
        "cpu": {"lower": 0.6, "upper": 0.85},
        "memory": {"lower": 0.7, "upper": 0.85},
        "io": {"lower": 0.4, "upper": 0.8},
    }

    assert decision.per_resource_score_upper == {"cpu": 1.0, "memory": 1.0, "io": 1.0}
    assert decision.score_upper == 3.0

    possible = decide_optimistic_replay_pruning(
        **payload,
        capabilities={"cpu": 100.0, "memory": 100.0, "io": 100.0},
        utility_config={"score_mode": "band_target"},
        best_utility=2.99,
        expected_error_bound_bindings=_expected_error_bound_bindings(),
    )
    assert not possible.pruned
    assert possible.reason == "utility_improvement_possible_in_joint_resource_box"


def test_pruning_fails_open_for_any_bad_resource_bound_or_calibration_scope() -> None:
    base = _resource_pruning_input()
    for mutation, reason in (
        ({"asymmetric_resource_errors": {**base["asymmetric_resource_errors"], "io": {"lower": math.nan, "upper": 1.0, "valid": True, "scope_matches": True}}}, "missing_or_invalid_resource_error_keep_for_real_replay"),
        ({"predicted_resources": {"cpu": 5.0, "memory": 75.0, "io": 60.0}}, "missing_or_invalid_resource_error_keep_for_real_replay"),
        ({"asymmetric_resource_errors": {**base["asymmetric_resource_errors"], "io": {"lower": 1.0, "upper": 1.0, "valid": False, "scope_matches": True}}}, "resource_error_not_valid_or_scope_mismatch"),
        ({"calibration_ready": False}, "calibration_not_ready_keep_for_real_replay"),
        ({"calibration_scope_matches": False}, "calibration_scope_mismatch_keep_for_real_replay"),
        ({"simultaneous_coverage_guaranteed": False}, "no_simultaneous_coverage_keep_for_real_replay"),
    ):
        decision = decide_optimistic_replay_pruning(
            **_resource_pruning_input(**mutation),
            capabilities={"cpu": 100.0, "memory": 100.0, "io": 100.0},
            utility_config={"score_mode": "band_target"},
            best_utility=100.0,
            expected_error_bound_bindings=_expected_error_bound_bindings(),
        )
        assert not decision.pruned
        assert not decision.bounds_usable
        assert decision.reason == reason

    extra_capability = decide_optimistic_replay_pruning(
        **base,
        capabilities={"cpu": 100.0, "memory": 100.0, "io": 100.0, "gpu": 1.0},
        utility_config={"score_mode": "band_target"},
        best_utility=100.0,
        expected_error_bound_bindings=_expected_error_bound_bindings(),
    )
    assert not extra_capability.pruned
    assert extra_capability.reason == "resource_schema_must_exactly_cover_cpu_memory_io"

    for provenance, expected, reason in (
        (None, _expected_error_bound_bindings(),
         "missing_error_bound_provenance_keep_for_real_replay"),
        (_resource_pruning_input()["error_bound_provenance"], None,
         "missing_error_bound_provenance_keep_for_real_replay"),
        (_resource_pruning_input()["error_bound_provenance"],
         _expected_error_bound_bindings(artifact_sha256="c" * 64),
         "error_bound_provenance_binding_mismatch_keep_for_real_replay"),
        (_resource_pruning_input()["error_bound_provenance"],
         _expected_error_bound_bindings(scenario_id="io"),
         "error_bound_provenance_binding_mismatch_keep_for_real_replay"),
    ):
        decision = decide_optimistic_replay_pruning(
            **_resource_pruning_input(error_bound_provenance=provenance),
            capabilities={"cpu": 100.0, "memory": 100.0, "io": 100.0},
            utility_config={"score_mode": "band_target"},
            best_utility=100.0,
            expected_error_bound_bindings=expected,
        )
        assert not decision.pruned
        assert not decision.bounds_usable
        assert decision.reason == reason


def test_performance_io_utility_uses_opposite_box_faces_for_io() -> None:
    decision = decide_optimistic_replay_pruning(
        **_resource_pruning_input(),
        capabilities={"cpu": 100.0, "memory": 100.0, "io": 100.0},
        utility_config={"score_mode": "performance_io"},
        best_utility=0.5,
        expected_error_bound_bindings=_expected_error_bound_bindings(),
    )
    # CPU/memory maximize at their upper faces (0.85 each); IO is a penalty
    # with default weight 3 and therefore maximizes at its lower face (0.40).
    assert decision.per_resource_score_upper == {
        "cpu": 0.85,
        "memory": 0.85,
        "io": -1.2000000000000002,
    }
    assert math.isclose(decision.score_upper, 0.5, abs_tol=1e-12)
    assert decision.pruned


def test_threshold_weight_jump_is_uncertifiable_and_fails_open() -> None:
    payload = _resource_pruning_input(
        predicted_resources={"cpu": 80.0, "memory": 75.0, "io": 60.0},
        asymmetric_resource_errors={
            "cpu": {"lower": 1.0, "upper": 1.0, "valid": True, "scope_matches": True},
            "memory": {"lower": 1.0, "upper": 1.0, "valid": True, "scope_matches": True},
            "io": {"lower": 1.0, "upper": 1.0, "valid": True, "scope_matches": True},
        },
    )
    decision = decide_optimistic_replay_pruning(
        **payload,
        capabilities={"cpu": 100.0, "memory": 100.0, "io": 100.0},
        utility_config={
            "score_mode": "threshold",
            "overload_threshold": 0.8,
            "resource_reward_weights": {"cpu": 1.0, "memory": 1.0, "io": 1.0},
            "resource_penalty_weights": {"cpu": 10.0, "memory": 1.0, "io": 1.0},
        },
        best_utility=100.0,
        expected_error_bound_bindings=_expected_error_bound_bindings(),
    )
    assert not decision.pruned
    assert not decision.bounds_usable
    assert decision.reason == "invalid_utility_configuration_keep_for_real_replay"
    assert decision.score_upper is None

    equal_weight = decide_optimistic_replay_pruning(
        **payload,
        capabilities={"cpu": 100.0, "memory": 100.0, "io": 100.0},
        utility_config={
            "score_mode": "threshold",
            "overload_threshold": 0.8,
            "resource_reward_weights": {"cpu": 2.0, "memory": 1.0, "io": 1.0},
            "resource_penalty_weights": {"cpu": 2.0, "memory": 1.0, "io": 1.0},
        },
        best_utility=100.0,
        expected_error_bound_bindings=_expected_error_bound_bindings(),
    )
    assert equal_weight.bounds_usable
    assert equal_weight.per_resource_argmax["cpu"]["kind"] == "threshold"
    assert "equal_weight" in equal_weight.per_resource_argmax["cpu"]["proof"]


def test_pruning_fail_open_and_does_not_consume_replay() -> None:
    records = [
        {"rank": 1, "optimized_params": {"p": 1.0}},
        {
            "rank": 2,
            "optimized_params": {"p": 2.0},
            "optimistic_replay_pruning_input": _resource_pruning_input(),
        },
        {
            "rank": 4,
            "optimized_params": {"p": 4.0},
            "optimistic_replay_pruning_input": {
                **_resource_pruning_input(),
                "error_audit": {"db_qps_upper_error": -999999.0},
            },
        },
        {
            "rank": 3,
            "optimized_params": {"p": 3.0},
            "optimistic_replay_pruning_input": {
                "calibration_ready": False,
                "calibration_scope_matches": True,
                "simultaneous_coverage_guaranteed": True,
                "predicted_resources": {"cpu": 0.0, "memory": 0.0, "io": 0.0},
                "asymmetric_resource_errors": {},
            },
        },
    ]
    kept, pruned, checks = prune_ranked_candidates_for_replay(
        records,
        capabilities={"cpu": 100.0, "memory": 100.0, "io": 100.0},
        utility_config={"score_mode": "band_target"},
        best_utility=3.0,
        expected_error_bound_bindings=_expected_error_bound_bindings(),
    )
    assert [record["rank"] for record in kept] == [1, 4, 3]
    assert [record["rank"] for record in pruned] == [2]
    assert len(checks) == 4
    assert records[1].get("optimistic_replay_pruning_decision") is None
    assert "db_qps_upper_error" not in checks[2]["decision"]
    assert "-999999" not in str(checks[2])
    assert checks[2]["pruning_input"] == {
        "schema_violation": "legacy_qps_bound_key_present",
        "violating_keys": ["error_audit.db_qps_upper_error"],
    }
    assert kept[1]["optimistic_replay_pruning_input"] == checks[2]["pruning_input"]


def test_recursive_formal_log_sanitizer_removes_qps_bound_values_only() -> None:
    sanitized = sanitize_optimistic_pruning_metadata_tree({
        "qps": 123.0,
        "candidate_rankings": [{
            "final_estimation_metadata": {
                "optimistic_replay_pruning_input": {
                    "predicted_resources": {"cpu": 1.0},
                    "db_qps_upper_error": 987654321.0,
                }
            }
        }],
    })
    assert sanitized["qps"] == 123.0
    pruning = sanitized["candidate_rankings"][0]["final_estimation_metadata"][
        "optimistic_replay_pruning_input"
    ]
    assert pruning == {
        "schema_violation": "legacy_qps_bound_key_present",
        "violating_keys": ["db_qps_upper_error"],
    }
    assert "987654321" not in str(sanitized)


if __name__ == "__main__":
    test_acceptance_truth_table_and_current_incumbent()
    test_transform_aware_binary_shrink()
    test_mysql_cost_defaults_to_log_transform()
    test_only_acceptance_shrinks_all_active_ranges_and_target_is_ten()
    test_optimistic_pruning_maximizes_true_utility_over_joint_box()
    test_pruning_fails_open_for_any_bad_resource_bound_or_calibration_scope()
    test_performance_io_utility_uses_opposite_box_faces_for_io()
    test_threshold_weight_jump_is_uncertifiable_and_fails_open()
    test_pruning_fail_open_and_does_not_consume_replay()
    test_recursive_formal_log_sanitizer_removes_qps_bound_values_only()
    print("common_outer_loop tests passed", flush=True)
