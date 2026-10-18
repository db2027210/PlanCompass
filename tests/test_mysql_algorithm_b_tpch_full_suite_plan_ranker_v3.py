from tests.test_mysql_algorithm_b_tpch_full_suite_plan_ranker_v2 import *  # noqa: F403

from scripts import mysql_algorithm_b_tpch_full_suite_plan_ranker_v3 as subject


def _row(rank: int, score: float, risk: float, negatives: int = 0):
    return {
        "experience_rank_before_full_plan": rank,
        "full_suite_plan_resource_score": score,
        "soft_negative_evidence_count": negatives,
        "optimized_params": {"io_block_read_cost": float(rank)},
        "resource_model": {
            "raw_plan_work_ratios": {
                "cpu": 1.0,
                "memory": risk,
                "io": 1.0,
            },
        },
    }


def test_large_downward_plan_change_is_also_softly_demoted():
    extreme = _row(1, 1.0, 1.0 / 16.0)  # noqa: F405
    safe = [_row(rank, 0.5, 1.0) for rank in range(2, 7)]  # noqa: F405
    selected = subject.select_plan_reranked_top_k([extreme, *safe])
    assert extreme not in selected
    assert extreme["raw_full_suite_plan_risk_ratio"] == 16.0
    assert extreme["plan_risk_adjusted_resource_score"] == 0.25


def test_query_local_extreme_is_not_hidden_by_aggregate_ratio():
    extreme = _row(1, 1.0, 1.0)  # noqa: F405
    extreme["resource_model"].update({
        "baseline_query_work": {"tpch_009": {"cpu_work": 1000.0}},
        "candidate_query_work": {"tpch_009": {"cpu_work": 1.0}},
    })
    safe = [_row(rank, 0.5, 1.0) for rank in range(2, 7)]  # noqa: F405
    selected = subject.select_plan_reranked_top_k([extreme, *safe])
    assert extreme not in selected
    assert extreme["raw_full_suite_plan_risk_ratio"] == 1000.0
    assert extreme["full_suite_plan_risk_components"]["tpch_009.cpu_work"] == 1000.0
