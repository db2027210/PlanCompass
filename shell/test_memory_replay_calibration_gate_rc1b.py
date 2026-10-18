"""Minimal RC#1b test: Memory replay-calibration gate (override §311-354 item 1 + §8 #4).

Beyond point-prediction R2/relative_error (RC#1), Memory models must also be
checked for DIRECTION-prediction reliability using historical real-replay
calibration evidence. v6 40 replays: 21 predicted Memory down but only 3 actual
down → predicted-down-actual-up ratio ~85% → model is unreliable for direction
ranking, so the gate must fail to prevent another small_loop_ranking_gate_failed
cycle.

This test builds a synthetic Memory retrain_result (good point-prediction
metrics) plus calibration evidence dicts, and asserts:
  - calibration disabled → no calibration checks (backward compatible)
  - calibration enabled + good evidence (ratio <= 0.5) → passes
  - calibration enabled + bad evidence (ratio > 0.5, v6-like) → FAILS
  - calibration enabled + insufficient predicted_down_count → FAILS
  - calibration enabled + no evidence dict → graceful no-op (no calibration checks)

Run: python3 shell/test_memory_replay_calibration_gate_rc1b.py
"""
from __future__ import annotations

import sys
import os
from typing import Dict, Any

# Make the project root importable
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from parameter_optimizer import validate_resource_model_quality_gate  # type: ignore


def _build_memory_retrain_result_good() -> Dict[str, Any]:
    """Build a synthetic Memory retrain_result with good point-prediction metrics.

    The model has good R2/relative_error so it passes RC#1; only the calibration
    gate (RC#1b) can fail it, proving the two gates are independent.
    """
    return {
        "resources": {
            "memory": {
                "status": "retrained",
                "source": "runtime_memory_train_test",
                "selected_model_set": "AP_only",
                "updated_model_sets": {
                    "AP_only": {
                        "metrics": {
                            "train": {"sample_count": 28},
                            "test": {
                                "sample_count": 7,
                                "r2": 0.5,
                                "relative_error_p95": 0.05,
                                "relative_error_max": 0.10,
                            },
                        },
                        "error_bound": {"sample_count": 35},
                    }
                },
            }
        }
    }


def _build_gate_cfg(
    *,
    calibration_enabled: bool,
    evidence: Dict[str, Any] = None,
    max_ratio: float = 0.5,
    min_predicted_down_count: int = 5,
) -> Dict[str, Any]:
    cfg: Dict[str, Any] = {
        "enabled": True,
        "required_resources": ["memory"],
        "optional_resources": [],
        "min_test_samples": 5,
        "min_test_r2": 0.0,
        "max_test_relative_error_p95": 1.0,
        "max_test_relative_error_max": 5.0,
        "enforce_test_r2": True,
        "enforce_test_relative_error_p95": False,
        "enforce_test_relative_error_max": False,
        "selected_model_only": True,
        "workload_family": "H",
        "warning_test_relative_error_p95": 0.5,
        "warning_test_relative_error_max": 5.0,
        "memory_replay_calibration": {
            "enabled": calibration_enabled,
            "max_predicted_down_actual_up_ratio": max_ratio,
            "min_predicted_down_count": min_predicted_down_count,
        },
    }
    if evidence is not None:
        cfg["memory_replay_calibration"]["evidence"] = evidence
    return cfg


def _has_calibration_failure(summary: Dict[str, Any]) -> bool:
    for item in summary.get("failures", []):
        if item.get("reason") == "memory_replay_calibration_failed":
            return True
    return False


def _has_calibration_check(summary: Dict[str, Any]) -> bool:
    for check in summary.get("checks", []):
        if check.get("check") == "memory_replay_calibration":
            return True
    return False


def test_calibration_disabled_no_checks():
    """When calibration gate disabled, no calibration checks should appear."""
    retrain = _build_memory_retrain_result_good()
    cfg = _build_gate_cfg(calibration_enabled=False)
    summary = validate_resource_model_quality_gate(retrain, cfg)
    assert summary["passed"] is True, (
        f"good model + calibration disabled should pass; "
        f"got passed={summary['passed']}; failures={summary.get('failures')}"
    )
    assert not _has_calibration_check(summary), (
        f"calibration disabled should produce no calibration check; "
        f"checks={summary.get('checks')}"
    )
    print("[PASS] test_calibration_disabled_no_checks: backward compatible")


def test_calibration_enabled_good_evidence_passes():
    """Calibration enabled + ratio 0.2 (<= 0.5) + count 20 (>= 5) → passes."""
    retrain = _build_memory_retrain_result_good()
    evidence = {
        "predicted_down_count": 20,
        "actual_down_count": 16,
        "predicted_down_actual_up_count": 4,
        "source_run_id": "v6_test",
    }
    cfg = _build_gate_cfg(calibration_enabled=True, evidence=evidence)
    summary = validate_resource_model_quality_gate(retrain, cfg)
    assert summary["passed"] is True, (
        f"good calibration evidence should pass; "
        f"got passed={summary['passed']}; failures={summary.get('failures')}"
    )
    assert _has_calibration_check(summary), (
        f"calibration enabled should produce a calibration check; "
        f"checks={summary.get('checks')}"
    )
    print("[PASS] test_calibration_enabled_good_evidence_passes: ratio 0.2 accepted")


