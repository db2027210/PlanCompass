from pathlib import Path

from scripts import build_mysql_algorithm_c_tpc_timeout_quarantine_v3 as subject


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"


def test_latest_timeout_outer_preserves_zero_credit_and_replay_tiers():
    document = subject.build(
        HISTORY
        / "c_v229_tpch_v225_outer10_timeout_checkpoint_20260929"
        / "prepared_binding.json",
        HISTORY
        / "c_v225_tpch_v217_outer10_technical_quarantine_20260929"
        / "replay_quarantine.json",
        HISTORY / "c_v225_tpch_quarantine_fix_recovery_20260929_tpch",
    )
    scene = subject.SCENE

    assert document["formal_state_mutation"] is False
    assert document["accepted_rounds_mutation"] is False
    assert document["rejected_rounds_mutation"] is False
    assert document["interval_convergence_mutation"] is False
    assert document["permanent_invalid_counts_by_scene"][scene] == 9
    assert document["soft_replayed_counts_by_scene"][scene] == 1
    assert document["quarantined_counts_by_scene"][scene] == 10
    assert not set(
        document["permanent_invalid_param_keys_by_scene"][scene]
    ).intersection(document["soft_replayed_param_keys_by_scene"][scene])
