from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load():
    path = ROOT / "materializer/mysql_xp_v26_same_live_adoption_successor_v1.py"
    spec = importlib.util.spec_from_file_location("xp_same_live_successor", path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def setup(tmp_path, monkeypatch, source_id):
    mod = load(); bundle = tmp_path / mod.TARGET_ID; bundle.mkdir()
    manifest = bundle / "bundle_manifest.json"; manifest.write_text("{}")
    adoption_path = tmp_path / "adoption.json"; adoption_path.write_text("{}")
    adoption = {"schema_version": "mysql_xp_node02_adopted_service_authority_v1",
                "status": "PASS_ADOPTED_SAME_LIVE_SERVICE", "bundle_id": source_id}
    monkeypatch.setattr(mod.socket, "gethostname", lambda: "node02")
    real_sha = mod.sha256
    monkeypatch.setattr(mod, "sha256", lambda path: (
        mod.TARGET_SHA if Path(path) == manifest else real_sha(Path(path))))
    monkeypatch.setattr(mod, "sealed", lambda *unused: adoption)
    monkeypatch.setattr(mod, "role_identity", lambda unused, role: {
        "Id": ("d" if role == "database" else "c") * 64,
        "Config": {"Labels": {"ra.bundle_id": source_id}}})
    monkeypatch.setattr(mod, "client_bundle_binding", lambda unused: {
        "label_bundle_id": source_id,
        "mount_source": mod.REMOTE_BUNDLE_PARENT + "/" + source_id,
        "mount_rw": False, "mount_type": "bind"})
    written = {}
    monkeypatch.setattr(mod, "atomic", lambda path, value: written.update(value))
    return mod, bundle, adoption_path, written


def test_v25_live_pair_cannot_be_relabelled_or_remounted(monkeypatch, tmp_path):
    mod, bundle, adoption, result = setup(tmp_path, monkeypatch, load().V25_ID)
    mod.materialize(bundle, adoption, "python3", tmp_path / "out.json")
    assert result["status"] == "REQUIRES_FRESH_V26_PAIR_NO_ZERO_RESTART_UPGRADE"
    assert result["zero_restart_upgrade_possible"] is False
    assert result["fresh_v26_database_and_client_required"] is True
    assert result["v26_adoption_materialized"] is False
    assert result["remote_mutation_performed"] is False


def test_exact_v26_pair_requires_python_and_systemd_readback(monkeypatch, tmp_path):
    mod, bundle, adoption, result = setup(tmp_path, monkeypatch, load().TARGET_ID)
    monkeypatch.setattr(mod, "python_readback", lambda *unused: {
        "version": [3, 11, 9], "version_pass": True})
    monkeypatch.setattr(mod, "systemd_readback", lambda: {
        "version": 219, "version_pass": True})
    mod.materialize(bundle, adoption, "/sealed/python3", tmp_path / "out.json")
    assert result["status"] == "PASS_STRICT_SAME_LIVE_V26_ADOPTION_COMPATIBILITY"
    assert result["capture_r0_compatibility_ready"] is True
    assert result["container_restart_performed"] is False
    assert result["formal_launch_authorized"] is False


def test_python36_is_fail_closed_for_post_adoption(monkeypatch, tmp_path):
    mod, bundle, adoption, result = setup(tmp_path, monkeypatch, load().TARGET_ID)
    monkeypatch.setattr(mod, "python_readback", lambda *unused: {
        "version": [3, 6, 8], "version_pass": False})
    monkeypatch.setattr(mod, "systemd_readback", lambda: {
        "version": 219, "version_pass": True})
    mod.materialize(bundle, adoption, "python3", tmp_path / "out.json")
    assert result["status"] == "V26_SAME_LIVE_HOST_COMPATIBILITY_BLOCKED"
    assert result["v26_adoption_materialized"] is False
    assert result["capture_r0_compatibility_ready"] is False


def test_materializer_is_python36_shaped_and_read_only():
    source = (ROOT / "materializer/mysql_xp_v26_same_live_adoption_successor_v1.py").read_text()
    assert "from __future__ import annotations" not in source
    assert "text=True" not in source
    assert "universal_newlines=True" in source
    for prohibited in ("docker start", "docker stop", "docker create", "docker update",
                       "systemd-run --unit"):
        assert prohibited not in source


def test_systemd219_help_capabilities_are_bound(monkeypatch):
    mod = load()
    help_text = "--quiet --service-type=TYPE --remain-after-exit --unit=UNIT"
    monkeypatch.setattr(mod, "run", lambda argv: (
        "systemd 219\n" if argv[-1] == "--version" else help_text))
    value = mod.systemd_readback()
    assert value["version_pass"] is True
    assert value["required_help_options_pass"] is True
    monkeypatch.setattr(mod, "run", lambda argv: (
        "systemd 219\n" if argv[-1] == "--version" else "--quiet"))
    assert mod.systemd_readback()["version_pass"] is False
