#!/usr/bin/env python3
"""Focused tests for the executor-physics direction estimator."""

from __future__ import annotations

import copy
import math
import sys
from pathlib import Path
from typing import Any, Dict


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plan_resource_estimator import PlanResourceEstimator  # noqa: E402


RELATION_STATS: Dict[str, Dict[str, float]] = {
    "outer_t": {
        "relpages": 100.0,
        "reltuples": 1_000.0,
        "relallvisible": 100.0,
    },
    "inner_t": {
        "relpages": 10_000.0,
        "reltuples": 1_000_000.0,
        "relallvisible": 7_500.0,
    },
    "inner_t_idx": {
        "relpages": 2_000.0,
        "reltuples": 1_000_000.0,
        "relallvisible": 0.0,
    },
}


def estimator() -> PlanResourceEstimator:
    return PlanResourceEstimator(
        pg_host="unused",
        pg_port="5432",
        pg_user="unused",
        pg_dbname="unused",
        psql_query_func=lambda _sql: "",
        estimator_cfg={
            "physical_model": {
                "enabled": True,
                "capture_first_point_as_baseline": True,
                "require_complete_explain": True,
                "seq_miss_rate": 0.15,
                "random_miss_rate": 0.70,
            },
            "shared": {
                "block_size": 8192,
                "work_mem_bytes": 32 * 1024 * 1024,
                "hash_mem_multiplier": 2.0,
            },
            "cpu": {"cpu_seconds_per_work": 1e-6},
            "io": {"wal_blocks_per_write_statement": 3.0},
            "memory": {
                "beta_sort": 1.0,
                "beta_hash": 1.0,
                "beta_hashagg": 1.0,
                "beta_materialize": 1.0,
                "beta_memoize": 1.0,
            },
        },
    )


def nested_loop_plan() -> Dict[str, Any]:
    return {
        "Node Type": "Nested Loop",
        "Plan Rows": 1_000,
        "Plan Width": 64,
        "Startup Cost": 0.0,
        "Total Cost": 999_999.0,
        "Plans": [
            {
                "Node Type": "Seq Scan",
                "Relation Name": "outer_t",
                "Plan Rows": 1_000,
                "Plan Width": 32,
                "Startup Cost": 0.0,
                "Total Cost": 100.0,
            },
            {
                "Node Type": "Index Scan",
                "Relation Name": "inner_t",
                "Index Name": "inner_t_idx",
                "Plan Rows": 1,
                "Plan Width": 32,
                "Startup Cost": 0.0,
                "Total Cost": 50.0,
            },
        ],
    }


def hash_join_plan() -> Dict[str, Any]:
    return {
        "Node Type": "Hash Join",
        "Plan Rows": 1_000,
        "Plan Width": 64,
        "Startup Cost": 0.0,
        "Total Cost": 1.0,
        "Plans": [
            {
                "Node Type": "Seq Scan",
                "Relation Name": "outer_t",
                "Plan Rows": 1_000,
                "Plan Width": 32,
                "Startup Cost": 0.0,
                "Total Cost": 1.0,
            },
            {
                "Node Type": "Hash",
                "Plan Rows": 1_000_000,
                "Plan Width": 64,
                "Startup Cost": 0.0,
                "Total Cost": 1.0,
                "Plans": [
                    {
                        "Node Type": "Seq Scan",
                        "Relation Name": "inner_t",
                        "Plan Rows": 1_000_000,
                        "Plan Width": 64,
                        "Startup Cost": 0.0,
                        "Total Cost": 1.0,
                    }
                ],
            },
        ],
    }


def resources(
    model: PlanResourceEstimator,
    plan: Dict[str, Any],
    *,
    query: str = "SELECT 1",
) -> Dict[str, Any]:
    nodes = model._extract_plan_node_features(  # pylint: disable=protected-access
        plan,
        relation_stats=RELATION_STATS,
    )
    return model._estimate_query_resources(  # pylint: disable=protected-access
        query_text=query,
        node_features=nodes,
        workload_context={"cache_hit_rate": 0.0},
    )


