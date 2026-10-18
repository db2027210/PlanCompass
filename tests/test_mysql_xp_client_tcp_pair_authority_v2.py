from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "shell/mysql_xp_distributed_live_readback_qualification_v2.py"
BUNDLE_ID = "a" * 64


def module():
    spec = importlib.util.spec_from_file_location("xp_tcp_pair_v2", SOURCE)
    assert spec is not None and spec.loader is not None
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def labels(role: str, attempt: str = "attempt-with-dashes") -> dict[str, str]:
    return {
        "ra.formal_scope": "mysql_only",
        "ra.cross_engine_comparison_prohibited": "true",
        "ra.scenario": "hybench_xp_io",
        "ra.bundle_id": BUNDLE_ID,
        "ra.attempt_id": attempt,
        "ra.handoff_token_sha256": "b" * 64,
        "ra.main_agent_token_sha256": "c" * 64,
        "ra.authorization_expires_at_utc": "2026-07-31T04:28:36+00:00",
        "ra.role": role,
    }


def row(role: str, name: str, pid: int, attempt: str = "attempt-with-dashes"):
    return {
        "Id": ("d" if role == "database" else "e") * 64,
        "Name": "/" + name,
        "Image": "sha256:" + "f" * 64,
        "Created": "2026-07-31T04:03:36Z",
        "State": {"Running": True, "Pid": pid},
        "Config": {
            "Image": "sha256:" + "f" * 64,
            "Entrypoint": None,
            "Cmd": ["sleep", "infinity"],
            "User": "",
            "Labels": labels(role, attempt),
        },
        "HostConfig": {
            "NetworkMode": "host",
            "CpuPeriod": 100000,
            "CpuQuota": 530000 if role == "database" else 1800000,
        },
        "Mounts": [],
    }


def test_dynamic_names_bind_by_labels_not_suffix(monkeypatch, tmp_path: Path):
    mod = module()
    database_name = "ra_mysql_xp_v3_db_attempt-with-dashes"
    client_name = "ra_mysql_xp_v3_client_attempt-with-dashes"
    database = row("database", database_name, 11781)
    client = row("client", client_name, 11921)
    monkeypatch.setattr(mod, "process_start_ticks", lambda pid: pid * 10)
    bundle = tmp_path / BUNDLE_ID
    defaults = bundle / "qualification/config/mysql-client.cnf"
    defaults.parent.mkdir(parents=True)
    defaults.write_text("[client]\n", encoding="utf-8")
    monkeypatch.setattr(
        mod,
        "inspect_container",
        lambda name: client if name == client_name else database,
    )
    monkeypatch.setattr(
        mod,
        "validate_client_container_readback",
        lambda *_args: {
            "container_id": client["Id"],
            "container_short_id": client["Id"][:12],
            "pid": 11921,
            "process_start_ticks": 119210,
            "inspect_config_sha256":
                mod._client_container_config_identity(client),
        },
    )
    monkeypatch.setattr(mod, "run", lambda *_args, **_kwargs: "mysql Ver 8.0")
    runtime = {
        "scenario": "hybench_xp_io",
        "database_counter_transport": {
            "schema_version": "mysql_algorithm_c_database_counter_transport_v2",
            "kind": "exact_client_container_docker_exec_stdout_v1",
            "mysql_binary_in_client": "/usr/bin/mysql",
            "mysql_defaults_file_relative":
                "qualification/config/mysql-client.cnf",
            "mysql_defaults_file_in_client":
                "/opt/mysql-xp-config/mysql-client.cnf",
            "client_bundle_destination": "/opt/mysql-xp-bundle",
            "client_runtime_destination": "/opt/mysql-xp-jdk",
            "client_config_source_relative": "qualification/config",
            "client_config_destination": "/opt/mysql-xp-config",
            "stdout_only": True,
            "host_mysql_cli_required": False,
            "docker_socket_mount_prohibited": True,
            "target_datadir_mount_prohibited": True,
        },
        "toolchain": {"deployment_target": "/runtime/jdk"},
        "client_resource_envelope": {},
    }
    transport = mod.prepare_database_counter_transport(
        runtime,
        bundle,
        database_name,
        database,
        defaults,
        client_name,
        "/usr/bin/mysql",
    )
    assert transport["pair_identity"]["labels"]["ra.attempt_id"] == (
        "attempt-with-dashes"
    )
    assert transport["pair_identity"]["database"]["pid"] == 11781
    assert transport["pair_identity"]["client"]["pid"] == 11921
    assert ".endswith(\"_db\")" not in SOURCE.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value["Config"]["Labels"].update(
            {"ra.attempt_id": "other-attempt"}), "pair label differs"),
        (lambda value: value["Config"]["Labels"].update(
            {"ra.role": "database"}), "role or scope"),
        (lambda value: value["Config"]["Labels"].update(
            {"ra.handoff_token_sha256": "0" * 64}), "pair label differs"),
    ],
)
def test_pair_authority_rejects_mismatched_labels(
    monkeypatch, mutation, message
):
    mod = module()
    database = row("database", "ra_mysql_xp_v3_db_attempt", 11781)
    client = row("client", "ra_mysql_xp_v3_client_attempt", 11921)
    mutation(client)
    monkeypatch.setattr(mod, "process_start_ticks", lambda pid: pid * 10)
    with pytest.raises(mod.QualificationError, match=message):
        mod._container_pair_snapshot(
            database, client, BUNDLE_ID, "hybench_xp_io"
        )


