from __future__ import annotations

import importlib.util
from pathlib import Path
import stat

import pytest


ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "shell/mysql_xp_node02_adopted_service_v2.py"
COLLECTOR = ROOT / "shell/mysql_xp_distributed_live_readback_qualification_v3.py"
MATERIALS = ROOT / "shell/mysql_xp_qualification_bootstrap_materials_v3.py"
BUNDLE_ID = "a" * 64


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def write_private_fixture(mod, root: Path, attempt: str):
    directory = root / attempt / "private_credentials"
    directory.mkdir(parents=True, mode=0o700)
    user = mod._qualification_user(attempt)
    password = "A" * 43
    mysql = directory / "mysql-client.cnf"
    mysql.write_text(
        "[client]\nuser=%s\npassword=%s\n" % (user, password),
        encoding="utf-8",
    )
    properties = directory / "hybench_xp_qualification.properties"
    properties.write_text(
        "username=%s\npassword=%s\nusername_ap=%s\npassword_ap=%s\n"
        % (user, password, user, password),
        encoding="utf-8",
    )
    mysql.chmod(0o600)
    properties.chmod(0o600)
    return directory, user, password


def test_private_credentials_are_attempt_owned_and_private(tmp_path: Path):
    mod = load(SERVICE, "xp_private_service")
    attempt = "xp-formal-qual-20260731T050000Z-abcdef0123456789abcdef01"
    directory, user, password = write_private_fixture(mod, tmp_path, attempt)
    value = mod._private_credentials(
        directory, tmp_path / attempt, attempt
    )
    assert value["user"] == user
    assert value["_password"] == password
    public = {
        key: item for key, item in value.items() if not key.startswith("_")
    }
    assert password not in repr(public)
    assert public["directory_mode"] == "0700"
    assert public["file_mode"] == "0600"


def test_private_credentials_reject_group_readable_file(tmp_path: Path):
    mod = load(SERVICE, "xp_private_mode")
    attempt = "xp-formal-qual-20260731T050000Z-abcdef0123456789abcdef01"
    directory, _user, _password = write_private_fixture(
        mod, tmp_path, attempt
    )
    (directory / "mysql-client.cnf").chmod(0o640)
    with pytest.raises(mod.ServiceError, match="metadata differs"):
        mod._private_credentials(directory, tmp_path / attempt, attempt)


def test_account_sql_is_minimal_and_private_stdin_does_not_log_secret(
    monkeypatch,
):
    mod = load(SERVICE, "xp_private_sql")
    user = "ra_xp_qual_" + "a" * 20
    password = "B" * 43
    sql = mod._qualification_account_sql(
        user, mod.QUALIFICATION_ACCOUNT_HOST, password
    )
    assert "GRANT PROCESS ON *.*" in sql
    assert (
        "GRANT SELECT,INSERT,UPDATE,DELETE ON hybench_sf10x.*" in sql
    )
    assert "GRANT ALL" not in sql
    captured = {}

    class Result:
        returncode = 1
        stdout = ""
        stderr = "failure"

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["input"] = kwargs["input"]
        return Result()

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    with pytest.raises(mod.ServiceError) as raised:
        mod._run_private_stdin(
            ["docker", "exec", "-i", "database", "mysql"], sql
        )
    assert password not in " ".join(captured["argv"])
    assert captured["input"] == sql
    assert password not in str(raised.value)


def pair_rows(collector):
    attempt = "xp-formal-qual-20260731T050000Z-abcdef0123456789abcdef01"
    common = {
        "ra.formal_scope": "mysql_only",
        "ra.cross_engine_comparison_prohibited": "true",
        "ra.scenario": "hybench_xp_io",
        "ra.bundle_id": BUNDLE_ID,
        "ra.attempt_id": attempt,
        "ra.handoff_token_sha256": "b" * 64,
        "ra.main_agent_token_sha256": "c" * 64,
        "ra.authorization_expires_at_utc": "2026-07-31T05:20:00+00:00",
    }

    def row(role, name, pid, identity):
        labels = dict(common)
        labels["ra.role"] = role
        return {
            "Id": identity * 64,
            "Name": "/" + name,
            "Image": "sha256:" + "f" * 64,
            "Created": "now",
            "State": {"Running": True, "Pid": pid},
            "Config": {
                "Image": "sha256:" + "f" * 64,
                "Labels": labels,
            },
            "HostConfig": {"NetworkMode": "host"},
            "Mounts": [],
        }

    return (
        attempt,
        row("database", "ra_mysql_xp_v3_db_" + attempt, 101, "d"),
        row("client", "ra_mysql_xp_v3_client_" + attempt, 102, "e"),
    )


def test_collector_requires_exact_qualification_account(monkeypatch):
    mod = load(COLLECTOR, "xp_private_collector")
    attempt, database, client = pair_rows(mod)
    monkeypatch.setattr(mod, "process_start_ticks", lambda pid: pid * 10)
    rows = {
        database["Name"][1:]: database,
        client["Name"][1:]: client,
    }
    monkeypatch.setattr(mod, "inspect_container", lambda name: rows[name])
    pair = mod._container_pair_snapshot(
        database, client, BUNDLE_ID, "hybench_xp_io"
    )
    user = "ra_xp_qual_" + mod.hashlib.sha256(
        attempt.encode("utf-8")
    ).hexdigest()[:20]
    transport = {
        "bundle_id": BUNDLE_ID,
        "scenario": "hybench_xp_io",
        "database_container": database["Name"][1:],
        "client_container": client["Name"][1:],
        "client_container_id": client["Id"],
        "mysql_binary_in_client": "/usr/bin/mysql",
        "mysql_defaults_file_in_client":
            "/run/mysql-xp-private/mysql-client.cnf",
        "qualification_user": user,
        "qualification_account_host": "192.0.2.6",
        "pair_identity": pair,
    }
    monkeypatch.setattr(
        mod,
        "run",
        lambda *_args, **_kwargs:
            "65104\t%s@192.0.2.6\n" % user,
    )
    assert mod.client_authenticated_tcp_readiness(
        transport, 65104
    )["authenticated_user"] == "%s@192.0.2.6" % user
    monkeypatch.setattr(
        mod,
        "run",
        lambda *_args, **_kwargs: "65104\troot@192.0.2.6\n",
    )
    with pytest.raises(mod.QualificationError, match="readiness differs"):
        mod.client_authenticated_tcp_readiness(transport, 65104)


def test_private_properties_only_replaces_credentials(tmp_path: Path):
    mod = load(MATERIALS, "xp_private_materials")
    public = tmp_path / "public.properties"
    public.write_text(
        "url=jdbc:mysql://127.0.0.1/db\n"
        "username=root\npassword=\n"
        "username_ap=root\npassword_ap=\n"
        "xapclient=8\n",
        encoding="utf-8",
    )
    payload = mod.private_properties(public, "ra_xp_qual_" + "a" * 20, "C" * 43)
    text = payload.decode("utf-8")
    assert "url=jdbc:mysql://127.0.0.1/db" in text
    assert "xapclient=8" in text
    assert "username=root" not in text
