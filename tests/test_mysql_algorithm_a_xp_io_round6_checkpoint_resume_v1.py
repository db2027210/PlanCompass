from pathlib import Path

from scripts import run_mysql_algorithm_a_xp_io_round6_checkpoint_resume_v1 as resume


def test_round6_checkpoint_retains_only_semantic_history() -> None:
    state, candidates = resume.validate_predecessor()
    assert state["status"] == "FAILED_CLOSED"
    assert state["round_index"] == 6
    assert state["accepted"] == 4
    assert state["rejected_rounds"] == 1
    assert state["consecutive_rejected_rounds"] == 1
    assert [row["round_index"] for row in state["accepted_history"]] == [1, 2, 3, 4]
    checkpoint = state["round_checkpoint"]
    assert checkpoint["attempted_ranks"] == []
    assert checkpoint["valid_ranks"] == []
    assert checkpoint["technical_ranks"] == []
    assert checkpoint["waves"] == []
    assert candidates["round_index"] == 6
    assert candidates["accepted_index"] == 4
    assert len(resume.candidate_vectors(candidates)) == 16


def test_round6_recovery_contract_is_frozen_and_bound() -> None:
    authority = (
        resume.HISTORY
        / "v1051_a_io_round4_post_c_live_authority_normalized_20260828"
        / "live_authority.json"
    )
    contract = resume.validate_contract(authority.resolve())
    assert contract["status"] == "FROZEN_RUNTIME_VALIDATED"
    assert contract["rounds_1_to_5_history_retained"] is True
    assert contract["round6_completed_candidate_measurements_reused_false"] is True
    assert contract["round6_new_physical_work_starts_at_rank1"] is True
    assert contract["docker_control_failure_credit"] is False


def test_round6_recovery_uses_current_eight_worker_runtime() -> None:
    assert Path(resume.runtime.__file__).name == (
        "run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v14.py"
    )
    assert resume.EXPECTED == {f"xp_io_w{i}" for i in range(1, 9)}
    assert resume.ACTIVE == [
        "io_block_read_cost",
        "disk_temptable_row_cost",
        "disk_temptable_create_cost",
        "memory_temptable_row_cost",
    ]
