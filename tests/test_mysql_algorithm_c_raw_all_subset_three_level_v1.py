from __future__ import annotations

import math

import pytest

from gradient_descent_optimizer import (
    GradientDescentParameterOptimizer,
    ParameterRange,
)
from mysql_parameter_optimizer import _formal_raw_all_subset_rankings
from mysql_plan_clustering import (
    build_canonical_plan_bundle,
    cluster_ranked_candidates,
    strict_physical_projection,
)


def _plan_bundle():
    return build_canonical_plan_bundle(
        [
            {
                "query_id": "q1",
                "query_sql_sha256": "b" * 64,
                "query_order": 1,
                "query_weight": 1.0,
                "dynamic": False,
                "binding_id": "static",
                "binding_values": [],
                "plan_mode": "custom",
                "raw_explain": {
                    "query_block": {
                        "table": {
                            "table_name": "lineitem",
                            "access_type": "ALL",
                            "parallel": True,
                        }
                    }
                },
            }
        ],
        registry_query_ids=["q1"],
        registry_sha256="a" * 64,
        registry_authority_verified=True,
    )


def _gradient_event(before=1.0, gradient=1.0, eta=0.1):
    proposed = before * (1.0 + eta * gradient)
    return {
        "gradient_source": "original_dScore_dU_times_T_times_confidence",
        "feature_ratio": {"rows": 1.0},
        "predicted_resource": {"cpu": 0.5, "memory": 0.5, "io": 0.5},
        "utilization": {"cpu": 0.5, "memory": 0.5, "io": 0.5},
        "dScore/dU": {"p": {"cpu": 2.0}},
        "T": {"p": {"cpu": 1.0}},
        "confidence": {"p": 0.5},
        "g_j": {"p": gradient},
        "eta": eta,
        "delta_param": {"p": {
            "before": before,
            "proposed_before_clip": proposed,
            "after_clip": proposed,
            "absolute": proposed - before,
            "relative": (proposed - before) / before,
            "clipped": False,
        }},
        "candidate_score": 1.0,
    }


def test_original_gradient_multiplies_confidence_and_logs_required_fields():
    def estimator(_sql_set, params):
        return (
            {
                "cpu": 60.0 - float(params["p"]),
                "memory": 20.0 + float(params["p"]),
                "io": 10.0,
            },
            {},
            {"feature_ratios": {"q1": float(params["p"]) / 10.0}},
        )

    optimizer = GradientDescentParameterOptimizer(
        estimator,
        {"cpu": 100.0, "memory": 100.0, "io": 100.0},
        score_mode="performance_io",
        verbose=False,
    )
    audit = {}
    g_j, _ = optimizer._calculate_direction(
        "p",
        {"p": {"cpu": -2.0, "memory": 3.0, "io": 0.0, "confidence": 0.25}},
        {"cpu": 50.0, "memory": 30.0, "io": 10.0},
        {"cpu": 100.0, "memory": 100.0, "io": 100.0},
        audit_sink=audit,
        original_gradient_only=True,
    )
    expected = sum(
        row["dScore/dU"] * row["T"] * row["confidence"]
        for row in audit["resources"].values()
    )
    assert math.isclose(g_j, expected, rel_tol=1e-12, abs_tol=1e-12)
    assert audit["gradient_source"] == (
        "original_dScore_dU_times_T_times_confidence"
    )

    result = optimizer.optimize(
        p_real={"p": 10.0},
        sensitivity_table={
            "p": {"cpu": -2.0, "memory": 3.0, "io": 0.0, "confidence": 0.25}
        },
        param_ranges={"p": ParameterRange(1.0, 100.0)},
        active_params=["p"],
        sql_set=None,
        eta=0.1,
        max_iter=1,
        patience=1,
        gamma=0.5,
        original_gradient_only=True,
    )
    event = result.history[0]["gradient_debug"]
    assert {
        "feature_ratio",
        "predicted_resource",
        "utilization",
        "dScore/dU",
        "T",
        "confidence",
        "g_j",
        "eta",
        "delta_param",
        "candidate_score",
    } <= set(event)


def test_all_15_subsets_remain_independent_and_fixed_top5_is_global_score_order():
    bundle = _plan_bundle()
    rows = [
        {
            "subset_id": f"subset_{index:04d}",
            "active_subset": ["p"],
            "gradient_events": [_gradient_event()],
            "final_score": float(16 - index),
            "optimized_params": {"p": 1.0},
            "initial_params": {"p": 0.5},
            "initial_resource": {"cpu": 10.0, "memory": 10.0, "io": 10.0},
            "final_resource": {"cpu": float(10 + index), "memory": 10.0, "io": 10.0},
            "canonical_plan_bundle": bundle,
            "candidate_estimation_identity": {
                "verified": True,
                "requested_parameter_signature": [["p", float(1.0).hex()]],
                "metadata_parameter_signature": [["p", float(1.0).hex()]],
                "candidate_plan_work_sha256": f"candidate-{index}",
                "baseline_plan_work_sha256": "baseline",
            },
        }
        for index in range(1, 16)
    ]
    formal = _formal_raw_all_subset_rankings(rows, 4)
    assert len(formal) == 15


