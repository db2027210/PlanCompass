#!/usr/bin/env python3

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math

from plan_transition_calibration import (
    attribute_query_impact,
    calibration_decision,
    changed_query_impact,
    diff_query_plans,
    difference_gate_95,
    joint_improvement_margin,
)


def test_plan_diff_reports_query_and_operator_changes() -> None:
    before = {
        "1": {
            "Node Type": "Hash Join",
            "Join Type": "Inner",
            "Parallel Aware": True,
            "Workers Planned": 2,
            "Plans": [{"Node Type": "Seq Scan", "Parallel Aware": True}],
        },
        "2": {"Node Type": "Seq Scan", "Parallel Aware": False},
    }
    after = {
        "1": {
            "Node Type": "Merge Join",
            "Join Type": "Inner",
            "Parallel Aware": False,
            "Plans": [{"Node Type": "Index Scan", "Parallel Aware": False}],
        },
        "2": {"Node Type": "Seq Scan", "Parallel Aware": False},
    }
    result = diff_query_plans(before, after)
    assert result["changed_query_ids"] == ["1"]
    change = result["query_changes"][0]
    assert change["joins"]["removed"]["Hash Join:Inner"] == 1
    assert change["joins"]["added"]["Merge Join:Inner"] == 1
    assert change["scans"]["removed"]["Seq Scan"] == 1
    assert change["scans"]["added"]["Index Scan"] == 1
    assert change["parallel"]["workers_planned_delta"] == -2


def test_query_impact_is_labelled_as_concurrency_apportioned() -> None:
    origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
    events = [
        {
            "event": "query_complete",
            "timestamp_utc": (origin + timedelta(seconds=2)).isoformat(),
            "sql_sha256": "a",
            "latency_ms": 2000.0,
            "success": True,
        },
        {
            "event": "query_complete",
            "timestamp_utc": (origin + timedelta(seconds=2)).isoformat(),
            "sql_sha256": "b",
            "latency_ms": 1000.0,
            "success": True,
        },
    ]
    samples = []
    for index in range(3):
        samples.append(
            {
                "timestamp_utc": (origin + timedelta(seconds=index)).isoformat(),
                "monotonic_ns": index * 1_000_000_000,
                "cpu_quota_us": 100000,
                "cpu_period_us": 100000,
                "cpu_usage_ns": index * 500_000_000,
                "io_read_bytes": index * 100,
                "io_write_bytes": 0,
                "memory_current_bytes": 50,
                "memory_limit_bytes": 100,
            }
        )
    result = attribute_query_impact(
        events,
        samples,
        query_hash_by_id={"1": "a", "2": "b"},
        io_read_cap_bps=100.0,
        io_write_cap_bps=100.0,
    )
    rows = {row["query_id"]: row for row in result["queries"]}
    assert math.isclose(rows["1"]["client_time_share"], 2.0 / 3.0)
    assert math.isclose(rows["2"]["client_time_share"], 1.0 / 3.0)
    assert rows["1"]["resource_attribution_quality"] == "concurrency_apportioned_estimate"
    assert math.isclose(
        rows["1"]["cpu_capacity_share"] + rows["2"]["cpu_capacity_share"], 1.0
    )
    assert result["resource_attribution"]["memory_semantics"] == (
        "pressure_exposure_not_allocated_bytes"
    )


def test_error_gate_requires_combined_endpoint_bounds() -> None:
    uncertain = difference_gate_95([0.80, 0.90, 0.85], [0.81, 0.91, 0.86])
    assert not uncertain.credible
    credible = difference_gate_95([0.80, 0.801, 0.799], [0.70, 0.701, 0.699])
    assert credible.credible
    assert credible.direction == "decrease"


def test_joint_margin_keeps_qps_and_resource_separate() -> None:
    result = joint_improvement_margin(
        incumbent_qps=100.0,
        candidate_qps=110.0,
        incumbent_target=90.0,
        candidate_target=80.0,
        hard_gate_passed=True,
        candidate_non_target={"cpu": 70.0, "io": 50.0},
        incumbent_qps_error=1.0,
        candidate_qps_error=1.0,
        incumbent_target_error=1.0,
        candidate_target_error=1.0,
    )
    assert result["strict_acceptance"]
    assert result["joint_margin_vector"] == {
        "qps_relative_delta_pct": 10.0,
        "target_improvement": 10.0,
    }
    assert result["conservative_qps_delta"] == 8.0
    assert result["conservative_target_improvement"] == 8.0


def test_calibration_requires_plan_impact_and_credible_delta() -> None:
    plan_diff = {"changed_query_count": 1, "changed_query_ids": ["7"]}
    impact = {
        "queries": [
            {
                "query_id": "7",
                "client_time_share": 0.40,
                "cpu_capacity_share": 0.30,
                "io_capacity_share": 0.20,
                "memory_pressure_share": 0.35,
            }
        ],
        "resource_attribution": {"quality": "concurrency_apportioned_estimate"},
    }
    changed = changed_query_impact(plan_diff, impact)
    gate = difference_gate_95([0.90, 0.901, 0.899], [0.80, 0.801, 0.799])
    decision = calibration_decision(
        plan_diff=plan_diff,
        changed_impact=changed,
        target_difference=gate,
        before_distance=0.2,
        after_distance=0.3,
    )
    assert decision["learnable"]
    assert decision["target_direction"] == "decrease"
    assert decision["target_secant_per_normalized_unit"] < 0.0

    low_impact = dict(changed)
    low_impact["shares"] = dict(changed["shares"], client_time_share=0.01)
    rejected = calibration_decision(
        plan_diff=plan_diff,
        changed_impact=low_impact,
        target_difference=gate,
        before_distance=0.2,
        after_distance=0.3,
    )
    assert not rejected["learnable"]
    assert "changed_queries_below_minimum_time_share" in rejected["reasons"]


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"PASS {len(tests)} plan-transition calibration tests")
