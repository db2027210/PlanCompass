"""Module."""


from __future__ import annotations

import copy
import os
import sys

# Make the project root importable
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from parameter_optimizer import _lane_aware_replay_prefix_validation
from adaptive_replay_controller import LaneQuotaConfig


_BASELINE = {"cpu": 4.0, "memory": 3.0, "io": 800.0}
_CAPABILITY = {"cpu": 8.0, "memory": 4.0, "io": 1000.0}


def _explainable_candidate(mem_pred: float, cpu_pred: float, db_qps_delta: float) -> dict:
    """memory down + cpu up → explainable lane candidate."""
    return {
        "predicted_resource": {
            "cpu": cpu_pred,
            "memory": mem_pred,
            "io": 800.0,
        },
        "predicted_db_qps_delta_pct": db_qps_delta,
    }


def _fallback_candidate(mem_pred: float, db_qps_delta: float) -> dict:
    """memory up wrong dir + high db_qps → fallback lane candidate."""
    return {
        "predicted_resource": {
            "cpu": 4.0,
            "memory": mem_pred,
            "io": 800.0,
        },
        "predicted_db_qps_delta_pct": db_qps_delta,
    }


def _anchor_candidate() -> dict:
    """mandatory anchor candidate (baseline-like)."""
    return {
        "predicted_resource": {
            "cpu": 4.0,
            "memory": 3.0,
            "io": 800.0,
        },
        "predicted_db_qps_delta_pct": 0.0,
    }


def test_lane_aware_empty_replayed_list() -> None:
    """test lane aware empty replayed list."""
    r = _lane_aware_replay_prefix_validation(
        [],
        target_resource="memory",
        baseline_resource=_BASELINE,
        capability=_CAPABILITY,
    )
    assert r["explainable_lane_empty"] is True, r
    assert r["explainable_lane_hit_count"] == 0, r
    assert r["no_count_fill_applied"] is True, r
    assert r["mandatory_anchor_count"] == 0, r
    assert r["lane_counts"]["explainable"] == 0, r
    assert r["lane_counts"]["fallback"] == 0, r
    assert r["lane_counts"]["mandatory_anchor"] == 0, r
    assert r["lane_assignment"]["explainable"] == [], r
    assert r["target_resource"] == "memory", r
    print("PASS test_lane_aware_empty_replayed_list")


def test_lane_aware_explainable_hit_count() -> None:
    """test lane aware explainable hit count."""
    cands = [
        _explainable_candidate(2.0, 5.0, 10.0),   # idx 0 explainable
        _explainable_candidate(1.5, 5.5, 12.0),   # idx 1 explainable
        _explainable_candidate(2.5, 6.0, 8.0),    # idx 2 explainable
        _fallback_candidate(3.5, 15.0),           # idx 3 fallback
        _fallback_candidate(3.3, 6.0),            # idx 4 fallback
        _anchor_candidate(),                      # idx 5 mandatory anchor
    ]
    r = _lane_aware_replay_prefix_validation(
        cands,
        target_resource="memory",
        baseline_resource=_BASELINE,
        capability=_CAPABILITY,
        mandatory_anchor_indices=(5,),
    )
    assert r["explainable_lane_empty"] is False, r
    assert r["explainable_lane_hit_count"] == 3, r
    assert r["mandatory_anchor_count"] == 1, r
    assert r["lane_counts"]["explainable"] == 3, r
    assert r["lane_counts"]["fallback"] == 2, r
    assert r["lane_counts"]["mandatory_anchor"] == 1, r
    assert r["no_count_fill_applied"] is True, r
    assert 0 in r["lane_assignment"]["explainable"], r
    assert 5 in r["lane_assignment"]["mandatory_anchor"], r
    print("PASS test_lane_aware_explainable_hit_count")


def test_lane_aware_no_cross_lane_fill_when_explainable_empty() -> None:
    """test lane aware no cross lane fill when explainable empty."""


    cands = [
        _fallback_candidate(3.5, 10.0),   # idx 0 fallback
        _fallback_candidate(3.8, 8.0),    # idx 1 fallback
    ]
    r = _lane_aware_replay_prefix_validation(
        cands,
        target_resource="memory",
        baseline_resource=_BASELINE,
        capability=_CAPABILITY,
    )
    assert r["explainable_lane_empty"] is True, r
    assert r["explainable_lane_hit_count"] == 0, r
    assert r["no_count_fill_applied"] is True, r
    assert r["lane_counts"]["explainable"] == 0, r
    assert r["lane_counts"]["fallback"] == 2, r

    assert r["lane_assignment"]["explainable"] == [], r
    print("PASS test_lane_aware_no_cross_lane_fill_when_explainable_empty")


