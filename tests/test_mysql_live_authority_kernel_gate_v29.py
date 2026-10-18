from pathlib import Path

from scripts import mysql_cross_project_wave_coordination_v29 as subject


def test_gate_binds_prepared_from_current_controller_authority(monkeypatch) -> None:
    authority = (
        subject.HISTORY
        / "v1261_c_xp_cpu_scene_authority_v113_20260906/authority"
    )
    monkeypatch.setenv("MYSQL_TWO_WORKER_AUTHORITY_ROOT", str(authority))
    old = subject.kernel.PREPARED
    try:
        assert subject.bind_current_authority() == authority / "prepared_binding.json"
        assert subject.kernel.PREPARED == authority / "prepared_binding.json"
    finally:
        subject.kernel.PREPARED = old


def test_gate_rejects_authority_outside_formal_history(monkeypatch) -> None:
    monkeypatch.setenv("MYSQL_TWO_WORKER_AUTHORITY_ROOT", "/tmp/not-authority")
    try:
        subject.bind_current_authority()
    except RuntimeError as error:
        assert "current authority root differs" in str(error)
    else:
        raise AssertionError("outside authority unexpectedly accepted")
