"""Minimal RC#1 test: Memory quality-gate branch must check test.r2 / relative_error.

Validates the fix for the override directive's root cause #1:
`validate_resource_model_quality_gate()` Memory branch previously only checked
sample counts and error-bound sample counts, letting AP_only Memory models with
test r2=-10.8868 and relative_error_p95=0.3455 still pass the gate.

This test builds a synthetic Memory retrain_result with a bad AP_only model and
asserts that when `enforce_test_r2=True` and `min_test_r2=0.0`, the gate FAILS
with a reason mentioning `test.r2`.

Run: python3 shell/test_resource_model_quality_gate_memory_rc1.py
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


def _build_memory_retrain_result(
    *,
    test_r2: float,
    test_rel_p95: float,
    test_rel_max: float,
    train_samples: int = 28,
    test_samples: int = 7,
    error_bound_samples: int = 35,
) -> Dict[str, Any]:
    """Build a synthetic Memory retrain_result mimicking v6 AP_only model."""
    return {
        "resources": {
            "memory": {
                "status": "retrained",
                "source": "runtime_memory_train_test",
                "selected_model_set": "AP_only",
                "updated_model_sets": {
                    "AP_only": {
                        "metrics": {
                            "train": {"sample_count": train_samples},
                            "test": {
                                "sample_count": test_samples,
                                "r2": test_r2,
                                "relative_error_p95": test_rel_p95,
                                "relative_error_max": test_rel_max,
                            },
                        },
                        "error_bound": {"sample_count": error_bound_samples},
                    }
                },
            }
        }
    }


def _build_gate_cfg(*, enforce_r2: bool, min_r2: float, enforce_p95: bool = False,
                    max_p95: float = 1.0) -> Dict[str, Any]:
    return {
        "enabled": True,
        "required_resources": ["memory"],
        "optional_resources": [],
        "min_test_samples": 5,
        "min_test_r2": min_r2,
        "max_test_relative_error_p95": max_p95,
        "max_test_relative_error_max": 5.0,
        "enforce_test_r2": enforce_r2,
        "enforce_test_relative_error_p95": enforce_p95,
        "enforce_test_relative_error_max": False,
        "selected_model_only": True,
        "workload_family": "H",
        "warning_test_relative_error_p95": 0.5,
        "warning_test_relative_error_max": 5.0,
    }


def test_bad_r2_with_enforce_fails():
    """RC#1: bad r2 (-10.0) + enforce_test_r2=True → gate must FAIL with test.r2 reason."""
    retrain = _build_memory_retrain_result(
        test_r2=-10.0, test_rel_p95=0.35, test_rel_max=0.50
    )
    cfg = _build_gate_cfg(enforce_r2=True, min_r2=0.0)
    summary = validate_resource_model_quality_gate(retrain, cfg)
    assert summary["passed"] is False, (
        f"expected passed=False when enforce_test_r2=True and r2=-10, got passed={summary['passed']}; "
        f"failures={summary.get('failures')}"
    )
    reasons_flat = " ".join(
        str(item.get("reasons", [])) for item in summary["failures"]
    )
    assert "test.r2" in reasons_flat, (
        f"expected failure reason mentioning 'test.r2', got failures={summary['failures']}"
    )
    print("[PASS] test_bad_r2_with_enforce_fails: gate correctly failed on bad r2")


def test_bad_r2_without_enforce_passes_but_warns():
    """When enforce_test_r2=False (current default), gate still passes but should warn
    when relative_error exceeds the warning threshold."""
    retrain = _build_memory_retrain_result(
        test_r2=-10.0, test_rel_p95=0.60, test_rel_max=0.70
    )
    cfg = _build_gate_cfg(enforce_r2=False, min_r2=0.0)
    summary = validate_resource_model_quality_gate(retrain, cfg)
    # Gate passes because enforce=False, but warnings should mention relative_error
    assert summary["passed"] is True, (
        f"with enforce_test_r2=False, gate should still pass (warnings only); "
        f"got passed={summary['passed']}; failures={summary.get('failures')}"
    )
    warnings_flat = " ".join(
        str(item.get("warnings", [])) for item in summary["warnings"]
    )
    assert "relative_error_p95" in warnings_flat, (
        f"expected warning mentioning relative_error_p95; got warnings={summary['warnings']}"
    )
    print("[PASS] test_bad_r2_without_enforce_passes_but_warns: warnings emitted correctly")


