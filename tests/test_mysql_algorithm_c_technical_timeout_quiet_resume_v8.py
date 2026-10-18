from pathlib import Path

from scripts import build_mysql_algorithm_c_outer_checkpoint_v3 as checkpointmod
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v8 as runtime


ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT = (
    ROOT / "history/mysql_four_scene_20260809"
    / "algorithm_c_v50_outer14_checkpoint_v15_20260816T2004p0800"
    / "prepared_binding.json"
)
SOURCE = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v50_formal_outer15_rank1_future_wave_aware_recovery_20260816_attempt022"
)


def authority():
    checkpoint = checkpointmod.verify_checkpoint(CHECKPOINT)
    return checkpoint, checkpoint["reconstructed_state"]


def test_clean_technical_timeout_then_quiet_boundary():
    _checkpoint, reconstructed = authority()
    assert runtime.clean_technical_timeout_then_quiet_boundary(
        source_root=SOURCE,
        reconstructed=reconstructed,
        start_rank=2,
    )


def test_validator_preserves_rank1_as_technical_exclusion():
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
        "technical_timeout_excluded_then_premeasurement_quiet_timeout"
    )
    assert result["completed_batches"] == [{
        "rank": 1,
        "path": str(SOURCE / "outer_015/top5_batch_1/batch_result.json"),
        "sha256": runtime.v4.runtime.alg.sha(
            SOURCE / "outer_015/top5_batch_1/batch_result.json"
        ),
        "technical_timeout_excluded": True,
    }]
    assert len(result["technical_batch_exclusions"]) == 1
    exclusion = result["technical_batch_exclusions"][0]
    assert exclusion["classification"] == "TECHNICAL_TIMEOUT_EXCLUDED"
    assert exclusion["candidate_state_mutation"] is False
    assert exclusion["accepted_state_credit_increment"] == 0
    assert all(not rows for rows in result["evaluations"].values())
    assert result["technical_timeout_quiet_recovery"][
        "fresh_quiet_gate_required"
    ] is True
