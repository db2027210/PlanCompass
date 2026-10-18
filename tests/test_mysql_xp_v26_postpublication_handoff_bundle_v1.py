from __future__ import annotations

import hashlib
import json
from pathlib import Path
import stat


ROOT = Path(__file__).resolve().parents[1]
TARGET_ID = "b8e574ea5d2a47faae7cbe1776c7407c6e4b6122b4567c8a9b7dde5513382dcc"
TARGET_SHA = "1baf26d67479544c2f9aa13e4bd1d9a87fccaec9e6912d0570de3780d896df02"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_handoff_manifest_binds_exact_v26_and_has_zero_authority():
    manifest = json.loads((ROOT / "bundle_manifest.json").read_text())
    assert manifest["schema_version"] == "mysql_xp_v26_postpublication_handoff_bundle_v1"
    assert manifest["target_bundle_id"] == TARGET_ID
    assert manifest["target_manifest_sha256"] == TARGET_SHA
    assert manifest["formal_scope"] == "mysql_only"
    assert manifest["cross_engine_comparison_prohibited"] is True
    assert manifest["postgresql_mutation_prohibited"] is True
    assert manifest["private_material_in_bundle"] is False
    assert manifest["private_material_transfer_authorized"] is False
    assert manifest["materializer_executes_workload"] is False
    assert manifest["formal_launch_authorized"] is False
    assert manifest["accepted_round_authorized"] is False
    assert manifest["accepted_rounds_credit"] == 0
    assert manifest["remote_actions"] == 0


def test_handoff_file_closure_and_immutability():
    manifest = json.loads((ROOT / "bundle_manifest.json").read_text())
    actual = {}
    for path in ROOT.rglob("*"):
        assert not path.is_symlink()
        assert not stat.S_IMODE(path.stat().st_mode) & 0o222
        relative = path.relative_to(ROOT).as_posix()
        if path.is_file() and relative not in {
            "bundle_manifest.json", "bundle_manifest.json.sha256"
        }:
            actual[relative] = sha(path)
    assert actual == manifest["files"]
    unsigned = dict(manifest); bundle_id = unsigned.pop("bundle_id")
    payload = json.dumps(unsigned, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode()
    assert hashlib.sha256(payload).hexdigest() == bundle_id == ROOT.name


def test_handoff_contains_only_public_paths_and_no_secret_values():
    materializer = (ROOT / "shell/materialize_mysql_xp_v26_capture_argv_v1.py").read_text()
    runbook = (ROOT / "preflight/xp_v26_postpublication_fresh_r0_shortest_runbook_20260803.md").read_text()
    assert "subprocess" not in materializer
    assert "private_material_transferred\": False" in materializer
    assert "--main-agent-token-file" in runbook
    assert "--main-agent-token " not in runbook
    assert "xbgydx222" not in materializer + runbook
