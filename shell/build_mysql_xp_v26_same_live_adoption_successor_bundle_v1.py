#!/usr/bin/env python3
"""Build immutable XP v26 systemd/Python/same-live adoption successor."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile


ROOT = Path(__file__).resolve().parents[1]; HERE = Path(__file__).resolve()
TARGET_ID = "b8e574ea5d2a47faae7cbe1776c7407c6e4b6122b4567c8a9b7dde5513382dcc"
TARGET_SHA = "1baf26d67479544c2f9aa13e4bd1d9a87fccaec9e6912d0570de3780d896df02"
TARGET = ROOT / "artifacts/mysql_xp_node02_successor_v26" / TARGET_ID
PREDECESSOR_ID = "34802b8cf219d055dd8e1a8ac31474f919343dc6beb7dc7cae31c196ad5ae185"
PREDECESSOR_SHA = "f3b1de510173a0f91c53b98fd3764852a6265bace0fb0015e3d649cb90812c24"
PREDECESSOR = ROOT / "artifacts/mysql_xp_v26_postpublication_handoff_v1" / PREDECESSOR_ID
OUT = ROOT / "artifacts/mysql_xp_v26_same_live_adoption_successor_v1"
MATERIALIZER_SOURCE = ROOT / "shell/materialize_mysql_xp_v26_same_live_adoption_successor_v1.py"
MATERIALIZER = "materializer/mysql_xp_v26_same_live_adoption_successor_v1.py"
REPORT_SOURCE = ROOT / "preflight/xp_v26_node02_systemd219_python_and_same_live_upgrade_audit_20260803.md"
REPORT = "preflight/xp_v26_node02_systemd219_python_and_same_live_upgrade_audit_20260803.md"
TEST_SOURCES = (
    ROOT / "tests/test_mysql_xp_v26_same_live_adoption_successor_v1.py",
    ROOT / "tests/test_mysql_xp_v26_same_live_adoption_successor_bundle_v1.py",
)
BUILDER = "builder/build_mysql_xp_v26_same_live_adoption_successor_bundle_v1.py"


class BuildError(RuntimeError): pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def sha_bytes(value): return hashlib.sha256(value).hexdigest()


def sha_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_identity(path, bundle_id, manifest_sha, schema, pretty):
    manifest_path = path / "bundle_manifest.json"
    if path.name != bundle_id or sha_file(manifest_path) != manifest_sha:
        raise BuildError("predecessor identity differs")
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    unsigned = dict(value); observed = unsigned.pop("bundle_id", None)
    payload = ((json.dumps(unsigned, sort_keys=True, indent=2) + "\n").encode()
               if pretty else canonical(unsigned))
    if observed != bundle_id or sha_bytes(payload) != bundle_id or value.get("schema_version") != schema:
        raise BuildError("predecessor manifest differs")
    return value


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        os.chmod(path, 0o555 if path.is_dir() else 0o444)
    os.chmod(root, 0o555)


def closure(root):
    result = {}
    for path in root.rglob("*"):
        if path.is_symlink(): raise BuildError("symlink prohibited")
        relative = path.relative_to(root).as_posix()
        if path.is_file() and relative not in {"bundle_manifest.json", "bundle_manifest.json.sha256"}:
            result[relative] = sha_file(path)
    return dict(sorted(result.items()))


def build():
    verify_identity(TARGET, TARGET_ID, TARGET_SHA,
                    "mysql_xp_node02_successor_bundle_v26", True)
    verify_identity(PREDECESSOR, PREDECESSOR_ID, PREDECESSOR_SHA,
                    "mysql_xp_v26_postpublication_handoff_bundle_v1", False)
    sources = {
        MATERIALIZER: MATERIALIZER_SOURCE, REPORT: REPORT_SOURCE,
        "tests/" + TEST_SOURCES[0].name: TEST_SOURCES[0],
        "tests/" + TEST_SOURCES[1].name: TEST_SOURCES[1], BUILDER: HERE,
    }
    content = {}
    for name, path in sources.items():
        if path.is_symlink() or not path.is_file(): raise BuildError("source absent: " + name)
        content[name] = path.read_bytes()
    files = {name: sha_bytes(data) for name, data in sorted(content.items())}
    identity = {
        "schema_version": "mysql_xp_v26_same_live_adoption_successor_bundle_v1",
        "status": "PASS_IMMUTABLE_LOCAL_COMPATIBILITY_SUCCESSOR_NOT_RUN",
        "immutable": True, "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "target_bundle_id": TARGET_ID, "target_manifest_sha256": TARGET_SHA,
        "predecessor_handoff_bundle_id": PREDECESSOR_ID,
        "predecessor_handoff_manifest_sha256": PREDECESSOR_SHA,
        "materializer_sha256": files[MATERIALIZER], "audit_sha256": files[REPORT],
        "systemd_version_required": 219,
        "systemd_wait_collect_pipe_prohibited": True,
        "post_adoption_python_minimum": [3, 7, 0],
        "v26_bootstrap_adoption_python36_compatible": True,
        "v26_complete_post_adoption_python36_compatible": False,
        "v24_v25_zero_restart_upgrade_possible": False,
        "exact_live_v26_pair_required": True,
        "private_material_in_bundle": False,
        "materializer_executes_workload": False,
        "materializer_mutates_remote_state": False,
        "formal_launch_authorized": False, "accepted_round_authorized": False,
        "accepted_rounds_credit": 0, "remote_actions": 0, "files": files,
    }
    bundle_id = sha_bytes(canonical(identity)); manifest = dict(identity, bundle_id=bundle_id)
    OUT.mkdir(parents=True, exist_ok=True); destination = OUT / bundle_id
    if not destination.exists():
        stage = Path(tempfile.mkdtemp(prefix=".xp-v26-same-live-", dir=OUT))
        try:
            for name, data in sorted(content.items()):
                path = stage / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
            raw = canonical(manifest); (stage / "bundle_manifest.json").write_bytes(raw)
            (stage / "bundle_manifest.json.sha256").write_text(
                sha_bytes(raw) + "  bundle_manifest.json\n", encoding="ascii")
            freeze(stage); os.replace(stage, destination)
        finally:
            if stage.exists(): shutil.rmtree(stage)
    if closure(destination) != files: raise BuildError("bundle closure differs")
    for path in (destination, *destination.rglob("*")):
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o222:
            raise BuildError("bundle is mutable")
    print(json.dumps({"status": manifest["status"], "bundle_id": bundle_id,
        "bundle": str(destination), "manifest_sha256": sha_file(destination / "bundle_manifest.json"),
        "files": len(files), "formal_launch_authorized": False,
        "accepted_round_authorized": False, "remote_actions": 0}, sort_keys=True))
    return destination


if __name__ == "__main__":
    try: build()
    except (BuildError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise SystemExit("NOT_READY: " + str(exc))
