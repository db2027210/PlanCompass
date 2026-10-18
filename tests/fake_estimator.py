"""Deterministic estimator used by tests."""

from typing import Any, Dict


def estimate(sql_set: Any, params: Dict[str, float]) -> Dict[str, float]:
    _ = sql_set
    return {
        "cpu": 7.0 + 0.1 * params.get("row_evaluate_cost", 1.0),
        "memory": 7.0,
        "io": 8.5 - 0.2 * params.get("io_block_read_cost", 1.0),
    }
