#!/usr/bin/env python3
"""Regression tests for the MySQL payload accepted by experience-table SGD."""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gradient_descent_optimizer import (  # noqa: E402
    GradientDescentParameterOptimizer,
    ParameterRange,
)


class MySQLDirectionOptimizer(GradientDescentParameterOptimizer):
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

    def _is_improved(
        self,
        resource_old: Dict[str, float],
        resource_new: Dict[str, float],
    ) -> bool:
        return resource_new["io"] < resource_old["io"]


class FlatMySQLEstimator:
    """Matches benchmark_estimator/trained_model_estimator's flat payload."""

    def __call__(self, sql_set: object, params: Dict[str, float]) -> Dict[str, Any]:
        _ = sql_set
        cost = float(params["io_block_read_cost"])
        return {
            "cpu": 1.0,
            "memory": 2.0,
            "io": cost,
            "qps": 120.0 - cost,
            "db_qps": 120.0 - cost,
            "query_count": 600.0,
            "query_time": 5.0,
            "latency_p95_ms": 8.0,
            "latency_p99_ms": 12.0,
            "explain_samples": ["EXPLAIN FORMAT=JSON ..."],
            "_metadata": {"engine": "mysql", "readback_exact": True},
        }


class NestedMySQLEstimator:
    def __call__(self, sql_set: object, params: Dict[str, float]) -> Dict[str, Any]:
        _ = sql_set, params
        return {
            "resource": {"cpu": 1.0, "memory": 2.0, "io": 3.0},
            "stats": {"qps": {"avg": 77.0}},
            "metadata": {"engine": "mysql", "shape": "nested"},
        }


def test_flat_mysql_payload_is_split_into_resource_stats_and_metadata() -> None:
    optimizer = MySQLDirectionOptimizer(
        FlatMySQLEstimator(),
        {"cpu": 8.0, "memory": 6.0, "io": 100.0},
        score_mode="performance_io",
        verbose=False,
    )
    result = optimizer.optimize(
        p_real={"io_block_read_cost": 1.0},
        sensitivity_table={"io_block_read_cost": {"direction": -1.0}},
        param_ranges={"io_block_read_cost": ParameterRange(0.1, 10.0)},
        sql_set={"benchmark": "tpch"},
        eta=0.1,
        max_iter=1,
        patience=1,
        gamma=0.5,
    )

    assert math.isclose(
        result.optimized_params["io_block_read_cost"],
        10.0 ** -0.2,
    )
    assert set(result.final_resource) == {"cpu", "memory", "io"}
    assert result.initial_qps == 119.0
    assert math.isclose(result.final_qps, 120.0 - 10.0 ** -0.2)
    assert result.final_estimation_metadata == {
        "engine": "mysql",
        "readback_exact": True,
    }


def test_nested_mysql_payload_is_supported() -> None:
    optimizer = MySQLDirectionOptimizer(
        NestedMySQLEstimator(),
        {"cpu": 8.0, "memory": 6.0, "io": 100.0},
        verbose=False,
    )
    result = optimizer.optimize(
        p_real={"row_evaluate_cost": 0.1},
        sensitivity_table={"row_evaluate_cost": {"direction": 0.0}},
        param_ranges={"row_evaluate_cost": ParameterRange(0.01, 1.0)},
        sql_set={"benchmark": "job"},
        max_iter=0,
    )

    assert result.initial_qps == 77.0
    assert result.initial_estimation_metadata == {
        "engine": "mysql",
        "shape": "nested",
    }


def test_db_qps_only_becomes_canonical_qps_and_mismatch_fails() -> None:
    class DBQPSOnly:
        def __call__(self, sql_set: object, params: Dict[str, float]) -> Dict[str, Any]:
            _ = sql_set, params
            return {"cpu": 1.0, "memory": 2.0, "io": 3.0, "db_qps": 55.0}

    optimizer = MySQLDirectionOptimizer(
        DBQPSOnly(),
        {"cpu": 8.0, "memory": 6.0, "io": 100.0},
        verbose=False,
    )
    result = optimizer.optimize(
        p_real={"row_evaluate_cost": 0.1},
        sensitivity_table={"row_evaluate_cost": {"direction": 0.0}},
        param_ranges={"row_evaluate_cost": ParameterRange(0.01, 1.0)},
        sql_set=None,
        max_iter=0,
    )
    assert result.initial_qps == 55.0

    class Mismatch:
        def __call__(self, sql_set: object, params: Dict[str, float]) -> Dict[str, Any]:
            _ = sql_set, params
            return {
                "cpu": 1.0,
                "memory": 2.0,
                "io": 3.0,
                "qps": 54.0,
                "db_qps": 55.0,
            }

    mismatch = MySQLDirectionOptimizer(
        Mismatch(),
        {"cpu": 8.0, "memory": 6.0, "io": 100.0},
        verbose=False,
    )
    try:
        mismatch.optimize(
            p_real={"row_evaluate_cost": 0.1},
            sensitivity_table={"row_evaluate_cost": {"direction": 0.0}},
            param_ranges={"row_evaluate_cost": ParameterRange(0.01, 1.0)},
            sql_set=None,
            max_iter=0,
        )
    except ValueError as exc:
        assert "qps must equal db_qps" in str(exc)
    else:
        raise AssertionError("qps/db_qps mismatch must fail closed")


def main() -> None:
    test_flat_mysql_payload_is_split_into_resource_stats_and_metadata()
    test_nested_mysql_payload_is_supported()
    test_db_qps_only_becomes_canonical_qps_and_mismatch_fails()
    print("experience-table MySQL interface tests passed", flush=True)


if __name__ == "__main__":
    main()
