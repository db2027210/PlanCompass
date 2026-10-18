from __future__ import annotations

from pathlib import Path

from scripts import reset_mysql_xp_data_planes_v8 as reset_v8
from scripts import (
    run_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v12
    as subject,
)


def test_v12_source_is_exact_preworkload_reset_failure():
    state = subject.validate_source()
    assert state["status"] == "FAILED_CLOSED"
    assert state["outer_attempt"] == 2
    assert state["error"] == subject.RESET_FAILURE
    assert not (subject.SOURCE / "outer_003/top5_batch_3/batch_result.json").exists()


def test_v12_reuses_complete_outer2_and_outer3_first_wave_only():
    assert sorted(subject.REUSED_BATCHES) == [
        (2, 1), (2, 2), (2, 3), (2, 4), (2, 5), (3, 1), (3, 2),
    ]
    assert all(path.is_file() for path in subject.REUSED_BATCHES.values())


def test_v12_state_records_bounded_metadata_recovery(monkeypatch):
    monkeypatch.setattr(subject.alg, "sha", lambda _path: "0" * 64)
    document = subject.state_document_v12("outer3_reset_resume_test")
    continuation = document["technical_continuation"]
    assert continuation["kind"].endswith("resume_v12")
    assert continuation["outer3_completed_candidate_ranks_reused"] == [1, 2]
    assert continuation["failed_outer3_rank3_workload_started"] is False
    assert continuation["credential_metadata_timeout_seconds"] == 120


def test_reset_v8_extends_only_reset_init_chown_timeout(monkeypatch):
    calls = []

    class FakeSubprocess:
        def run(self, argv, *args, **kwargs):
            calls.append((argv, kwargs))
            return object()

        def __getattr__(self, name):
            raise AttributeError(name)

    fake = FakeSubprocess()
    monkeypatch.setattr(reset_v8, "_ORIGINAL_SUBPROCESS", fake)
    proxy = reset_v8._SubprocessProxy()
    credential = Path("/disk2/x/credentials/reset-init.sql")
    proxy.run(["sudo", "-n", "chown", "999:999", str(credential)], timeout=30)
    proxy.run(["sudo", "-n", "chmod", "0400", str(credential)], timeout=30)
    assert calls[0][1]["timeout"] == 120
    assert calls[1][1]["timeout"] == 30
