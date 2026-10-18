from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import sys
import uuid

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load(path: Path):
    spec = importlib.util.spec_from_file_location("xp_v26_" + uuid.uuid4().hex, path)
    module = importlib.util.module_from_spec(spec)
    old = list(sys.path); sys.path.insert(0, str(path.parent))
    try: spec.loader.exec_module(module)
    finally: sys.path[:] = old
    return module


def test_fresh_java_uses_only_private_properties():
    mod = load(ROOT / "launch/mysql_xp_fresh_r0_executor_v2.py")
    argv = mod.java_argv("a" * 64)
    assert mod.PRIVATE_PROPERTIES in argv
    assert not any("/opt/mysql-xp-bundle/qualification/config/hybench_xp_qualification.properties" == row for row in argv)


def test_fresh_private_readback_rejects_hash_or_mode(monkeypatch):
    mod = load(ROOT / "launch/mysql_xp_fresh_r0_executor_v2.py")
    attempt = "xp-v26-test-attempt"
    expected = hashlib.sha256(b"private-properties").hexdigest()
    adoption = {"attempt_id": attempt, "private_credentials": {
        "destination": "/run/mysql-xp-private",
        "qualification_config": "hybench_xp_qualification.properties",
        "directory_mode": "0700", "file_mode": "0600",
        "user": "ra_xp_qual_" + hashlib.sha256(attempt.encode()).hexdigest()[:20],
        "content_sha256": {"hybench_xp_qualification.properties": expected},
    }}
    answers = iter([expected + "  " + mod.PRIVATE_PROPERTIES, "600"])
    monkeypatch.setattr(mod, "docker", lambda *unused: next(answers))
    assert mod.client_private_properties_readback("c" * 64, adoption)["observed_sha256"] == expected
    answers = iter([expected + "  " + mod.PRIVATE_PROPERTIES, "644"])
    monkeypatch.setattr(mod, "docker", lambda *unused: next(answers))
    with pytest.raises(mod.ExecutorError, match="readback"):
        mod.client_private_properties_readback("c" * 64, adoption)


def test_capture_java_uses_only_private_properties():
    source = (ROOT / "launch/mysql_xp_raw_evidence_executor_v1.py").read_text()
    assert 'CLIENT_PRIVATE_PROPERTIES = "/run/mysql-xp-private/hybench_xp_qualification.properties"' in source
    assert 'CLIENT_BUNDLE + "/" + PROPERTIES,' not in source
    assert "private_properties_before = _private_properties_readback" in source
    assert "private_properties_after = (" in source


def test_producer_binds_container_readback_to_adoption_hash():
    mod = load(ROOT / "launch/mysql_xp_formal_evidence_producer_v1.py")
    attempt = "xp-v26-producer-attempt"
    expected = hashlib.sha256(b"private-properties").hexdigest()
    adoption = {"attempt_id": attempt, "private_credentials": {
        "destination": "/run/mysql-xp-private",
        "qualification_config": "hybench_xp_qualification.properties",
        "directory_mode": "0700", "file_mode": "0600",
        "user": "ra_xp_qual_" + hashlib.sha256(attempt.encode()).hexdigest()[:20],
        "content_sha256": {"hybench_xp_qualification.properties": expected},
    }}
    readback = {
        "path_in_client": "/run/mysql-xp-private/hybench_xp_qualification.properties",
        "observed_sha256": expected, "observed_mode": "600",
        "readback_transport": "exact_adopted_client_docker_exec_stdout_v1",
        "public_bundle_properties_prohibited": True,
    }
    ledger = {"private_workload_properties": {
        "pre_readback": readback, "post_readback": dict(readback),
        "unchanged_during_workload": True,
    }}
    assert mod._private_properties_closure(ledger, adoption)
    ledger["private_workload_properties"]["post_readback"]["observed_sha256"] = "0" * 64
    assert not mod._private_properties_closure(ledger, adoption)


def test_dynamic_gate_token_is_file_only(tmp_path: Path):
    mod = load(ROOT / "qualification/mysql_distributed_live_readback_qualification_v1.py")
    token = tmp_path / "token"; token.write_text("T" * 64 + "\n", encoding="ascii")
    token.chmod(0o600)
    assert mod.read_private_token(token) == "T" * 64
    token.chmod(0o644)
    with pytest.raises(mod.QualificationError, match="token file"):
        mod.read_private_token(token)
    token.chmod(0o600)
    token.write_text("contains whitespace\ninside\n", encoding="ascii")
    with pytest.raises(mod.QualificationError, match="content"):
        mod.read_private_token(token)
    real = tmp_path / "real"; real.write_text("T" * 64, encoding="ascii"); real.chmod(0o600)
    link = tmp_path / "link"; link.symlink_to(real)
    with pytest.raises(mod.QualificationError, match="token file"):
        mod.read_private_token(link)
    source = Path(mod.__file__).read_text()
    assert 'parser.add_argument("--main-agent-token")' not in source
    assert 'parser.add_argument("--main-agent-token-file", type=Path)' in source


def test_v26_manifest_zero_authority_and_private_binding():
    manifest = json.loads((ROOT / "bundle_manifest.json").read_text())
    assert manifest["schema_version"] == "mysql_xp_node02_successor_bundle_v26"
    assert manifest["private_workload_properties_path"] == "/run/mysql-xp-private/hybench_xp_qualification.properties"
    assert manifest["dynamic_gate_token_transport"] == "private_file_only"
    assert manifest["formal_launch_authorized"] is False
    assert manifest["accepted_round_authorized"] is False
    for path in (ROOT, *ROOT.rglob("*")):
        assert not path.is_symlink()
        assert not stat.S_IMODE(path.stat().st_mode) & 0o222
