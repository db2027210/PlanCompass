from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest


ROOT = Path(os.environ.get(
    "XP_BUNDLE_UNDER_TEST",
    Path(__file__).resolve().parents[1],
))
DEPLOYER = ROOT / "deployment/mysql_xp_compute00_daemon_deploy_v1.py"
CONTRACT = ROOT / "authority/mysql_xp_compute00_daemon_deployment_contract_v1.json"
FAILURE = ROOT / "authority/mysql_xp_v6_missing_dedicated_daemon_20260803.json"
SCHEMA = "mysql_xp_compute00_daemon_deployment_successor_v7"


def load_deployer():
    spec = importlib.util.spec_from_file_location("xp_daemon_v7", DEPLOYER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode()


def test_content_addressed_manifest_and_daemon_only_authority():
    manifest = json.loads((ROOT / "bundle_manifest.json").read_text())
    bundle_id = os.environ.get("XP_EXPECTED_BUNDLE_ID", ROOT.name)
    unsigned = dict(manifest); observed = unsigned.pop("bundle_id")
    assert observed == bundle_id
    if len(ROOT.name) == 64 and all(value in "0123456789abcdef"
                                    for value in ROOT.name):
        assert ROOT.name == bundle_id
    assert hashlib.sha256(canonical(unsigned)).hexdigest() == bundle_id
    assert manifest["schema_version"] == SCHEMA
    assert manifest["daemon_deployment_authorized"] is True
    assert manifest["transactional_daemon_deployment"] is True
    assert manifest["workload_launch_authorized"] is False
    assert manifest["database_container_launch_authorized"] is False
    assert manifest["client_container_launch_authorized"] is False
    assert manifest["formal_launch_authorized"] is False
    assert manifest["accepted_round_authorized"] is False
    assert manifest["accepted_rounds_credit"] == 0
    assert manifest["formal_scope"] == "mysql_only"
    assert manifest["cross_engine_comparison_prohibited"] is True
    assert manifest["postgresql_mutation_prohibited"] is True
    for relative, expected in manifest["files"].items():
        assert sha(ROOT / relative) == expected


def test_actual_deployer_verifies_final_content_addressed_bundle():
    mod = load_deployer()
    resolved, manifest, config = mod.verify_bundle(ROOT, ROOT.name)
    assert resolved == ROOT.resolve()
    assert manifest["bundle_id"] == ROOT.name
    assert config == ROOT.resolve() / mod.CONFIG_RELATIVE


def test_exact_deployment_contract_and_v6_failure_are_sealed():
    contract = json.loads(CONTRACT.read_text())
    assert contract["unit"] == "ra-docker-mysql-xp-v2.service"
    assert contract["fragment_path"] == (
        "/run/systemd/transient/ra-docker-mysql-xp-v2.service"
    )
    assert contract["control_group"] == (
        "/system.slice/ra-docker-mysql-xp-v2.service"
    )
    assert contract["socket"] == "/run/ra-docker-mysql-xp-v2.sock"
    assert contract["data_root"] == (
        "/disk1/resource_adaptive_mysql_algorithm_c_local_v2/xp/"
        "docker_ra_xp_v29"
    )
    assert contract["daemon_cpuset"] == "192-193,448-449"
    assert contract["data_major_minor"] == "259:6"
    assert contract["postgresql_reserved_cpuset"] == "32-51,288-307"
    assert contract["postgresql_cpu_overlap"] == []
    assert contract["workload_launch_authorized"] is False
    assert contract["transaction"] == {
        "prepare": "read_only_preflight_and_pg_service_root_snapshot",
        "single_manager_mutation": "exact_systemd_run_transient_unit_creation_and_start",
        "commit": "full_live_readback_then_atomic_receipt",
        "crash_after_exact_start": "rerun_adopts_same_pid_startticks_and_commits",
        "nonexact_or_failed_loaded_unit": "quarantine_fail_closed",
        "automatic_stop_remove_or_reset_failed_authorized": False,
        "workload_commit_coupled": False,
    }
    failure = json.loads(FAILURE.read_text())
    assert failure["predecessor_bundle_id"] == (
        "02b460d96dfd69972ca76a2b9620228fc2d239d46453d3c880d8ac001a7f12b4"
    )
    assert failure["observed_load_state"] == "not-found"
    assert failure["journal_history_entries"] == 0
    assert failure["existing_unit_authority_available"] is False
    assert failure["accepted_rounds_credit"] == 0


def test_complete_systemd_run_and_dockerd_argv_are_frozen():
    mod = load_deployer(); config = ROOT / mod.CONFIG_RELATIVE
    assert config.read_bytes() == b"{}\n"
    expected_dockerd = [
        "/usr/bin/dockerd", "--config-file=" + str(config.resolve()),
        "--host=unix:///run/ra-docker-mysql-xp-v2.sock",
        "--data-root=/disk1/resource_adaptive_mysql_algorithm_c_local_v2/"
        "xp/docker_ra_xp_v29",
        "--exec-root=/run/ra-docker-mysql-xp-v2",
        "--pidfile=/run/ra-docker-mysql-xp-v2.pid",
        "--storage-driver=overlay2",
        "--exec-opt=native.cgroupdriver=cgroupfs", "--bridge=none",
        "--iptables=false", "--ip-forward=false", "--ip-masq=false",
        "--userland-proxy=false", "--log-driver=none", "--group=root",
    ]
    assert mod.dockerd_argv(config) == expected_dockerd
    assert mod.unit_exec_argv(config) == [
        "/usr/bin/taskset", "-c", "192-193,448-449", *expected_dockerd,
    ]
    argv = mod.systemd_run_argv(config)
    assert argv[:4] == [
        "/usr/bin/systemd-run",
        "--unit=ra-docker-mysql-xp-v2.service",
        "--description=Dedicated Docker daemon for MySQL XP only",
        "--service-type=notify",
    ]
    assert [value.removeprefix("--property=") for value in argv
            if value.startswith("--property=")] == list(mod.SYSTEMD_PROPERTIES)
    assert argv[-len(mod.unit_exec_argv(config)):] == mod.unit_exec_argv(config)
    assert not any("postgres" in value.lower() or "/mnt/nvme2n1" in value
                   for value in argv)


def test_mutation_and_read_only_command_allowlists_fail_closed():
    mod = load_deployer(); config = ROOT / mod.CONFIG_RELATIVE
    mod.validate_command(mod.systemd_run_argv(config), mutation=True)
    mod.validate_command(mod.show_argv())
    mod.validate_command([
        mod.DOCKER, "--host", mod.SOCKET_URI, "info", "--format",
        "{{json .}}",
    ])
    for argv, mutation in (
        ([mod.SYSTEMCTL, "start", mod.UNIT], False),
        ([mod.SYSTEMCTL, "daemon-reload"], False),
        ([mod.DOCKER, "run", "mysql"], False),
        ([mod.DOCKER, "--host", "unix:///var/run/docker.sock", "info"],
         False),
        (mod.systemd_run_argv(config) + ["unexpected"], True),
    ):
        with pytest.raises(mod.DeploymentError):
            mod.validate_command(argv, mutation=mutation)


def test_execstart_parser_requires_complete_exact_argv():
    mod = load_deployer(); expected = mod.unit_exec_argv(ROOT / mod.CONFIG_RELATIVE)
    serialized = "ExecStart={ path=%s ; argv[]=%s ; ignore_errors=no ; }" % (
        expected[0], " ".join(shlex_quote(value) for value in expected),
    )
    assert mod.parse_execstart(serialized) == expected
    with pytest.raises(mod.DeploymentError):
        mod.parse_execstart("ExecStart=/usr/bin/dockerd --host=unix:///tmp/x")


def shlex_quote(value):
    import shlex
    return shlex.quote(value)


def cgroup_fixture(unit):
    path = "/system.slice/" + unit
    return "\n".join((
        "12:cpuset:/", "11:rdma:/", "10:pids:" + path,
        "9:freezer:/", "8:perf_event:/", "7:blkio:" + path,
        "6:cpu,cpuacct:" + path, "5:memory:" + path,
        "4:devices:" + path, "3:hugetlb:/", "2:net_cls,net_prio:/",
        "1:name=systemd:" + path, "0::" + path, "",
    ))


def test_exact_systemd219_hybrid_cgroup_and_drift_rejection():
    mod = load_deployer(); valid = cgroup_fixture(mod.UNIT)
    result = mod.cgroup_projection(valid)
    assert result["hybrid_exact_unit_rows"] == 1
    assert result["rdma_projection"] == "root"
    assert set(result["active_exact_unit_controllers"]) == mod.ACTIVE_CONTROLLERS
    assert set(result["root_controllers"]) == mod.ROOT_CONTROLLERS
    for changed in (
        valid.replace("0::" + mod.CONTROL_GROUP, "0::/"),
        valid.replace("11:rdma:/", "11:rdma:" + mod.CONTROL_GROUP),
        valid.replace("10:pids:" + mod.CONTROL_GROUP, "10:pids:/"),
        valid + "13::" + mod.CONTROL_GROUP + "\n",
    ):
        with pytest.raises(mod.DeploymentError):
            mod.cgroup_projection(changed)


def test_systemd_property_identity_has_no_lifecycle_hooks_or_pg_dependency():
    mod = load_deployer()
    properties = {key: "" for key in mod.SHOW_PROPERTIES}
    properties.update({
        "Id": mod.UNIT, "LoadState": "loaded", "ActiveState": "active",
        "SubState": "running", "MainPID": "123", "ControlGroup": mod.CONTROL_GROUP,
        "FragmentPath": mod.FRAGMENT_PATH, "UnitFileState": "transient",
        "Transient": "yes", "ExecStart": "fixture", "Requires": "basic.target",
        "After": "basic.target system.slice", "CPUShares": "256",
        "MemoryLimit": "2147483648", "TasksMax": "512",
        "LimitNOFILE": "1048576", "Delegate": "yes", "Restart": "no",
        "KillMode": "process",
    })
    mod.check_property_identity(properties)
    properties["After"] += " postgresql.service"
    with pytest.raises(mod.DeploymentError):
        mod.check_property_identity(properties)
    properties["After"] = "basic.target"; properties["ExecStartPre"] = "/bin/true"
    with pytest.raises(mod.DeploymentError):
        mod.check_property_identity(properties)


def test_v7_does_not_enable_inherited_workload_entrypoints():
    service = (ROOT / "launch/mysql_xp_compute00_adopted_service_v1.py").read_text()
    materials = (ROOT / "test_support/mysql_xp_qualification_bootstrap_materials_v5.py").read_text()
    assert SCHEMA not in service
    assert SCHEMA not in materials
    source = DEPLOYER.read_text()
    assert '"systemctl", "start"' not in source
    assert '"docker", "run"' not in source
    assert "/var/run/docker.sock" not in source


def test_receipt_idempotency_ignores_only_create_vs_adopt_disposition(tmp_path):
    mod = load_deployer(); path = tmp_path / "receipt.json"
    common = {
        "schema_version": "mysql_xp_compute00_daemon_deployment_receipt_v1",
        "status": "PASS_XP_DEDICATED_DAEMON_ONLY", "bundle_id": "a" * 64,
        "bundle_manifest_sha256": "b" * 64, "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "live_identity": {"main_pid": 123, "process_startticks": 456},
        "postgresql_process_identity_unchanged": True,
        "postgresql_mutation": False, "database_container_started": False,
        "client_container_started": False, "workload_started": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False, "accepted_rounds_credit": 0,
    }
    first = dict(common, disposition="CREATED_AND_STARTED_EXACT_TRANSIENT_UNIT",
                 preflight={"action": "CREATE_EXACT_TRANSIENT_UNIT"})
    assert mod.atomic_receipt(path, first) == "PUBLISHED_NEW_RECEIPT"
    second = dict(common, disposition="ADOPTED_EXACT_EXISTING_TRANSIENT_UNIT",
                  preflight={"action": "ADOPT_EXACT_EXISTING_ONLY"})
    assert mod.atomic_receipt(path, second) == "ADOPTED_IDENTICAL_RECEIPT"
    changed = dict(second, live_identity={"main_pid": 124,
                                          "process_startticks": 456})
    with pytest.raises(mod.DeploymentError):
        mod.atomic_receipt(path, changed)


def test_cgroup_namespace_is_optional_but_symmetric(monkeypatch):
    mod = load_deployer()

    class State:
        st_dev = 1
        st_ino = 2

    monkeypatch.setattr(mod.os, "stat", lambda _path: State())
    monkeypatch.setattr(mod.os.path, "lexists", lambda path: False)
    value = mod.host_namespace_identity(123)
    assert value["optional_cgroup"] == {
        "supported": False, "path_absent": True, "daemon": None,
        "pid1": None,
        "fallback": "/proc/<daemon>/cgroup_plus_systemd_ControlGroup",
    }

    monkeypatch.setattr(
        mod.os.path, "lexists", lambda path: path.startswith("/proc/123/"))
    with pytest.raises(mod.DeploymentError, match="asymmetric"):
        mod.host_namespace_identity(123)


def test_main_agent_one_click_is_deployment_only():
    runbook = (ROOT / "operator/mysql_xp_compute00_daemon_deployment_v7.md").read_text()
    assert "--authorize-exact-bundle-id" in runbook
    assert "mysql_xp_compute00_daemon_deploy_v1.py" in runbook
    assert "systemctl start" not in runbook
    assert "daemon-reload" not in runbook
    assert "workload launch are prohibited" in runbook.lower()
