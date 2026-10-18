#!/usr/bin/env python3
"""Build the fail-closed TPC-H successor that makes memory+swap executable.

The predecessor's manifest and runtime contract freeze both limits at
6174015488 bytes, but two executable constants still carried the old
5583454208-byte memory+swap value.  Docker correctly rejected that impossible
combination because the memory+swap limit was smaller than the memory limit.
This offline builder changes only those current executable constants, records
the zero-credit failed attempt, and rebinds the complete immutable closure.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(__file__).resolve()
PARENT_ID = "1b8ff1920184c113ce08a3a0ffda1f70c2bc3684bc01e7b296f5cb6704fe36e6"
PARENT_MANIFEST_SHA256 = "64df8de078511046c76bf15778d971c20bb64368255fb5e4f9e1c6c4d73e71ac"
PARENT = ROOT / "artifacts/mysql_tpch_affinity_remediation_successor_v1" / PARENT_ID
OUTPUT = ROOT / "artifacts/mysql_tpch_memswap_consistency_successor_v1"
BOOTSTRAP = "mysql_tpch_distributed_v2_authority.py"
READINESS = "tpch_formal10_readiness_v2.py"
FORMAL = "tpch_formal10_contract.json"
BUILDER = "builder/build_mysql_tpch_memswap_consistency_successor_v1.py"
INVALIDATION = "invalidated_f6b_memswap_inconsistent_attempt.json"
MEMORY_BYTES = 6174015488
OLD_MEMSWAP_BYTES = 5583454208


class BuildError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BuildError("JSON object required: " + str(path))
    return value


def relative_files(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise BuildError("symlink prohibited: " + str(path))
        relative = path.relative_to(root).as_posix()
        if path.is_file() and relative not in {
            "bundle_manifest.json", "bundle_manifest.json.sha256"
        }:
            result[relative] = sha_file(path)
    return result


def replace_once(payload: bytes, old: str, new: str, label: str) -> bytes:
    text = payload.decode("utf-8")
    if text.count(old) != 1:
        raise BuildError(label + " patch point cardinality differs")
    return text.replace(old, new).encode("utf-8")


def invalidation() -> dict[str, Any]:
    return {
        "schema_version": "mysql_tpch_impossible_memswap_attempt_invalidation_v1",
        "status": "INVALIDATED_ZERO_CREDIT_DO_NOT_RERUN",
        "attempt_token": "tpch-v2-20260731t142242z-f6b7168fd98f56ffd1ae8c2d",
        "bundle_id": PARENT_ID,
        "failure_stage": "bootstrap_before_database_container_creation",
        "failure": "Docker rejected memory_swap_bytes smaller than memory_bytes",
        "declared_memory_bytes": MEMORY_BYTES,
        "erroneous_executable_memory_swap_bytes": OLD_MEMSWAP_BYTES,
        "database_container_created": False,
        "workload_started": False,
        "qualification_credit": 0,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "replay_or_rerun_authorized": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }


def verify_parent() -> dict[str, Any]:
    manifest = load(PARENT / "bundle_manifest.json")
    contract = load(PARENT / "distributed_runtime_contract.json")
    unsigned = {key: value for key, value in manifest.items()
                if key != "bundle_id"}
    if (
        manifest.get("bundle_id") != PARENT_ID
        or sha_bytes(canonical(unsigned)) != PARENT_ID
        or sha_file(PARENT / "bundle_manifest.json") != PARENT_MANIFEST_SHA256
        or relative_files(PARENT) != manifest.get("files")
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
        or (contract.get("resource_envelope") or {}).get("memory_bytes")
        != MEMORY_BYTES
        or (contract.get("resource_envelope") or {}).get("memory_swap_bytes")
        != MEMORY_BYTES
    ):
        raise BuildError("parent identity, closure, authority, or envelope differs")
    return manifest


def seal_permissions(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        os.chmod(path, 0o444 if path.is_file() else 0o555)
    os.chmod(root, 0o555)


def build() -> dict[str, Any]:
    parent = verify_parent()
    content: dict[str, bytes] = {}
    for relative, expected in sorted(parent["files"].items()):
        payload = (PARENT / relative).read_bytes()
        if sha_bytes(payload) != expected:
            raise BuildError("parent file hash differs: " + relative)
        content[relative] = payload

    old = f'    "memory_swap_bytes": {OLD_MEMSWAP_BYTES},'
    new = f'    "memory_swap_bytes": {MEMORY_BYTES},'
    content[BOOTSTRAP] = replace_once(content[BOOTSTRAP], old, new, BOOTSTRAP)
    content[READINESS] = replace_once(content[READINESS], old, new, READINESS)
    content[BUILDER] = SOURCE.read_bytes()
    content[INVALIDATION] = canonical(invalidation())

    reverse = {digest: relative for relative, digest in parent["files"].items()}
    rebound: dict[str, str] = {}
    for name, old_hash in (parent.get("component_hashes") or {}).items():
        relative = reverse.get(old_hash)
        if relative is None:
            raise BuildError("component hash is outside parent closure: " + name)
        rebound[name] = sha_bytes(content[relative])

    formal = json.loads(content[FORMAL])
    formal["qualification_hashes"] = {
        name: value for name, value in rebound.items()
        if name not in {"self_contained_read_only_verifier",
                        "systemd_oneshot_materializer"}
    }
    content[FORMAL] = canonical(formal)
    files = {name: sha_bytes(payload) for name, payload in sorted(content.items())}

    identity = {key: value for key, value in parent.items()
                if key not in {"bundle_id", "files"}}
    identity.update({
        "successor_generation": "executable_memswap_consistency_v1",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_MANIFEST_SHA256,
        "builder_sha256": sha_file(SOURCE),
        "component_hashes": rebound,
        "formal10_contract_sha256": files[FORMAL],
        "memory_bytes": MEMORY_BYTES,
        "memory_swap_bytes": MEMORY_BYTES,
        "swap_capacity_bytes": 0,
        "docker_memory_swap_feasibility_verified": True,
        "invalidated_impossible_memswap_attempt_sha256": files[INVALIDATION],
        "fresh_qualification_required": True,
        "fresh_r0_required": True,
        "qualification_credit": 0,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "offline_build_only": True,
        "remote_actions": 0,
        "files": files,
    })
    bundle_id = sha_bytes(canonical(identity))
    manifest = dict(identity, bundle_id=bundle_id)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    destination = OUTPUT / bundle_id
    if not destination.exists():
        stage = OUTPUT / (".stage-" + bundle_id + "-" + secrets.token_hex(6))
        stage.mkdir(mode=0o700)
        try:
            for relative, payload in sorted(content.items()):
                target = stage / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
            raw = canonical(manifest)
            (stage / "bundle_manifest.json").write_bytes(raw)
            (stage / "bundle_manifest.json.sha256").write_text(
                sha_bytes(raw) + "  bundle_manifest.json\n", encoding="ascii"
            )
            seal_permissions(stage)
            os.replace(stage, destination)
        except BaseException:
            if stage.exists():
                shutil.rmtree(stage)
            raise

    published = load(destination / "bundle_manifest.json")
    unsigned = {key: value for key, value in published.items()
                if key != "bundle_id"}
    if (
        published != manifest
        or destination.name != bundle_id
        or sha_bytes(canonical(unsigned)) != bundle_id
        or relative_files(destination) != files
        or (destination / "bundle_manifest.json.sha256").read_text(encoding="ascii")
        != sha_file(destination / "bundle_manifest.json") + "  bundle_manifest.json\n"
    ):
        raise BuildError("published successor identity or closure differs")
    for path in [destination, *destination.rglob("*")]:
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o222:
            raise BuildError("published successor is mutable: " + str(path))
    return {
        "status": "PASS_IMMUTABLE_TPCH_MEMSWAP_CONSISTENCY_SUCCESSOR",
        "bundle": str(destination),
        "bundle_id": bundle_id,
        "bundle_manifest_sha256": sha_file(destination / "bundle_manifest.json"),
        "verified_files": len(files),
        "memory_bytes": MEMORY_BYTES,
        "memory_swap_bytes": MEMORY_BYTES,
        "fresh_qualification_required": True,
        "fresh_r0_required": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }


if __name__ == "__main__":
    try:
        print(json.dumps(build(), indent=2, sort_keys=True))
    except (BuildError, OSError, UnicodeError, ValueError, KeyError) as exc:
        sys.stderr.write("NOT_READY: " + str(exc) + "\n")
        raise SystemExit(2)
