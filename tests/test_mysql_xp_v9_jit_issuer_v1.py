from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import stat

import pytest

from shell import build_mysql_xp_v9_jit_issuer_v1 as jit_builder


ROOT = Path(__file__).resolve().parents[1]
ISSUER = ROOT / "issuer/mysql_xp_v9_jit_issuer_v1.py"
V9 = (
    ROOT.parents[1] / "mysql_xp_formal10_launch_v9"
    / "168f1505fa89a80dee9fb39533300b7e1e1a4ecce6e9d60af2704072e4cb67ac"
)


def module():
    spec = importlib.util.spec_from_file_location("xp_v9_jit_issuer", ISSUER)
    assert spec is not None and spec.loader is not None
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def token(tmp_path):
    directory = tmp_path / "private_issuer"
    directory.mkdir(mode=0o700)
    path = directory / "issuer_token"
    path.write_text("I" * 64 + "\n", encoding="ascii")
    path.chmod(0o600)
    return path


def test_60min_lease_is_v9_bound_and_zero_credit(tmp_path):
    mod = module()
    observed = datetime(2026, 7, 31, 7, 20, tzinfo=timezone.utc)
    lease_path = tmp_path / "issuer_lease.json"
    lease = mod.prepare_lease(V9, token(tmp_path), lease_path, observed)
    assert lease["bundle_id"] == mod.V9_ID
    assert lease["bundle_manifest_sha256"] == mod.V9_MANIFEST_SHA256
    assert (
        mod.parse_utc(lease["issuer_expires_at_utc"])
        - mod.parse_utc(lease["issuer_created_at_utc"])
    ).total_seconds() == 3600
    assert lease["gate_binding"] == mod.gate_binding()
    assert lease["formal_launch_authorized"] is False
    assert lease["accepted_round_authorized"] is False
    assert stat.S_IMODE(lease_path.stat().st_mode) == 0o400


def test_jit_child_is_v9_bound_private_external_and_25min(tmp_path):
    mod = module()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    private = token(tmp_path)
    lease_path = tmp_path / "issuer_lease.json"
    mod.prepare_lease(V9, private, lease_path, now)
    output = tmp_path / "child_materials"
    result = mod.issue(V9, private, lease_path, output, 25)
    material = Path(result["materials_directory"])
    spec = json.loads((material / "bootstrap_spec.json").read_text())
    manifest = json.loads(
        (material / "materials_manifest.json").read_text()
    )
    created = mod.parse_utc(manifest["created_at_utc"])
    expires = mod.parse_utc(manifest["expires_at_utc"])
    assert (expires - created).total_seconds() == 1500
    assert manifest["bundle_id"] == mod.V9_ID
    assert manifest["bundle_manifest_sha256"] == mod.V9_MANIFEST_SHA256
    assert spec["formal_scope"] == "mysql_only"
    assert spec["formal_gate_binding"]["database_cpu_quota_cores"] == 5.3
    assert spec["formal_gate_binding"]["database_io_read_bps"] == 29360128
    assert spec["formal_gate_binding"]["database_io_write_bps"] == 524288
    assert (
        spec["formal_gate_binding"]["capture_only_io_bps_per_direction"]
        == 1205862400
    )
    assert spec["formal_gate_binding"]["capture_accepted_round_credit"] == 0
    assert spec["formal_gate_binding"]["required_nonempty_subsets"] == 127
    assert spec["formal_gate_binding"]["required_trace_transactions"] == [
        "AT1", "AT2", "AT3", "AT4", "AT5", "AT6",
    ]
    assert "/authorizations/" in manifest["authorization_root"]
    assert manifest["authorization_outside_attempt"] is True
    assert stat.S_IMODE(material.stat().st_mode) == 0o700
    assert stat.S_IMODE(
        (material / "private_authority").stat().st_mode
    ) == 0o700
    assert stat.S_IMODE(
        (material / "private_authority/main_agent_token").stat().st_mode
    ) == 0o600
    assert stat.S_IMODE(
        (material / "private_credentials").stat().st_mode
    ) == 0o700
    assert all(
        stat.S_IMODE(path.stat().st_mode) == 0o600
        for path in (material / "private_credentials").iterdir()
    )
    assert manifest["formal_r0_authorized"] is False
    assert manifest["accepted_round_authorized"] is False
    assert Path(result["issuer_record"]).is_file()


def test_runtime_60min_and_expired_or_weak_private_authority_fail_closed(
    tmp_path,
):
    mod = module()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    private = token(tmp_path)
    lease_path = tmp_path / "issuer_lease.json"
    mod.prepare_lease(V9, private, lease_path, now)
    with pytest.raises(mod.IssuerError, match="10..30"):
        mod.issue(V9, private, lease_path, tmp_path / "out", 60)
    with pytest.raises(mod.IssuerError, match="expired"):
        mod.load_lease(
            lease_path, private, now=now + timedelta(minutes=61)
        )
    private.chmod(0o640)
    with pytest.raises(mod.IssuerError, match="metadata"):
        mod.load_lease(lease_path, private, now=now)


def test_jit_builder_refuses_v9_with_non_self_contained_runtime_test_runner():
    """The inherited runner derives a root incompatible with v9 packaging."""
    with pytest.raises(
        jit_builder.BuildError,
        match="embedded runtime self-test runner is not relocatable",
    ):
        jit_builder.verify_v9()
