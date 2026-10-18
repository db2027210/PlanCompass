from pathlib import Path


from scripts import (
    launch_mysql_four_scene_algorithm_b_independent_resume_wave11_amended_v3 as launcher,
)
from scripts import (
    run_mysql_four_scene_algorithm_b_independent_resume_wave11_amended_v3 as resume,
)


SOURCE = (
    Path(__file__).resolve().parents[1]
    / "history/mysql_four_scene_20260809"
    / "v3_formal_independent_resume_cal11_retry1_v63_20260817_attempt001"
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
        / "scripts/run_mysql_four_scene_algorithm_b_independent_resume_wave11_amended_v3.py"
    )
    assert launcher.CONTRACT.parent.name == (
        "frozen_mysql_algorithm_b_independent_resume_wave11_amended_20260817_v4"
    )


def test_tpch_amendment_changes_only_two_high_row_cost_points() -> None:
    source = resume.alg.load(SOURCE / "calibration_design.json")["scene_designs"]
    amended = {scene: [dict(row) for row in rows] for scene, rows in source.items()}
    amended = resume.copy.deepcopy(amended)
    audit = resume.amend_tpch_calibration_design(amended)
    assert [row["rank"] for row in audit] == [11, 15]
    for scene in resume.SCENES:
        for rank in range(1, 16):
            if scene == "tpch_sf4_memory" and rank in {11, 15}:
                assert amended[scene][rank - 1] != source[scene][rank - 1]
            else:
                assert amended[scene][rank - 1] == source[scene][rank - 1]