def test_good_r2_with_enforce_passes():
    """Sanity: good r2 (0.5) + enforce_test_r2=True → gate PASSES."""
    retrain = _build_memory_retrain_result(
        test_r2=0.5, test_rel_p95=0.05, test_rel_max=0.10
    )
    cfg = _build_gate_cfg(enforce_r2=True, min_r2=0.0, enforce_p95=True, max_p95=0.5)
    summary = validate_resource_model_quality_gate(retrain, cfg)
    assert summary["passed"] is True, (
        f"good model should pass; got passed={summary['passed']}; failures={summary['failures']}"
    )
    print("[PASS] test_good_r2_with_enforce_passes: good model correctly passed")


def test_mild_negative_r2_low_error_passes_with_warning():
    """Current Memory AP model pattern: mildly negative R2 but very low relative errors.

    This should not stop Bonect before candidate search; the low R2 is treated
    as a low-variance warning while relative-error bounds remain tight.
    """
    retrain = _build_memory_retrain_result(
        test_r2=-0.1268, test_rel_p95=0.0467, test_rel_max=0.0545,
        train_samples=31, test_samples=8, error_bound_samples=8,
    )
    cfg = _build_gate_cfg(enforce_r2=True, min_r2=0.0, enforce_p95=True, max_p95=1.0)
    summary = validate_resource_model_quality_gate(retrain, cfg)
    assert summary["passed"] is True, (
        f"mild negative r2 with low relative errors should pass as warning; "
        f"failures={summary.get('failures')}"
    )
    warnings_flat = " ".join(
        str(item.get("warnings", [])) for item in summary["warnings"]
    )
    assert "low-variance R2 warning" in warnings_flat, (
        f"expected low-variance R2 warning; got warnings={summary['warnings']}"
    )
    print("[PASS] test_mild_negative_r2_low_error_passes_with_warning: low-error memory model passed")


def test_check_dict_contains_r2_and_rel_error_fields():
    """RC#1 transparency: check dict must include test_r2/relative_error_p95/max."""
    retrain = _build_memory_retrain_result(
        test_r2=-10.0, test_rel_p95=0.35, test_rel_max=0.50
    )
    cfg = _build_gate_cfg(enforce_r2=False, min_r2=0.0)
    summary = validate_resource_model_quality_gate(retrain, cfg)
    memory_checks = [c for c in summary["checks"] if c.get("resource") == "memory"]
    assert memory_checks, "expected at least one memory check entry"
    check = memory_checks[0]
    assert "test_r2" in check, f"check dict missing test_r2; check={check}"
    assert "test_relative_error_p95" in check, f"check dict missing test_relative_error_p95; check={check}"
    assert "test_relative_error_max" in check, f"check dict missing test_relative_error_max; check={check}"
    assert check["test_r2"] == -10.0, f"test_r2 should be -10.0; got {check['test_r2']}"
    assert check["test_relative_error_p95"] == 0.35, (
        f"test_relative_error_p95 should be 0.35; got {check['test_relative_error_p95']}"
    )
    print("[PASS] test_check_dict_contains_r2_and_rel_error_fields: transparency fields present")


def test_bad_rel_p95_with_enforce_fails():
    """RC#1: bad relative_error_p95 (0.9) + enforce=True → gate FAILS."""
    retrain = _build_memory_retrain_result(
        test_r2=0.5, test_rel_p95=0.9, test_rel_max=0.95
    )
    cfg = _build_gate_cfg(enforce_r2=False, min_r2=0.0, enforce_p95=True, max_p95=0.5)
    summary = validate_resource_model_quality_gate(retrain, cfg)
    assert summary["passed"] is False, (
        f"bad rel_p95 with enforce should fail; got passed={summary['passed']}"
    )
    reasons_flat = " ".join(
        str(item.get("reasons", [])) for item in summary["failures"]
    )
    assert "relative_error_p95" in reasons_flat, (
        f"expected failure mentioning relative_error_p95; got failures={summary['failures']}"
    )
    print("[PASS] test_bad_rel_p95_with_enforce_fails: gate correctly failed on bad rel_p95")


def main() -> int:
    print("=" * 70)
    print("RC#1 test: Memory quality-gate R2/relative_error checks")
    print("=" * 70)
    test_bad_r2_with_enforce_fails()
    test_bad_r2_without_enforce_passes_but_warns()
    test_good_r2_with_enforce_passes()
    test_mild_negative_r2_low_error_passes_with_warning()
    test_check_dict_contains_r2_and_rel_error_fields()
    test_bad_rel_p95_with_enforce_fails()
    print("=" * 70)
    print("ALL RC#1 TESTS PASSED")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
