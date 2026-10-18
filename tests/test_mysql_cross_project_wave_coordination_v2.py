from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v1 as predecessor
from scripts import mysql_cross_project_wave_coordination_v2 as coordination


def _isolation(*, passed=True, readback=True, row_errors=None, overlap=None):
    return {
        "passed": passed,
        "container_readback": {"passed": readback},
        "workloads": [{
            "pid": 42,
            "errors": [] if row_errors is None else row_errors,
            "overlap_target_cpus": [] if overlap is None else overlap,
        }],
    }


def test_only_complete_same_sample_isolation_proves_pid_exemption() -> None:
    assert coordination._proved_isolated_workload_pids(_isolation()) == {42}
    assert coordination._proved_isolated_workload_pids(
        _isolation(passed=False)
    ) == set()
    assert coordination._proved_isolated_workload_pids(
        _isolation(readback=False)
    ) == set()
    assert coordination._proved_isolated_workload_pids(
        _isolation(row_errors=["drift"])
    ) == set()
    assert coordination._proved_isolated_workload_pids(
        _isolation(overlap=[0])
    ) == set()


def test_proved_disk_disjoint_external_dstate_does_not_reset_window(
    monkeypatch,
) -> None:
    pressures = iter([
        {"blocking_d_state_pids": [42], "load1": 100.0},
        {"blocking_d_state_pids": [42], "load1": 100.0},
        {"blocking_d_state_pids": [42], "load1": 100.0},
    ])
    inventory = {
        "workloads": [{"pid": 42, "command": "postgres SELECT"}],
        "controllers": [],
    }
    monkeypatch.setattr(
        predecessor, "external_process_inventory", lambda run_id, mems: inventory
    )
    monkeypatch.setattr(
        predecessor, "evaluate_co_resident_pg_isolation", lambda rows: _isolation()
    )
    monkeypatch.setattr(predecessor, "same_run_process_pids", lambda run_id: set())
    monkeypatch.setattr(predecessor, "sanitized_rows", lambda rows: rows)
    monkeypatch.setattr(coordination.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        coordination.time, "monotonic",
        iter((0.0, 0.0, 8.0, 16.0, 16.0)).__next__,
    )
    from scripts import preflight_mysql_four_scene_live_v8 as preflight
    monkeypatch.setattr(preflight, "host_pressure", lambda: next(pressures))
    result = coordination.rolling_quiet_gate(
        run_id="formal_resume", required_seconds=15.0,
        maximum_wait_seconds=20.0, interval_seconds=1.0,
    )
    assert result["status"] == "PASS"
    assert result["schema_version"] == "mysql_cross_project_wave_quiet_gate_v2"
    for sample in result["samples"]:
        pressure = sample["host_pressure"]
        assert pressure["raw_blocking_d_state_pids"] == [42]
        assert pressure["proved_isolated_external_d_state_pids"] == [42]
        assert pressure["blocking_d_state_pids"] == []
        assert sample["persistent_d_state_pids"] == []


def test_unknown_or_unproved_persistent_dstate_still_fails_closed(
    monkeypatch,
) -> None:
    pressures = iter([
        {"blocking_d_state_pids": [99], "load1": 1.0},
        {"blocking_d_state_pids": [99], "load1": 1.0},
        {"blocking_d_state_pids": [99], "load1": 1.0},
        {"blocking_d_state_pids": [99], "load1": 1.0},
    ])
    inventory = {
        "workloads": [{"pid": 42, "command": "postgres SELECT"}],
        "controllers": [],
    }
    monkeypatch.setattr(
        predecessor, "external_process_inventory", lambda run_id, mems: inventory
    )
    monkeypatch.setattr(
        predecessor, "evaluate_co_resident_pg_isolation", lambda rows: _isolation()
    )
    monkeypatch.setattr(predecessor, "same_run_process_pids", lambda run_id: set())
    monkeypatch.setattr(predecessor, "sanitized_rows", lambda rows: rows)
    monkeypatch.setattr(coordination.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        coordination.time, "monotonic",
        iter((0.0, 0.0, 1.0, 2.0, 21.0, 21.0)).__next__,
    )
    from scripts import preflight_mysql_four_scene_live_v8 as preflight
    monkeypatch.setattr(preflight, "host_pressure", lambda: next(pressures))
    result = coordination.rolling_quiet_gate(
        run_id="formal_resume", required_seconds=15.0,
        maximum_wait_seconds=20.0, interval_seconds=1.0,
    )
    assert result["status"] == "BLOCKED_FAIL_CLOSED"
    assert result["errors"] == ["coordinated_quiet_window_timeout"]
    assert result["samples"][2]["persistent_d_state_pids"] == [99]
    assert "host_uninterruptible_sleep_present" in result["samples"][2]["errors"]