def test_readiness_uses_authenticated_client_and_rechecks_pair(monkeypatch):
    mod = module()
    database_name = "ra_mysql_xp_v3_db_attempt"
    client_name = "ra_mysql_xp_v3_client_attempt"
    database = row("database", database_name, 11781)
    client = row("client", client_name, 11921)
    monkeypatch.setattr(mod, "process_start_ticks", lambda pid: pid * 10)
    rows = {database_name: database, client_name: client}
    monkeypatch.setattr(mod, "inspect_container", lambda name: rows[name])
    pair = mod._container_pair_snapshot(
        database, client, BUNDLE_ID, "hybench_xp_io"
    )
    transport = {
        "bundle_id": BUNDLE_ID,
        "scenario": "hybench_xp_io",
        "database_container": database_name,
        "client_container": client_name,
        "client_container_id": client["Id"],
        "mysql_binary_in_client": "/usr/bin/mysql",
        "mysql_defaults_file_in_client": "/opt/mysql-xp-config/mysql-client.cnf",
        "pair_identity": pair,
    }
    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        return "65104\tra_user\n"

    monkeypatch.setattr(mod, "run", run)
    monkeypatch.setattr(
        mod,
        "port_probe",
        lambda _port: pytest.fail("controller loopback must not be probed"),
    )
    result = mod.client_authenticated_tcp_readiness(transport, 65104)
    assert result["controller_loopback_probe_used"] is False
    assert calls[0][:4] == [
        "docker", "exec", client["Id"], "/usr/bin/mysql"
    ]
    assert "--protocol=TCP" in calls[0]
    assert "--host=127.0.0.1" in calls[0]


def test_readiness_rejects_identity_drift(monkeypatch):
    mod = module()
    database_name = "ra_mysql_xp_v3_db_attempt"
    client_name = "ra_mysql_xp_v3_client_attempt"
    database = row("database", database_name, 11781)
    client = row("client", client_name, 11921)
    monkeypatch.setattr(mod, "process_start_ticks", lambda pid: pid * 10)
    rows = {database_name: database, client_name: client}
    monkeypatch.setattr(mod, "inspect_container", lambda name: rows[name])
    pair = mod._container_pair_snapshot(
        database, client, BUNDLE_ID, "hybench_xp_io"
    )
    transport = {
        "bundle_id": BUNDLE_ID,
        "scenario": "hybench_xp_io",
        "database_container": database_name,
        "client_container": client_name,
        "client_container_id": client["Id"],
        "mysql_binary_in_client": "/usr/bin/mysql",
        "mysql_defaults_file_in_client": "/opt/mysql-xp-config/mysql-client.cnf",
        "pair_identity": pair,
    }

    def run(_argv, **_kwargs):
        client["Config"]["Cmd"] = ["changed"]
        return "65104\tra_user\n"

    monkeypatch.setattr(mod, "run", run)
    with pytest.raises(mod.QualificationError, match="identity changed"):
        mod.client_authenticated_tcp_readiness(transport, 65104)


@pytest.mark.parametrize("output", ["65105\tra_user\n", "65104\t\n", "garbage\n"])
def test_readiness_rejects_wrong_port_or_auth(
    monkeypatch, output: str
):
    mod = module()
    database_name = "ra_mysql_xp_v3_db_attempt"
    client_name = "ra_mysql_xp_v3_client_attempt"
    database = row("database", database_name, 11781)
    client = row("client", client_name, 11921)
    monkeypatch.setattr(mod, "process_start_ticks", lambda pid: pid * 10)
    rows = {database_name: database, client_name: client}
    monkeypatch.setattr(mod, "inspect_container", lambda name: rows[name])
    transport = {
        "bundle_id": BUNDLE_ID,
        "scenario": "hybench_xp_io",
        "database_container": database_name,
        "client_container": client_name,
        "client_container_id": client["Id"],
        "mysql_binary_in_client": "/usr/bin/mysql",
        "mysql_defaults_file_in_client": "/opt/mysql-xp-config/mysql-client.cnf",
        "pair_identity": mod._container_pair_snapshot(
            database, client, BUNDLE_ID, "hybench_xp_io"
        ),
    }
    monkeypatch.setattr(mod, "run", lambda *_args, **_kwargs: output)
    with pytest.raises(mod.QualificationError, match="readiness differs"):
        mod.client_authenticated_tcp_readiness(transport, 65104)
