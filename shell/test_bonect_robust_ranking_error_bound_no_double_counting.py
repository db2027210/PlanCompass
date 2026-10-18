"""Minimal RC#2 test: robust-ranking error profile must use max(model,baseline), not sum.

Validates the fix for the override directive's root cause #4
(`.agents/bonect-opencode-command-current.md` §319-336, lines 333-336) and
topk-advisor §8 #5: when the post-retrain model error bound and the baseline
anchor residual are derived from overlapping samples (typical for AP_only
Memory with 1-sample baseline anchor, v6 had `model_abs_bound=1337.884 MB`
and `baseline_abs_bound=1337.884 MB` simultaneously), the legacy
`abs_bound = model_abs_bound + baseline_abs_bound` inflated the robust-ranking
bound to ~2× the true test p95. This saturates the optimistic pruning
envelope and lets count-fill / forced-anchor candidates that should have been
pruned squeeze into the small-loop replay prefix (v6 pairwise inversion 0.7218).

The fix replaces all four call sites:
- `bonect_robust_ranking.py::_resource_uncertainty_profile` confidence calc
- `bonect_robust_ranking.py::_resource_bound_value` variant bound calc
- `parameter_optimizer.py` line ~19387 raw_abs_bound in pruning gate
- `binary_plan_resource_tuner.py::summarize_resource_error_profile` total_bound

with `max(model_abs_bound, baseline_abs_bound)` so the larger bound dominates
and the smaller is treated as already explained by it, not as additional
uncertainty.

Required cases (iter92 next_iter_signal):
  - baseline_only: model=0, baseline=B  -> bound=B
  - post_retrain_larger: model=M, baseline=b (M>b) -> bound=M (not M+b)
  - baseline_larger: model=m, baseline=B (B>m) -> bound=B (not m+B)
  - tie boundary: model=X, baseline=X -> bound=X (not 2X)

Run: python3 shell/test_bonect_robust_ranking_error_bound_no_double_counting.py
"""
from __future__ import annotations

import sys
import os
from typing import Dict, Any

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from bonect_robust_ranking import (  # type: ignore
    _resource_bound_value,
    extract_error_profile,
    RESOURCE_NAMES,
)


def _build_profile(
    *,
    model_abs_bound: float,
    baseline_abs_bound: float,
    relative_p95: float = 0.0,
    resource: str = "memory",
) -> Dict[str, Dict[str, float]]:
    """Build a single-resource robust-ranking profile fragment."""
    profile: Dict[str, Dict[str, float]] = {
        name: {
            "model_abs_bound": 0.0,
            "baseline_abs_bound": 0.0,
            "relative_p95": 0.0,
            "confidence": 1.0,
        }
        for name in RESOURCE_NAMES
    }
    profile[resource]["model_abs_bound"] = float(model_abs_bound)
    profile[resource]["baseline_abs_bound"] = float(baseline_abs_bound)
    profile[resource]["relative_p95"] = float(relative_p95)
    return profile


def _pessimistic_bound(
    profile: Dict[str, Dict[str, float]],
    *,
    value: float = 100.0,
    capability_gb: float = 100000.0,
    resource: str = "memory",
) -> float:
    """Return pessimistic_bound variant = max(0, value + bound).

    With the legacy sum formula, pessimistic = value + (model + baseline).
    With the RC#2 max formula, pessimistic = value + max(model, baseline).
    Capability is set generously (cap*0.75 = 75000 >> max test bound 400) so
    `bounded_abs = min(abs_bound, cap*0.75)` does not clip the bound; this
    isolates the sum-vs-max behavior.
    """
    capability = {name: capability_gb for name in RESOURCE_NAMES}
    return _resource_bound_value(
        resource_name=resource,
        value=value,
        profile=profile,
        capability=capability,
        variant="pessimistic_bound",
        scenario=resource,
    )


