#!/usr/bin/env python3
"""Seal an offline repair bridge for TPC-C v13 same-live phase authority.

This does not promote a phase.  It corrects the verifier's former assumption
that the contract digest lived in a manifest root field: the canonical v13
source binds it in ``manifest.files``.  The current same-live observation is
kept as evidence only.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import secrets
import shutil
import stat
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
VERIFIER = ROOT / "shell/mysql_tpcc_v13_phase_authority_repair_verifier_v1.py"
TEST = ROOT / "shell/test_mysql_tpcc_v13_same_live_phase_authority_successor_v1.py"
CURRENT = ROOT / "artifacts/mysql_tpcc_v13_same_live_current_successor_v2/cb27af77b6c210e2ac2f4c859a4998a80e28bbd6d5ee405c00c8b3b98368f4ff"
UPSTREAM = ROOT / "artifacts/mysql_tpcc_v13_raw_all_subset_successor_v3/22bf40890070a2bf147e593d8bd163ea2e16c13123d063583e52ff563461721d"
OUT = ROOT / "artifacts/mysql_tpcc_v13_same_live_phase_authority_successor_v1"
CONTRACT = "authority/four_scene_successor_contract.json"


class BuildError(RuntimeError):
    pass


def canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def module() -> Any:
    spec = importlib.util.spec_from_file_location("tpcc_v13_repair_verify", VERIFIER)
    if spec is None or spec.loader is None:
        raise BuildError("repair verifier cannot be loaded")
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def freeze(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise BuildError("symlink in staged bundle")
        if path.is_dir():
            os.chmod(path, 0o555)
        elif path.is_file():
            mode = stat.S_IMODE(path.stat().st_mode)
            os.chmod(path, 0o555 if mode & 0o111 else 0o444)
    os.chmod(root, 0o555)


def main() -> int:
    verify = module()
    current = verify.verify_manifest_closure(
        CURRENT, expected_schema="mysql_tpcc_v13_current_same_live_successor_bundle_v2"
    )
    upstream = verify.verify_manifest_closure(
        UPSTREAM, expected_schema="mysql_tpcc_v13_dedicated_daemon_successor_bundle_v3"
    )
    contract_digest = verify.verify_manifest_contract_member(UPSTREAM, upstream)
    materials = CURRENT / "authority/current_materials.json"
    current_materials = json.loads(materials.read_text(encoding="utf-8"))
    upstream_raw = (UPSTREAM / "bundle_manifest.json").read_bytes()
    current_raw = (CURRENT / "bundle_manifest.json").read_bytes()
    if (current_materials.get("bundle_id") != upstream["bundle_id"]
            or current_materials.get("bundle_manifest_sha256") != digest(upstream_raw)):
        raise BuildError("same-live materials do not bind the upstream v13 bundle")
    repair = {
        "schema_version": "mysql_tpcc_v13_same_live_phase_authority_repair_contract_v1",
        "status": "SEALED_OFFLINE_REPAIR_NOT_A_PHASE_AUTHORITY",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "same_live_observation_bundle_id": current["bundle_id"],
        "same_live_observation_manifest_sha256": digest(current_raw),
        "upstream_v13_bundle_id": upstream["bundle_id"],
        "upstream_v13_manifest_sha256": digest(upstream_raw),
        "four_scene_contract_manifest_member_sha256": contract_digest,
        "verification_source": "manifest.files.authority/four_scene_successor_contract.json",
        "legacy_manifest_root_digest_is_not_an_authority_input": True,
        "phase_authority_issued": False,
        "live_labels_modified": False,
        "remote_actions": 0,
        "accepted_rounds_credit": 0,
    }
    files = {
        "authority/four_scene_successor_contract.json": (UPSTREAM / CONTRACT).read_bytes(),
        "authority/upstream_v13_bundle_manifest.json": upstream_raw,
        "authority/same_live_observation_bundle_manifest.json": current_raw,
        "authority/same_live_current_materials.json": materials.read_bytes(),
        "authority/phase_authority_repair_contract.json": canonical(repair),
        "authority/mysql_tpcc_v13_phase_authority_repair_verifier_v1.py": VERIFIER.read_bytes(),
        "authority/build_mysql_tpcc_v13_same_live_phase_authority_successor_v1.py": SELF.read_bytes(),
        "tests/test_mysql_tpcc_v13_same_live_phase_authority_successor_v1.py": TEST.read_bytes(),
    }
    manifest = {
        "schema_version": "mysql_tpcc_v13_same_live_phase_authority_repair_bundle_v1",
        "status": "SEALED_OFFLINE_REPAIR_NOT_A_PHASE_AUTHORITY",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "same_live_observation_bundle_id": current["bundle_id"],
        "upstream_v13_bundle_id": upstream["bundle_id"],
        "contract_verification_source": "manifest.files.authority/four_scene_successor_contract.json",
        "qualification_authorized": False,
        "capture_authorized": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "phase_authority_issued": False,
        "files": {name: digest(value) for name, value in sorted(files.items())},
    }
    bundle_id = digest(canonical(manifest))
    manifest["bundle_id"] = bundle_id
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / bundle_id
    if target.exists() or target.is_symlink():
        existing = json.loads((target / "bundle_manifest.json").read_text(encoding="utf-8"))
        if existing != manifest:
            raise BuildError("existing content-addressed repair bundle differs")
    else:
        stage = OUT / (".stage-" + bundle_id + "-" + secrets.token_hex(6))
        stage.mkdir(mode=0o700)
        try:
            for name, value in files.items():
                path = stage / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(value)
            raw = canonical(manifest)
            (stage / "bundle_manifest.json").write_bytes(raw)
            (stage / "bundle_manifest.json.sha256").write_text(
                digest(raw) + "  bundle_manifest.json\n", encoding="ascii"
            )
            freeze(stage)
            os.rename(stage, target)
        except BaseException:
            if stage.exists():
                shutil.rmtree(stage)
            raise
    verified = verify.verify_repair_bundle(target)
    print(json.dumps({"status": "PASS_SEALED_OFFLINE_REPAIR_NOT_A_PHASE_AUTHORITY",
                      "bundle": str(target), **verified,
                      "accepted_round_authorized": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        sys.stderr.write("NOT_READY: " + str(exc) + "\n")
        raise SystemExit(2)
