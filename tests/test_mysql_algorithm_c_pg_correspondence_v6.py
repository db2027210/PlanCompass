from __future__ import annotations

import math

from scripts import mysql_algorithm_c_interval_control_v2 as interval_control
from scripts import mysql_algorithm_c_pg_recovery_v2 as pg_recovery
from scripts import mysql_outer_rejection_cycle_v3 as rejection_cycle
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v58 as runtime


def _observation(identity: str, *, category: str, qps_gain: float) -> dict:
    return {
        "observation_id": identity + category,
        "parameter_identity": identity,
        "category": category,
        "params": {"a": 10.0, "b": 10.0},
        "qps_gate_gain": qps_gain,
        "required_qps_gain": 0.0,
        "non_target_safe": category != "qps_pass_resource_unsafe",
        "non_target_unsafe_excess_pct": 10.0,
    }


def test_runtime_binds_v2_recovery_and_same_action_fallback() -> None:
    assert runtime.interval_control is interval_control
    assert runtime.pg_recovery is pg_recovery
    assert runtime.rejection_cycle is rejection_cycle
    assert runtime._source.count("boundary_fallback_audit") >= 1
    assert runtime._source.count("EMPTY_BOUNDARY_FALLBACK_DUE_SHRINK") == 1
    assert runtime._source.count("algorithm_c_progressive_numeric_cursor") >= 1


def test_expanded_first_decision_dependencies_resolve() -> None:
    before = {
        "a": {"lower": 1.0, "upper": 100.0},
        "b": {"lower": 1.0, "upper": 100.0},
    }
    after, progress = runtime.rejection_cycle.advance_rejection_cycle(
        accepted=False,
        consecutive_rejections=1,
        ranges=before,
        incumbent_params={"a": 10.0, "b": 10.0},
        active_params=["a", "b"],
    )
    assert progress["forced_incumbent_shrink"] is True
    audit = runtime.interval_control.require_binary_progress(
        before, after, ["a", "b"],
    )
    assert audit["strictly_shrunk"] == ["a", "b"]


def test_boundary_pair_requires_distinct_parameter_identities() -> None:
    observations = [
        _observation(
            "same",
            category="resource_feasible_qps_below_gate",
            qps_gain=-0.01,
        ),
        _observation(
            "same",
            category="qps_pass_resource_unsafe",
            qps_gain=0.10,
        ),
    ]
    assert pg_recovery.outer_boundary_pair(
        observations,
        ranges={"a": {"lower": 1.0, "upper": 100.0},
                "b": {"lower": 1.0, "upper": 100.0}},
        active=["a", "b"],
    ) is None


def test_progressive_numeric_cursor_advances_like_pg() -> None:
    vectors, audit = pg_recovery.progressive_numeric_vectors(
        incumbent={"a": 10.0, "b": 10.0},
        ranges={"a": {"lower": 1.0, "upper": 100.0},
                "b": {"lower": 1.0, "upper": 100.0}},
        active=["a", "b"],
        window_index=0,
        count=8,
        cursor=17,
    )
    assert vectors
    assert audit["cursor_start"] == 17
    assert audit["cursor_end_exclusive"] >= 25


def test_empty_boundary_executes_due_shrink_without_third_miss() -> None:
    before = {
        "a": {"lower": 1.0, "upper": 100.0},
        "b": {"lower": 1.0, "upper": 100.0},
    }
    after, progress = pg_recovery.shrink_due_after_empty_boundary(
        ranges=before,
        incumbent_params={"a": 10.0, "b": 90.0},
        active=["a", "b"],
        consecutive_rejections=2,
    )
    assert progress["consecutive_rejections_before"] == 2
    assert progress["consecutive_rejections_after"] == 0
    assert progress["physical_miss_increment"] == 0
    assert progress["forced_incumbent_shrink"] is True
    assert math.isclose(after["a"]["upper"], 10.0)
    assert math.isclose(after["b"]["lower"], 10.0)
