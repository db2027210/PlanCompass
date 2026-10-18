from __future__ import annotations

from scripts import mysql_algorithm_b_tpch_full_suite_plan_ranker_v1 as subject


def test_full_suite_registry_covers_q1_through_q22_once():
    explains = subject.tpch_full_suite_explains()
    assert list(explains) == list(range(1, 23))
    assert all(sql.startswith("EXPLAIN FORMAT=JSON ") for sql in explains.values())
    assert all(not sql.rstrip().endswith(";") for sql in explains.values())
    assert "WITH revenue0 AS" in explains[15]


def test_full_suite_capture_uses_all_22_queries(monkeypatch):
    seen: list[str] = []

    def fake_explain(_worker, sqls):
        seen.extend(sqls)
        return [f'{{"query":{index}}}' for index in range(1, len(sqls) + 1)]

    monkeypatch.setattr(subject.base.alg, "explain", fake_explain)
    plans = subject.full_suite_capture_scene_plans(
        {}, subject.TPCH_SCENE, {"worker_id": "tpch_w1"},
    )
    assert len(seen) == 22
    assert list(plans) == [f"tpch_{query_id:03d}" for query_id in range(1, 23)]


def test_full_plan_score_changes_top5_and_single_reject_is_only_soft_weight():
    rows = [
        {
            "experience_rank_before_full_plan": rank,
            "full_suite_plan_resource_score": float(rank),
            "soft_negative_evidence_count": 0,
            "optimized_params": {"row_evaluate_cost": float(rank)},
        }
        for rank in range(1, 11)
    ]
    # The experience Top-5 are 1..5, but full-plan scoring must choose 10..6.
    selected = subject.select_plan_reranked_top_k(rows)
    assert [row["experience_rank_before_full_plan"] for row in selected] == [
        10, 9, 8, 7, 6
    ]
    # A single reject remains ranking-only evidence: it is retained, not pruned.
    rows[0]["soft_negative_evidence_count"] = 1
    selected = subject.select_plan_reranked_top_k(rows)
    assert rows[0] in rows
    assert rows[0].get("hard_pruned") is None
    assert len(selected) == 5
