from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
from pathlib import Path
import socket
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
        ROOT / "shell/build_mysql_xp_node02_authority_ttl_successor_v23.py",
        "xp_v23_builder",
    )
    return builder.build()


@pytest.fixture()
def service(bundle):
    return load(
        bundle / "launch/mysql_xp_node02_adopted_service_v1.py",
        "xp_v23_service",
    )


def iso(value):
    return value.replace(microsecond=0).isoformat()


def test_exact_3600_authority_passes_and_3601_fails(service):
    now = datetime(2026, 8, 3, 2, 40, tzinfo=timezone.utc)
    created = now - timedelta(minutes=5)
    exact = created + timedelta(seconds=3600)
    assert service.verify_short_lived_authority(
        iso(created), iso(exact), now=now
    )["expires_at_utc"] == iso(exact)
    with pytest.raises(service.ServiceError, match="exceeds 60 minutes"):
        service.verify_short_lived_authority(
            iso(created), iso(exact + timedelta(seconds=1)), now=now
        )


def test_tampered_future_and_old_expired_authorities_fail(service):
    now = datetime(2026, 8, 3, 2, 40, tzinfo=timezone.utc)
    with pytest.raises(service.ServiceError, match="not currently valid"):
        service.verify_short_lived_authority(
            iso(now + timedelta(seconds=1)), iso(now + timedelta(minutes=60)), now=now
        )
    old_created = now - timedelta(hours=2)
    with pytest.raises(service.ServiceError, match="not currently valid"):
        service.verify_short_lived_authority(
            iso(old_created), iso(old_created + timedelta(hours=1)), now=now
        )


def test_build_plan_uses_exact_60_authority_gate(
    tmp_path, bundle, service, monkeypatch
):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    created, expires = iso(now - timedelta(seconds=1)), iso(now + timedelta(seconds=3599))
    attempt = "xp-formal-qual-20260803T024000Z-0123456789abcdef01234567"
    monkeypatch.setattr(service, "verify_bundle", lambda unused: {"bundle_id": bundle.name})
    monkeypatch.setattr(service, "verify_runtime_publication", lambda *unused: {"status": "sealed"})
    monkeypatch.setattr(service, "verify_attempt", lambda *unused: "a" * 64)
    monkeypatch.setattr(service, "_safe_image", lambda value, unused: value)
    monkeypatch.setattr(service, "_private_credentials", lambda *unused: {
        "directory": str(tmp_path), "user": "u", "account_host": "192.0.2.6",
        "_password": "p", "_properties": {},
    })
    monkeypatch.setattr(service, "_dedicated_daemon_identity", lambda: {"id": "daemon"})
    monkeypatch.setattr(service, "_host_executor_identity", lambda unused: {"id": "executor"})
    config = bundle / "qualification/config"
    public = {}
    for line in (config / "hybench_xp_qualification.properties").read_text().splitlines():
        if line and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1); public[key.strip()] = value.strip()
    monkeypatch.setattr(service, "_private_credentials", lambda *unused: {
        "directory": str(tmp_path), "user": "u", "account_host": "192.0.2.6",
        "_password": "p", "_properties": public,
    })
    plan = service.build_plan(
        bundle, "sha256:" + "1" * 64, "sha256:" + "1" * 64, config,
        Path(service.RUNTIME_ROOT) / attempt / "datadir",
        Path(service.RUNTIME_ROOT) / attempt / "host_measurement_tmpfs",
        tmp_path / "runtime.json", tmp_path, attempt, attempt, "2" * 64,
        created, expires, "ra-mysql-xp-v12-plan-0123456789abcdef.service",
    )
    assert plan["short_lived_authority"] == {
        "created_at_utc": created, "expires_at_utc": expires,
    }


class ReachedCreate(RuntimeError):
    pass


def test_bootstrap_rechecks_exact_60_and_rejects_3601(service, monkeypatch):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    created = now - timedelta(seconds=1)
    monkeypatch.setattr(service.socket, "gethostname", lambda: "node02")
    monkeypatch.setattr(service.os, "geteuid", lambda: 0)
    monkeypatch.setattr(service, "_dedicated_daemon_identity", lambda: {"id": "daemon"})
    monkeypatch.setattr(service, "_host_executor_identity", lambda unused: {"id": "executor"})
    for name in ("_validate_copy", "_validate_storage_topology", "_validate_host_tmpfs", "_validate_port_available"):
        monkeypatch.setattr(service, name, lambda *unused, **unused_kwargs: None)
    monkeypatch.setattr(service, "_inspect", lambda unused: None)
    monkeypatch.setattr(service, "_private_credentials", lambda *unused: {"user": "u"})
    monkeypatch.setattr(service, "_run", lambda *unused, **kwargs: (_ for _ in ()).throw(ReachedCreate()))
    token = "sealed-token"
    plan = {
        "dedicated_docker_daemon": {"id": "daemon"}, "host_executor": {"id": "executor"},
        "executor_unit": "ra-mysql-xp-v12-bootstrap-0123456789abcdef.service",
        "main_agent_token_sha256": hashlib.sha256(token.encode()).hexdigest(),
        "sql_stdout_bridge": {"host_output_tmpfs": "/sealed/tmpfs"},
        "database": {"name": "db", "create_argv": ["create-db"]},
        "client": {"name": "client", "create_argv": ["create-client"], "private_credentials": {"directory": "/sealed/credentials"}},
        "attempt_id": "xp-formal-qual-20260803T024000Z-0123456789abcdef01234567",
        "short_lived_authority": {"created_at_utc": iso(created), "expires_at_utc": iso(created + timedelta(seconds=3600))},
    }
    with pytest.raises(ReachedCreate):
        service.bootstrap(plan, {}, Path("/sealed/output"), token)
    plan["short_lived_authority"]["expires_at_utc"] = iso(created + timedelta(seconds=3601))
    with pytest.raises(service.ServiceError, match="exceeds 60 minutes"):
        service.bootstrap(plan, {}, Path("/sealed/output"), token)
