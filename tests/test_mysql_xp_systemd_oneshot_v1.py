from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MATERIALIZER = (
    ROOT / "shell/mysql_xp_qualification_bootstrap_materials_v5.py"
)
SERVICE = ROOT / "shell/mysql_xp_node02_adopted_service_v4.py"
HELP_FIXTURE = (
    ROOT / "artifacts/worker_systemd_run_help_20260731/"
    "node02_node04_systemd_run_help.stdout"
)
HELP_SHA256 = (
    "b2d1a41d2a3e6402d3927ed76f7e2e053ba425ccd80d4d6c6bf1b5a25a53a665"
)
UNIT = "ra-mysql-xp-v8-bootstrap-0123456789abcdef.service"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def show(**values):
    return "\n".join(
        "%s=%s" % (key, value) for key, value in values.items()
    ) + "\n"


def test_real_node02_systemd_219_help_fixture_is_parsed():
    mod = load(MATERIALIZER, "xp_systemd_help")
    evidence = mod.parse_systemd_run_help_fixture(HELP_FIXTURE)
    assert evidence["stdout_sha256"] == HELP_SHA256
    assert "--service-type" in evidence["parsed_supported_options"]
    assert "--remain-after-exit" in evidence["parsed_supported_options"]
    assert "--collect" in evidence["parsed_supported_options"]
    assert "--wait" not in evidence["parsed_supported_options"]
    assert evidence["launch_policy_prohibited_options"] == [
        "--wait", "--collect",
    ]


def test_launch_argv_is_old_systemd_compatible_and_audit_retaining():
    mod = load(MATERIALIZER, "xp_systemd_argv")
    argv = mod.systemd_run_argv(
        UNIT, ["python3", "-B", "/sealed/service.py", "--bootstrap"]
    )
    assert "--service-type=oneshot" in argv
    assert "--remain-after-exit" in argv
    assert "--wait" not in argv
    assert "--collect" not in argv
    assert "--unit=" + UNIT in argv
    poll = mod.controller_poll_spec(UNIT)
    assert poll["unit_retained_for_audit"] is True
    assert poll["collect_prohibited"] is True
    assert poll["argv"][:3] == ["systemctl", "show", UNIT]
    assert {
        "--property=ActiveState",
        "--property=SubState",
        "--property=ExecMainStatus",
        "--property=Result",
    }.issubset(set(poll["argv"]))


def test_offline_e2e_launch_poll_success_and_failure_are_fail_closed():
    mod = load(MATERIALIZER, "xp_systemd_e2e")
    cgroup = "/system.slice/" + UNIT
    running = show(
        ActiveState="activating",
        SubState="start",
        ExecMainStatus="0",
        Result="success",
        MainPID="42123",
        ControlGroup=cgroup,
    )
    success = show(
        ActiveState="active",
        SubState="exited",
        ExecMainStatus="0",
        Result="success",
        MainPID="0",
        ControlGroup=cgroup,
    )
    failed = show(
        ActiveState="failed",
        SubState="failed",
        ExecMainStatus="2",
        Result="exit-code",
        MainPID="0",
        ControlGroup=cgroup,
    )
    assert [
        mod.classify_systemctl_show(item, UNIT)
        for item in (running, success)
    ] == ["running", "success"]
    assert mod.classify_systemctl_show(failed, UNIT) == "failure"
    assert mod.classify_systemctl_show("", UNIT) == "failure"
    assert mod.classify_systemctl_show(
        success.replace(cgroup, "/docker/escape"), UNIT
    ) == "failure"


def test_sealed_service_requires_oneshot_running_identity():
    text = SERVICE.read_text(encoding="utf-8")
    assert "ra-mysql-xp-v8-" in text
    assert 'unit.get("ActiveState") != "activating"' in text
    assert 'unit.get("SubState") != "start"' in text
    assert "ra-mysql-xp-v7-" not in text


def test_help_fixture_hash_mismatch_is_rejected(tmp_path: Path):
    mod = load(MATERIALIZER, "xp_systemd_help_bad")
    bad = tmp_path / "systemd-run-help.stdout"
    bad.write_text(HELP_FIXTURE.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(mod.MaterialError, match="SHA-256 differs"):
        mod.parse_systemd_run_help_fixture(bad)


def test_materializer_e2e_emits_four_serial_poll_gates(tmp_path: Path):
    mod = load(MATERIALIZER, "xp_systemd_materializer_e2e")
    result = mod.create_materials(mod.DEFAULT_BUNDLE, tmp_path)
    plan = json.loads(
        Path(result["command_plan"]).read_text(encoding="utf-8")
    )
    commands = plan[
        "host_systemd_run_commands_after_separate_authorization"
    ]
    polls = plan["controller_poll_after_each_systemd_run"]
    assert set(commands) == {
        "physical_copy", "plan_only", "bootstrap", "adopt_readback",
    }
    assert set(polls) == set(commands)
    for action, argv in commands.items():
        assert argv[:2] == ["systemd-run", "--quiet"]
        assert "--service-type=oneshot" in argv
        assert "--remain-after-exit" in argv
        assert "--wait" not in argv
        assert "--collect" not in argv
        assert polls[action]["unit_retained_for_audit"] is True
        assert polls[action]["success_state"]["Result"] == "success"
    assert result["formal_r0_authorized"] is False
    assert result["accepted_round_authorized"] is False
