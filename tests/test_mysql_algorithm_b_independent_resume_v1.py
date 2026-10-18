from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_four_scene_algorithm_b_independent_resume_v1 as resume


SOURCE = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v1_formal_independent_after_c_v91_20260817_attempt001"
)


def test_real_failed_run_is_a_seven_wave_premeasurement_checkpoint() -> None:
    value = resume.validate_resume_source(SOURCE)
    assert value["state"]["status"] == "FAILED_CLOSED"
    assert value["state"]["outer_attempt"] == 0
    assert len(value["baseline_rows"]) == 8
    assert sorted(value["wave_rows"]) == list(range(1, 8))
    assert all(len(rows) == 4 for rows in value["wave_rows"].values())
    assert not (SOURCE / "fresh_calibration/wave_08").exists()
    assert value["failure_gate_sha256"] == resume.alg.sha(
        value["failure_gate_path"]
    )


def test_resume_keeps_algorithm_c_and_outer_state_out_of_lineage() -> None:
    state = resume.state_document("formal_resume_test")
    independence = state["independence_contract"]
    assert independence["algorithm_c_checkpoint_read"] is False
    assert independence["algorithm_c_release_read"] is False
    assert independence["algorithm_c_measurement_read"] is False
    assert state["accepted_rounds"] == {
        scene: 0 for scene in resume.SCENES
    }
    assert state["outer_attempt"] == 0


def test_resume_controller_uses_device_scoped_fail_closed_release() -> None:
    source = Path(resume.__file__).read_text(encoding="utf-8")
    assert "mysql_co_resident_pg_isolation_v12" in source
    assert "unknown_or_unproved_d_state" in source
    assert "fail_closed" in source
    assert "proved_cpu_storage_memory_isolated_d_state" in source
    assert "record_nonblocking" in source
    assert "accepted_rounds_inherited\": False" in source
