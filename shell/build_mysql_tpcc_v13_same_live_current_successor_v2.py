#!/usr/bin/env python3
"""Publish the content-addressed offline TPC-C current-attempt materializer."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
MATERIALS = ROOT / "artifacts/mysql_tpcc_v13_live_materials_v3/238ea3432baf3ddf71b69e458257a20d5054c29ac5933501e5386c262b9b1816/materials.json"
MATERIALIZER = ROOT / "shell/mysql_tpcc_v13_same_live_current_materializer_v2.py"
TEST = ROOT / "shell/test_mysql_tpcc_v13_same_live_current_materializer_v2.py"
OUTPUT_ROOT = ROOT / "artifacts/mysql_tpcc_v13_same_live_current_successor_v2"


def canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def main() -> int:
    materials = MATERIALS.read_bytes()
    source_sidecar = MATERIALS.with_name(MATERIALS.name + ".sha256").read_text(encoding="ascii")
    if source_sidecar != f"{digest(materials)}  {MATERIALS.name}\n":
        raise RuntimeError("current TPC-C materials sidecar differs")
    embedded_sidecar = f"{digest(materials)}  current_materials.json\n".encode("ascii")
    material_data = json.loads(materials)
    if material_data.get("attempt_id") != "tpcc-dd-af4819e35f81d17f2d82560b":
        raise RuntimeError("current TPC-C attempt binding differs")
    if any(material_data.get(key) is not False for key in ("formal_launch_authorized", "formal_r0_authorized", "accepted_round_authorized")):
        raise RuntimeError("current TPC-C materials unexpectedly authorize a formal launch")
    files = {
        "materializer/mysql_tpcc_v13_same_live_current_materializer_v2.py": MATERIALIZER.read_bytes(),
        "tests/test_mysql_tpcc_v13_same_live_current_materializer_v2.py": TEST.read_bytes(),
        "authority/current_materials.json": materials,
        "authority/current_materials.json.sha256": embedded_sidecar,
        "authority/build_mysql_tpcc_v13_same_live_current_successor_v2.py": SELF.read_bytes(),
    }
    manifest = {
        "schema_version": "mysql_tpcc_v13_current_same_live_successor_bundle_v2",
        "status": "SEALED_OFFLINE_READ_ONLY_CURRENT_ATTEMPT_MATERIALIZER",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "current_attempt_id": material_data["attempt_id"],
        "current_materials_sha256": digest(materials),
        "remote_inputs_required": ["db_inspect", "client_inspect", "runtime_readback", "phase_ledger"],
        "phase_ledger_semantics_verified": False,
        "typed_capture_evidence_verified": False,
        "typed_capture_evidence_required_before_capture_authority": True,
        "qps_resource_input_prohibited": True,
        "qualification_authorized": False,
        "capture_authorized": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "files": {name: digest(payload) for name, payload in sorted(files.items())},
    }
    bundle_id = digest(canonical(manifest))
    manifest["bundle_id"] = bundle_id
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT_ROOT / bundle_id
    if target.exists() or target.is_symlink():
        existing = json.loads((target / "bundle_manifest.json").read_text(encoding="utf-8"))
        if existing != manifest:
            raise RuntimeError("existing successor bundle differs")
    else:
        stage = OUTPUT_ROOT / f".stage-{bundle_id}-{secrets.token_hex(6)}"
        stage.mkdir(mode=0o700)
        try:
            for name, payload in files.items():
                destination = stage / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(payload)
                os.chmod(destination, 0o444)
            raw = canonical(manifest)
            (stage / "bundle_manifest.json").write_bytes(raw)
            (stage / "bundle_manifest.json.sha256").write_text(f"{digest(raw)}  bundle_manifest.json\n", encoding="ascii")
            os.rename(stage, target)
        except BaseException:
            if stage.exists():
                shutil.rmtree(stage)
            raise
    print(json.dumps({"status": "PASS_SEALED_OFFLINE_CURRENT_ATTEMPT_MATERIALIZER", "bundle": str(target), "bundle_id": bundle_id, "accepted_round_authorized": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        sys.stderr.write(f"NOT_READY: {exc}\n")
        raise SystemExit(2)
