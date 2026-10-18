from pathlib import Path

from scripts import build_mysql_algorithm_c_tpc_timeout_quarantine_v7 as quarantine
from scripts import launch_mysql_algorithm_c_v244_tpch_fifth_timeout_recovery as launcher
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v135 as controller


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"


def test_fifth_timeout_wave_adds_one_permanent_and_one_soft_zero_credit_row() -> None:
    document = quarantine.build(
        HISTORY
        / "c_v244_tpch_v243_outer10_timeout_checkpoint_20260929"
        / "prepared_binding.json",
        HISTORY
        / "c_v238_tpch_v234_outer10_timeout_quarantine_20260929"
        / "replay_quarantine.json",
        HISTORY / "c_v243_tpch_confirmed_cleanup_recovery_20260929_tpch",
    )
    scene = quarantine.SCENE

    assert document["formal_state_mutation"] is False
    assert document["accepted_rounds_mutation"] is False
    assert document["rejected_rounds_mutation"] is False
    assert document["interval_convergence_mutation"] is False
    assert document["permanent_invalid_counts_by_scene"][scene] == 18
    assert document["soft_replayed_counts_by_scene"][scene] == 9
    assert document["quarantined_counts_by_scene"][scene] == 27
    assert document["rows_by_scene"][scene][-2]["classification"] == (
        "TECHNICAL_TPCH_FULL_SUITE_TIMEOUT_EXCLUDED"
    )
    assert document["rows_by_scene"][scene][-1]["classification"] == (
        "VALID_REPLAY_OUTER_ABORTED_BEFORE_DECISION"
    )


def test_v244_contract_and_launch_preserve_outer10_and_cleanup_readback() -> None:
    contract = controller.validate_contract()
    launch_contract, checkpoint = launcher.validate()

    assert contract == launch_contract
    assert contract["technical_timeout_state_credit"] == 0
    assert contract["technical_timeout_rejected_round_increment"] == 0
    assert contract["permanent_invalid_candidate_count"] == 18
    assert contract["soft_replayed_candidate_count"] == 9
    assert contract["quarantined_candidate_count"] == 27
    assert contract["tpch_cleanup_readback_after_error"] is True
    assert checkpoint["completed_outer_attempts"] == 10
    assert checkpoint["reconstructed_state"]["accepted_rounds"][quarantine.SCENE] == 9
    assert checkpoint["reconstructed_state"]["rejected_outer_attempts"][quarantine.SCENE] == 1