def test_lane_aware_mandatory_anchor_does_not_consume_explainable_quota() -> None:
    """test lane aware mandatory anchor does not consume explainable quota."""


    cands = [
        _anchor_candidate(),                      # idx 0 mandatory anchor
        _anchor_candidate(),                      # idx 1 mandatory anchor
        _explainable_candidate(2.0, 5.0, 10.0),   # idx 2 explainable
        _explainable_candidate(1.5, 5.5, 12.0),   # idx 3 explainable
    ]
    r = _lane_aware_replay_prefix_validation(
        cands,
        target_resource="memory",
        baseline_resource=_BASELINE,
        capability=_CAPABILITY,
        mandatory_anchor_indices=(0, 1),
    )
    assert r["mandatory_anchor_count"] == 2, r
    assert r["explainable_lane_hit_count"] == 2, r
    assert r["lane_counts"]["mandatory_anchor"] == 2, r
    assert r["lane_counts"]["explainable"] == 2, r

    assert 0 not in r["lane_assignment"]["explainable"], r
    assert 1 not in r["lane_assignment"]["explainable"], r
    assert 0 in r["lane_assignment"]["mandatory_anchor"], r
    assert 1 in r["lane_assignment"]["mandatory_anchor"], r
    print("PASS test_lane_aware_mandatory_anchor_does_not_consume_explainable_quota")


def test_lane_aware_quota_exceeded_detection() -> None:
    """test lane aware quota exceeded detection."""


    small_quota = LaneQuotaConfig(
        max_budget=24,
        min_evidence=12,
        batch_size=6,
        max_expansions=2,
        explainable=1,
        fallback=1,
        diagnostic=5,
        mandatory_anchor=2,
        reserve=5,
    )
    cands = [
        _explainable_candidate(2.0, 5.0, 10.0),   # idx 0 explainable
        _explainable_candidate(1.5, 5.5, 12.0),   # idx 1 explainable
        _fallback_candidate(3.5, 15.0),           # idx 2 fallback
        _fallback_candidate(3.8, 8.0),            # idx 3 fallback
    ]
    r = _lane_aware_replay_prefix_validation(
        cands,
        target_resource="memory",
        baseline_resource=_BASELINE,
        capability=_CAPABILITY,
        lane_quota_config=small_quota,
    )
    assert r["lane_counts"]["explainable"] == 2, r
    assert r["lane_counts"]["fallback"] == 2, r
    assert "explainable" in r["lane_quota_exceeded"], r
    assert r["lane_quota_exceeded"]["explainable"] == 1, r  # 2-1=1
    assert "fallback" in r["lane_quota_exceeded"], r
    assert r["lane_quota_exceeded"]["fallback"] == 1, r  # 2-1=1
    print("PASS test_lane_aware_quota_exceeded_detection")


def test_lane_aware_pure_function_no_mutation() -> None:
    """test lane aware pure function no mutation."""
    cands = [
        _explainable_candidate(2.0, 5.0, 10.0),
        _fallback_candidate(3.5, 15.0),
        _anchor_candidate(),
    ]
    cands_before = copy.deepcopy(cands)
    _ = _lane_aware_replay_prefix_validation(
        cands,
        target_resource="memory",
        baseline_resource=_BASELINE,
        capability=_CAPABILITY,
        mandatory_anchor_indices=(2,),
    )
    assert cands == cands_before, " candidates "

    _ = _lane_aware_replay_prefix_validation(
        cands,
        target_resource="memory",
        baseline_resource=_BASELINE,
        capability=_CAPABILITY,
        mandatory_anchor_indices=(2,),
    )
    assert cands == cands_before, ""
    print("PASS test_lane_aware_pure_function_no_mutation")


def _run_all() -> int:
    """ run all."""
    tests = [
        test_lane_aware_empty_replayed_list,
        test_lane_aware_explainable_hit_count,
        test_lane_aware_no_cross_lane_fill_when_explainable_empty,
        test_lane_aware_mandatory_anchor_does_not_consume_explainable_quota,
        test_lane_aware_quota_exceeded_detection,
        test_lane_aware_pure_function_no_mutation,
    ]
    failures = 0
    for t in tests:
        try:
            t()
        except Exception as exc:
            failures += 1
            print(f"FAIL {t.__name__}: {exc!r}")
    print(f"\n=== {len(tests) - failures}/{len(tests)} PASS, {failures} FAIL ===")
    return failures


if __name__ == "__main__":
    sys.exit(0 if _run_all() == 0 else 1)
