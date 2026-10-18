#!/usr/bin/env python3
"""Build immutable XP v26 fresh-pair gate/capture successor (no launch)."""
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
HANDOFF_ID = "34802b8cf219d055dd8e1a8ac31474f919343dc6beb7dc7cae31c196ad5ae185"
HANDOFF_SHA = "f3b1de510173a0f91c53b98fd3764852a6265bace0fb0015e3d649cb90812c24"
COMPAT_ID = "e14797c14485ac49e4cf8765533d9cbb4bf154f35c5793cf166568757f21c452"
COMPAT_SHA = "8a851808aab17450ecadb6d99de517562d035582520ffd68838e72c20f3be4fc"
TARGET = ROOT / "artifacts/mysql_xp_node02_successor_v26" / TARGET_ID
HANDOFF = ROOT / "artifacts/mysql_xp_v26_postpublication_handoff_v1" / HANDOFF_ID
COMPAT = ROOT / "artifacts/mysql_xp_v26_same_live_adoption_successor_v1" / COMPAT_ID
OUT = ROOT / "artifacts/mysql_xp_v26_fresh_pair_gate_successor_v1"
MATERIALIZER = "shell/materialize_mysql_xp_v26_fresh_pair_capture_argv_v2.py"
REPORT = "preflight/xp_v26_fresh_pair_end_to_end_gate_audit_20260803.md"
TESTS = (
    "tests/test_materialize_mysql_xp_v26_fresh_pair_capture_argv_v2.py",
    "tests/test_mysql_xp_v26_fresh_pair_gate_successor_bundle_v1.py",
)
BUILDER = "builder/build_mysql_xp_v26_fresh_pair_gate_successor_bundle_v1.py"
ACTUAL_COLLECTOR_SHA = "ac78a618881d8adcb9ed274d4f5eef173242ca893c2b43d9d711a3701ea883c6"
STALE_CONTRACT_COLLECTOR_SHA = "90a527a8ceb003887064cf8e2042965ffd68a497698814373bbf95c7bc225f49"


