from pathlib import Path

from scripts import audit_mysql_algorithm_c_global_incumbent_chain_v3 as audit


RUN = Path(
    "history/mysql_four_scene_20260809/"
    "v1255_to_c_full_docker_path_resilience_v111_20260906_job"
)


def test_semantic_batch_filter_matches_runtime_selection() -> None:
    transition = audit.decision_transition(
        RUN / "outer_019/job_memory.decision.json"
    )
    assert transition["selected_semantic_batch_kind"] == "fallback_plan_change"
    assert transition["selected_db_qps"] == 1.1771248086910415


def test_failed_job_run_has_a_valid_incumbent_chain() -> None:
    result = audit.audit_run(RUN)
    assert result["status"] == "PASS"
    assert result["failures"] == []

