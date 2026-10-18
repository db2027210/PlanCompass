#!/usr/bin/env python3
"""Seal the root-only credential remediation helper for the current TPC-H bundle."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil


ROOT = Path(__file__).resolve().parents[1]
TARGET_ID = "87ed507ed689c103781db5f96332ae7875e4773d04d69b98a7e63e3d4097b257"
TARGET_SHA = "fe747c7811a6288a4b624f5bab48ace95727069a534e7314ec6e79938ca47e04"
TARGET = ROOT / "artifacts/mysql_tpch_memswap_consistency_successor_v1" / TARGET_ID
HELPER = ROOT / "shell/mysql_tpch_remediate_client_credentials_v1.py"
OUTPUT = ROOT / "artifacts/mysql_tpch_current_credential_remediation_v1"


def canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def main() -> int:
    target_manifest_path = TARGET / "bundle_manifest.json"
    target_manifest = json.loads(target_manifest_path.read_text(encoding="utf-8"))
    unsigned = dict(target_manifest)
    unsigned.pop("bundle_id", None)
    if (
        TARGET.name != TARGET_ID
        or digest(target_manifest_path) != TARGET_SHA
        or target_manifest.get("bundle_id") != TARGET_ID
        or sha_bytes(canonical(unsigned)) != TARGET_ID
        or target_manifest.get("formal_scope") != "mysql_only"
        or target_manifest.get("cross_engine_comparison_prohibited") is not True
    ):
        raise RuntimeError("current TPC-H target identity differs")
    files = {
        "remediation/mysql_tpch_remediate_client_credentials_v1.py": HELPER.read_bytes(),
        "authority/target_bundle_manifest.json": target_manifest_path.read_bytes(),
        "authority/build_mysql_tpch_current_credential_remediation_bundle_v1.py": Path(__file__).read_bytes(),
    }
    identity = {
        "schema_version": "mysql_tpch_current_credential_remediation_bundle_v1",
        "status": "SEALED_ROOT_ONLY_HELPER_NOT_EXECUTED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "target_bundle_id": TARGET_ID,
        "target_manifest_sha256": TARGET_SHA,
        "permitted_mutation": "current_attempt_credentials_directory_owner_and_mode_only",
        "workload_start_implemented": False,
        "formal_r0_authorized": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "files": {name: sha_bytes(payload) for name, payload in sorted(files.items())},
    }
    bundle_id = sha_bytes(canonical(identity))
    manifest = {**identity, "bundle_id": bundle_id}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / bundle_id
    if target.exists():
        existing = json.loads((target / "bundle_manifest.json").read_text(encoding="utf-8"))
        if existing != manifest:
            raise RuntimeError("existing credential remediation bundle differs")
    else:
        stage = OUTPUT / (".stage-" + bundle_id + "-" + secrets.token_hex(6))
        stage.mkdir(mode=0o700)
        try:
            for relative, payload in files.items():
                path = stage / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
                os.chmod(path, 0o444)
            raw = canonical(manifest)
            (stage / "bundle_manifest.json").write_bytes(raw)
            (stage / "bundle_manifest.json.sha256").write_text(
                sha_bytes(raw) + "  bundle_manifest.json\n", encoding="ascii"
            )
            os.rename(stage, target)
        except BaseException:
            if stage.exists():
                shutil.rmtree(stage)
            raise
    print(json.dumps({"status": identity["status"], "bundle": str(target), "bundle_id": bundle_id}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        raise SystemExit("NOT_READY: " + str(exc))
