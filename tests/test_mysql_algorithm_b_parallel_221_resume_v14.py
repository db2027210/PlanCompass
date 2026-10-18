from __future__ import annotations

from scripts import (
    run_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v14
    as subject,
)


def test_v14_source_is_exact_preworkload_quiet_gate_failure():
    state = subject.validate_source()
    assert state["status"] == "FAILED_CLOSED"
    assert state["outer_attempt"] == 3
    assert state["error"] == subject.QUIET_GATE_ERROR
    assert not (subject.SOURCE / "outer_004/top5_batch_1").exists()


def test_v14_reuses_outer2_and_complete_outer3_only():
    assert sorted(subject.REUSED_BATCHES) == [
        *[(2, rank) for rank in range(1, 6)],
        *[(3, rank) for rank in range(1, 6)],
    ]
    assert all(path.is_file() for path in subject.REUSED_BATCHES.values())


def test_v14_state_records_fresh_gate_boundary(monkeypatch):
    monkeypatch.setattr(subject.alg, "sha", lambda _path: "0" * 64)
    document = subject.state_document_v14("v14_state_test")
    continuation = document["technical_continuation"]
    assert continuation["kind"].endswith("resume_v14")
    assert continuation["completed_outer_decisions_reconstructed"] == [1, 2, 3]
    assert continuation["failed_outer4_rank1_workload_started"] is False
    assert continuation["outer4_ranking_recomputed_and_identity_checked"] is True
