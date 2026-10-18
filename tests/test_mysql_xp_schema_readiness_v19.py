from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import uuid

import pytest


CHECKOUT_ROOT = Path(__file__).resolve().parents[1]
V18_ID = "f43f33b5d60cf6de2fe63f96852e6a46ea3b132b5d52039a1e60021771dc9262"


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


@pytest.fixture(scope="session")
def bundles():
    if (CHECKOUT_ROOT / "bundle_manifest.json").is_file():
        v19 = CHECKOUT_ROOT
        v18 = v19 / "predecessor"
        while v18.name != V18_ID and (v18 / "predecessor").is_dir():
            v18 = v18 / "predecessor"
        if v18.name != V18_ID:
            v18 = None
        return v18, v19

    builder = load_module(
        CHECKOUT_ROOT
        / "shell/build_mysql_xp_node02_schema_readiness_successor_v19.py",
        "xp_v19_builder",
    )
    v19 = builder.build()
    v18 = (
        CHECKOUT_ROOT / "artifacts/mysql_xp_node02_successor_v18" / V18_ID
    )
    return v18, v19


def test_successor_validators_accept_outer_v18_and_v19(bundles):
    v18, v19 = bundles
    authority = load_module(
        v19 / "qualification/mysql_xp_qualification_authority_v1.py",
        "xp_v19_authority",
    )
    collector = load_module(
        v19 / "qualification/mysql_distributed_live_readback_qualification_v1.py",
        "xp_v19_collector",
    )
    materializer = load_module(
        v19 / "launch/mysql_xp_node02_formal10_materializer_v1.py",
        "xp_v19_materializer",
    )

    cases = [(v19, "mysql_xp_node02_successor_bundle_v19")]
    if v18 is not None:
        cases.insert(0, (v18, "mysql_xp_node02_successor_bundle_v18"))
    for bundle, schema in cases:
        unused_path, authority_manifest = authority.verify_bundle(bundle)
        collector_manifest, runtime = collector.verify_bundle(bundle)
        materializer_manifest = materializer.verify_bundle(bundle)
        assert authority_manifest["schema_version"] == schema
        assert collector_manifest["schema_version"] == schema
        assert materializer_manifest["schema_version"] == schema
        assert runtime["formal_scope"] == "mysql_only"
        assert runtime["formal_launch_authorized"] is False


class Completed:
    def __init__(self, returncode, stdout):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


def test_socket_readiness_can_succeed_after_old_120_second_limit(
    bundles, monkeypatch
):
    unused_v18, v19 = bundles
    service = load_module(
        v19 / "launch/mysql_xp_node02_adopted_service_v1.py",
        "xp_v19_service_slow_success",
    )
    calls = []
    sleeps = []

    def fake_run(argv, check=True, **unused):
        calls.append((argv, check))
        if len(calls) <= 130:
            return Completed(1, "")
        return Completed(0, "1\n")

    monkeypatch.setattr(service, "_run", fake_run)
    monkeypatch.setattr(service.time, "sleep", sleeps.append)
    monkeypatch.setattr(service, "DATABASE_SOCKET_READINESS_TIMEOUT_SECONDS", 131)
    result = service._wait_for_database_socket(
        {"database": {"name": "sealed-db"}}
    )

    assert result.returncode == 0
    assert len(calls) == 131
    assert sleeps == [1] * 130
    argv, check = calls[-1]
    assert check is False
    assert argv[-2:] == ["--execute", "SELECT 1"]
    assert "--protocol=socket" in argv
    assert argv[:5] == [
        "docker", "--host", "unix:///run/ra-docker-mysql-xp-v2.sock",
        "exec", "sealed-db",
    ]


def test_socket_readiness_remains_fail_closed(bundles, monkeypatch):
    unused_v18, v19 = bundles
    service = load_module(
        v19 / "launch/mysql_xp_node02_adopted_service_v1.py",
        "xp_v19_service_fail_closed",
    )
    calls = []

    def never_ready(argv, check=True, **unused):
        calls.append((argv, check))
        return Completed(0, "0\n")

    monkeypatch.setattr(service, "_run", never_ready)
    monkeypatch.setattr(service.time, "sleep", lambda unused: None)
    monkeypatch.setattr(service, "DATABASE_SOCKET_READINESS_TIMEOUT_SECONDS", 3)
    with pytest.raises(
        service.ServiceError, match="database socket bootstrap readiness failed"
    ):
        service._wait_for_database_socket({"database": {"name": "sealed-db"}})
    assert len(calls) == 3


def test_v19_manifest_is_immutable_mysql_only_zero_credit(bundles):
    unused_v18, v19 = bundles
    manifest = json.loads((v19 / "bundle_manifest.json").read_text())
    remediation = json.loads(
        (v19 / "xp_node02_schema_readiness_remediation_v1.json").read_text()
    )
    service = (v19 / "launch/mysql_xp_node02_adopted_service_v1.py").read_text()
    assert manifest["bundle_id"] == v19.name
    assert manifest["formal_scope"] == "mysql_only"
    assert manifest["cross_engine_comparison_prohibited"] is True
    assert manifest["formal_launch_authorized"] is False
    assert manifest["accepted_round_authorized"] is False
    assert remediation["bootstrap_readiness_timeout_seconds"] == 600
    assert remediation["health_check_bypass_prohibited"] is True
    assert remediation["r0_credit"] == 0
    assert remediation["accepted_rounds_credit"] == 0
    assert "for _unused in range(DATABASE_SOCKET_READINESS_TIMEOUT_SECONDS)" in service
    assert "for _unused in range(120)" not in service
