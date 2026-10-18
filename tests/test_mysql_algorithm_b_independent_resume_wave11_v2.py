from pathlib import Path


from scripts import (
    launch_mysql_four_scene_algorithm_b_independent_resume_wave11_v2 as launcher,
)
from scripts import run_mysql_four_scene_algorithm_b_independent_resume_wave11_v2 as resume


SOURCE = (
    Path(__file__).resolve().parents[1]
    / "history/mysql_four_scene_20260809"
    / "v2_formal_independent_resume_cal08_v63_20260817_attempt001"
)


def test_wave11_resume_accepts_only_verified_whole_wave_retry_source() -> None:
    evidence = resume.validate_resume_source(SOURCE)
    assert resume.RESUME_CALIBRATION_WAVES == 10
    assert resume.RESUME_FAILED_WAVE == 11
    assert len(evidence["wave_rows"]) == 10
    assert set(evidence["wave_rows"]) == set(range(1, 11))
    assert evidence["failure_evidence_path"] == (
        SOURCE / "fresh_calibration/wave_11/batch_result.json"
    )
    assert evidence["state"]["outer_attempt"] == 0
    assert all(
        value == 0 for value in evidence["state"]["accepted_rounds"].values()
    )


def test_wave11_launcher_binds_new_controller_and_v3_contract() -> None:
    assert launcher.CONTROLLER == (
        Path(__file__).resolve().parents[1]
        / "scripts/run_mysql_four_scene_algorithm_b_independent_resume_wave11_v2.py"
    )
    assert launcher.CONTRACT.parent.name == (
        "frozen_mysql_algorithm_b_independent_resume_wave11_20260817_v3"
    )

