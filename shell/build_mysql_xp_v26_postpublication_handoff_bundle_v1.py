#!/usr/bin/env python3
"""Build the immutable, public-only XP v26 post-publication handoff bundle."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
TARGET_ID = "b8e574ea5d2a47faae7cbe1776c7407c6e4b6122b4567c8a9b7dde5513382dcc"
TARGET_SHA = "1baf26d67479544c2f9aa13e4bd1d9a87fccaec9e6912d0570de3780d896df02"
TARGET = ROOT / "artifacts/mysql_xp_node02_successor_v26" / TARGET_ID
OUT = ROOT / "artifacts/mysql_xp_v26_postpublication_handoff_v1"
MATERIALIZER = "shell/materialize_mysql_xp_v26_capture_argv_v1.py"
RUNBOOK = "preflight/xp_v26_postpublication_fresh_r0_shortest_runbook_20260803.md"
TESTS = (
    "tests/test_materialize_mysql_xp_v26_capture_argv_v1.py",
    "tests/test_mysql_xp_v26_postpublication_handoff_bundle_v1.py",
)
BUILDER = "builder/build_mysql_xp_v26_postpublication_handoff_bundle_v1.py"


class BuildError(RuntimeError):
    pass


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def target_canonical(value) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2,
                       ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8")


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_target() -> dict:
    path = TARGET / "bundle_manifest.json"
    if TARGET.name != TARGET_ID or not path.is_file() or path.is_symlink():
        raise BuildError("target XP v26 bundle is absent")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    unsigned = dict(manifest); bundle_id = unsigned.pop("bundle_id", None)
    if (bundle_id != TARGET_ID or sha_file(path) != TARGET_SHA
            or sha_bytes(target_canonical(unsigned)) != TARGET_ID
            or manifest.get("schema_version") != "mysql_xp_node02_successor_bundle_v26"):
        raise BuildError("target XP v26 identity differs")
    actual = {}
    for candidate in TARGET.rglob("*"):
        if candidate.is_symlink():
            raise BuildError("target XP v26 contains a symlink")
        relative = candidate.relative_to(TARGET).as_posix()
        if candidate.is_file() and relative not in {
            "bundle_manifest.json", "bundle_manifest.json.sha256"
        }:
            actual[relative] = sha_file(candidate)
    if actual != manifest.get("files"):
        raise BuildError("target XP v26 file closure differs")
    return manifest


def freeze(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        os.chmod(path, 0o555 if path.is_dir() else 0o444)
    os.chmod(root, 0o555)


def relative_files(root: Path) -> dict:
    result = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise BuildError("handoff contains a symlink")
        relative = path.relative_to(root).as_posix()
        if path.is_file() and relative not in {
            "bundle_manifest.json", "bundle_manifest.json.sha256"
        }:
            result[relative] = sha_file(path)
    return dict(sorted(result.items()))


def build() -> Path:
    target = verify_target()
    sources = {
        MATERIALIZER: ROOT / MATERIALIZER,
        RUNBOOK: ROOT / RUNBOOK,
        TESTS[0]: ROOT / TESTS[0],
        TESTS[1]: ROOT / TESTS[1],
        BUILDER: HERE,
    }
    content = {}
    for name, path in sources.items():
        if path.is_symlink() or not path.is_file():
            raise BuildError("handoff source is absent: " + name)
        content[name] = path.read_bytes()
    files = {name: sha_bytes(value) for name, value in sorted(content.items())}
    identity = {
        "schema_version": "mysql_xp_v26_postpublication_handoff_bundle_v1",
        "status": "PASS_IMMUTABLE_LOCAL_HANDOFF_NOT_LAUNCHED",
        "immutable": True,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "target_bundle_id": TARGET_ID,
        "target_manifest_sha256": TARGET_SHA,
        "target_schema_version": target["schema_version"],
        "target_formal_launch_authorized": target["formal_launch_authorized"],
        "target_accepted_round_authorized": target["accepted_round_authorized"],
        "capture_argv_materializer_sha256": files[MATERIALIZER],
        "runbook_sha256": files[RUNBOOK],
        "private_material_in_bundle": False,
        "private_material_transfer_authorized": False,
        "node02_local_private_generation_required": True,
        "materializer_executes_workload": False,
        "data_plane_started": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
        "remote_actions": 0,
        "files": files,
    }
    bundle_id = sha_bytes(canonical(identity))
    manifest = dict(identity, bundle_id=bundle_id)
    destination = OUT / bundle_id
    OUT.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        stage = Path(tempfile.mkdtemp(prefix=".xp-v26-handoff-", dir=OUT))
        try:
            for name, value in sorted(content.items()):
                path = stage / name; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(value)
            raw = canonical(manifest)
            (stage / "bundle_manifest.json").write_bytes(raw)
            (stage / "bundle_manifest.json.sha256").write_text(
                sha_bytes(raw) + "  bundle_manifest.json\n", encoding="ascii")
            freeze(stage); os.replace(stage, destination)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    if relative_files(destination) != files:
        raise BuildError("handoff file closure differs")
    for path in (destination, *destination.rglob("*")):
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o222:
            raise BuildError("handoff is mutable")
    print(json.dumps({
        "status": manifest["status"], "bundle_id": bundle_id,
        "bundle": str(destination), "manifest_sha256": sha_file(
            destination / "bundle_manifest.json"),
        "files": len(files), "formal_launch_authorized": False,
        "accepted_round_authorized": False, "remote_actions": 0,
    }, sort_keys=True))
    return destination


if __name__ == "__main__":
    try:
        build()
    except (BuildError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise SystemExit("NOT_READY: " + str(exc))
