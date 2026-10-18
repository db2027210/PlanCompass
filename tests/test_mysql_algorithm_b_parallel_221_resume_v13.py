from __future__ import annotations

from scripts import (
    recover_mysql_algorithm_b_v12_partial_xp_reset_v1 as recovery_source,
)
from scripts import (
    run_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v13
    as subject,
)


def test_v13_recovery_chain_is_complete_and_credit_neutral():
    recovery = subject.validate_recovery()
    assert recovery["status"] == "PASS"
    assert recovery["formal_state_credit_increment"] == 0
    assert recovery["job_tpch_container_mutation"] is False
    assert recovery["external_container_mutation"] is False
    assert {row["worker_id"] for row in recovery["workers"]} == recovery_source.XP_IDS


def test_v13_uses_recovered_prepared_and_accounts():
    assert subject.PREPARED.parent == subject.RECOVERY_ROOT
    assert subject.ACCOUNTS.parent == subject.RECOVERY_ROOT
    assert subject.PREPARED.is_file()
    assert subject.ACCOUNTS.is_file()


def test_v13_state_records_exact_resume_boundary(monkeypatch):
    monkeypatch.setattr(subject.alg, "sha", lambda _path: "0" * 64)
    document = subject.state_document_v13("v13_state_test")
    continuation = document["technical_continuation"]
    assert continuation["kind"].endswith("resume_v13")
    assert continuation["outer3_completed_candidate_ranks_reused"] == [1, 2]
    assert continuation["failed_outer3_rank3_workload_started"] is False
    assert continuation["formal_state_credit_increment_during_recovery"] == 0


def test_stopped_container_classifier_requires_exact_pid_zero(monkeypatch):
    rows = [
        {
            "Id": "a" * 64,
            "State": {"Status": "exited", "Running": False, "Pid": 0},
            "Config": {"Labels": {
                "ra.owner": "mysql_four_scene_v5", "ra.worker_id": "xp_cpu_w2",
            }},
        },
        {
            "Id": "a" * 64,
            "State": {"Status": "exited", "Running": False, "Pid": 0},
            "Config": {"Labels": {
                "ra.owner": "mysql_four_scene_v5", "ra.worker_id": "xp_cpu_w2",
            }},
        },
    ]
    monkeypatch.setattr(recovery_source, "inspect", lambda _name: rows.pop(0))
    worker = {
        "worker_id": "xp_cpu_w2",
        "container": "ra_mysql_disk2_algc_xp_cpu_w2_20260809",
        "container_id": "a" * 64,
    }
    result = recovery_source.classify_stopped_or_live(worker)
    assert result["status"] == "PROVEN_NO_LIVE_MYSQLD"
    assert result["safe_to_discard_unresponsive_predecessor"] is True
    assert result["session_count_semantics"].endswith("pid_zero")
