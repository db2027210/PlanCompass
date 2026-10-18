import importlib.util
import json
from pathlib import Path
import stat
import subprocess


ROOT = Path(__file__).resolve().parents[1]
BUNDLE_ROOT = ROOT / "artifacts/mysql_xp_node02_successor_v27"


def latest_bundle():
    if (ROOT / "bundle_manifest.json").is_file():
        return ROOT
    rows = sorted(
        path for path in BUNDLE_ROOT.iterdir()
        if path.is_dir() and (path / "bundle_manifest.json").is_file()
    )
    assert rows
    return max(rows, key=lambda path: path.stat().st_mtime_ns)


def load_service(bundle):
    path = bundle / "launch/mysql_xp_node02_adopted_service_v1.py"
    spec = importlib.util.spec_from_file_location("xp_v27_service", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_credentials(mod, tmp_path, attempt="attempt-1234567890abcdef"):
    root = tmp_path / attempt
    directory = root / "private_credentials"
    directory.mkdir(parents=True, mode=0o700)
    user = mod._qualification_user(attempt)
    password = "CHANGE_ME" * 48
    (directory / "mysql-client.cnf").write_text(
        "[client]\nuser=%s\npassword=%s\n" % (user, password)
    )
    (directory / "hybench_xp_qualification.properties").write_text(
        "username=%s\npassword=%s\nusername_ap=%s\npassword_ap=%s\n"
        % (user, password, user, password)
    )
    (directory / "mysql-root-bootstrap.cnf").write_text(
        "[client]\nuser=root\npassword=private-test-value\n"
    )
    for path in directory.iterdir():
        path.chmod(0o600)
    return root, directory


def test_private_root_defaults_are_exact_and_never_public(tmp_path):
    mod = load_service(latest_bundle())
    root, directory = make_credentials(mod, tmp_path)
    value = mod._private_credentials(directory, root, root.name)
    assert value["root_defaults"] == "mysql-root-bootstrap.cnf"
    assert value["_root_defaults_payload"].startswith(b"[client]\nuser=root\n")
    public = {key: row for key, row in value.items() if not key.startswith("_")}
    assert "private-test-value" not in json.dumps(public)
    assert stat.S_IMODE((directory / value["root_defaults"]).stat().st_mode) == 0o600


def test_root_defaults_install_uses_stdin_not_argv(monkeypatch):
    mod = load_service(latest_bundle())
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = list(argv)
        captured["input"] = kwargs.get("input")
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    payload = b"[client]\nuser=root\npassword=CHANGE_ME\n"
    mod._root_defaults_install({"database": {"name": "db"}}, payload)
    assert captured["input"] == payload
    assert "CHANGE_ME" not in " ".join(captured["argv"])
    assert "--defaults-extra-file" not in " ".join(captured["argv"])


def test_readiness_uses_defaults_and_saves_redacted_last_error(monkeypatch, tmp_path):
    mod = load_service(latest_bundle())
    mod.RUNTIME_ROOT = str(tmp_path)
    mod.DATABASE_SOCKET_READINESS_TIMEOUT_SECONDS = 1
    mod.DATABASE_SOCKET_READINESS_POLL_SECONDS = 0
    completed = subprocess.CompletedProcess(
        ["mysql"], 1, "", "Access denied for user root using password: YES"
    )
    observed = {}

    def fake_run(argv, check=True):
        observed["argv"] = list(argv)
        return completed

    monkeypatch.setattr(mod, "_run", fake_run)
    plan = {
        "database": {"name": "db"},
        "attempt_id": "attempt-1234567890abcdef",
        "bundle_id": latest_bundle().name,
    }
    try:
        mod._wait_for_database_socket(plan)
    except mod.ServiceError as exc:
        assert "error_category=access_denied" in str(exc)
    else:
        raise AssertionError("readiness must fail closed")
    assert any(
        row == "--defaults-extra-file=/run/mysql-xp-root-bootstrap.cnf"
        for row in observed["argv"]
    )
    diagnostic = json.loads(
        (tmp_path / plan["attempt_id"]
         / "bootstrap_root_readiness_diagnostic.json").read_text()
    )
    assert diagnostic["last_mysql_returncode"] == 1
    assert diagnostic["last_error_category"] == "access_denied"
    assert "Access denied" not in json.dumps(diagnostic)


def test_manifest_contract_and_service_bind_root_auth():
    bundle = latest_bundle()
    manifest = json.loads((bundle / "bundle_manifest.json").read_text())
    contract = json.loads((bundle / "xp_executable_launch_contract_v3.json").read_text())
    assert manifest["schema_version"] == "mysql_xp_node02_successor_bundle_v27"
    root = contract["adopted_service"]["bootstrap_root_auth"]
    assert root["credential_literal_in_manifest_argv_stdout"] is False
    assert root["ephemeral_container_file_removed_after_provisioning"] is True
    source = (bundle / "launch/mysql_xp_node02_adopted_service_v1.py").read_text()
    assert '"--user=root"' not in source
    assert "finally:\n        _root_defaults_remove(plan)" in source
