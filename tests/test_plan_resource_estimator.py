from __future__ import annotations

import copy
import math

import pytest

from benchmark_estimator import build_resource_estimator
from gradient_descent_optimizer import ParameterRange, optimize_with_gradient_descent
from plan_resource_estimator import (
    CacheProfile,
    MySQLPlanPhysics,
    MySQLRelationStatsCollector,
    calibrate_resources,
    predict_resources,
)


RELATION_STATS = {
    "outer_t": {
        "table_rows": 6_000_000,
        "data_pages": 60_000,
        "avg_row_length": 64,
        "index_length": 32 * 1024 * 1024,
    },
    "lineitem": {
        "table_rows": 6_000_000,
        "data_pages": 800_000,
        "avg_row_length": 128,
        "index_length": 128 * 1024 * 1024,
    },
    "lineitem.idx_fk": {"index_pages": 8_000},
}


def _index_nested_loop_plan():
    return {
        "query_block": {
            "nested_loop": [
                {
                    "table": {
                        "table_name": "outer_t",
                        "access_type": "const",
                        "rows_examined_per_scan": 1,
                        "rows_produced_per_join": 6_000_000,
                    }
                },
                {
                    "table": {
                        "table_name": "lineitem",
                        "access_type": "ref",
                        "key": "idx_fk",
                        "rows_examined_per_scan": 1,
                        "rows_produced_per_join": 6_000_000,
                    }
                },
            ]
        }
    }


def _hash_join_plan():
    return {
        "query_block": {
            "nested_loop": [
                {
                    "table": {
                        "table_name": "outer_t",
                        "access_type": "ALL",
                        "rows_examined_per_scan": 6_000_000,
                        "rows_produced_per_join": 6_000_000,
                    }
                },
                {
                    "table": {
                        "table_name": "lineitem",
                        "access_type": "ALL",
                        "rows_examined_per_scan": 6_000_000,
                        "rows_produced_per_join": 6_000_000,
                        "using_join_buffer": "hash join",
                    }
                },
            ]
        }
    }


def test_planner_cost_numbers_do_not_change_physical_work() -> None:
    plan = _index_nested_loop_plan()
    changed = copy.deepcopy(plan)
    plan["query_block"]["nested_loop"][1]["table"]["cost_info"] = {
        "read_cost": "1",
        "eval_cost": "2",
        "prefix_cost": "3",
        "data_read_per_join": "768M",
    }
    changed["query_block"]["nested_loop"][1]["table"]["cost_info"] = {
        "read_cost": "999999",
        "eval_cost": "0.0001",
        "prefix_cost": "1234567",
        "data_read_per_join": "768M",
    }
    physics = MySQLPlanPhysics(RELATION_STATS)
    assert physics.work(plan) == physics.work(changed)


def test_plan_switch_recomputes_all_three_resource_dimensions() -> None:
    executor = {"join_buffer_size": 1024 * 1024 * 1024}
    physics = MySQLPlanPhysics(RELATION_STATS, executor_parameters=executor)
    nested = physics.work(_index_nested_loop_plan())
    hashed = physics.work(_hash_join_plan())

    assert nested.io.random_pages > 0
    assert hashed.io.sequential_pages > nested.io.sequential_pages
    assert hashed.io.random_pages < nested.io.random_pages
    assert nested.memory_bytes == 0
    assert hashed.memory_bytes > 100 * 1024 * 1024
    assert hashed.cpu != nested.cpu


def test_zero_operator_memory_baseline_uses_resident_plus_delta() -> None:
    baseline_real = {"cpu": 3.5, "memory": 2.3e9, "io": 45e6}
    executor = {"join_buffer_size": 1024 * 1024 * 1024}
    calibration = calibrate_resources(
        baseline_real,
        {"q": _index_nested_loop_plan()},
        {"q": 1.0},
        RELATION_STATS,
        executor_parameters=executor,
    )
    assert calibration.baseline_memory_work == 0
    assert calibration.memory_resident == baseline_real["memory"]

    baseline_prediction = predict_resources(
        baseline_real,
        {"q": _index_nested_loop_plan()},
        {"q": _index_nested_loop_plan()},
        {"q": 1.0},
        RELATION_STATS,
        1,
        executor_parameters=executor,
    )
    candidate_prediction = predict_resources(
        baseline_real,
        {"q": _index_nested_loop_plan()},
        {"q": _hash_join_plan()},
        {"q": 1.0},
        RELATION_STATS,
        1,
        executor_parameters=executor,
    )
    assert baseline_prediction == pytest.approx(baseline_real)
    assert candidate_prediction["memory"] > baseline_prediction["memory"]
    assert all(math.isfinite(value) for value in candidate_prediction.values())


