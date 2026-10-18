from __future__ import annotations

from scripts import (
    run_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v15
    as subject,
)


def test_v15_source_is_exact_post_reset_settle_failure():
    state = subject.validate_source()
    assert state["status"] == "FAILED_CLOSED"
    assert state["outer_attempt"] == 4
    assert state["error"] == subject.SETTLE_ERROR
    assert not (subject.SOURCE / "outer_005/top5_batch_3").exists()


def test_v15_reuses_through_outer5_rank2_only():
    assert sorted(key for key in subject.REUSED_BATCHES if key[0] == 4) == [
        (4, rank) for rank in range(1, 6)
    ]
    assert sorted(key for key in subject.REUSED_BATCHES if key[0] == 5) == [
        (5, 1), (5, 2),
    ]
    assert all(path.is_file() for path in subject.REUSED_BATCHES.values())


def test_v15_state_records_fresh_reset_and_unchanged_settle_gate(monkeypatch):
    monkeypatch.setattr(subject.alg, "sha", lambda _path: "0" * 64)
    document = subject.state_document_v15("v15_state_test")
    continuation = document["technical_continuation"]
    assert continuation["kind"].endswith("retry_v15")
    assert continuation["completed_outer_decisions_reconstructed"] == [1, 2, 3, 4]
    assert continuation["outer5_completed_candidate_ranks_reused"] == [1, 2]
    assert continuation["failed_outer5_rank3_workload_started"] is False
    assert continuation["fresh_outer5_rank3_reset_required"] is True
    assert continuation["post_reset_settle_thresholds_unchanged"] is True
