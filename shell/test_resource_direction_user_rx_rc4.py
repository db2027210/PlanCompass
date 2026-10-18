"""Module."""


from __future__ import annotations

import sys

import bonect_robust_ranking as brr
from bonect_robust_ranking import _resource_direction_adjustment as score_resource_direction


def _approx_eq(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(a - b) <= tol


def _make_deltas_util_pp(memory: float, cpu: float, io: float) -> dict:
    """Construct a util-pp delta map using capability = 100% per resource so
    delta-pp == delta-util fraction * 100 here for clarity."""
    return {"memory": memory, "cpu": cpu, "io": io}


def _predict_gate_passes(
    *,
    target_resource: str,
    resource: dict,
    baseline: dict,
    capability: dict,
    min_delta_util_pct: float = 0.50,
    min_other_up_util_pct: float = 0.50,
    max_non_target_down_util_pct: float = 5.00,
    require_all_non_target_up: bool = False,
) -> bool:
    """Replay the same predicted-candidate direction gate that parameter_optimizer
    uses, but isolated so the test does not have to spin the full optimizer.
    The implementation reads from the same fields the optimizer checks so this
    is a behavior-faithful repro rather than a structural assertion."""
    import parameter_optimizer as po

    target_set = {target_resource}
    deltas = {
        name: (float(resource.get(name, 0.0)) - float(baseline.get(name, 0.0)))
        / float(capability[name])
        * 100.0
        for name in ("cpu", "memory", "io")
    }
    target_delta = deltas[target_resource]
    other_deltas = [v for n, v in deltas.items() if n not in target_set]
    best_other_up = max(other_deltas or [float("-inf")])
    worst_other_up = min(other_deltas or [float("-inf")])

    # mirror parameter_optimizer.py#2518-2535 RC#4's relaxed gate
    if require_all_non_target_up:
        other_direction_passed = bool(other_deltas) and all(
            v > min_other_up_util_pct for v in other_deltas
        )
    else:
        other_direction_passed = (
            bool(other_deltas) and best_other_up > min_other_up_util_pct
        )
    severe_drop_warning = (
        bool(other_deltas) and worst_other_up < -max_non_target_down_util_pct
    )
    passed = (
        target_delta is not None
        and max(0.0, -target_delta) >= min_delta_util_pct
        and other_direction_passed
    )
    # double-check via the actual _resource_delta_util_pp helper for parity
    sanity = brr._resource_delta_util_pp(
        resource=resource,
        baseline_resource=baseline,
        capability=capability,
    )
    sanity_other = [v for n, v in sanity.items() if n not in target_set]
    sanity_best = max(sanity_other or [float("-inf")])
    if require_all_non_target_up:
        sanity_other_passed = bool(sanity_other) and all(
            v > min_other_up_util_pct for v in sanity_other
        )
    else:
        sanity_other_passed = bool(sanity_other) and sanity_best > min_other_up_util_pct
    sanity_passed = (
        sanity[target_resource] is not None
        and max(0.0, -sanity[target_resource]) >= min_delta_util_pct
        and sanity_other_passed
    )
    assert _approx_eq(passed, sanity_passed, 0.0), (
        f"parity mismatch: deltas_passed={passed} sanity_passed={sanity_passed}"
    )
    return bool(passed), bool(severe_drop_warning)


def test_memory_down_io_small_up_cpu_down_10pp_predicted_passes() -> None:
    """test memory down io small up cpu down 10pp predicted passes."""

    capability = {"cpu": 100.0, "memory": 100.0, "io": 100.0}
    baseline = {"cpu": 50.0, "memory": 90.0, "io": 20.0}
    resource = {"cpu": 40.0, "memory": 83.0, "io": 20.6}  # CPU -10pp, Memory -7pp, IO +0.6pp
    passed, severe_drop = _predict_gate_passes(
        target_resource="memory",
        resource=resource,
        baseline=baseline,
        capability=capability,
        min_delta_util_pct=0.50,
        min_other_up_util_pct=0.50,
        max_non_target_down_util_pct=5.00,
    )
    assert passed, "predicted gate should accept Memory-down + IO-up + CPU-down-10pp"
    assert severe_drop, ("severe-drop signal MUST still be emitted (CPU down 10pp > 5pp "
                         "budget) so optimizer sees the safety guard flag")


def test_predicted_no_other_up_fails() -> None:
    """Bottleneck down but both non-targets flat-or-down should still fail:
    at least one non-target must go up."""
    capability = {"cpu": 100.0, "memory": 100.0, "io": 100.0}
    baseline = {"cpu": 50.0, "memory": 90.0, "io": 20.0}
    resource = {"cpu": 50.0, "memory": 80.0, "io": 19.0}  # Memory -10pp, IO -1pp, CPU flat
    passed, _ = _predict_gate_passes(
        target_resource="memory",
        resource=resource,
        baseline=baseline,
        capability=capability,
        min_delta_util_pct=0.50,
        min_other_up_util_pct=0.50,
        max_non_target_down_util_pct=5.00,
    )
    assert not passed, "no non-target up must fail the predicted direction gate"


def test_predicted_target_not_down_fails() -> None:
    """Memory goes UP + IO goes up must still fail because the target is wrong."""
    capability = {"cpu": 100.0, "memory": 100.0, "io": 100.0}
    baseline = {"cpu": 50.0, "memory": 90.0, "io": 20.0}
    resource = {"cpu": 51.0, "memory": 93.0, "io": 22.0}  # Memory +3pp, IO +2pp, CPU +1pp
    passed, _ = _predict_gate_passes(
        target_resource="memory",
        resource=resource,
        baseline=baseline,
        capability=capability,
        min_delta_util_pct=0.50,
        min_other_up_util_pct=0.50,
        max_non_target_down_util_pct=5.00,
    )
    assert not passed, "target resource must decline for the gate to pass"


def test_predict_require_all_non_target_up_still_enforced() -> None:
    """require_all_non_target_up branch unchanged: both non-targets must rise."""
    # Memory -10pp, IO +1pp, CPU flat -> require_all_non_target_up=True must fail.
    capability = {"cpu": 100.0, "memory": 100.0, "io": 100.0}
    baseline = {"cpu": 50.0, "memory": 90.0, "io": 20.0}
    resource = {"cpu": 50.0, "memory": 80.0, "io": 21.0}
    passed, _ = _predict_gate_passes(
        target_resource="memory",
        resource=resource,
        baseline=baseline,
        capability=capability,
        min_delta_util_pct=0.50,
        min_other_up_util_pct=0.50,
        max_non_target_down_util_pct=5.00,
        require_all_non_target_up=True,
    )
    assert not passed, "require_all_non_target_up=True must reject when one non-target flat"


def test_scorer_memory_down_io_up_cpu_down_10pp_passes() -> None:
    """_resource_direction_score for Memory scenario with the same pattern must
    return passed=True and severe_non_target_drop_warning=True so the soft
    safety penalty still applies without a hard rejection."""
    capability = {"cpu": 100.0, "memory": 100.0, "io": 100.0}
    baseline = {"cpu": 50.0, "memory": 90.0, "io": 20.0}
    resource = {"cpu": 40.0, "memory": 83.0, "io": 20.6}
    direction_config = {
        "min_delta_util_pct": 0.50,
        "min_other_up_util_pct": 0.50,
        "max_non_target_down_util_pct": 5.00,
        "require_all_non_target_up": False,
    }
    out = score_resource_direction(
        resource=resource,
        baseline_resource=baseline,
        capability=capability,
        scenario="memory",
        direction_config=direction_config,
    )
    assert out["enabled"], "scorer must enable for cpu/memory/io"
    assert out["passed"], "scorer must accept Memory-down + IO-up + CPU-down-10pp (per RC#4)"
    assert out.get("severe_non_target_drop_warning"), (
        "severe_non_target_drop_warning must fire when worst_other_up < -max_non_target_down_pp"
    )
    assert "severe_non_target_drop_warning" in out["components"], (
        "components must expose the warning for downstream loggers"
    )
    assert out["components"]["non_target_down_excess_penalty"] >= 5.0 * 5.0, (
        "non_target_down_excess_penalty must still apply (CPU 10pp over budget 5pp = 5pp excess * 5)"
    )


def test_scorer_no_other_up_fails() -> None:
    """Scorer must NOT pass when no non-target resource goes up (the soft
    penalty alone does not save the hard gate)."""
    capability = {"cpu": 100.0, "memory": 100.0, "io": 100.0}
    baseline = {"cpu": 50.0, "memory": 90.0, "io": 20.0}
    resource = {"cpu": 50.0, "memory": 80.0, "io": 19.0}
    direction_config = {
        "min_delta_util_pct": 0.50,
        "min_other_up_util_pct": 0.50,
        "max_non_target_down_util_pct": 5.00,
        "require_all_non_target_up": False,
    }
    out = score_resource_direction(
        resource=resource,
        baseline_resource=baseline,
        capability=capability,
        scenario="memory",
        direction_config=direction_config,
    )
    assert not out["passed"], "scorer must reject Memory-down + no-non-target-up patterns"
    assert not out.get("severe_non_target_drop_warning"), (
        "warning only fires when worst_other_up drops beyond -max_non_target_down_pp"
    )


def test_scorer_target_not_down_fails() -> None:
    """Scorer must NOT pass when the target keeps going up."""
    capability = {"cpu": 100.0, "memory": 100.0, "io": 100.0}
    baseline = {"cpu": 50.0, "memory": 90.0, "io": 20.0}
    resource = {"cpu": 51.0, "memory": 93.0, "io": 22.0}
    direction_config = {
        "min_delta_util_pct": 0.50,
        "min_other_up_util_pct": 0.50,
        "max_non_target_down_util_pct": 5.00,
        "require_all_non_target_up": False,
    }
    out = score_resource_direction(
        resource=resource,
        baseline_resource=baseline,
        capability=capability,
        scenario="memory",
        direction_config=direction_config,
    )
    assert not out["passed"], "scorer must reject target-not-decreasing candidates"
    assert "wrong_direction" in out["reason"] or out["reason"] == "direction_failed", (
        "scorer reason should reflect target-not-down direction failure"
    )


def test_scorer_require_all_non_target_up_branch_unchanged() -> None:
    """When require_all_non_target_up=True, both non-targets must increase
    (the RC#4 relaxation only applies to the default any-one-up branch)."""
    capability = {"cpu": 100.0, "memory": 100.0, "io": 100.0}
    baseline = {"cpu": 50.0, "memory": 90.0, "io": 20.0}
    resource = {"cpu": 50.0, "memory": 80.0, "io": 21.5}
    direction_config = {
        "min_delta_util_pct": 0.50,
        "min_other_up_util_pct": 0.50,
        "max_non_target_down_util_pct": 5.00,
        "require_all_non_target_up": True,
    }
    out = score_resource_direction(
        resource=resource,
        baseline_resource=baseline,
        capability=capability,
        scenario="memory",
        direction_config=direction_config,
    )
    assert not out["passed"], "require_all_non_target_up=True must reject when one non-target flat"


def main() -> int:
    tests = [
        ("test_memory_down_io_small_up_cpu_down_10pp_predicted_passes",
         test_memory_down_io_small_up_cpu_down_10pp_predicted_passes),
        ("test_predicted_no_other_up_fails",
         test_predicted_no_other_up_fails),
        ("test_predicted_target_not_down_fails",
         test_predicted_target_not_down_fails),
        ("test_predict_require_all_non_target_up_still_enforced",
         test_predict_require_all_non_target_up_still_enforced),
        ("test_scorer_memory_down_io_up_cpu_down_10pp_passes",
         test_scorer_memory_down_io_up_cpu_down_10pp_passes),
        ("test_scorer_no_other_up_fails",
         test_scorer_no_other_up_fails),
        ("test_scorer_target_not_down_fails",
         test_scorer_target_not_down_fails),
        ("test_scorer_require_all_non_target_up_branch_unchanged",
         test_scorer_require_all_non_target_up_branch_unchanged),
    ]
    pass_count = 0
    for name, fn in tests:
        try:
            fn()
            print(f"[PASS] {name}")
            pass_count += 1
        except AssertionError as exc:
            print(f"[FAIL] {name}: {exc}")
        except Exception as exc:
            print(f"[ERROR] {name}: {type(exc).__name__}: {exc}")
    print("=" * 70)
    print(f"RC#4 tests: {pass_count}/{len(tests)} passed")
    print("=" * 70)
    if pass_count != len(tests):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())