def test_calibration_enabled_v6_like_bad_evidence_fails():
    """Calibration enabled + v6-like ratio 0.857 (21 predicted, 18 actual-up) → FAILS.

    This is the direct root-cause scenario: v6 had 21 predicted Memory down but
    only 3 actual down (18 actual-up). ratio = 18/21 = 0.857 > 0.5 → must fail.
    """
    retrain = _build_memory_retrain_result_good()
    evidence = {
        "predicted_down_count": 21,
        "actual_down_count": 3,
        "predicted_down_actual_up_count": 18,
        "source_run_id": "pg_bonect_phase2_..._v6_20260706_015306",
    }
    cfg = _build_gate_cfg(calibration_enabled=True, evidence=evidence)
    summary = validate_resource_model_quality_gate(retrain, cfg)
    assert summary["passed"] is False, (
        f"v6-like bad calibration (ratio 0.857 > 0.5) should FAIL; "
        f"got passed={summary['passed']}"
    )
    assert _has_calibration_failure(summary), (
        f"expected memory_replay_calibration_failed in failures; "
        f"failures={summary.get('failures')}"
    )
    # Verify the failure reason mentions the ratio
    cal_failures = [
        item for item in summary["failures"]
        if item.get("reason") == "memory_replay_calibration_failed"
    ]
    assert cal_failures, "expected at least one calibration failure item"
    reasons_flat = " ".join(
        str(item.get("reasons", [])) for item in cal_failures
    )
    assert "predicted_down_actual_up_ratio" in reasons_flat, (
        f"expected failure reason mentioning predicted_down_actual_up_ratio; "
        f"reasons={reasons_flat}"
    )
    print("[PASS] test_calibration_enabled_v6_like_bad_evidence_fails: v6 scenario correctly blocked")


def test_calibration_enabled_insufficient_count_fails():
    """Calibration enabled + predicted_down_count 2 (< min 5) → FAILS."""
    retrain = _build_memory_retrain_result_good()
    evidence = {
        "predicted_down_count": 2,
        "actual_down_count": 1,
        "predicted_down_actual_up_count": 1,
        "source_run_id": "small_sample_test",
    }
    cfg = _build_gate_cfg(calibration_enabled=True, evidence=evidence)
    summary = validate_resource_model_quality_gate(retrain, cfg)
    assert summary["passed"] is False, (
        f"insufficient predicted_down_count should FAIL; "
        f"got passed={summary['passed']}"
    )
    assert _has_calibration_failure(summary), (
        f"expected calibration failure; failures={summary.get('failures')}"
    )
    reasons_flat = " ".join(
        str(item.get("reasons", []))
        for item in summary["failures"]
        if item.get("reason") == "memory_replay_calibration_failed"
    )
    assert "predicted_down_count" in reasons_flat, (
        f"expected failure mentioning predicted_down_count; reasons={reasons_flat}"
    )
    print("[PASS] test_calibration_enabled_insufficient_count_fails: small sample blocked")


def test_calibration_enabled_no_evidence_graceful_noop():
    """Calibration enabled but no evidence dict → no calibration checks (graceful).

    The gate should not crash when evidence is absent; it simply doesn't run the
    calibration check. This allows callers to enable the gate config before
    having calibration data ready.
    """
    retrain = _build_memory_retrain_result_good()
    cfg = _build_gate_cfg(calibration_enabled=True, evidence=None)
    summary = validate_resource_model_quality_gate(retrain, cfg)
    assert summary["passed"] is True, (
        f"calibration enabled + no evidence should not fail (graceful no-op); "
        f"got passed={summary['passed']}; failures={summary.get('failures')}"
    )
    assert not _has_calibration_check(summary), (
        f"no evidence → no calibration check expected; checks={summary.get('checks')}"
    )
    print("[PASS] test_calibration_enabled_no_evidence_graceful_noop: no crash when evidence absent")


def test_calibration_thresholds_in_summary():
    """RC#1b transparency: summary thresholds must include calibration config."""
    retrain = _build_memory_retrain_result_good()
    cfg = _build_gate_cfg(calibration_enabled=True, max_ratio=0.4, min_predicted_down_count=8)
    summary = validate_resource_model_quality_gate(retrain, cfg)
    thresholds = summary.get("thresholds", {})
    assert thresholds.get("memory_replay_calibration_enabled") is True, (
        f"thresholds missing memory_replay_calibration_enabled; thresholds={thresholds}"
    )
    assert thresholds.get("memory_replay_calibration_max_predicted_down_actual_up_ratio") == 0.4, (
        f"thresholds max_ratio mismatch; thresholds={thresholds}"
    )
    assert thresholds.get("memory_replay_calibration_min_predicted_down_count") == 8, (
        f"thresholds min_count mismatch; thresholds={thresholds}"
    )
    print("[PASS] test_calibration_thresholds_in_summary: transparency fields present")


def main() -> int:
    print("=" * 70)
    print("RC#1b test: Memory replay-calibration gate (direction-prediction reliability)")
    print("=" * 70)
    test_calibration_disabled_no_checks()
    test_calibration_enabled_good_evidence_passes()
    test_calibration_enabled_v6_like_bad_evidence_fails()
    test_calibration_enabled_insufficient_count_fails()
    test_calibration_enabled_no_evidence_graceful_noop()
    test_calibration_thresholds_in_summary()
    print("=" * 70)
    print("ALL RC#1b TESTS PASSED")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
