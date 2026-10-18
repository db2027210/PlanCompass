from __future__ import annotations

import hashlib
import json
from pathlib import Path
import stat


ROOT = Path(__file__).resolve().parents[1]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_manifest_binds_lineage_and_fail_closed_boundary():
    manifest = json.loads((ROOT / "bundle_manifest.json").read_text())
    assert manifest["schema_version"] == "mysql_xp_v26_fresh_pair_gate_successor_bundle_v1"
    assert manifest["target_bundle_id"] == "b8e574ea5d2a47faae7cbe1776c7407c6e4b6122b4567c8a9b7dde5513382dcc"
    assert manifest["predecessor_handoff_bundle_id"] == "34802b8cf219d055dd8e1a8ac31474f919343dc6beb7dc7cae31c196ad5ae185"
    assert manifest["compatibility_bundle_id"] == "e14797c14485ac49e4cf8765533d9cbb4bf154f35c5793cf166568757f21c452"
    assert manifest["formal_scope"] == "mysql_only"
    assert manifest["cross_engine_comparison_prohibited"] is True
    assert manifest["postgresql_mutation_prohibited"] is True
    assert manifest["capture_interpreter_hardcode_repaired"] is True
    assert manifest["target_collector_contract_consistent"] is False
    assert manifest["v26_debug_data_plane_producer_present"] is False
    assert manifest["fresh_v26_capture_r0_ready_after_runtime_gates"] is True
    assert manifest["candidate_top5_launch_ready"] is False
    assert manifest["ten_accepted_rounds_launch_ready"] is False
    assert manifest["formal_launch_authorized"] is False
    assert manifest["accepted_round_authorized"] is False
    assert manifest["accepted_rounds_credit"] == 0
    assert manifest["remote_actions"] == 0


def test_file_closure_identity_and_immutability():
    manifest = json.loads((ROOT / "bundle_manifest.json").read_text())
    actual = {}
    for path in ROOT.rglob("*"):
        assert not path.is_symlink()
        assert not stat.S_IMODE(path.stat().st_mode) & 0o222
        relative = path.relative_to(ROOT).as_posix()
        if path.is_file() and relative not in {"bundle_manifest.json", "bundle_manifest.json.sha256"}:
            actual[relative] = sha(path)
    assert actual == manifest["files"]
    unsigned = dict(manifest)
    bundle_id = unsigned.pop("bundle_id")
    payload = json.dumps(unsigned, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode()
    assert hashlib.sha256(payload).hexdigest() == bundle_id == ROOT.name


def test_public_only_and_python_is_not_unbound():
    materializer = (ROOT / "shell/materialize_mysql_xp_v26_fresh_pair_capture_argv_v2.py").read_text()
    report = (ROOT / "preflight/xp_v26_fresh_pair_end_to_end_gate_audit_20260803.md").read_text()
    assert "argv = [\n        str(interpreter)" in materializer
    assert '"python3", str(bundle' not in materializer
    assert "private_material_transferred\": False" in materializer
    assert "No private" in report
    assert "PostgreSQL mutation is" in report
    assert "accepted credit 0" in report
