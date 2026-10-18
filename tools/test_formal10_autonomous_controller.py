#!/usr/bin/env python3
"""Module."""

from __future__ import annotations

import copy

from formal10_autonomous_controller import (
    _validate_prepared,
    evaluate_stability,
)


def _point(index: int, *, score_bias: float = 0.0) -> dict:
    predicted_score = 3.0 + index * 0.1
    return {
        "selection_role": "trusted",
        "valid_replay": True,
        "predicted_score": predicted_score,
        "actual_score": predicted_score + score_bias,
        "predicted_resource": {
            "cpu": 1.0 + index * 0.01,
            "io": 2.0 + index * 0.01,
            "memory": 3.0 + index * 0.01,
        },
        "actual_resource": {
            "cpu": 1.1 + index * 0.01,
            "io": 1.9 + index * 0.01,
            "memory": 3.1 + index * 0.01,
        },
        "g_j": {"cpu_tuple_cost": 0.2 + index * 0.01},
    }


def _audit(batch: int) -> dict:
    return {
        "batch_id": f"batch-{batch}",
        "technical_failure_count": 0,
        "baseline_restored": True,
        "formal_state_unchanged_when_required": True,
        "points": [_point(index + batch * 5) for index in range(5)],
    }


def _prepared() -> dict:
    candidates = []
    for index in range(5):
        candidates.append(
            {
                "selection_role": "trusted" if index < 3 else "uncertain_probe",
                "exact_plan_regime_validated": index < 3,
                "three_level_fingerprint": {
                    "id": f"three-level-sha256:{index:064x}",
                },
                "feature_ratio": {"cpu": 1.0, "io": 1.0, "memory": 1.0},
                "predicted_resource": {"cpu": 1.0, "io": 1.0, "memory": 1.0},
                "utilization": {"cpu": 0.4, "io": 0.5, "memory": 0.6},
                "dScore/dU": {"cpu_tuple_cost": {"cpu": 1.0}},
                "T": {"cpu_tuple_cost": {"cpu": 0.1}},
                "confidence": {"cpu_tuple_cost": 0.8},
                "g_j": {"cpu_tuple_cost": 0.08},
                "eta": 0.1,
                "delta_param": {"cpu_tuple_cost": {"absolute": 0.001}},
                "candidate_score": 4.0 - index * 0.01,
            }
        )
    return {
        "candidates": candidates,
        "formal_update_flags": {
            "accepted": False,
            "incumbent": False,
            "range": False,
            "gradient": False,
        },
        "qps_used_for_inner_scoring": False,
        "strict_serial_replay": True,
        "maximum_physical_calls": 5,
    }


def main() -> int:
    thresholds = {
        "consecutive_batches": 3,
        "minimum_trusted_points": 15,
        "minimum_aggregate_spearman": 0.3,
        "minimum_top3_hit_rate": 0.8,
        "resource_ratio_min": 0.5,
        "resource_ratio_max": 2.0,
        "maximum_gradient_reversal_rate": 0.4,
    }
    assert not evaluate_stability([_audit(1), _audit(2)], thresholds)["stable"]
    stable = evaluate_stability([_audit(1), _audit(2), _audit(3)], thresholds)
    assert stable["stable"], stable

    negative = [_audit(1), _audit(2), _audit(3)]
    negative[-1]["points"] = list(reversed(negative[-1]["points"]))
    for index, point in enumerate(negative[-1]["points"]):
        point["actual_score"] = float(index)
    assert not evaluate_stability(negative, thresholds)["stable"]

    prepared = _prepared()
    _validate_prepared(prepared, "diagnostic")
    polluted = copy.deepcopy(prepared)
    polluted["formal_update_flags"]["accepted"] = True
    try:
        _validate_prepared(polluted, "diagnostic")
    except RuntimeError:
        pass
    else:
        raise AssertionError("diagnostic accepted mutation was not rejected")

    duplicate = copy.deepcopy(prepared)
    duplicate["candidates"][1]["three_level_fingerprint"] = copy.deepcopy(
        duplicate["candidates"][0]["three_level_fingerprint"]
    )
    try:
        _validate_prepared(duplicate, "formal")
    except RuntimeError:
        pass
    else:
        raise AssertionError("duplicate fingerprint was not rejected")

    print("formal10 autonomous controller offline tests: PASS", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
