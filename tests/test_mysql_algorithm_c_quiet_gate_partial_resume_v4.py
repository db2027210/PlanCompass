from pathlib import Path

from scripts import build_mysql_algorithm_c_outer_checkpoint_v3 as checkpointmod
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v4 as runtime


ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT = (
    ROOT
    / "history/mysql_four_scene_20260809"
    / "algorithm_c_v46_outer12_checkpoint_v13_20260816T0210p0800"
    / "prepared_binding.json"
)
SOURCE = (
    ROOT
    / "history/mysql_four_scene_20260809"
    / "v49_formal_outer13_contract_pointer_fix_20260816_attempt006"
)


def reconstructed_state():
    checkpoint = checkpointmod.verify_checkpoint(CHECKPOINT)
    return checkpoint, checkpoint["reconstructed_state"]


def test_clean_quiet_timeout_preserves_completed_rank_one():
    _checkpoint, reconstructed = reconstructed_state()
    assert runtime.clean_quiet_timeout_partial_boundary(
        source_root=SOURCE,
        reconstructed=reconstructed,
        start_rank=2,
    )


def test_partial_validator_binds_rank_one_and_resumes_rank_two():
    checkpoint, reconstructed = reconstructed_state()
    result = runtime.validate_formal_partial_continuation(
        source_root=SOURCE,
        checkpoint=checkpoint,
        reconstructed=reconstructed,
        start_rank=2,
        completed_rank5_root=None,
    )
    assert result["start_rank"] == 2
    assert result["outer_attempt"] == 13
    assert result["continuation_reason"] == (
        "premeasurement_quiet_window_timeout_after_completed_rank"
    )
    assert [row["rank"] for row in result["completed_batches"]] == [1]
    assert Path(result["completed_batches"][0]["path"]).name == "batch_result.json"
    assert result["candidate_state_mutation_before_continuation"] is False
    assert result["accepted_state_credit_before_continuation"] == 0
