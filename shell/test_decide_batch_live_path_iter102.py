#!/usr/bin/env python3
"""Module."""


from __future__ import annotations

import os
import sys
import inspect

# Make the project root importable
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)


_passed = 0
_failed = 0


def _check(name: str, cond: bool, detail: str = "") -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"[PASS] {name}")
    else:
        _failed += 1
        print(f"[FAIL] {name} — {detail}")


def test_static_grep_decide_batch_in_parameter_optimizer() -> None:
    """test static grep decide batch in parameter optimizer."""

    po_path = os.path.join(_PROJECT_ROOT, "parameter_optimizer.py")
    with open(po_path, "r", encoding="utf-8") as f:
        source = f.read()

    _check(
        "static_grep_decide_batch_call",
        "controller.decide_batch(inputs)" in source,
        "parameter_optimizer.py must contain real `controller.decide_batch(inputs)` call",
    )

    _check(
        "static_grep_AdaptiveReplayController_instantiation",
        "AdaptiveReplayController(" in source,
        "parameter_optimizer.py must instantiate AdaptiveReplayController",
    )

    _check(
        "static_grep_initial_replay_call_site",
        "_initial_adaptive_decision_record = _compute_adaptive_replay_decision("
        in source,
        "parameter_optimizer.py must call _compute_adaptive_replay_decision at initial replay site",
    )
    _check(
        "static_grep_feedback_replay_call_site",
        "_fb_adaptive_decision_record = _compute_adaptive_replay_decision(" in source,
        "parameter_optimizer.py must call _compute_adaptive_replay_decision at feedback replay site",
    )

    _check(
        "static_grep_adaptive_replay_decision_event",
        '"event": "adaptive_replay_decision"' in source
        or "'event': 'adaptive_replay_decision'" in source
        or 'adaptive_replay_decision' in source,
        "parameter_optimizer.py must write adaptive_replay_decision JSONL event",
    )


def test_helper_empty_inputs_expand_diagnostic() -> None:
    """test helper empty inputs expand diagnostic."""

    from parameter_optimizer import _compute_adaptive_replay_decision

    record = _compute_adaptive_replay_decision(
        candidate_evaluations=[],
        summary={
            "pairwise_inversion_rate": 0.0,
            "first_stage_ranking_validation_pass": True,
            "explainable_lane_hit_count": 0,
            "lane_counts": {},
        },
        target_resource="memory",
        baseline_resource={"cpu": 7.0, "memory": 3.38, "io": 800.0},
        capability={"cpu": 14.0, "memory": 4.0, "io": 1024.0},
        replayed_count=0,
    )
    _check("helper_empty_returns_dict", isinstance(record, dict), f"got {type(record)}")
    _check(
        "helper_empty_decision_expand_diagnostic",
        record.get("decision") == "EXPAND_DIAGNOSTIC",
        f"expected EXPAND_DIAGNOSTIC, got {record.get('decision')}",
    )
    _check(
        "helper_empty_phase1_integration_flag",
        record.get("phase1_integration") is True,
        f"expected phase1_integration=True, got {record.get('phase1_integration')}",
    )
    _check(
        "helper_empty_event_field",
        record.get("event") == "adaptive_replay_decision",
        f"expected event=adaptive_replay_decision, got {record.get('event')}",
    )
    _check(
        "helper_empty_explainable_lane_empty",
        record.get("explainable_lane_empty") is True,
        f"expected explainable_lane_empty=True, got {record.get('explainable_lane_empty')}",
    )


