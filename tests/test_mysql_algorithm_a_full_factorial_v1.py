from __future__ import annotations

import math

import pytest

from scripts.mysql_algorithm_a_full_factorial_v1 import (
    attach_mysql_prediction_audit,
    full_factorial_parameter_vectors,
    interval_pair_points,
    physical_waves,
)


def test_interval_pair_points_match_pg_log_rule() -> None:
    left, right = interval_pair_points(1.0, 16.0)
    assert left == pytest.approx(2.0)
    assert right == pytest.approx(8.0)


def test_interval_pair_points_match_pg_linear_zero_rule() -> None:
    assert interval_pair_points(0.0, 8.0) == pytest.approx((2.0, 6.0))


def test_complete_product_is_canonical_and_keeps_inactive_mysql_params() -> None:
    rows = full_factorial_parameter_vectors(
        {
            "io_block_read_cost": {"lower": 1.0, "upper": 16.0},
            "row_evaluate_cost": {"lower": 0.01, "upper": 1.0},
        },
        ("io_block_read_cost", "row_evaluate_cost"),
        {
            "io_block_read_cost": 1.0,
            "row_evaluate_cost": 0.1,
            "key_compare_cost": 0.05,
        },
    )
    assert len(rows) == 4
    assert [row["rank"] for row in rows] == [1, 2, 3, 4]
    assert [row["algorithm_a_factor_levels"] for row in rows] == [
        {"io_block_read_cost": "L", "row_evaluate_cost": "L"},
        {"io_block_read_cost": "L", "row_evaluate_cost": "R"},
        {"io_block_read_cost": "R", "row_evaluate_cost": "L"},
        {"io_block_read_cost": "R", "row_evaluate_cost": "R"},
    ]
    assert all(row["optimized_params"]["key_compare_cost"] == 0.05 for row in rows)
    assert len({
        tuple(row["optimized_params"][name] for name in (
            "io_block_read_cost", "row_evaluate_cost"
        ))
        for row in rows
    }) == 4
    assert all(row["prediction_used_for_selection"] is False for row in rows)


def test_wave_schedule_physically_covers_every_global_rank_once() -> None:
    waves = physical_waves(7, parallelism=2)
    assert waves == [[1, 2], [3, 4], [5, 6], [7]]
    assert [rank for wave in waves for rank in wave] == list(range(1, 8))


def test_mysql_prediction_is_audit_only_and_keeps_canonical_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import run_mysql_four_scene_algorithm_c_v2 as mysql

    monkeypatch.setattr(mysql, "load", lambda _path: {
        "table": {
            "io_block_read_cost": {"cpu": 0.1, "memory": 0.2, "io": 0.3},
        }
    })
    monkeypatch.setattr(mysql, "apply_costs", lambda _worker, params: dict(params))
    monkeypatch.setattr(
        mysql, "capture_scene_plans", lambda _freeze, _scene, _worker: {"q": "{}"},
    )
    monkeypatch.setattr(mysql, "relation_stats", lambda _worker: {})
    monkeypatch.setattr(mysql, "executor_parameters", lambda _worker: {})
    monkeypatch.setattr(mysql, "enhance_resource_prediction", lambda **kwargs: {
        "predicted_resources": dict(kwargs["experience_prediction"]),
        "confidence": 1.0,
    })
    monkeypatch.setattr(mysql, "confidence_adjusted_score", lambda score, _confidence: score)
    candidates = full_factorial_parameter_vectors(
        {"io_block_read_cost": {"lower": 0.1, "upper": 10.0}},
        ["io_block_read_cost"],
        {**mysql.DEFAULTS},
    )
    result = attach_mysql_prediction_audit(
        "job_memory", candidates, mysql.DEFAULTS,
        {"cpu": 0.4, "memory": 0.5, "io": 0.6},
        freeze={}, worker={},
    )
    assert [row["rank"] for row in result] == [1, 2]
    assert [row["algorithm_a_factor_levels"] for row in result] == [
        {"io_block_read_cost": "L"},
        {"io_block_read_cost": "R"},
    ]
    assert all(row["prediction_role"] == "mysql_plan_physics_audit_only" for row in result)
    assert all(row["prediction_used_for_selection"] is False for row in result)


@pytest.mark.parametrize("low,high", [(1.0, 1.0), (-1.0, 2.0), (math.nan, 2.0)])
def test_invalid_interval_fails_closed(low: float, high: float) -> None:
    with pytest.raises(ValueError):
        interval_pair_points(low, high)