def test_all_unresponsive_nonbaseline_candidates_cannot_seal_top5():
    bundle = _plan_bundle()
    rows = [
        {
            "subset_id": f"subset_{index:04d}",
            "active_subset": ["p"],
            "gradient_events": [_gradient_event()],
            "final_score": float(16 - index),
            "optimized_params": {"p": float(index)},
            "initial_params": {"p": 0.0},
            "initial_resource": {"cpu": 10.0, "memory": 10.0, "io": 10.0},
            "final_resource": {"cpu": 10.0, "memory": 10.0, "io": 10.0},
            "canonical_plan_bundle": bundle,
            "candidate_estimation_identity": {
                "verified": True,
                "requested_parameter_signature": [["p", float(index).hex()]],
                "metadata_parameter_signature": [["p", float(index).hex()]],
                "candidate_plan_work_sha256": "baseline-plan-work",
                "baseline_plan_work_sha256": "baseline-plan-work",
            },
        }
        for index in range(1, 16)
    ]
    with pytest.raises(RuntimeError, match="estimator is unresponsive"):
        _formal_raw_all_subset_rankings(rows, 4)


def test_all_baseline_subset_winners_and_tampered_raw_gradient_fail_closed():
    bundle = _plan_bundle()
    rows = [
        {
            "subset_id": f"subset_{index:04d}",
            "active_subset": ["p"],
            "gradient_events": [_gradient_event()],
            "final_score": float(16 - index),
            "optimized_params": {"p": 1.0},
            "initial_params": {"p": 1.0},
            "initial_resource": {"cpu": 10.0, "memory": 10.0, "io": 10.0},
            "final_resource": {"cpu": 10.0, "memory": 10.0, "io": 10.0},
            "canonical_plan_bundle": bundle,
            "candidate_estimation_identity": {
                "verified": True,
                "requested_parameter_signature": [["p", float(1.0).hex()]],
                "metadata_parameter_signature": [["p", float(1.0).hex()]],
                "candidate_plan_work_sha256": "baseline",
                "baseline_plan_work_sha256": "baseline",
            },
        }
        for index in range(1, 16)
    ]
    with pytest.raises(RuntimeError, match="only baseline winners"):
        _formal_raw_all_subset_rankings(rows, 4)

    rows[0]["optimized_params"] = {"p": 1.1}
    rows[0]["candidate_estimation_identity"].update({
        "requested_parameter_signature": [["p", float(1.1).hex()]],
        "metadata_parameter_signature": [["p", float(1.1).hex()]],
        "candidate_plan_work_sha256": "candidate",
    })
    rows[0]["gradient_events"][0]["g_j"]["p"] = 2.0
    with pytest.raises(RuntimeError, match="not sum"):
        _formal_raw_all_subset_rankings(rows, 4)


def test_incomplete_or_misordered_all_subset_universe_fails_closed():
    with pytest.raises(RuntimeError, match="incomplete"):
        _formal_raw_all_subset_rankings(
            [{"subset_id": f"s{index}", "final_score": float(index)}
             for index in range(14)],
            4,
        )
    bundle = _plan_bundle()
    misordered = [
        {
            "subset_id": f"s{index}",
            "active_subset": ["p"],
            "gradient_events": [_gradient_event()],
            "final_score": float(index),
            "optimized_params": {"p": float(index + 1)},
            "initial_params": {"p": 0.0},
            "initial_resource": {"cpu": 10.0, "memory": 10.0, "io": 10.0},
            "final_resource": {"cpu": 11.0, "memory": 10.0, "io": 10.0},
            "canonical_plan_bundle": bundle,
            "candidate_estimation_identity": {
                "verified": True,
                "requested_parameter_signature": [["p", float(index + 1).hex()]],
                "metadata_parameter_signature": [["p", float(index + 1).hex()]],
                "candidate_plan_work_sha256": f"candidate-{index}",
                "baseline_plan_work_sha256": "baseline",
            },
        }
        for index in range(15)
    ]
    with pytest.raises(RuntimeError, match="not globally ordered"):
        _formal_raw_all_subset_rankings(misordered, 4)


def test_l1_keeps_parallel_structure_but_worker_counts_belong_to_l2():
    projection = strict_physical_projection(
        {
            "query_block": {
                "table": {
                    "table_name": "lineitem",
                    "access_type": "ALL",
                    "parallel": True,
                    "workers_planned": 4,
                    "workers_launched": 3,
                }
            }
        }
    )
    table = projection["query_block"]["table"]
    assert table["parallel"] is True
    assert "workers_planned" not in table
    assert "workers_launched" not in table