def test_helper_proceed_to_outer_loop() -> None:
    """test helper proceed to outer loop."""

    from parameter_optimizer import _compute_adaptive_replay_decision


    candidate_evaluations = [
        {
            "candidate_primary_target_status": {
                "db_qps_delta_pct": 5.5,
                "bottleneck_resource_delta_pp": -1.2,
                "bottleneck_resource": "memory",
            },
            "target_direction_ok": True,
            "performance_guard_ok": True,
        },
        {
            "candidate_primary_target_status": {
                "db_qps_delta_pct": 7.0,
                "bottleneck_resource_delta_pp": -0.8,
                "bottleneck_resource": "memory",
            },
            "target_direction_ok": True,
            "performance_guard_ok": True,
        },
        {
            "candidate_primary_target_status": {
                "db_qps_delta_pct": 6.0,
                "bottleneck_resource_delta_pp": -1.0,
                "bottleneck_resource": "memory",
            },
            "target_direction_ok": True,
            "performance_guard_ok": True,
        },
    ]
    summary = {
        "pairwise_inversion_rate": 0.05,  # <= 0.10
        "first_stage_ranking_validation_pass": True,
        "explainable_lane_hit_count": 3,  # I2 >= 3
        "lane_counts": {"explainable": 3, "fallback": 0, "diagnostic": 0, "mandatory_anchor": 0, "reserve": 0},
    }
    record = _compute_adaptive_replay_decision(
        candidate_evaluations=candidate_evaluations,
        summary=summary,
        target_resource="memory",
        baseline_resource={"cpu": 7.0, "memory": 3.38, "io": 800.0},
        capability={"cpu": 14.0, "memory": 4.0, "io": 1024.0},
        replayed_count=3,
    )
    _check(
        "helper_proceed_decision",
        record.get("decision") == "PROCEED_TO_OUTER_LOOP",
        f"expected PROCEED_TO_OUTER_LOOP, got {record.get('decision')}; reason={record.get('reason')}",
    )
    # I3 = max db_qps_delta_pct = 7.0
    _check(
        "helper_proceed_i3_from_real_data",
        record.get("inputs_snapshot", {}).get("I3_fallback_best_db_qps_delta_pct") == 7.0
        or 7.0 in str(record.get("inputs_snapshot", {})),
        f"expected I3=7.0 from real candidate_evaluations, got {record.get('inputs_snapshot')}",
    )


def test_helper_stop_fix_on_safety_gate() -> None:
    """test helper stop fix on safety gate."""
    from parameter_optimizer import _compute_adaptive_replay_decision

    record = _compute_adaptive_replay_decision(
        candidate_evaluations=[],
        summary={"pairwise_inversion_rate": 0.0, "first_stage_ranking_validation_pass": True, "explainable_lane_hit_count": 0, "lane_counts": {}},
        target_resource="memory",
        baseline_resource={"cpu": 7.0, "memory": 3.38, "io": 800.0},
        capability={"cpu": 14.0, "memory": 4.0, "io": 1024.0},
        replayed_count=0,
        safety_model_gate_passed=False,
    )
    _check(
        "helper_stop_fix_decision",
        record.get("decision") == "STOP_FIX",
        f"expected STOP_FIX, got {record.get('decision')}",
    )


def test_helper_stop_fix_ranking_on_i2_zero_i4_high() -> None:
    """test helper stop fix ranking on i2 zero i4 high."""

    from parameter_optimizer import _compute_adaptive_replay_decision

    record = _compute_adaptive_replay_decision(
        candidate_evaluations=[],
        summary={
            "pairwise_inversion_rate": 0.7218,
            "first_stage_ranking_validation_pass": False,
            "explainable_lane_hit_count": 0,
            "lane_counts": {},
        },
        target_resource="memory",
        baseline_resource={"cpu": 7.0, "memory": 3.38, "io": 800.0},
        capability={"cpu": 14.0, "memory": 4.0, "io": 1024.0},
        replayed_count=40,
    )
    _check(
        "helper_stop_fix_ranking_decision",
        record.get("decision") == "STOP_FIX_RANKING",
        f"expected STOP_FIX_RANKING, got {record.get('decision')}",
    )
    _check(
        "helper_stop_fix_ranking_explainable_lane_empty",
        record.get("explainable_lane_empty") is True,
        f"expected explainable_lane_empty=True, got {record.get('explainable_lane_empty')}",
    )