def test_nested_loop_propagates_inner_execution_multiplier() -> None:
    model = estimator()
    nodes = model._extract_plan_node_features(  # pylint: disable=protected-access
        nested_loop_plan(),
        relation_stats=RELATION_STATS,
    )
    index_node = next(node for node in nodes if node["node_type"] == "Index Scan")
    assert index_node["execution_multiplier"] == 1_000.0
    result = resources(model, nested_loop_plan())
    index_cpu = next(
        item
        for item in result["cpu"]["operator_breakdown"]
        if item["node_type"] == "Index Scan"
    )
    assert index_cpu["cpu_work"] == 3_000.0


def test_scan_work_is_independent_of_planner_cost_values() -> None:
    model = estimator()
    first = nested_loop_plan()
    second = copy.deepcopy(first)
    first["Plans"][0]["Total Cost"] = 10.0
    second["Plans"][0]["Total Cost"] = 10_000_000.0
    first_result = resources(model, first)
    second_result = resources(model, second)
    assert (
        first_result["cpu"]["cpu_work_per_query"]
        == second_result["cpu"]["cpu_work_per_query"]
    )
    assert (
        first_result["io"]["read_blocks_per_query"]
        == second_result["io"]["read_blocks_per_query"]
    )
    assert (
        first_result["memory"]["mem_query_parallel_bytes"]
        == second_result["memory"]["mem_query_parallel_bytes"]
    )


def test_wal_base_is_counted_once_per_statement() -> None:
    model = estimator()
    result = resources(
        model,
        nested_loop_plan(),
        query="UPDATE outer_t SET x = x + 1",
    )
    assert result["io"]["wal_write_blocks_per_query"] == 3.0


def test_hash_memory_is_not_double_counted_at_join_node() -> None:
    model = estimator()
    baseline = resources(model, nested_loop_plan())
    candidate = resources(model, hash_join_plan())
    assert baseline["memory"]["mem_query_parallel_bytes"] == 0.0
    assert candidate["memory"]["mem_query_parallel_bytes"] > 0.0
    hash_join_entry = next(
        item
        for item in candidate["memory"]["operator_breakdown"]
        if item["node_type"] == "Hash Join"
    )
    assert hash_join_entry["memory_bytes"] == 0.0


def test_physical_delta_anchor_preserves_measured_baseline() -> None:
    model = estimator()
    context = {
        "runtime_resource_raw": {"cpu": 3.0},
        "current_physical_plan_fingerprint": "plan-a",
    }
    baseline, baseline_detail = model._apply_physical_delta_anchor(  # pylint: disable=protected-access
        resource_type="cpu",
        formula_value=2.0,
        workload_context=context,
    )
    candidate, candidate_detail = model._apply_physical_delta_anchor(  # pylint: disable=protected-access
        resource_type="cpu",
        formula_value=1.0,
        workload_context={
            **context,
            "current_physical_plan_fingerprint": "plan-b",
        },
    )
    assert baseline == 3.0
    assert math.isclose(candidate, 1.5)
    assert baseline_detail["applied"] is True
    assert candidate_detail["source"] == "first_measured_baseline_delta_anchor"


def test_same_physical_plan_ignores_cardinality_estimate_drift() -> None:
    model = estimator()
    context = {
        "runtime_resource_raw": {"cpu": 3.0},
        "current_physical_plan_fingerprint": "same-plan",
    }
    model._apply_physical_delta_anchor(  # pylint: disable=protected-access
        resource_type="cpu",
        formula_value=2.0,
        workload_context=context,
    )
    candidate, detail = model._apply_physical_delta_anchor(  # pylint: disable=protected-access
        resource_type="cpu",
        formula_value=200.0,
        workload_context=context,
    )
    assert candidate == 3.0
    assert detail["same_physical_plan"] is True


