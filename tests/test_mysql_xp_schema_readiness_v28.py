from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import uuid

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_module(path: Path, stem: str):
    name = "%s_%s" % (stem, uuid.uuid4().hex)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    old_path = list(sys.path)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = old_path
    return module


class Completed:
    def __init__(self, returncode, stdout, stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def plan(tmp_path: Path, bundle: Path):
    attempt = "xp-formal-qual-v28-readiness-test-00000001"
    (tmp_path / attempt).mkdir()
    return {
        "attempt_id": attempt,
        "bundle_id": bundle.name,
        "database": {"name": "sealed-db"},
    }


def test_v28_validators_accept_exact_outer_bundle():
    authority = load_module(
        ROOT / "qualification/mysql_xp_qualification_authority_v1.py",
        "xp_v28_authority",
    )
    collector = load_module(
        ROOT / "qualification/mysql_distributed_live_readback_qualification_v1.py",
        "xp_v28_collector",
    )
    materializer = load_module(
        ROOT / "launch/mysql_xp_node02_formal10_materializer_v1.py",
        "xp_v28_materializer",
    )
    unused_path, authority_manifest = authority.verify_bundle(ROOT)
    collector_manifest, runtime = collector.verify_bundle(ROOT)
    materializer_manifest = materializer.verify_bundle(ROOT)
    for manifest in (
        authority_manifest, collector_manifest, materializer_manifest,
    ):
        assert manifest["schema_version"] == "mysql_xp_node02_successor_bundle_v28"
        assert manifest["bundle_id"] == ROOT.name
    assert runtime["formal_scope"] == "mysql_only"
    assert runtime["formal_launch_authorized"] is False


def test_authenticated_socket_readiness_can_pass_after_120_polls(
    tmp_path, monkeypatch,
):
    service = load_module(
        ROOT / "launch/mysql_xp_node02_adopted_service_v1.py",
        "xp_v28_service_slow_success",
    )
    calls = []
    sleeps = []

    def fake_run(argv, check=True, **unused):
        calls.append((argv, check))
        return Completed(1, "", "not ready") if len(calls) <= 130 else Completed(0, "1\n")

    monkeypatch.setattr(service, "_run", fake_run)
    monkeypatch.setattr(service.time, "sleep", sleeps.append)
    monkeypatch.setattr(service, "RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setattr(service, "DATABASE_SOCKET_READINESS_TIMEOUT_SECONDS", 131)
    result = service._wait_for_database_socket(plan(tmp_path, ROOT))
    assert result.returncode == 0
    assert len(calls) == 131
    assert sleeps == [1] * 130
    argv, check = calls[-1]
    assert check is False
    assert argv[-2:] == ["--execute", "SELECT 1"]
    assert "--protocol=socket" in argv
    assert any(row.startswith("--defaults-extra-file=") for row in argv)


def test_authenticated_socket_readiness_fails_with_redacted_diagnostic(
    tmp_path, monkeypatch,
):
    service = load_module(
        ROOT / "launch/mysql_xp_node02_adopted_service_v1.py",
        "xp_v28_service_fail_closed",
    )
    calls = []

    def never_ready(argv, check=True, **unused):
        calls.append((argv, check))
        return Completed(1, "", "Access denied using password: YES")

    monkeypatch.setattr(service, "_run", never_ready)
    monkeypatch.setattr(service.time, "sleep", lambda unused: None)
    monkeypatch.setattr(service, "RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setattr(service, "DATABASE_SOCKET_READINESS_TIMEOUT_SECONDS", 3)
    value = plan(tmp_path, ROOT)
    with pytest.raises(service.ServiceError, match="error_category=access_denied"):
        service._wait_for_database_socket(value)
    assert len(calls) == 3
    diagnostic = json.loads(
        (tmp_path / value["attempt_id"]
         / service.ROOT_READINESS_DIAGNOSTIC).read_text()
    )
    assert diagnostic["last_error_category"] == "access_denied"
    assert diagnostic["raw_stderr_recorded"] is False
    assert "Access denied" not in json.dumps(diagnostic)


def test_v28_manifest_remains_mysql_only_zero_credit():
    manifest = json.loads((ROOT / "bundle_manifest.json").read_text())
    source = (
        ROOT / "launch/mysql_xp_node02_adopted_service_v1.py"
    ).read_text()
    assert manifest["schema_version"] == "mysql_xp_node02_successor_bundle_v28"
    assert manifest["formal_scope"] == "mysql_only"
    assert manifest["cross_engine_comparison_prohibited"] is True
    assert manifest["formal_launch_authorized"] is False
    assert manifest["accepted_round_authorized"] is False
    assert "for attempt in range(1, DATABASE_SOCKET_READINESS_TIMEOUT_SECONDS + 1)" in source
    assert "--defaults-extra-file=%s" in source
    assert '"--user=root"' not in source
