from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load():
    path = ROOT / "shell/materialize_mysql_xp_v26_fresh_pair_capture_argv_v2.py"
    spec = importlib.util.spec_from_file_location("xp_v26_capture_plan_v2_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def seal(path: Path) -> None:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_name(path.name + ".sha256").write_text(
        digest + "  " + path.name + "\n", encoding="ascii")


def fixture(tmp_path, monkeypatch):
    mod = load()
    bundle = tmp_path / mod.BUNDLE_ID
    (bundle / "launch").mkdir(parents=True)
    (bundle / "qualification/config").mkdir(parents=True)
    manifest = bundle / "bundle_manifest.json"
    manifest.write_text("{}")
    raw = bundle / mod.RAW_RELATIVE
    raw.write_text("# sealed\n")
    (bundle / "qualification/config/hybench_xp_qualification.properties").write_text("# public\n")
    host_tmpfs = tmp_path / "attempt/tmpfs"
    host_tmpfs.mkdir(parents=True)
    runtime = tmp_path / "jdk"
    (runtime / "bin").mkdir(parents=True)
    java = runtime / "bin/java"
    java.write_bytes(b"java")
    interpreter = tmp_path / "python3.11"
    interpreter.write_bytes(b"attested python")
    adoption = tmp_path / "adoption.json"
    adoption.write_text("{}")
    seal(adoption)
    formal = tmp_path / "formal.json"
    formal.write_text("{}")
    runtime_authority = tmp_path / "runtime-authority.json"
    runtime_authority.write_text("{}")
    capture_authority = tmp_path / "capture-authority.json"
    capture_authority.write_text("{}")
    identity = {"client_container_id": "c" * 64,
                "database_container_id": "d" * 64}
    adoption_value = {
        "host_output_tmpfs": {"path": str(host_tmpfs)},
        "runtime_publication": {"live_tree_readback": {"root": str(runtime)}},
    }
    fake = SimpleNamespace(
        verify_bundle=lambda unused: {"bundle_id": mod.BUNDLE_ID},
        verify_adoption=lambda *unused: (adoption_value, identity),
    )
    monkeypatch.setattr(mod, "load_materializer", lambda unused: fake)
    real_sha = mod.sha256

    def fake_sha(path):
        path = Path(path)
        if path == manifest:
            return mod.MANIFEST_SHA256
        if path == java:
            return mod.JAVA_SHA256
        if path == raw:
            return mod.RAW_SHA256
        return real_sha(path)

    monkeypatch.setattr(mod, "sha256", fake_sha)
    compatibility = tmp_path / "compatibility.json"
    compatibility.write_text(json.dumps({
        "schema_version": mod.COMPATIBILITY_SCHEMA,
        "status": mod.COMPATIBILITY_STATUS,
        "target_bundle_id": mod.BUNDLE_ID,
        "target_manifest_sha256": mod.MANIFEST_SHA256,
        "source_adoption_bundle_id": mod.BUNDLE_ID,
        "source_adoption_sha256": real_sha(adoption),
        "client_container_id": identity["client_container_id"],
        "database_container_id": identity["database_container_id"],
        "same_live_pid_starttime_pass": True,
        "v26_adoption_materialized": True,
        "capture_r0_compatibility_ready": True,
        "post_adoption_python": {
            "path": str(interpreter),
            "sha256": real_sha(interpreter),
            "version": [3, 11, 7],
            "version_pass": True,
            "critical_compile_sha256": {mod.RAW_RELATIVE: mod.RAW_SHA256},
        },
    }))
    seal(compatibility)
    return (mod, bundle, adoption, compatibility, formal, runtime_authority,
            capture_authority, host_tmpfs, interpreter)