def _metadata_record(
    *,
    model_abs_bound: float,
    baseline_abs_bound: float,
    resource: str = "memory",
    predicted_value: float = 3.5,
) -> Dict[str, Any]:
    """Build a metadata-style record mimicking v6 robust-ranking input.

    The metadata walker (`_walk_dicts`) traverses nested dicts/lists and
    collects any dict carrying `resource`/`target`/`error_bound`/`baseline_*`
    keys. We embed a single resource summary that contributes both a post-retrain
    `error_bound` and a `baseline_error_bound`, replicating the v6 condition
    where the two are derived from the same anchor sample.
    """
    return {
        "metadata": {
            "summary": {
                resource: {
                    "resource": resource,
                    "value": predicted_value,
                    "error_bound": {"value": float(model_abs_bound), "unit": "MB"},
                    "baseline_error_bound": {
                        "value": float(baseline_abs_bound),
                        "unit": "MB",
                    },
                    "metrics": {},
                }
            }
        }
    }


# ---------------------------------------------------------------------------
# Case 1: baseline_only — model_abs_bound=0, baseline_abs_bound=B
# Expected bound = B (no model contribution; both sum and max give B here,
# this case is the sanity baseline that the legacy formula would also pass).
# ---------------------------------------------------------------------------
def test_baseline_only():
    profile = _build_profile(model_abs_bound=0.0, baseline_abs_bound=200.0)
    got = _pessimistic_bound(profile, value=100.0)
    expected = 100.0 + 200.0  # value + max(0, 200) = 300
    assert abs(got - expected) < 1e-6, (
        f"baseline_only: expected pessimistic={expected}, got {got}"
    )
    # Sanity: ensure we did NOT somehow add 0+200 twice.
    assert got < 100.0 + 200.0 + 1.0, f"unexpected inflation: got={got}"
    print(f"[PASS] test_baseline_only: pessimistic={got} (bound=200 from baseline only)")


# ---------------------------------------------------------------------------
# Case 2: post_retrain_larger — model=M > baseline=b
# Expected bound = M (max). Legacy sum would give M+b.
# ---------------------------------------------------------------------------
def test_post_retrain_larger():
    profile = _build_profile(model_abs_bound=300.0, baseline_abs_bound=100.0)
    got = _pessimistic_bound(profile, value=100.0)
    expected = 100.0 + 300.0  # value + max(300, 100) = 400
    legacy_sum = 100.0 + (300.0 + 100.0)  # = 500
    assert abs(got - expected) < 1e-6, (
        f"post_retrain_larger: expected pessimistic={expected} (max), got {got}; "
        f"legacy_sum would be {legacy_sum}"
    )
    assert abs(got - legacy_sum) > 1.0, (
        f"post_retrain_larger: bound should NOT be legacy sum {legacy_sum}, got {got}"
    )
    print(
        f"[PASS] test_post_retrain_larger: pessimistic={got} "
        f"(max=300, NOT sum=400 -> total would be {legacy_sum})"
    )


# ---------------------------------------------------------------------------
# Case 3: baseline_larger — model=m < baseline=B
# Expected bound = B (max). Legacy sum would give m+B.
# ---------------------------------------------------------------------------
def test_baseline_larger():
    profile = _build_profile(model_abs_bound=50.0, baseline_abs_bound=400.0)
    got = _pessimistic_bound(profile, value=100.0)
    expected = 100.0 + 400.0  # value + max(50, 400) = 500
    legacy_sum = 100.0 + (50.0 + 400.0)  # = 550
    assert abs(got - expected) < 1e-6, (
        f"baseline_larger: expected pessimistic={expected} (max), got {got}; "
        f"legacy_sum would be {legacy_sum}"
    )
    assert abs(got - legacy_sum) > 1.0, (
        f"baseline_larger: bound should NOT be legacy sum {legacy_sum}, got {got}"
    )
    print(
        f"[PASS] test_baseline_larger: pessimistic={got} "
        f"(max=400, NOT sum=450 -> total would be {legacy_sum})"
    )