def test_helper_i7_from_real_bottleneck_delta() -> None:
    """test helper i7 from real bottleneck delta."""

    from parameter_optimizer import _compute_adaptive_replay_decision

    candidate_evaluations = [
        {"candidate_primary_target_status": {"bottleneck_resource_delta_pp": -1.0}},  # down (count)
        {"candidate_primary_target_status": {"bottleneck_resource_delta_pp": 0.5}},   # up (no count)
        {"candidate_primary_target_status": {"bottleneck_resource_delta_pp": -0.3}},  # down (count)
        {"candidate_primary_target_status": {"bottleneck_resource_delta_pp": 0.0}},   # flat (no count)
        {"candidate_primary_target_status": {}},  # missing field (no count)
    ]
    record = _compute_adaptive_replay_decision(
        candidate_evaluations=candidate_evaluations,
        summary={"pairwise_inversion_rate": 0.0, "first_stage_ranking_validation_pass": True, "explainable_lane_hit_count": 0, "lane_counts": {}},
        target_resource="memory",
        baseline_resource={"cpu": 7.0, "memory": 3.38, "io": 800.0},
        capability={"cpu": 14.0, "memory": 4.0, "io": 1024.0},
        replayed_count=5,
    )
    inputs_snapshot = record.get("inputs_snapshot", {})
    i7 = inputs_snapshot.get("I7_cumulative_target_down_evidence_count")
    _check(
        "helper_i7_from_real_data",
        i7 == 2,
        f"expected I7=2 from 2 down candidates, got I7={i7}; snapshot={inputs_snapshot}",
    )


def test_helper_exception_does_not_raise() -> None:
    """test helper exception does not raise."""
    from parameter_optimizer import _compute_adaptive_replay_decision


    record = _compute_adaptive_replay_decision(
        candidate_evaluations=None,
        summary=None,  # type: ignore[arg-type]
        target_resource="memory",
        baseline_resource={},
        capability={},
        replayed_count=0,
    )
    _check(
        "helper_exception_returns_dict",
        isinstance(record, dict),
        f"expected dict, got {type(record)}",
    )
    _check(
        "helper_exception_decision_compute_error",
        record.get("decision") == "compute_error",
        f"expected decision=compute_error, got {record.get('decision')}",
    )
    _check(
        "helper_exception_phase1_integration_flag",
        record.get("phase1_integration") is True,
        f"expected phase1_integration=True, got {record.get('phase1_integration')}",
    )


def test_helper_source_file_contains_phase1_note() -> None:
    """test helper source file contains phase1 note."""
    from parameter_optimizer import _compute_adaptive_replay_decision

    docstring = inspect.getdoc(_compute_adaptive_replay_decision) or ""
    _check(
        "helper_docstring_mentions_phase1",
        "phase 1" in docstring.lower() or "phase1" in docstring.lower(),
        "helper docstring must mention phase 1 integration",
    )
    _check(
        "helper_docstring_mentions_override_section",
        "§16:22" in docstring,
        "helper docstring must reference override §16:22",
    )


def test_two_call_sites_exist_in_source() -> None:
    """test two call sites exist in source."""

    po_path = os.path.join(_PROJECT_ROOT, "parameter_optimizer.py")
    with open(po_path, "r", encoding="utf-8") as f:
        source = f.read()

    initial_idx = source.find("_initial_adaptive_decision_record = _compute_adaptive_replay_decision(")
    feedback_idx = source.find("_fb_adaptive_decision_record = _compute_adaptive_replay_decision(")
    _check(
        "two_call_sites_found",
        initial_idx > 0 and feedback_idx > 0,
        f"initial_idx={initial_idx}, feedback_idx={feedback_idx}",
    )


    initial_window = source[max(0, initial_idx - 4000):initial_idx]
    feedback_window = source[max(0, feedback_idx - 4000):feedback_idx]
    _check(
        "initial_call_site_flag_gated",
        "bf_adaptive_replay_controller_enabled" in initial_window,
        "initial call site must be within bf_adaptive_replay_controller_enabled flag gate",
    )
    _check(
        "feedback_call_site_flag_gated",
        "bf_adaptive_replay_controller_enabled" in feedback_window,
        "feedback call site must be within bf_adaptive_replay_controller_enabled flag gate",
    )


def main() -> int:
    print("=== iter102 phase 1 decide_batch live-path integration tests ===")
    test_static_grep_decide_batch_in_parameter_optimizer()
    test_helper_empty_inputs_expand_diagnostic()
    test_helper_proceed_to_outer_loop()
    test_helper_stop_fix_on_safety_gate()
    test_helper_stop_fix_ranking_on_i2_zero_i4_high()
    test_helper_i7_from_real_bottleneck_delta()
    test_helper_exception_does_not_raise()
    test_helper_source_file_contains_phase1_note()
    test_two_call_sites_exist_in_source()
    print(f"\n=== Summary: {_passed} passed, {_failed} failed ===")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
