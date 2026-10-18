#!/usr/bin/env python3
"""Seal registry-v2 successor that adds v13 raw dynamic-gate closure binding."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
SEALER = ROOT / "shell/mysql_tpcc_typed_trace_registry_sealer_v2.py"
MATERIALS = ROOT / "shell/mysql_tpcc_typed_registry_seal_materials_v1.py"
TEST = ROOT / "shell/test_mysql_tpcc_typed_trace_registry_sealer_v2.py"
PREDECESSOR = ROOT / "artifacts/mysql_tpcc_typed_registry_sealer_successor_v1/42013dc288e98c85ad23e07bca88a76735b58c6d69a74baf7fefe9fa8c08ee04"
PREDECESSOR_MANIFEST_SHA256 = "2e04a1ce74954e064293ad507c79b0cf6be145549bc44050f5dda4161505f2a7"
OUT = ROOT / "artifacts/mysql_tpcc_typed_registry_sealer_successor_v2"


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    predecessor = PREDECESSOR / "bundle_manifest.json"
    if sha(predecessor) != PREDECESSOR_MANIFEST_SHA256:
        raise RuntimeError("42013d predecessor binding differs")
    payloads = {
        "registry/mysql_tpcc_typed_trace_registry_sealer_v2.py": SEALER.read_bytes(),
        "materials/mysql_tpcc_typed_registry_seal_materials_v1.py": MATERIALS.read_bytes(),
        "tests/test_mysql_tpcc_typed_trace_registry_sealer_v2.py": TEST.read_bytes(),
        "authority/build_mysql_tpcc_typed_registry_sealer_successor_v2.py": SELF.read_bytes(),
        "authority/predecessor_42013d_bundle_manifest.json": predecessor.read_bytes(),
    }
    files = {name: hashlib.sha256(data).hexdigest() for name, data in sorted(payloads.items())}
    identity = {
        "schema_version": "mysql_tpcc_typed_registry_sealer_successor_bundle_v2",
        "status": "SEALED_LOCAL_RAW_DYNAMIC_GATE_CLOSURE_PUBLISHER_NOT_FORMAL_AUTHORITY",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "predecessor_bundle_id": PREDECESSOR.name,
        "predecessor_manifest_sha256": PREDECESSOR_MANIFEST_SHA256,
        "registry_schema": "mysql_tpcc_typed_trace_registry_bundle_v2",
        "v13_dynamic_gate_attempt_token_bound": True,
        "v13_dynamic_gate_raw_four_input_closure_required": True,
        "real_trace_required": True,
        "synthetic_trace_prohibited": True,
        "qps_resource_input_prohibited": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "files": files,
    }
    bundle_id = hashlib.sha256(canonical(identity)).hexdigest()
    manifest = {**identity, "bundle_id": bundle_id}
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / bundle_id
    if target.exists() or target.is_symlink():
        if json.loads((target / "bundle_manifest.json").read_text(encoding="utf-8")) != manifest:
            raise RuntimeError("existing bundle differs")
    else:
        stage = OUT / (".stage-" + bundle_id + "-" + secrets.token_hex(8))
        stage.mkdir(mode=0o700)
        try:
            for name, data in payloads.items():
                path = stage / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                os.chmod(path, 0o444)
            raw = canonical(manifest)
            (stage / "bundle_manifest.json").write_bytes(raw)
            (stage / "bundle_manifest.json.sha256").write_text(hashlib.sha256(raw).hexdigest() + "  bundle_manifest.json\n", encoding="ascii")
            os.rename(stage, target)
        except BaseException:
            if stage.exists():
                shutil.rmtree(stage)
            raise
    print(json.dumps({"status": "PASS_SEALED_LOCAL_DYNAMIC_GATE_CLOSURE_SUCCESSOR_NOT_FORMAL_AUTHORITY", "bundle": str(target), "bundle_id": bundle_id, "manifest_sha256": sha(target / "bundle_manifest.json"), "accepted_round_authorized": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)
