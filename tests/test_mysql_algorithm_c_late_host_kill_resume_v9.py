from pathlib import Path

from scripts import build_mysql_algorithm_c_outer_checkpoint_v3 as checkpointmod
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v9 as runtime


ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT = (
    ROOT / "history/mysql_four_scene_20260809"
    / "algorithm_c_v50_outer14_checkpoint_v15_20260816T2004p0800"
    / "prepared_binding.json"
)
SOURCE = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v50_formal_outer15_rank2_technical_timeout_quiet_recovery_20260816_attempt023"
)


def authority():
    checkpoint = checkpointmod.verify_checkpoint(CHECKPOINT)
    return checkpoint, checkpoint["reconstructed_state"]


def test_clean_late_host_kill_reset_boundary():
    _checkpoint, reconstructed = authority()
    assert runtime.clean_late_host_kill_reset_boundary(
        source_root=SOURCE,
        reconstructed=reconstructed,
        start_rank=2,
    )
    assert set(runtime.LAST_RESTART_READBACK) == {"xp_cpu_w1", "xp_io_w1"}


def test_validator_preserves_inherited_technical_rank1():
    checkpoint, reconstructed = authority()
    result = runtime.validate_formal_partial_continuation(
        source_root=SOURCE,
        checkpoint=checkpoint,
        reconstructed=reconstructed,
        start_rank=2,
        completed_rank5_root=None,
    )
    assert result["start_rank"] == 2
    assert result["outer_attempt"] == 15
    assert result["continuation_reason"] == (
        "late_host_pid_kill_completed_then_same_identity_restarted"
    )
    assert [row["rank"] for row in result["completed_batches"]] == [1]
    assert result["completed_batches"][0]["technical_timeout_excluded"] is True
    assert len(result["technical_batch_exclusions"]) == 1
    assert result["technical_batch_exclusions"][0]["classification"] == (
        "TECHNICAL_TIMEOUT_EXCLUDED"
    )
    assert all(not rows for rows in result["evaluations"].values())
    recovery = result["late_host_kill_reset_recovery"]
    assert set(recovery["live_restart_readback"]) == {
        "xp_cpu_w1", "xp_io_w1",
    }
    assert recovery["fresh_reset_required"] is True
