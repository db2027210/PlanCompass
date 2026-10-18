"""Aligned search/real estimators for orchestration integration tests."""

from typing import Any, Dict


def estimate(sql_set: Any, params: Dict[str, float]) -> Dict[str, float]:
    _ = sql_set
    io_cost = params.get("io_block_read_cost", 1.0)
    row_cost = params.get("row_evaluate_cost", 1.0)
    return {
        "cpu": 6.0 + 0.05 * row_cost,
        "memory": 6.5 + 0.02 * row_cost,
        "io": 8.0 - 0.1 * io_cost,
    }