class BuildError(RuntimeError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def pretty(value):
    return (json.dumps(value, sort_keys=True, indent=2,
                       ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8")


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def sha_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_identity(path, bundle_id, manifest_sha, schema, pretty_manifest=False):
    manifest_path = path / "bundle_manifest.json"
    if path.name != bundle_id or sha_file(manifest_path) != manifest_sha:
        raise BuildError("lineage identity differs")
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    unsigned = dict(value)
    observed = unsigned.pop("bundle_id", None)
    encoded = pretty(unsigned) if pretty_manifest else canonical(unsigned)
    if observed != bundle_id or sha_bytes(encoded) != bundle_id or value.get("schema_version") != schema:
        raise BuildError("lineage manifest differs")
    return value


def verify_target_defects(target):
    contract = json.loads((TARGET / "xp_executable_launch_contract_v3.json").read_text())
    if (target.get("collector_sha256") != ACTUAL_COLLECTOR_SHA
            or contract.get("bidirectional_qualification_gate", {}).get("collector_sha256")
            != STALE_CONTRACT_COLLECTOR_SHA):
        raise BuildError("audited collector identities changed")
    launch_names = {path.name for path in (TARGET / "launch").iterdir() if path.is_file()}
    if any("debug" in name and "materializer" not in name for name in launch_names):
        raise BuildError("v26 debug producer inventory changed; re-audit required")


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        os.chmod(path, 0o555 if path.is_dir() else 0o444)
    os.chmod(root, 0o555)


def closure(root):
    result = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise BuildError("symlink prohibited")
        relative = path.relative_to(root).as_posix()
        if path.is_file() and relative not in {"bundle_manifest.json", "bundle_manifest.json.sha256"}:
            result[relative] = sha_file(path)
    return dict(sorted(result.items()))


def build():
    target = verify_identity(TARGET, TARGET_ID, TARGET_SHA,
                             "mysql_xp_node02_successor_bundle_v26", True)
    verify_identity(HANDOFF, HANDOFF_ID, HANDOFF_SHA,
                    "mysql_xp_v26_postpublication_handoff_bundle_v1")
    verify_identity(COMPAT, COMPAT_ID, COMPAT_SHA,
                    "mysql_xp_v26_same_live_adoption_successor_bundle_v1")
    verify_target_defects(target)
    sources = {
        MATERIALIZER: ROOT / MATERIALIZER,
        REPORT: ROOT / REPORT,
        TESTS[0]: ROOT / TESTS[0],
        TESTS[1]: ROOT / TESTS[1],
        BUILDER: HERE,
    }
    content = {}
    for name, path in sources.items():
        if path.is_symlink() or not path.is_file():
            raise BuildError("source absent: " + name)
        content[name] = path.read_bytes()
    files = {name: sha_bytes(data) for name, data in sorted(content.items())}
    identity = {
        "schema_version": "mysql_xp_v26_fresh_pair_gate_successor_bundle_v1",
        "status": "PASS_IMMUTABLE_LOCAL_FRESH_PAIR_GATE_SUCCESSOR_NOT_LAUNCHED",
        "immutable": True,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "target_bundle_id": TARGET_ID,
        "target_manifest_sha256": TARGET_SHA,
        "predecessor_handoff_bundle_id": HANDOFF_ID,
        "predecessor_handoff_manifest_sha256": HANDOFF_SHA,
        "compatibility_bundle_id": COMPAT_ID,
        "compatibility_manifest_sha256": COMPAT_SHA,
        "capture_interpreter_hardcode_repaired": True,
        "capture_interpreter_minimum": [3, 7, 0],
        "capture_interpreter_readback_binding_required": True,
        "actual_collector_sha256": ACTUAL_COLLECTOR_SHA,
        "target_contract_collector_sha256_stale": STALE_CONTRACT_COLLECTOR_SHA,
        "target_collector_contract_consistent": False,
        "v26_debug_data_plane_producer_present": False,
        "fresh_v26_capture_r0_ready_after_runtime_gates": True,
        "candidate_top5_launch_ready": False,
        "ten_accepted_rounds_launch_ready": False,
        "fresh_v26_database_and_client_required": True,
        "old_v24_v25_private_material_reuse_prohibited": True,
        "node02_local_private_generation_required": True,
        "private_material_in_bundle": False,
        "private_material_transfer_authorized": False,
        "materializer_executes_workload": False,
        "materializer_mutates_remote_state": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
        "remote_actions": 0,
        "materializer_sha256": files[MATERIALIZER],
        "audit_sha256": files[REPORT],
        "files": files,
    }
    bundle_id = sha_bytes(canonical(identity))
    manifest = dict(identity, bundle_id=bundle_id)
    OUT.mkdir(parents=True, exist_ok=True)
    destination = OUT / bundle_id
    if not destination.exists():
        stage = Path(tempfile.mkdtemp(prefix=".xp-v26-fresh-pair-gate-", dir=OUT))
        try:
            for name, data in sorted(content.items()):
                path = stage / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            raw = canonical(manifest)
            (stage / "bundle_manifest.json").write_bytes(raw)
            (stage / "bundle_manifest.json.sha256").write_text(
                sha_bytes(raw) + "  bundle_manifest.json\n", encoding="ascii")
            freeze(stage)
            os.replace(stage, destination)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    if closure(destination) != files:
        raise BuildError("bundle closure differs")
    for path in (destination, *destination.rglob("*")):
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o222:
            raise BuildError("bundle is mutable")
    print(json.dumps({
        "status": manifest["status"],
        "bundle_id": bundle_id,
        "bundle": str(destination),
        "manifest_sha256": sha_file(destination / "bundle_manifest.json"),
        "files": len(files),
        "capture_r0_ready_after_runtime_gates": True,
        "candidate_top5_launch_ready": False,
        "accepted_rounds_credit": 0,
        "remote_actions": 0,
    }, sort_keys=True))
    return destination


if __name__ == "__main__":
    try:
        build()
    except (BuildError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise SystemExit("NOT_READY: " + str(exc))
