from __future__ import annotations

from scripts import (
    run_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v16
    as subject,
)


def test_v16_source_is_exact_rank5_post_reset_settle_failure():
    state = subject.validate_source()
    assert state["status"] == "FAILED_CLOSED"
    assert state["outer_attempt"] == 4
    assert state["error"] == subject.SETTLE_ERROR
    assert not (subject.SOURCE / "outer_005/top5_batch_5").exists()


def test_v16_reuses_through_outer5_rank4_only():
    assert sorted(key for key in subject.REUSED_BATCHES if key[0] == 5) == [
        (5, 1), (5, 2), (5, 3), (5, 4),
    ]
    assert all(path.is_file() for path in subject.REUSED_BATCHES.values())


def test_v16_uses_kernel_write_inflight_not_total_inflight():
    sectors, read_inflight, write_inflight = subject._disk2_write_state()
    assert sectors >= 0
    assert read_inflight >= 0
    assert write_inflight >= 0
    assert subject.DISK2_DEVICE_INFLIGHT.read_text().split().__len__() == 2


def test_v16_state_records_write_specific_unchanged_thresholds(monkeypatch):
    monkeypatch.setattr(subject.alg, "sha", lambda _path: "0" * 64)
    document = subject.state_document_v16("v16_state_test")
    continuation = document["technical_continuation"]
    release = document["post_reset_storage_settle_release"]
    assert continuation["kind"].endswith("resume_v16")
    assert continuation["outer5_completed_candidate_ranks_reused"] == [1, 2, 3, 4]
    assert continuation["failed_outer5_rank5_workload_started"] is False
    assert continuation["read_inflight_diagnostic_only"] is True
    assert continuation["numeric_writeback_thresholds_changed"] is False
    assert release["maximum_write_bytes_per_second"] == 8 * 1024 * 1024
    assert release["maximum_write_in_flight_ios"] == 1
    assert release["quiet_required_seconds"] == 15
    assert release["quiet_maximum_seconds"] == 120
