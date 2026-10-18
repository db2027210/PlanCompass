from __future__ import annotations

from types import SimpleNamespace

from scripts import mysql_algorithm_b_tpch_full_suite_plan_ranker_v2 as subject


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


def test_extreme_plan_is_softly_demoted_without_hard_pruning():
    extreme = _row(1, 1.0, 16.0)
    safe = [_row(rank, 0.5, 1.0) for rank in range(2, 7)]
    selected = subject.select_plan_reranked_top_k([extreme, *safe])
    assert extreme not in selected
    assert extreme["plan_risk_adjusted_resource_score"] == 0.25
    assert extreme["candidate_hard_pruned_by_plan_risk"] is False
    assert len(selected) == 5


def test_soft_plan_risk_can_still_win_with_sufficient_resource_score():
    extreme = _row(1, 100.0, 16.0)
    safe = [_row(rank, 0.5, 1.0) for rank in range(2, 7)]
    selected = subject.select_plan_reranked_top_k([extreme, *safe])
    assert extreme in selected
    assert extreme["candidate_hard_pruned_by_plan_risk"] is False


def test_soft_negative_evidence_remains_primary_and_qps_is_absent():
    negative = _row(1, 100.0, 1.0, negatives=1)
    clean = [_row(rank, 0.5, 1.0) for rank in range(2, 7)]
    selected = subject.select_plan_reranked_top_k([negative, *clean])
    assert negative not in selected
    assert all("qps" not in key for row in selected for key in row)
    assert subject.PLAN_RERANK_SHORTLIST_SIZE == 64


def test_release_boundary_preserves_outer6_and_activates_outer7(monkeypatch):
    seen = []

    def fake_rank(**_kwargs):
        seen.append((
            subject.v1.PLAN_RERANK_SHORTLIST_SIZE,
            subject.v1.select_plan_reranked_top_k,
        ))
        return {"scene": subject.v1.TPCH_SCENE}

    monkeypatch.setattr(subject, "_PREDECESSOR_RANK_SCENE", fake_rank)
    outer6 = subject.rank_scene(scene=subject.v1.TPCH_SCENE, round_index=6)
    outer7 = subject.rank_scene(scene=subject.v1.TPCH_SCENE, round_index=7)
    assert seen[0] == (
        subject._PREDECESSOR_SHORTLIST_SIZE,
        subject._PREDECESSOR_SELECT,
    )
    assert seen[1] == (
        subject.PLAN_RERANK_SHORTLIST_SIZE,
        subject.select_plan_reranked_top_k,
    )
    assert outer6["tpch_soft_plan_risk_release"]["applied"] is False
    assert outer7["tpch_soft_plan_risk_release"]["applied"] is True


def test_batched_explain_uses_one_client_and_strict_sentinels(monkeypatch, tmp_path):
    calls = []

    def fake_pinned_runner(_worker, _log):
        def run(argv, **kwargs):
            calls.append((argv, kwargs["input"]))
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "{\n  \\\"query_block\\\": 1\n}\n"
                    "__RA_PLAN_END_001__\n"
                    "{\n  \\\"query_block\\\": 2\n}\n"
                    "__RA_PLAN_END_002__\n"
                ),
                stderr="",
            )
        return run

    monkeypatch.setattr(subject.v1.base.alg, "private_credential", lambda _worker: None)
    monkeypatch.setattr(subject.v1.base.alg.r0mod, "PinnedRunner", fake_pinned_runner)
    plans = subject.batched_explain({
        "worker_id": "tpch_w1",
        "benchmark_defaults": str(tmp_path / "benchmark.cnf"),
        "formal_client_log": str(tmp_path / "clients.jsonl"),
        "host_port": 65321,
        "database": "tpch",
    }, ["EXPLAIN FORMAT=JSON SELECT 1", "EXPLAIN FORMAT=JSON SELECT 2"])
    assert len(calls) == 1
    assert calls[0][1].count("__RA_PLAN_END_") == 2
    assert plans == [
        "{\n  \\\"query_block\\\": 1\n}",
        "{\n  \\\"query_block\\\": 2\n}",
    ]
