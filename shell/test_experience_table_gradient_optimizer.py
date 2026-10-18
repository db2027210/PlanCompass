#!/usr/bin/env python3
"""Module."""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gradient_descent_optimizer import GradientDescentParameterOptimizer, ParameterRange
from resource_utility import ResourceUtilityCalculator


class FixedDirectionOptimizer(GradientDescentParameterOptimizer):
    def _calculate_direction(
        self,
        param_name: str,
        sensitivity_table: Dict[str, Dict[str, float]],
        resource_now: Dict[str, float],
        capability: Dict[str, float],
        **_: Any,
    ) -> Tuple[float, Optional[str]]:
        _ = resource_now, capability
        return float(sensitivity_table[param_name]["direction"]), None


class Estimator:
    def __init__(self) -> None:
        self.calls = []

    def __call__(self, _sql: object, params: Dict[str, float]):
        self.calls.append(dict(params))
        return {"cpu": float(params["p"])}, {}, {
            "optimistic_replay_pruning_input": {"predicted_db_qps": 123456.0}
        }


def test_sgd_updates_only_subset_and_starts_at_incumbent() -> None:
    estimator = Estimator()
    optimizer = FixedDirectionOptimizer(
        estimator,
        {"cpu": 100.0},
        score_mode="performance_io",
        verbose=False,
    )
    result = optimizer.optimize(
        p_real={"p": 10.0, "fixed": 7.0},
        p_start={"p": 99.0, "fixed": -1.0},
        sensitivity_table={"p": {"direction": 1.0}},
        param_ranges={"p": ParameterRange(0.0, 100.0)},
        active_params=["p"],
        sql_set=None,
        eta=0.1,
        max_iter=2,
        patience=2,
        gamma=0.5,
    )
    assert result.optimized_params == {"p": 30.0, "fixed": 7.0}
    assert estimator.calls[0] == {"p": 10.0, "fixed": 7.0}
    assert all(call["fixed"] == 7.0 for call in estimator.calls)
    assert result.iterations == 2
    assert all(row["algorithm"] == "experience_table_analytic_sgd_v1" for row in result.history)
    assert all("finite" not in row["gradient_source"] for row in result.history)


def test_rejections_do_not_move_incumbent_and_decay_eta() -> None:
    estimator = Estimator()
    optimizer = FixedDirectionOptimizer(
        estimator,
        {"cpu": 100.0},
        score_mode="performance_io",
        verbose=False,
    )
    result = optimizer.optimize(
        p_real={"p": 50.0},
        sensitivity_table={"p": {"direction": -1.0}},
        param_ranges={"p": ParameterRange(0.0, 100.0)},
        sql_set=None,
        eta=0.2,
        max_iter=10,
        patience=2,
        gamma=0.5,
    )
    assert result.optimized_params == {"p": 50.0}
    assert result.iterations == 2
    assert [row["learning_rate"] for row in result.history] == [0.2, 0.1]
    assert not any(row["accepted_inner"] for row in result.history)


def test_real_direction_uses_authoritative_analytic_derivative() -> None:
    optimizer = GradientDescentParameterOptimizer(
        lambda _sql, _params: {"cpu": 50.0},
        {"cpu": 100.0},
        score_mode="performance_io",
        verbose=False,
    )
    direction, _ = optimizer._calculate_direction(
        "p", {"p": {"cpu": 2.0}}, {"cpu": 50.0}, {"cpu": 100.0}
    )
    assert direction == 2.0
    calculator = ResourceUtilityCalculator(score_mode="band_target")
    assert calculator.resource_score_derivative(0.5, "cpu") > 0.0
    assert calculator.resource_score_derivative(0.7, "cpu") == 0.0
    assert calculator.resource_score_derivative(0.85, "cpu") < 0.0


def test_legacy_qps_bound_value_is_not_retained() -> None:
    estimator = Estimator()
    optimizer = FixedDirectionOptimizer(
        estimator, {"cpu": 100.0}, score_mode="performance_io", verbose=False
    )
    result = optimizer.optimize(
        p_real={"p": 10.0},
        sensitivity_table={"p": {"direction": 1.0}},
        param_ranges={"p": ParameterRange(0.0, 100.0)},
        sql_set=None,
        max_iter=1,
    )
    assert result.final_estimation_metadata == {
        "optimistic_replay_pruning_input": {
            "schema_violation": "legacy_qps_bound_key_present",
            "violating_keys": ["predicted_db_qps"],
        }
    }
    assert "CHANGE_ME" not in repr(result.final_estimation_metadata)


def test_missing_experience_table_entry_fails_closed() -> None:
    optimizer = GradientDescentParameterOptimizer(
        lambda _sql, params: {"cpu": params["p"]},
        {"cpu": 100.0},
        verbose=False,
    )
    try:
        optimizer.optimize(
            p_real={"p": 10.0},
            sensitivity_table={},
            param_ranges={"p": ParameterRange(0.0, 100.0)},
            sql_set=None,
        )
    except ValueError as exc:
        assert "sensitivity prior is missing" in str(exc)
    else:
        raise AssertionError("missing experience-table row must fail closed")


if __name__ == "__main__":
    test_sgd_updates_only_subset_and_starts_at_incumbent()
    test_rejections_do_not_move_incumbent_and_decay_eta()
    test_real_direction_uses_authoritative_analytic_derivative()
    test_legacy_qps_bound_value_is_not_retained()
    test_missing_experience_table_entry_fails_closed()
    print("experience-table analytic SGD tests passed")