def test_cache_profile_is_explicit_and_physically_ordered() -> None:
    with pytest.raises(ValueError, match="must exceed"):
        CacheProfile(0.2, 0.3)


class _StatsClient:
    def execute(self, sql: str) -> str:
        if "information_schema.TABLES" in sql:
            return "bench\tlineitem\t6000000\t13107200000\t1048576000\t2184\n"
        if "mysql.innodb_index_stats" in sql:
            return "bench\tlineitem\tidx_fk\t64000\n"
        raise AssertionError(sql)


def test_relation_stats_collector_caches_table_and_index_pages() -> None:
    collector = MySQLRelationStatsCollector(_StatsClient())
    stats = collector.collect()
    assert stats["lineitem"]["table_rows"] == 6_000_000
    assert stats["bench.lineitem.idx_fk"]["index_pages"] == 64_000
    assert stats["lineitem.idx_fk"]["index_pages"] == 64_000


def test_multiscale_finite_difference_is_opt_in_and_needs_no_experience_table() -> None:
    def estimator(_sql_set, params):
        return {
            "cpu": 1.3 - 0.8 * params["x"],
            "memory": 0.5,
            "io": 0.5,
        }

    result = optimize_with_gradient_descent(
        estimator,
        {"cpu": 1.0, "memory": 1.0, "io": 1.0},
        {"x": 0.25},
        {"x": 0.25},
        {},
        {"x": ParameterRange(0.01, 1.0)},
        None,
        eta=0.2,
        max_iter=2,
        patience=2,
        active_params=["x"],
        direction_mode="multiscale_finite_difference",
        finite_difference_scales=(0.05, 0.1),
        verbose=False,
    )
    assert result.optimized_params["x"] > 0.25
    assert result.final_score > result.initial_score
    assert result.history[0]["gradient_source"] == (
        "resource_utility_multiscale_finite_difference"
    )
    assert len(result.history[0]["finite_difference"]["x"]["samples"]) == 2


class _EstimatorClient:
    def execute(self, sql: str) -> str:
        if sql == "SELECT @@GLOBAL.innodb_buffer_pool_size":
            return f"{1024**3}\n"
        if sql.startswith("EXPLAIN FORMAT=JSON"):
            return (
                '{"query_block":{"table":{"table_name":"lineitem",'
                '"access_type":"ALL","rows_examined_per_scan":6000000,'
                '"rows_produced_per_join":6000000,'
                '"cost_info":{"read_cost":"999999","eval_cost":"999999"}}}}\n'
            )
        if "information_schema.TABLES" in sql:
            return "bench\tlineitem\t6000000\t13107200000\t1048576000\t2184\n"
        if "mysql.innodb_index_stats" in sql:
            return ""
        raise AssertionError(sql)


class _EstimatorAdapter:
    def __init__(self) -> None:
        self.client = _EstimatorClient()
        self.applied = []

    def apply(self, params) -> None:
        self.applied.append(dict(params))


def test_plan_physics_estimator_type_anchors_first_explain_to_baseline() -> None:
    adapter = _EstimatorAdapter()
    estimator = build_resource_estimator(
        adapter,
        {
            "type": "plan_physics",
            "baseline_real": {"cpu": 3.5, "memory": 2.3, "io": 45.0},
            "memory_bytes_per_unit": 1024**3,
            "feature_source": {
                "queries": [
                    {"query_id": "q1", "sql": "SELECT * FROM lineitem", "weight": 1}
                ]
            },
        },
    )
    result = estimator(None, {"io_block_read_cost": 100.0})
    assert {name: result[name] for name in ("cpu", "memory", "io")} == pytest.approx(
        {"cpu": 3.5, "memory": 2.3, "io": 45.0}
    )
    assert adapter.applied == [{"io_block_read_cost": 100.0}]
    assert result["_metadata"]["planner_cost_fields_used_as_physical_work"] is False


