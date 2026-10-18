from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load():
    path = ROOT / "shell/materialize_mysql_xp_v26_capture_argv_v1.py"
    spec = importlib.util.spec_from_file_location("xp_v26_capture_plan_test", path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def fixture(tmp_path, monkeypatch):
    mod = load()
    bundle = tmp_path / mod.BUNDLE_ID
    (bundle / "launch").mkdir(parents=True)
    (bundle / "qualification/config").mkdir(parents=True)
    manifest = bundle / "bundle_manifest.json"; manifest.write_text("{}")
    (bundle / "launch/mysql_xp_raw_evidence_executor_v1.py").write_text("# sealed\n")
    props = bundle / "qualification/config/hybench_xp_qualification.properties"
    props.write_text("# public template\n")
    host_tmpfs = tmp_path / "attempt/tmpfs"; host_tmpfs.mkdir(parents=True)
    runtime = tmp_path / "jdk"; (runtime / "bin").mkdir(parents=True)
    java = runtime / "bin/java"; java.write_bytes(b"java")
    adoption = tmp_path / "adoption.json"; adoption.write_text("{}")
    formal = tmp_path / "formal.json"; formal.write_text("{}")
    runtime_authority = tmp_path / "runtime-authority.json"; runtime_authority.write_text("{}")
    capture_authority = tmp_path / "capture-authority.json"; capture_authority.write_text("{}")
    identity = {"client_container_id": "c" * 64}
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
    monkeypatch.setattr(mod, "sha256", lambda path: (
        mod.MANIFEST_SHA256 if Path(path) == manifest else
        mod.JAVA_SHA256 if Path(path) == java else real_sha(Path(path))))
    return mod, bundle, adoption, formal, runtime_authority, capture_authority, host_tmpfs


def test_public_capture_argv_contains_no_private_material(tmp_path, monkeypatch):
    mod, bundle, adoption, formal, runtime_auth, capture_auth, tmpfs = fixture(tmp_path, monkeypatch)
    output = tmpfs / "capture-argv.json"
    result = mod.materialize(bundle, adoption, formal, runtime_auth, capture_auth,
        "xp-capture-0123456789", tmpfs / "capture", tmpfs / "transition", output)
    argv = json.loads(output.read_text())
    assert result["data_plane_started"] is False
    assert result["private_material_transferred"] is False
    assert argv[0] == "python3"
    assert argv[argv.index("--client-container") + 1] == "c" * 64
    assert not any("main_agent_token" in row or "private_credentials" in row
                   or "/run/mysql-xp-private" in row for row in argv)


def test_rejects_output_outside_attempt_tmpfs(tmp_path, monkeypatch):
    mod, bundle, adoption, formal, runtime_auth, capture_auth, tmpfs = fixture(tmp_path, monkeypatch)
    with pytest.raises(mod.PlanError, match="tmpfs"):
        mod.materialize(bundle, adoption, formal, runtime_auth, capture_auth,
            "xp-capture-0123456789", tmpfs / "capture", tmpfs / "transition",
            tmp_path / "outside.json")


def test_runbook_is_v26_only_and_has_complete_zero_credit_chain():
    text = (ROOT / "preflight/xp_v26_postpublication_fresh_r0_shortest_runbook_20260803.md").read_text()
    assert "b8e574ea5d2a47faae7cbe1776c7407c6e4b6122b4567c8a9b7dde5513382dcc" in text
    assert "9783caeb6e5e610f2fa008388d7f82e9e30ee302b7a29af41d5c549a0f8da0db" not in text
    for stage in ("PASS_ADOPTED_SAME_LIVE_SERVICE", "--stage trace",
                  "--compare-bidirectional", "--stage gate",
                  "mysql_xp_fresh_r0_executor_v2.py", "--stage fresh-r0"):
        assert stage in text
    assert "accepted credit `0`" in text
    assert "--main-agent-token-file" in text
    assert "--main-agent-token " not in text
