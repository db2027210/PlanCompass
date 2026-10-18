#!/usr/bin/env python3
"""Bind the v5 four-flow collector to the TPC-C v15 32 GiB successor."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat


ROOT = Path(__file__).resolve().parents[1]
PARENT_ID = "9fac9c3f897c2bf2764b49485cc94fd86658ec088c645cbee9807b26936c3e1d"
PARENT_SHA = "9192f52ee52cb7dbadb92787a6503c9b81fae095d5ac5f46d4e2c93a92fed7b6"
PARENT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v5" / PARENT_ID
TPCC_ID = "c95c59a40423dfe1cc47c09a357ad78aafd4cafd69056c51ba253254dedc22ec"
TPCC = ROOT / "artifacts/mysql_tpcc_typed_envelope_successor_v15" / TPCC_ID
OUT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v6"
SCHEMA = "mysql_tpch_tpcc_shared_collector_bundle_v6"
ENTRY = "qualification/mysql_tpch_tpcc_shared_collector_entry_v1.py"
TARGET_MANIFEST = "authority/tpcc_target_manifest.json"
BUILDER = "authority/build_mysql_tpch_tpcc_shared_collector_v6.py"
INVALIDATION = "authority/tpcc_v5_8g_envelope_binding_invalidation.json"
TEST = "tests/test_shared_collector_tpcc_v15_binding_v6.py"
OLD_TARGET_SCHEMA = "mysql_tpcc_v13_dedicated_daemon_successor_bundle_v3"
NEW_TARGET_SCHEMA = "mysql_tpcc_typed_envelope_successor_bundle_v15"


class BuildError(RuntimeError):
    pass


def canonical(value):
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def digest(path):
    return sha(Path(path).read_bytes())


def checked_manifest(root, schema, expected_id, expected_sha=None):
    path = root / "bundle_manifest.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    unsigned = dict(value)
    unsigned.pop("bundle_id", None)
    if (
        value.get("bundle_id") != expected_id
        or value.get("schema_version") != schema
        or sha(canonical(unsigned)) != expected_id
        or (expected_sha is not None and digest(path) != expected_sha)
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("formal_launch_authorized") is not False
    ):
        raise BuildError("manifest identity/scope differs: " + expected_id)
    for relative, expected in (value.get("files") or {}).items():
        candidate = root / relative
        if candidate.is_symlink() or not candidate.is_file() or digest(candidate) != expected:
            raise BuildError("bundle closure differs: " + relative)
    return value


def replace_once(data, old, new, label):
    if data.count(old) != 1:
        raise BuildError(label + " patch cardinality differs")
    return data.replace(old, new)


def test_source():
    return b'''from __future__ import annotations
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
TPCC_ID="c95c59a40423dfe1cc47c09a357ad78aafd4cafd69056c51ba253254dedc22ec"
def test_v6_binds_v15_without_changing_four_flow_semantics():
 m=json.loads((ROOT/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id")
 raw=(json.dumps(u,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\\n").encode()
 assert m["bundle_id"]==ROOT.name==hashlib.sha256(raw).hexdigest()
 assert m["schema_version"]=="mysql_tpch_tpcc_shared_collector_bundle_v6"
 for relative,expected in m["files"].items():assert hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()==expected
 target=json.loads((ROOT/"authority/tpcc_target_manifest.json").read_text())
 assert target["bundle_id"]==TPCC_ID
 assert target["schema_version"]=="mysql_tpcc_typed_envelope_successor_bundle_v15"
 assert target["database_memory_bytes"]==32*1024**3
 entry=(ROOT/"qualification/mysql_tpch_tpcc_shared_collector_entry_v1.py").read_text()
 assert '"manifest_schema": "mysql_tpcc_typed_envelope_successor_bundle_v15"' in entry
 core=(ROOT/"qualification/mysql_distributed_live_readback_qualification_v1.py").read_text()
 assert "timeout_seconds=90.0" in core and "timeout_seconds=2.5" in core
'''


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise BuildError("symlink in shared collector v6")
        os.chmod(path, stat.S_IMODE(path.stat().st_mode) & ~0o222)
    os.chmod(root, stat.S_IMODE(root.stat().st_mode) & ~0o222)


def main():
    parent = checked_manifest(
        PARENT, "mysql_tpch_tpcc_shared_collector_bundle_v5", PARENT_ID, PARENT_SHA
    )
    target = checked_manifest(TPCC, NEW_TARGET_SCHEMA, TPCC_ID)
    payload = {relative: (PARENT / relative).read_bytes() for relative in parent["files"]}
    modes = {relative: stat.S_IMODE((PARENT / relative).stat().st_mode) for relative in parent["files"]}
    payload[TARGET_MANIFEST] = (TPCC / "bundle_manifest.json").read_bytes()
    payload[ENTRY] = replace_once(
        payload[ENTRY], OLD_TARGET_SCHEMA.encode("ascii"), NEW_TARGET_SCHEMA.encode("ascii"),
        "entry TPC-C schema binding",
    )
    for relative in tuple(payload):
        if relative.startswith("tests/"):
            payload[relative] = payload[relative].replace(
                b"mysql_tpch_tpcc_shared_collector_bundle_v5", SCHEMA.encode("ascii")
            )
    payload[BUILDER] = Path(__file__).read_bytes(); modes[BUILDER] = 0o444
    payload[INVALIDATION] = canonical({
        "schema_version": "mysql_shared_collector_tpcc_envelope_binding_invalidation_v1",
        "invalidated_collector_bundle_id": PARENT_ID,
        "invalidated_tpcc_bundle_id":
            "22bf40890070a2bf147e593d8bd163ea2e16c13123d063583e52ff563461721d",
        "replacement_tpcc_bundle_id": TPCC_ID,
        "reason": "8 GiB target accumulated 237681 memory.failcnt events; fresh four-flow evidence required under sealed 32 GiB target",
        "r0_credit": 0,
        "accepted_round_credit": 0,
        "postgresql_mutation": False,
    }); modes[INVALIDATION] = 0o444
    payload[TEST] = test_source(); modes[TEST] = 0o444
    unsigned = dict(parent); unsigned.pop("bundle_id", None)
    unsigned.update({
        "schema_version": SCHEMA,
        "status": "SEALED_V5_SEMANTICS_TPCC_V15_BINDING_NOT_LAUNCH_AUTHORITY",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_SHA,
        "tpcc_target_bundle_id": TPCC_ID,
        "tpcc_target_manifest_sha256": digest(TPCC / "bundle_manifest.json"),
        "tpcc_database_memory_bytes": 32 * 1024**3,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "files": {relative: sha(data) for relative, data in sorted(payload.items())},
    })
    bundle_id = sha(canonical(unsigned)); manifest = {**unsigned, "bundle_id": bundle_id}
    OUT.mkdir(parents=True, exist_ok=True); destination = OUT / bundle_id
    if destination.exists():
        if json.loads((destination / "bundle_manifest.json").read_text()) != manifest:
            raise BuildError("existing shared v6 differs")
        print(destination); return
    stage = OUT / (".stage-" + bundle_id + "-" + secrets.token_hex(6)); stage.mkdir(mode=0o700)
    try:
        for relative, data in payload.items():
            path = stage / relative; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data); os.chmod(path, modes.get(relative, 0o444) & ~0o222)
        raw = canonical(manifest); (stage / "bundle_manifest.json").write_bytes(raw)
        (stage / "bundle_manifest.json.sha256").write_text(
            sha(raw) + "  bundle_manifest.json\n", encoding="ascii"
        )
        freeze(stage); os.rename(stage, destination)
    finally:
        if stage.exists(): shutil.rmtree(stage)
    print(destination)


if __name__ == "__main__":
    main()
