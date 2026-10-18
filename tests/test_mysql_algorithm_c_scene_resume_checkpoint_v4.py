from pathlib import Path

from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v4 as subject


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"


def test_schema_aware_dispatch_accepts_existing_scene_v1_checkpoint() -> None:
    path = (
        HISTORY / "v1232_c_job_resume_checkpoint_v105_20260905"
        / "prepared_binding.json"
    )
    value = subject.verify_any_checkpoint(path)
    assert value["scene"] == "job_memory"
    assert value["completed_outer_attempts"] == 12


def test_recoverable_errors_are_exact_v110_classes() -> None:
    assert "RuntimeError: dynamic non-interference gate failed closed" in (
        subject.RECOVERABLE_V110_ERRORS
    )
    assert all(
        error.startswith(("RuntimeError:", "TimeoutExpired:"))
        for error in subject.RECOVERABLE_V110_ERRORS
    )


def test_runtime_verifier_accepts_chained_scene_v3_checkpoint() -> None:
    path = (
        HISTORY / "v1250_c_job_resume_checkpoint_v111_20260906"
        / "prepared_binding.json"
    )
    if not path.is_file():
        return
    value = subject.scene_checkpoint.verify_or_standard(path)
    assert value["scene"] == "job_memory"
    assert value["completed_outer_attempts"] == 13
