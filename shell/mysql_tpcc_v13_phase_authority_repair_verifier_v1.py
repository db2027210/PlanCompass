#!/usr/bin/env python3
"""Verify the immutable TPC-C v13 same-live authority repair bridge.

The v13 bundle records the four-scene contract as a member of ``files``.  A
top-level ``four_scene_contract_sha256`` is legacy optional metadata and is
never an authority input here.  This module is offline-only.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


class VerificationError(RuntimeError):
    pass


CONTRACT = "authority/four_scene_successor_contract.json"


def canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest(path: Path) -> str:
    return digest_bytes(path.read_bytes())


def load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise VerificationError("regular JSON file required: " + str(path))
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise VerificationError("invalid JSON: " + str(path)) from exc
    if not isinstance(value, dict):
        raise VerificationError("JSON object required: " + str(path))
    return value


def verify_manifest_closure(bundle: Path, *, expected_schema: str | None = None) -> dict[str, Any]:
    bundle = bundle.resolve(strict=True)
    manifest_path = bundle / "bundle_manifest.json"
    sidecar = bundle / "bundle_manifest.json.sha256"
    manifest = load(manifest_path)
    if sidecar.is_symlink() or not sidecar.is_file():
        raise VerificationError("bundle manifest sidecar is absent")
    raw = manifest_path.read_bytes()
    if sidecar.read_bytes() != (digest_bytes(raw) + "  bundle_manifest.json\n").encode("ascii"):
        raise VerificationError("bundle manifest sidecar differs")
    unsigned = dict(manifest)
    bundle_id = unsigned.pop("bundle_id", None)
    if bundle_id != bundle.name or digest_bytes(canonical(unsigned)) != bundle_id:
        raise VerificationError("bundle manifest content identity differs")
    if expected_schema is not None and manifest.get("schema_version") != expected_schema:
        raise VerificationError("bundle schema differs")
    files = manifest.get("files")
    if not isinstance(files, Mapping) or not files:
        raise VerificationError("manifest files closure is absent")
    for relative, expected in files.items():
        if not isinstance(relative, str) or not isinstance(expected, str):
            raise VerificationError("manifest files member differs")
        candidate = bundle / relative
        if candidate.is_symlink() or not candidate.is_file() or digest(candidate) != expected:
            raise VerificationError("manifest files digest differs: " + relative)
    return manifest


def verify_manifest_contract_member(bundle: Path, manifest: Mapping[str, Any]) -> str:
    """Verify the contract by its manifest ``files`` member, not root metadata."""
    files = manifest.get("files")
    if not isinstance(files, Mapping):
        raise VerificationError("manifest files closure is absent")
    expected = files.get(CONTRACT)
    if not isinstance(expected, str) or len(expected) != 64:
        raise VerificationError("manifest does not bind four-scene contract member")
    path = bundle / CONTRACT
    if path.is_symlink() or not path.is_file() or digest(path) != expected:
        raise VerificationError("manifest-bound four-scene contract differs")
    load(path)
    return expected


def verify_repair_bundle(bundle: Path) -> dict[str, Any]:
    manifest = verify_manifest_closure(
        bundle, expected_schema="mysql_tpcc_v13_same_live_phase_authority_repair_bundle_v1"
    )
    if any(manifest.get(key) is not False for key in (
        "qualification_authorized", "capture_authorized", "formal_launch_authorized",
        "accepted_round_authorized", "phase_authority_issued",
    )):
        raise VerificationError("repair bridge unexpectedly authorizes a phase")
    contract_digest = verify_manifest_contract_member(bundle, manifest)
    repair = load(bundle / "authority/phase_authority_repair_contract.json")
    if repair.get("four_scene_contract_manifest_member_sha256") != contract_digest:
        raise VerificationError("repair contract/member digest differs")
    if repair.get("phase_authority_issued") is not False or repair.get("live_labels_modified") is not False:
        raise VerificationError("repair contract unexpectedly changes live authority")
    return {"bundle_id": manifest["bundle_id"], "contract_sha256": contract_digest}