# ---------------------------------------------------------------------------
# Case 4: tie_boundary — model=X = baseline=X
# Expected bound = X (max). Legacy sum would give 2X.
# This is the v6 condition: `model_abs_bound=1337.884` AND
# `baseline_abs_bound=1337.884` simultaneously, producing 2675.768 MB bound
# (≈2.68 GB, larger than the 2.68 GiB total Memory capacity slice), saturating
# optimistic pruning to >100pp envelope.
# ---------------------------------------------------------------------------
def test_tie_boundary():
    profile = _build_profile(model_abs_bound=150.0, baseline_abs_bound=150.0)
    got = _pessimistic_bound(profile, value=100.0)
    expected = 100.0 + 150.0  # value + max(150, 150) = 250
    legacy_sum = 100.0 + (150.0 + 150.0)  # = 400
    assert abs(got - expected) < 1e-6, (
        f"tie_boundary: expected pessimistic={expected} (max), got {got}; "
        f"legacy_sum would be {legacy_sum}"
    )
    assert abs(got - legacy_sum) > 1.0, (
        f"tie_boundary: bound should NOT be legacy sum {legacy_sum}, got {got}"
    )
    print(
        f"[PASS] test_tie_boundary: pessimistic={got} "
        f"(max=150, NOT sum=300 -> total would be {legacy_sum})"
    )


# ---------------------------------------------------------------------------
# Case 5: extract_error_profile confidence must reflect max, not sum.
# When both bounds equal X, the legacy confidence denominator would be
# 1 + rel + 2X/1e6; the RC#2 denominator is 1 + rel + X/1e6. We pick X
# large enough that the relative difference exceeds floating-point noise.
# ---------------------------------------------------------------------------
def test_extract_error_profile_confidence_uses_max():
    """v6 reproduction: model=baseline=1337.884 MB → confidence reflects max."""
    record = _metadata_record(
        model_abs_bound=1337.884,
        baseline_abs_bound=1337.884,
        resource="memory",
        predicted_value=3.5,
    )
    profile = extract_error_profile(record)
    mem = profile["memory"]
    # abs_bound used for confidence = max(model, baseline) = 1337.884
    # confidence = 1 / (1 + 0 + 1337.884/1e6) ≈ 0.99999866...
    expected_abs_bound = 1337.884
    legacy_abs_bound = 1337.884 + 1337.884  # 2675.768
    expected_confidence = 1.0 / (1.0 + 0.0 + expected_abs_bound / 1e6)
    legacy_confidence = 1.0 / (1.0 + 0.0 + legacy_abs_bound / 1e6)
    assert abs(mem["confidence"] - expected_confidence) < 1e-9, (
        f"confidence should reflect max({mem['model_abs_bound']}, "
        f"{mem['baseline_abs_bound']})={expected_abs_bound}; "
        f"got confidence={mem['confidence']}, expected={expected_confidence}, "
        f"legacy_sum would give {legacy_confidence}"
    )
    assert mem["confidence"] > legacy_confidence + 1e-12, (
        f"max-based confidence {mem['confidence']} should be STRICTLY larger than "
        f"sum-based {legacy_confidence}"
    )
    # Sanity bounds contents: extract must populate both fields from metadata.
    assert abs(mem["model_abs_bound"] - 1337.884) < 1e-6, (
        f"model_abs_bound not extracted; got {mem['model_abs_bound']}"
    )
    assert abs(mem["baseline_abs_bound"] - 1337.884) < 1e-6, (
        f"baseline_abs_bound not extracted; got {mem['baseline_abs_bound']}"
    )
    print(
        f"[PASS] test_extract_error_profile_confidence_uses_max: "
        f"confidence={mem['confidence']:.10f} (max-based), "
        f"legacy_sum_confidence={legacy_confidence:.10f}"
    )


def main() -> int:
    print("=" * 70)
    print("RC#2 test: robust-ranking error bound uses max(model, baseline), not sum")
    print("=" * 70)
    test_baseline_only()
    test_post_retrain_larger()
    test_baseline_larger()
    test_tie_boundary()
    test_extract_error_profile_confidence_uses_max()
    print("=" * 70)
    print("ALL RC#2 TESTS PASSED")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())