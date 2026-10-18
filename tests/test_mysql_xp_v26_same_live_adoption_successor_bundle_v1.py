from __future__ import annotations

import hashlib
import json
from pathlib import Path
import stat


ROOT = Path(__file__).resolve().parents[1]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_successor_manifest_bindings_and_zero_authority():
    value = json.loads((ROOT / "bundle_manifest.json").read_text())
    assert value["schema_version"] == "mysql_xp_v26_same_live_adoption_successor_bundle_v1"
    assert value["target_bundle_id"] == "b8e574ea5d2a47faae7cbe1776c7407c6e4b6122b4567c8a9b7dde5513382dcc"
    assert value["target_manifest_sha256"] == "1baf26d67479544c2f9aa13e4bd1d9a87fccaec9e6912d0570de3780d896df02"
    assert value["predecessor_handoff_bundle_id"] == "34802b8cf219d055dd8e1a8ac31474f919343dc6beb7dc7cae31c196ad5ae185"
    assert value["systemd_version_required"] == 219
    assert value["post_adoption_python_minimum"] == [3, 7, 0]
    assert value["v24_v25_zero_restart_upgrade_possible"] is False
    assert value["private_material_in_bundle"] is False
    assert value["materializer_executes_workload"] is False
    assert value["formal_launch_authorized"] is False
    assert value["accepted_round_authorized"] is False
    assert value["accepted_rounds_credit"] == 0
    assert value["remote_actions"] == 0


def test_successor_closure_is_immutable():
    value = json.loads((ROOT / "bundle_manifest.json").read_text())
    actual = {}
    for path in ROOT.rglob("*"):
        assert not path.is_symlink()
        assert not stat.S_IMODE(path.stat().st_mode) & 0o222
        relative = path.relative_to(ROOT).as_posix()
        if path.is_file() and relative not in {
            "bundle_manifest.json", "bundle_manifest.json.sha256"
        }:
            actual[relative] = sha(path)
    assert actual == value["files"]
    unsigned = dict(value); bundle_id = unsigned.pop("bundle_id")
    payload = json.dumps(unsigned, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode()
    assert hashlib.sha256(payload).hexdigest() == bundle_id == ROOT.name

