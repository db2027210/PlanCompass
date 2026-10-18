from __future__ import annotations

import json
from pathlib import Path

import pytest

from shell import launch_mysql_algorithm_c_four_scenes as launcher
from shell import monitor_mysql_algorithm_c_four_scenes as monitor


SCENARIOS = ("job_memory", "hybench_xp_io", "tpch_memory", "tpcc_cpu")


def manifest() -> dict:
    return {
        "bundle_id": "test-bundle",
        "formal_scope": "mysql_only",
        "method_contract": {
            "global_real_replay_top_k_hard_max": 5,
            "target_accepted_shrinks": 10,
            "qps_role": "outer_acceptance_only",
            "acceptance": "qps_strictly_up_and_target_resource_strictly_down_and_each_non_target_strictly_below_0.8",
        },
        "launch_specs": {
            name: {"host": "compute00", "command": f"command-{name}", "stage": "test"}
            for name in SCENARIOS
        },
    }


def test_contract_and_launch_specs_are_strict() -> None:
    value = manifest()
    launcher.validate_contract(value)
    assert tuple(launcher.launch_specs(value)) == SCENARIOS


@pytest.mark.parametrize(
    ("key", "wrong"),
    (("global_real_replay_top_k_hard_max", 6), ("target_accepted_shrinks", 9), ("qps_role", "inner_step")),
)
def test_contract_rejects_wrong_core_value(key: str, wrong: object) -> None:
    value = manifest()
    value["method_contract"][key] = wrong
    with pytest.raises(ValueError, match=key):
        launcher.validate_contract(value)


def test_contract_rejects_missing_non_target_guard() -> None:
    value = manifest()
    value["method_contract"]["acceptance"] = "qps_strictly_up"
    with pytest.raises(ValueError, match="non-target"):
        launcher.validate_contract(value)


def test_dry_plan_covers_four_scenes_without_starting() -> None:
    plans = launcher.dry_plan(launcher.launch_specs(manifest()), Path("manifest.json"))
    assert [plan["scenario"] for plan in plans] == list(SCENARIOS)
    assert all(plan["would_start"] is True for plan in plans)


def test_dry_plan_can_cover_one_selected_scene() -> None:
    specs = launcher.launch_specs(manifest())
    plans = launcher.dry_plan({"hybench_xp_io": specs["hybench_xp_io"]}, Path("manifest.json"))
    assert [plan["scenario"] for plan in plans] == ["hybench_xp_io"]


def test_all_frozen_entry_points_execute_on_compute00() -> None:
    assert launcher.route_command("compute00", "echo x") == "echo x"
    assert launcher.route_command("node01_via_compute00", "echo x") == "echo x"
    assert launcher.route_command("node02_via_compute00", "echo x") == "echo x"
    with pytest.raises(ValueError, match="unsupported"):
        launcher.route_command("unknown", "echo x")


def test_monitor_reports_pending_evidence_without_guessing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_root = tmp_path / "state"
    monkeypatch.setattr(monitor, "ROOT", tmp_path)
    monkeypatch.setattr(monitor, "CONTROLLER_ROOT", tmp_path / "controllers")
    monkeypatch.setattr(monitor, "PRODUCTION_ROOT", tmp_path / "production")
    monkeypatch.setattr(monitor, "FORMAL_CAMPAIGN_ROOT", tmp_path / "formal")
    table = monitor.markdown(manifest(), state_root)
    assert "| Experiment benchmark | Machine | Alive | Outer-loop exploration | Accepted refinement |" in table
    assert table.count(": / :") == 4
    assert "0/10" in table


def test_running_state_does_not_reuse_stale_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_root = tmp_path / "state"
    scenario_dir = tmp_path / "controllers/job_memory"
    scenario_dir.mkdir(parents=True)
    (scenario_dir / "state.json").write_text(json.dumps({
        "scenario": "job_memory",
        "controller_pid": 41,
        "campaign_pid": 42,
    }), encoding="utf-8")
    evidence = tmp_path / "production/job_memory/production_points/current"
    evidence.mkdir(parents=True)
    (evidence / "campaign_runtime.json").write_text(json.dumps({
        "phase": "R0_RUNNING",
        "campaign_pid": 42,
        "accepted_shrinkage": 0,
    }), encoding="utf-8")
    monkeypatch.setattr(monitor, "ROOT", tmp_path)
    monkeypatch.setattr(monitor, "CONTROLLER_ROOT", tmp_path / "controllers")
    monkeypatch.setattr(monitor, "PRODUCTION_ROOT", tmp_path / "production")
    monkeypatch.setattr(monitor, "FORMAL_CAMPAIGN_ROOT", tmp_path / "formal")
    monkeypatch.setattr(monitor, "pid_live", lambda pid: pid in {41, 42})
    table = monitor.markdown(manifest(), state_root)
    job_row = next(line for line in table.splitlines() if line.startswith("| JOB /"))
    assert "R0_RUNNING" in job_row
    assert ": / :" in job_row
    assert "maximum_measurement_seconds_reached_unstable" not in job_row


def test_monitor_uses_current_runtime_acceptance_not_controller_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = tmp_path / "controllers/job_memory"
    controller.mkdir(parents=True)
    (controller / "state.json").write_text(
        json.dumps({"controller_pid": 1, "accepted_shrinkage": 6}),
        encoding="utf-8",
    )
    point = tmp_path / "production/job_memory/production_points/new"
    point.mkdir(parents=True)
    (point / "campaign_runtime.json").write_text(
        json.dumps({"campaign_pid": 2, "accepted_shrinkage": 0, "phase": "R0_RUNNING"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(monitor, "CONTROLLER_ROOT", tmp_path / "controllers")
    monkeypatch.setattr(monitor, "PRODUCTION_ROOT", tmp_path / "production")
    monkeypatch.setattr(monitor, "FORMAL_CAMPAIGN_ROOT", tmp_path / "formal")
    monkeypatch.setattr(monitor, "pid_live", lambda pid: False)
    assert monitor.row("job_memory")["Accepted refinement"] == "0/10"
