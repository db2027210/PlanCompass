#!/usr/bin/env python3
"""Fast offline checks for the Algorithm B PostgreSQL integration."""

from __future__ import annotations

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from double_loop_grid_optimizer import DoubleLoopGridOptimizer, GridInnerLoopConfig
from double_loop_search_common import OuterLoopConfig, ParameterRange
from shell.run_pg_algorithm_b_scene import (
    ALGORITHM_B_COMBINATION_MAX_CANDIDATES,
    ALGORITHM_B_GRID_POINTS,
    _grid_target_resource,
    _plan_diverse_order,
    _utility_calculator,
)


def test_plan_identity_capture_and_plan_first_order() -> None:
    def estimate(_sql_set: object, params: dict[str, float]) -> dict[str, object]:
        plan = "plan-a" if params["x"] < 0.75 else "plan-b"
        resource = {"cpu": params["x"], "memory": 0.5, "io": 0.5}
        return {
            "resources": resource,
            "score": params["x"],
            "estimated_score": params["x"],
            "final_estimation_metadata": {"workload_plan_fingerprint": plan},
        }

    optimizer = DoubleLoopGridOptimizer(
        estimate,
        estimate,
        {"cpu": 1.0, "memory": 1.0, "io": 1.0},
        grid_config=GridInnerLoopConfig(
            max_workers=1,
            max_passes=1,
            no_improvement_patience=1,
            combination_probe_enabled=False,
        ),
        outer_config=OuterLoopConfig(max_rounds=1, target_resource="cpu"),
        verbose=False,
    )
    points, _audit = optimizer._grid_small_loop(
        {},
        {"x": 0.5},
        ["x"],
        {"x": ParameterRange(0.0, 1.0, discrete_count=5)},
        round_index=1,
        iteration_writer=None,
    )
    ranked = optimizer._deduplicate_and_rank(points)
    ordered, plan_count = _plan_diverse_order(ranked)
    assert plan_count == 2
    assert [point.plan_fingerprint for point in ordered[:2]] == ["plan-b", "plan-a"]


def test_frozen_contracts() -> None:
    assert ALGORITHM_B_GRID_POINTS == 15
    assert ALGORITHM_B_COMBINATION_MAX_CANDIDATES == 0
    for target in ("cpu", "memory", "io"):
        calculator = _utility_calculator(target)
        audit = calculator.directional_band_target_contract_audit(
            target_resource=target
        )
        assert audit["passed"] is True
    none_calculator = _utility_calculator("none")
    assert _grid_target_resource("none") is None
    assert _grid_target_resource("cpu") == "cpu"
    for resource in ("cpu", "memory", "io"):
        resource_range = none_calculator._resource_target_range(resource)
        assert resource_range["role"] == (
            "non_bottleneck_io_cost" if resource == "io" else "non_bottleneck"
        )
        assert resource_range["hard"] == 0.8

    DoubleLoopGridOptimizer(
        lambda _sql_set, _params: {
            "resources": {"cpu": 0.5, "memory": 0.5, "io": 0.5},
            "score": 1.0,
        },
        lambda _sql_set, _params: {
            "resources": {"cpu": 0.5, "memory": 0.5, "io": 0.5},
            "score": 1.0,
        },
        {"cpu": 1.0, "memory": 1.0, "io": 1.0},
        outer_config=OuterLoopConfig(
            max_rounds=1,
            target_resource=_grid_target_resource("none"),
        ),
        verbose=False,
    )


def main() -> int:
    test_plan_identity_capture_and_plan_first_order()
    test_frozen_contracts()
    print("pg_algorithm_b_same_framework: PASS", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