def test_argv_binds_attested_python_and_no_private_material(tmp_path, monkeypatch):
    values = fixture(tmp_path, monkeypatch)
    mod, bundle, adoption, compatibility, formal, runtime_auth, capture_auth, tmpfs, interpreter = values
    output = tmpfs / "capture-argv.json"
    result = mod.materialize(bundle, adoption, compatibility, formal,
        runtime_auth, capture_auth, "xp-capture-0123456789",
        tmpfs / "capture", tmpfs / "transition", output)
    argv = json.loads(output.read_text())
    assert argv[0] == str(interpreter)
    assert argv[0] != "python3"
    assert result["post_adoption_python_sha256"] == mod.sha256(interpreter)
    assert result["data_plane_started"] is False
    assert result["private_material_transferred"] is False
    assert not any("main_agent_token" in row or "private_credentials" in row
                   or "/run/mysql-xp-private" in row for row in argv)


def test_rejects_compatibility_adoption_hash_mismatch(tmp_path, monkeypatch):
    values = fixture(tmp_path, monkeypatch)
    mod, bundle, adoption, compatibility, formal, runtime_auth, capture_auth, tmpfs, _ = values
    document = json.loads(compatibility.read_text())
    document["source_adoption_sha256"] = "0" * 64
    compatibility.write_text(json.dumps(document))
    seal(compatibility)
    with pytest.raises(mod.PlanError, match="Python/adoption"):
        mod.materialize(bundle, adoption, compatibility, formal, runtime_auth,
            capture_auth, "xp-capture-0123456789", tmpfs / "capture",
            tmpfs / "transition", tmpfs / "argv.json")


def test_rejects_python_below_37(tmp_path, monkeypatch):
    values = fixture(tmp_path, monkeypatch)
    mod, bundle, adoption, compatibility, formal, runtime_auth, capture_auth, tmpfs, _ = values
    document = json.loads(compatibility.read_text())
    document["post_adoption_python"]["version"] = [3, 6, 8]
    compatibility.write_text(json.dumps(document))
    seal(compatibility)
    with pytest.raises(mod.PlanError, match="Python/adoption"):
        mod.materialize(bundle, adoption, compatibility, formal, runtime_auth,
            capture_auth, "xp-capture-0123456789", tmpfs / "capture",
            tmpfs / "transition", tmpfs / "argv.json")


def test_rejects_changed_interpreter(tmp_path, monkeypatch):
    values = fixture(tmp_path, monkeypatch)
    mod, bundle, adoption, compatibility, formal, runtime_auth, capture_auth, tmpfs, interpreter = values
    interpreter.write_bytes(b"changed")
    with pytest.raises(mod.PlanError, match="Python/adoption"):
        mod.materialize(bundle, adoption, compatibility, formal, runtime_auth,
            capture_auth, "xp-capture-0123456789", tmpfs / "capture",
            tmpfs / "transition", tmpfs / "argv.json")


def test_audit_fail_closes_missing_v26_debug_producer_and_stale_collector():
    project = next((candidate for candidate in (ROOT, *ROOT.parents)
                    if (candidate / "artifacts/mysql_xp_node02_successor_v26").is_dir()), None)
    assert project is not None
    bundle = project / "artifacts/mysql_xp_node02_successor_v26" / load().BUNDLE_ID
    manifest = json.loads((bundle / "bundle_manifest.json").read_text())
    contract = json.loads((bundle / "xp_executable_launch_contract_v3.json").read_text())
    launch_names = {path.name for path in (bundle / "launch").iterdir() if path.is_file()}
    assert manifest["collector_sha256"] == "ac78a618881d8adcb9ed274d4f5eef173242ca893c2b43d9d711a3701ea883c6"
    assert contract["bidirectional_qualification_gate"]["collector_sha256"] == \
        "90a527a8ceb003887064cf8e2042965ffd68a497698814373bbf95c7bc225f49"
    assert not any("debug" in name and "materializer" not in name for name in launch_names)
    report = (ROOT / "preflight/xp_v26_fresh_pair_end_to_end_gate_audit_20260803.md").read_text()
    assert "2^7-1 = 127" in report
    assert "data-plane **producer**" in report
    assert "accepted credit 0" in report
