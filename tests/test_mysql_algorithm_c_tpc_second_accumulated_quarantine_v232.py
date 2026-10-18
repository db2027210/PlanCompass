from pathlib import Path

from scripts import build_mysql_algorithm_c_tpc_timeout_quarantine_v4 as subject
from scripts import launch_mysql_algorithm_c_v232_tpch_second_accumulated_quarantine_recovery as launcher
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v128 as controller


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"


def test_second_timeout_outer_preserves_zero_credit_and_accumulates_tiers():
    document = subject.build(
        HISTORY
        / "c_v229_tpch_v225_outer10_timeout_checkpoint_20260929"
        / "prepared_binding.json",
        HISTORY
        / "c_v229_tpch_v225_outer10_timeout_quarantine_20260929"
        / "replay_quarantine.json",
        HISTORY / "c_v230_tpch_accumulated_quarantine_recovery_20260929_tpch",
    )
    scene = subject.SCENE

    assert document["formal_state_mutation"] is False
    assert document["accepted_rounds_mutation"] is False
    assert document["rejected_rounds_mutation"] is False
    assert document["interval_convergence_mutation"] is False
    assert document["permanent_invalid_counts_by_scene"][scene] == 13
    assert document["soft_replayed_counts_by_scene"][scene] == 2
    assert document["quarantined_counts_by_scene"][scene] == 15
    assert not set(
        document["permanent_invalid_param_keys_by_scene"][scene]
    ).intersection(document["soft_replayed_param_keys_by_scene"][scene])


def test_v232_contract_and_launch_authority_preserve_outer_10_state():
    contract = controller.validate_contract()
    launch_contract, checkpoint = launcher.validate()

    assert contract == launch_contract
    assert contract["normal_stop_uses_actual_interval_width"] is True
    assert contract["accepted_round_target"] is None
    assert contract["legacy_post_target_cap_retired"] is True
    assert contract["technical_timeout_state_credit"] == 0
    assert contract["technical_timeout_rejected_round_increment"] == 0
    assert contract["permanent_invalid_candidate_count"] == 13
    assert contract["soft_replayed_candidate_count"] == 2
    assert contract["quarantined_candidate_count"] == 15
    assert checkpoint["completed_outer_attempts"] == 10
    assert checkpoint["reconstructed_state"]["accepted_rounds"][subject.SCENE] == 9
    assert checkpoint["reconstructed_state"]["rejected_outer_attempts"][subject.SCENE] == 1
