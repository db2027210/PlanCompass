from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import uuid

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load(path: Path, stem: str):
    spec = importlib.util.spec_from_file_location(stem + uuid.uuid4().hex, path)
    module = importlib.util.module_from_spec(spec)
    old = list(sys.path)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = old
    return module


@pytest.fixture(scope="session")
def bundle():
    if (ROOT / "bundle_manifest.json").is_file():
        return ROOT
    builder = load(
        ROOT / "shell/build_mysql_xp_node02_systemd219_successor_v21.py",
        "xp_v21_builder",
    )
    return builder.build()


@pytest.fixture()
def materials(bundle):
    return load(
        bundle / "test_support/mysql_xp_qualification_bootstrap_materials_v5.py",
        "xp_v21_materials",
    )


UNIT = "ra-mysql-xp-v12-plan-0123456789abcdef.service"
ARGV = ["taskset", "-c", "21-33,57-69", "python3", "-B", "/sealed.py", "--plan"]


def show(**values):
    return "".join("%s=%s\n" % item for item in values.items())


def valid_empty_audit(materials):
    identity = dict(
        Id=UNIT, Transient="yes", RemainAfterExit="yes",
        ActiveState="active", SubState="exited", ExecMainStatus="0",
        Result="success", MainPID="0", ControlGroup="",
    )
    prior = dict(
        systemctl_show=show(
            Id=UNIT, Transient="yes", RemainAfterExit="yes",
            ActiveState="activating", SubState="start", ExecMainStatus="0",
            Result="success", MainPID="42123",
            ControlGroup="/system.slice/" + UNIT,
        ),
        normalized_exec_start_argv=ARGV,
        process_cgroup="/system.slice/" + UNIT,
        pid_starttime_ticks=987654,
    )
    return {
        "systemctl_show": show(**identity),
        "normalized_exec_start_argv": ARGV,
        "prior_running": prior,
        "journal_evidence": {
            "unit": UNIT, "sealed": True, "content": "journal: success\n",
            "sha256": hashlib.sha256(b"journal: success\n").hexdigest(),
        },
        "output_evidence": {
            "unit": UNIT, "sealed": True, "content": "output: sealed\n",
            "sha256": hashlib.sha256(b"output: sealed\n").hexdigest(),
        },
    }


def test_systemd219_empty_cgroup_with_complete_audit_succeeds(materials):
    audit = valid_empty_audit(materials)
    assert materials.classify_systemctl_audit(audit, UNIT, ARGV) == "success"


def test_wrapper_internal_running_identity_is_valid_alternative(materials):
    audit = valid_empty_audit(materials)
    audit.pop("prior_running")
    unsigned = {
        "unit": UNIT,
        "cgroup": "/system.slice/" + UNIT,
        "pid": 42123,
        "pid_starttime_ticks": 987654,
        "exec_start_argv_sha256": materials._exec_argv_sha256(ARGV),
    }
    wrapper = dict(unsigned)
    wrapper["identity_sha256"] = hashlib.sha256(materials.canonical(unsigned)).hexdigest()
    audit["wrapper_running_identity"] = wrapper
    assert materials.classify_systemctl_audit(audit, UNIT, ARGV) == "success"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda a: a.update(normalized_exec_start_argv=ARGV + ["tampered"]),
        lambda a: a.update(systemctl_show=a["systemctl_show"].replace("Id=" + UNIT, "Id=escape.service")),
        lambda a: a["prior_running"].update(process_cgroup="/system.slice/escape.service"),
        lambda a: a["journal_evidence"].update(sha256="3" * 64),
        lambda a: a["output_evidence"].update(sealed=False),
    ],
)
def test_empty_cgroup_tampering_fails_closed(materials, mutate):
    audit = valid_empty_audit(materials)
    mutate(audit)
    assert materials.classify_systemctl_audit(audit, UNIT, ARGV) == "failure"


def test_empty_cgroup_without_running_identity_fails_closed(materials):
    audit = valid_empty_audit(materials)
    audit.pop("prior_running")
    assert materials.classify_systemctl_audit(audit, UNIT, ARGV) == "failure"


def test_running_state_still_requires_exact_cgroup_and_pid(materials):
    audit = valid_empty_audit(materials)["prior_running"]
    current = {
        "systemctl_show": audit["systemctl_show"],
        "normalized_exec_start_argv": ARGV,
    }
    assert materials.classify_systemctl_audit(current, UNIT, ARGV) == "running"
    current["systemctl_show"] = current["systemctl_show"].replace(
        "/system.slice/" + UNIT, "/system.slice/escape.service"
    )
    assert materials.classify_systemctl_audit(current, UNIT, ARGV) == "failure"


def test_generated_plan_binds_all_four_exec_argv_and_preserves_gates(
    tmp_path, bundle, materials
):
    result = materials.create_materials(bundle, tmp_path, 10)
    path = Path(result["command_plan"])
    plan = json.loads(path.read_text(encoding="utf-8"))
    commands = plan["commands_to_run_on_node02_after_separate_authorization"]
    polls = plan["controller_poll_after_each_systemd_run"]
    mapping = {
        "physical_copy": "physical_copy", "plan_only": "plan_only",
        "bootstrap": "bootstrap", "adopt_readback": "adopt_readback",
    }
    for action, command_key in mapping.items():
        poll = polls[action]
        assert poll["expected_exec_start_argv"] == commands[command_key]
        assert poll["systemd219_empty_control_group_terminal_success"][
            "missing_or_tampered_evidence_policy"
        ] == "FAIL_CLOSED_ABANDON_ATTEMPT"
        assert {"--property=Id", "--property=Transient", "--property=RemainAfterExit", "--property=ExecStart"}.issubset(poll["argv"])
    spec = json.loads(Path(result["bootstrap_spec"]).read_text())
    gate = spec["formal_gate_binding"]
    assert spec["formal_scope"] == "mysql_only"
    assert spec["postgresql_mutation_prohibited"] is True
    assert gate["required_nonempty_subsets"] == 127
    assert gate["fixed_real_replay_top_k"] == 5
    assert gate["database_cpu_quota_cores"] == 5.3
    assert gate["database_io_read_bps"] == 29360128
    assert gate["database_io_write_bps"] == 524288