def test_same_query_plan_reuses_baseline_work_inside_changed_workload() -> None:
    model = estimator()
    query_estimate = {
        "query_identity": "q1",
        "physical_plan_fingerprint": "plan-q1",
    }
    baseline_cpu = {
        "cpu_work_per_query": 123.0,
        "cpu_seconds_per_query": 0.01,
        "physical_direction_supported": True,
    }
    selected = model._physical_baseline_query_resource(  # pylint: disable=protected-access
        query_estimate=query_estimate,
        resource_type="cpu",
        workload_context={
            "physical_baseline_query_work": {
                "q1": {
                    "physical_plan_fingerprint": "plan-q1",
                    "cpu": baseline_cpu,
                }
            }
        },
    )
    assert selected == baseline_cpu
    changed = model._physical_baseline_query_resource(  # pylint: disable=protected-access
        query_estimate={
            **query_estimate,
            "physical_plan_fingerprint": "plan-q1-new",
        },
        resource_type="cpu",
        workload_context={
            "physical_baseline_query_work": {
                "q1": {
                    "physical_plan_fingerprint": "plan-q1",
                    "cpu": baseline_cpu,
                }
            }
        },
    )
    assert changed == {}


def test_subthreshold_physical_delta_is_reported_as_unknown_flat() -> None:
    model = estimator()
    baseline_context = {
        "runtime_resource_raw": {"io": 100.0},
        "current_physical_plan_fingerprint": "plan-a",
    }
    model._apply_physical_delta_anchor(  # pylint: disable=protected-access
        resource_type="io",
        formula_value=1000.0,
        workload_context=baseline_context,
    )
    predicted, detail = model._apply_physical_delta_anchor(  # pylint: disable=protected-access
        resource_type="io",
        formula_value=1005.0,
        workload_context={
            **baseline_context,
            "current_physical_plan_fingerprint": "plan-b",
        },
    )
    assert predicted == 100.0
    assert detail["direction_signal_above_floor"] is False


def test_unknown_executor_node_disables_optimistic_pruning() -> None:
    model = estimator()
    plan = {
        "Node Type": "Future Magic Scan",
        "Plan Rows": 10,
        "Plan Width": 8,
        "Startup Cost": 0.0,
        "Total Cost": 1.0,
    }
    result = resources(model, plan)
    assert result["cpu"]["physical_direction_supported"] is False
    assert result["io"]["physical_direction_supported"] is False


def test_enhanced_plan_shape_features_include_loops_catalog_and_spill() -> None:
    model = estimator()
    nested_nodes = model._extract_plan_node_features(  # pylint: disable=protected-access
        nested_loop_plan(),
        relation_stats=RELATION_STATS,
    )
    nested_shape = model._aggregate_plan_shape_features(  # pylint: disable=protected-access
        [{"nodes": nested_nodes, "replay_count": 1}],
        {},
    )
    assert nested_shape["plan_max_execution_multiplier"] == 1_000.0
    assert nested_shape["plan_nested_loop_inner_rows_proxy"] > 0.0
    assert nested_shape["plan_relation_stats_scan_coverage"] == 1.0
    assert nested_shape["plan_parent_child_ratio_count"] > 0.0

    hash_nodes = model._extract_plan_node_features(  # pylint: disable=protected-access
        hash_join_plan(),
        relation_stats=RELATION_STATS,
    )
    hash_shape = model._aggregate_plan_shape_features(  # pylint: disable=protected-access
        [{"nodes": hash_nodes, "replay_count": 1}],
        {},
    )
    assert hash_shape["plan_hash_spill_bytes_proxy"] > 0.0
    assert hash_shape["plan_hash_join_spill_bytes_proxy"] > 0.0
    assert hash_shape["plan_spill_batches_proxy"] > 0.0


def main() -> None:
    tests = [
        test_nested_loop_propagates_inner_execution_multiplier,
        test_scan_work_is_independent_of_planner_cost_values,
        test_wal_base_is_counted_once_per_statement,
        test_hash_memory_is_not_double_counted_at_join_node,
        test_physical_delta_anchor_preserves_measured_baseline,
        test_same_physical_plan_ignores_cardinality_estimate_drift,
        test_same_query_plan_reuses_baseline_work_inside_changed_workload,
        test_subthreshold_physical_delta_is_reported_as_unknown_flat,
        test_unknown_executor_node_disables_optimistic_pruning,
        test_enhanced_plan_shape_features_include_loops_catalog_and_spill,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}", flush=True)


if __name__ == "__main__":
    main()
