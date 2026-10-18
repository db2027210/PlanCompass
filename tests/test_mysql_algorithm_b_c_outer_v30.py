from __future__ import annotations

import copy

import pytest

from scripts import mysql_four_scene_algorithm_b_pg_grid_v30 as inner
from scripts import run_mysql_four_scene_algorithm_b_c_outer_v30 as controller


def _table(active: list[str]) -> dict:
    return {
        "parameter_space": {"active_params": active},
        "table": {
            name: {
                "cpu": -0.04 if index % 2 == 0 else 0.02,
                "memory": -0.08 if index % 2 == 0 else 0.03,
                "io": -0.02 if index % 2 == 0 else 0.01,
            }
            for index, name in enumerate(active)
        },
    }


def test_contract_matches_pg_b_grid_and_mysql_only_parameter_universe() -> None:
    contract = inner.search_contract()
    assert contract["grid_points"] == 15
    assert contract["max_coordinate_passes"] == 20
    assert contract["no_improvement_patience"] == 2
    assert contract["interaction_min_size"] == 2
    assert contract["interaction_max_size"] == 4
    assert contract["interaction_values_per_parameter"] == 3
    assert contract["interaction_candidate_cap"] == 0
    assert contract["real_top_k_limit"] == 5
    assert contract["top_k_is_upper_bound"] is True
    assert contract["partial_top_k_allowed"] is True
    assert set(contract["candidate_parameter_universe"]) == {
        "io_block_read_cost",
        "memory_block_read_cost",
        "row_evaluate_cost",
        "key_compare_cost",
        "disk_temptable_create_cost",
        "disk_temptable_row_cost",
        "memory_temptable_create_cost",
        "memory_temptable_row_cost",
    }


def test_pg_parameter_is_rejected_before_mysql_access(monkeypatch) -> None:
    monkeypatch.setattr(
        inner, "_load_table", lambda _scene: _table(["random_page_cost"]),
    )
    touched = []
    monkeypatch.setattr(inner.alg, "apply_costs", lambda *_args: touched.append(True))
    with pytest.raises(RuntimeError, match="MySQL-only"):
        inner.candidate_rankings(
            "job_memory",
            {"random_page_cost": 4.0},
            {"random_page_cost": {"lower": 1.0, "upper": 8.0, "transform": "log"}},
            {"cpu": 0.4, "memory": 0.7, "io": 0.3},
            freeze={},
            worker={"worker_id": "job_w1"},
        )
    assert touched == []


def test_grid_rows_fit_c_outer_interface_without_qps(monkeypatch) -> None:
    active = ["io_block_read_cost", "memory_block_read_cost"]
    monkeypatch.setattr(inner, "_load_table", lambda _scene: _table(active))
    current = {}

    def apply(_worker, params):
        current.clear()
        current.update(params)
        return dict(params)

    monkeypatch.setattr(inner.alg, "apply_costs", apply)
    monkeypatch.setattr(
        inner.alg,
        "capture_scene_plans",
        lambda *_args: {"plan": str(round(float(current.get(active[0], 1.0)), 6))},
    )
    monkeypatch.setattr(inner.alg, "relation_stats", lambda _worker: {})
    monkeypatch.setattr(inner.alg, "executor_parameters", lambda _worker: {})

    def enhance(**kwargs):
        signature = str(sorted(current.items())).encode().hex()[:64].ljust(64, "0")
        return {
            "predicted_resources": copy.deepcopy(kwargs["experience_prediction"]),
            "confidence": 1.0,
            "candidate_plan_sha256": signature,
        }

    monkeypatch.setattr(inner.alg, "enhance_resource_prediction", enhance)
    rows = inner.candidate_rankings(
        "job_memory",
        {"io_block_read_cost": 1.0, "memory_block_read_cost": 0.25},
        {
            "io_block_read_cost": {"lower": 0.5, "upper": 2.0, "transform": "log"},
            "memory_block_read_cost": {"lower": 0.125, "upper": 0.5, "transform": "log"},
        },
        {"cpu": 0.4, "memory": 0.7, "io": 0.3},
        freeze={},
        worker={"worker_id": "job_w1"},
    )
    assert len(rows) >= 5
    assert [row["rank"] for row in rows] == list(range(1, len(rows) + 1))
    assert all(set(row["optimized_params"]) == set(active) for row in rows)
    assert all(row["qps_used_for_generation_or_ranking"] is False for row in rows)
    assert all("db_qps" not in row["predicted_resources"] for row in rows)
    assert all(len(row["resource_model"]["candidate_plan_sha256"]) == 64 for row in rows)


def test_controller_declares_c_outer_as_unchanged() -> None:
    contract = controller.strategy_contract()
    assert contract["only_algorithmic_difference_from_mysql_c"] == (
        "inner_candidate_generation"
    )
    assert contract["algorithm_c_gradient_subset_generator_enabled"] is False
    assert contract["algorithm_c_outer_plan_history_enabled"] is True
    assert "three_tier_acceptance_with_two_failure_downgrade" in contract[
        "unchanged_outer_strategies"
    ]
    assert (
        "accepted_credit_capped_at_ten_with_up_to_three_stability_confirmations"
        in contract["unchanged_outer_strategies"]
    )
    assert contract["formal_launch_release_frozen"] is False


def test_merge_keeps_distinct_plan_representatives_before_refinements() -> None:
    def row(value: float, score: float, plan: str) -> dict:
        return {
            "subset_id": f"p-{value}",
            "optimized_params": {"io_block_read_cost": value},
            "candidate_score": score,
            "resource_model": {"candidate_plan_sha256": plan * 64},
        }

    merged = inner.merge_candidate_rankings([[
        row(1.0, 10.0, "a"),
        row(1.1, 9.0, "a"),
        row(1.2, 8.0, "b"),
    ]])

    assert [item["optimized_params"]["io_block_read_cost"] for item in merged] == [
        1.0, 1.2, 1.1,
    ]
    assert [item["rank"] for item in merged] == [1, 2, 3]