def test_ratio_scale_calibrates_configured_incumbent_before_first_candidate() -> None:
    adapter = _EstimatorAdapter()
    baseline = {"io_block_read_cost": 1.0}
    candidate = {"io_block_read_cost": 100.0}
    estimator = build_resource_estimator(
        adapter,
        {
            "type": "ratio_scale",
            "baseline_real": {"cpu": 3.5, "memory": 2.3, "io": 45.0},
            "baseline_params": baseline,
            "memory_bytes_per_unit": 1024**3,
            "feature_source": {
                "queries": [
                    {"query_id": "q1", "sql": "SELECT * FROM lineitem", "weight": 1}
                ]
            },
        },
    )

    result = estimator(None, candidate)

    assert adapter.applied == [baseline, candidate]
    assert {name: result[name] for name in ("cpu", "memory", "io")} == pytest.approx(
        {"cpu": 3.5, "memory": 2.3, "io": 45.0}
    )
    metadata = result["_metadata"]
    assert metadata["estimator"] == "mysql_ratio_scale_resource_estimator_v1"
    assert metadata["resource_estimation_mode"] == "ratio_scale"
    assert metadata["cost_parameter_free_features"] is True
    assert metadata["planner_cost_fields_used_as_physical_work"] is False
    assert metadata["baseline_params"] == baseline
    assert metadata["feature_ratios"] == pytest.approx(
        {"cpu": 1.0, "memory": 1.0, "io": 1.0}
    )
    assert metadata["ratio_clip"] == [0.05, 20.0]


class _PlanChangingClient:
    def __init__(self, adapter) -> None:
        self.adapter = adapter

    def execute(self, sql: str) -> str:
        if sql == "SELECT @@GLOBAL.innodb_buffer_pool_size":
            return f"{5 * 1024**3}\n"
        if sql.startswith("EXPLAIN FORMAT=JSON"):
            filesort = self.adapter.current.get("io_block_read_cost") == 1.0
            return (
                '{"query_block":{"table":{"table_name":"lineitem",'
                '"access_type":"ALL","rows_examined_per_scan":6000000,'
                '"rows_produced_per_join":6000000,"using_filesort":'
                + str(filesort).lower()
                + '}}}\n'
            )
        if "information_schema.TABLES" in sql:
            return "bench\tlineitem\t6000000\t13107200000\t1048576000\t2184\n"
        if "mysql.innodb_index_stats" in sql:
            return ""
        raise AssertionError(sql)


class _PlanChangingAdapter:
    def __init__(self) -> None:
        self.current = {}
        self.client = _PlanChangingClient(self)

    def apply(self, params) -> None:
        self.current = dict(params)


def test_ratio_scale_memory_uses_operator_demand_not_buffer_pool() -> None:
    adapter = _PlanChangingAdapter()
    estimator = build_resource_estimator(
        adapter,
        {
            "type": "ratio_scale",
            "baseline_real": {"cpu": 0.4, "memory": 0.9, "io": 0.5},
            "baseline_params": {"io_block_read_cost": 1.0},
            "feature_source": {
                "queries": [
                    {"query_id": "q1", "sql": "SELECT * FROM lineitem", "weight": 1}
                ]
            },
        },
    )

    result = estimator(None, {"io_block_read_cost": 100.0})

    direction_only_ratio = math.exp(-0.05)
    assert result["memory"] == pytest.approx(0.9 * direction_only_ratio)
    metadata = result["_metadata"]
    assert metadata["feature_ratios_clipped"]["memory"] == pytest.approx(0.05)
    assert metadata["feature_ratios"]["memory"] == pytest.approx(
        direction_only_ratio
    )
    assert metadata["calibrated_plan_work"] is False
    assert metadata["magnitude_trust"] == (
        "direction_only_uncalibrated_plan_transition"
    )
    assert metadata["memory_feature_semantics"] == (
        "peak_operator_demand_bytes_without_buffer_pool"
    )
