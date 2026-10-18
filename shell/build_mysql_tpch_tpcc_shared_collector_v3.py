#!/usr/bin/env python3
"""Seal the shared collector with full-bundle evidence identity."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import stat


ROOT = Path(__file__).resolve().parents[1]
PARENT_ID = "6fbde6fae696fc82c157ca79ce6e85ab4ee78cac2bd02f888a97f9e197daf0a1"
PARENT_SHA256 = "4b3049b3653c38c519a7a80670c1facc9b71540c93206674383934361058368b"
PARENT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v2" / PARENT_ID
OUTPUT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v3"
CORE = "qualification/mysql_distributed_live_readback_qualification_v1.py"
BUILDER = "authority/build_mysql_tpch_tpcc_shared_collector_v3.py"
TEST = "tests/test_shared_collector_full_bundle_identity_v3.py"
ARITY_TEST = "tests/test_shared_collector_tpch_client_readback_arity_v2.py"
INVALIDATION = "authority/core_only_collector_identity_invalidation.json"


class BuildError(RuntimeError):
    pass


def canonical(value):
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                   allow_nan=False) + "\n"
    ).encode("utf-8")


def sha(value):
    return hashlib.sha256(value).hexdigest()


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def verify_parent():
    path = PARENT / "bundle_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    unsigned = dict(manifest)
    unsigned.pop("bundle_id", None)
    if (
        manifest.get("bundle_id") != PARENT_ID
        or digest(path) != PARENT_SHA256
        or sha(canonical(unsigned)) != PARENT_ID
        or manifest.get("schema_version")
        != "mysql_tpch_tpcc_shared_collector_bundle_v2"
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise BuildError("shared collector v2 parent identity differs")
    for relative, expected in manifest["files"].items():
        path = PARENT / relative
        if path.is_symlink() or not path.is_file() or digest(path) != expected:
            raise BuildError("shared collector v2 closure differs: " + relative)
    return manifest


def patch_core(payload):
    old = b'        "collector_sha256": sha256(Path(__file__).resolve()),\n'
    new = b'''        "collector_sha256": sha256(
            Path(__file__).resolve().parents[1] / "bundle_manifest.json"
        ),
'''
    if payload.count(old) != 1:
        raise BuildError("collector evidence identity patch point differs")
    return payload.replace(old, new)


def patch_arity_test(payload):
    old = b'mysql_tpch_tpcc_shared_collector_bundle_v2'
    new = b'mysql_tpch_tpcc_shared_collector_bundle_v3'
    if payload.count(old) != 1:
        raise BuildError("inherited arity test schema patch point differs")
    return payload.replace(old, new)


def invalidation():
    return {
        "schema_version": "mysql_shared_collector_identity_invalidation_v1",
        "status": "INVALID_FOR_FOUR_FLOW_GATE_RAW_DIAGNOSTIC_RETAINED",
        "reason": "CORE_ONLY_SHA_OMITTED_MATERIAL_ENTRY_WRAPPER_IDENTITY",
        "invalidated_unpublished_builder_test_bundle": {
            "bundle_id":
                "517b4aaa417c423728fc62b76ed1ced892e25437a9a5c8056c1d98504ed1ae3c",
            "manifest_sha256":
                "24ec9b92120aea9d17cb586160215c1fc715d6a3f3aa05808820d6319a0a345f",
            "reason": "INHERITED_ARITY_TEST_EXPECTED_V2_SCHEMA",
            "credit": 0,
        },
        "predecessor_core_sha256":
            "06add87de91001f847b18d83f193827b098a81eb95ea5138289398a682498156",
        "invalidated_gate_inputs": [
            {
                "scenario": "tpcc_cpu",
                "flow": "B_solo_v6",
                "evidence_sha256":
                    "1d6e07cf9ed8fa6199f1fb9b7da2330ae85779c0816309388d34987c9b982e64",
            },
            {
                "scenario": "tpch_memory",
                "flow": "A_solo_v4",
                "evidence_sha256":
                    "f2b81e8d87d20fb691ca4d35c79496c3f8c50a11e506f80168c24967f77daf9e",
            },
        ],
        "r0_credit": 0,
        "accepted_round_credit": 0,
        "raw_measurements_deleted": False,
        "fresh_four_flow_inputs_required": True,
        "postgresql_mutation": False,
    }


TEST_SOURCE = r'''#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def canonical(v):return (json.dumps(v,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()
def main():
 manifest=json.loads((ROOT/"bundle_manifest.json").read_text());unsigned=dict(manifest);unsigned.pop("bundle_id")
 assert manifest["bundle_id"]==ROOT.name
 assert hashlib.sha256(canonical(unsigned)).hexdigest()==ROOT.name
 assert manifest["schema_version"]=="mysql_tpch_tpcc_shared_collector_bundle_v3"
 assert manifest["collector_identity"]=="FULL_SUPPORT_BUNDLE_MANIFEST_SHA256"
 for relative,expected in manifest["files"].items():
  path=ROOT/relative;assert path.is_file() and not path.is_symlink();assert hashlib.sha256(path.read_bytes()).hexdigest()==expected
 core=(ROOT/"qualification/mysql_distributed_live_readback_qualification_v1.py").read_text()
 assert 'Path(__file__).resolve().parents[1] / "bundle_manifest.json"' in core
 assert '"collector_sha256": sha256(Path(__file__).resolve()),' not in core
 invalid=json.loads((ROOT/"authority/core_only_collector_identity_invalidation.json").read_text())
 assert invalid["status"]=="INVALID_FOR_FOUR_FLOW_GATE_RAW_DIAGNOSTIC_RETAINED"
 assert len(invalid["invalidated_gate_inputs"])==2
if __name__=="__main__":main()
'''.encode("utf-8")


def freeze(path):
    for candidate in sorted(Path(path).rglob("*"), reverse=True):
        if candidate.is_symlink():
            raise BuildError("symlink in shared collector v3")
        os.chmod(candidate, 0o444 if candidate.is_file() else 0o555)
    os.chmod(path, 0o555)


def main():
    parent = verify_parent()
    payload = {}
    modes = {}
    for relative in parent["files"]:
        path = PARENT / relative
        payload[relative] = path.read_bytes()
        modes[relative] = stat.S_IMODE(path.stat().st_mode)
    payload[CORE] = patch_core(payload[CORE])
    payload[ARITY_TEST] = patch_arity_test(payload[ARITY_TEST])
    payload[BUILDER] = Path(__file__).read_bytes()
    payload[TEST] = TEST_SOURCE
    payload[INVALIDATION] = canonical(invalidation())
    modes.update({CORE:0o555,BUILDER:0o444,TEST:0o444,INVALIDATION:0o444})
    manifest = {
        "schema_version": "mysql_tpch_tpcc_shared_collector_bundle_v3",
        "status":
            "SEALED_FULL_BUNDLE_IDENTIFIED_BYTE_IDENTICAL_FOUR_FLOW_COLLECTOR_NOT_LAUNCH_AUTHORITY",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_SHA256,
        "collector_identity": "FULL_SUPPORT_BUNDLE_MANIFEST_SHA256",
        "same_collector_required_for_all_four_flows": True,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "files": {name:sha(data) for name,data in sorted(payload.items())},
    }
    bundle_id=sha(canonical(manifest));manifest["bundle_id"]=bundle_id
    OUTPUT.mkdir(parents=True,exist_ok=True);target=OUTPUT/bundle_id
    if target.exists():raise BuildError("immutable shared collector v3 already exists")
    stage=OUTPUT/(".stage-"+bundle_id+"-"+secrets.token_hex(6));stage.mkdir(mode=0o700)
    for relative,data in payload.items():
        destination=stage/relative;destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_bytes(data);os.chmod(destination,modes[relative]&~0o222)
    raw=canonical(manifest);(stage/"bundle_manifest.json").write_bytes(raw)
    (stage/"bundle_manifest.json.sha256").write_text(sha(raw)+"  bundle_manifest.json\n",encoding="ascii")
    freeze(stage);os.rename(stage,target)
    print(json.dumps({"status":manifest["status"],"bundle":str(target),"bundle_id":bundle_id,
                      "manifest_sha256":digest(target/"bundle_manifest.json"),
                      "formal_launch_authorized":False,"accepted_round_authorized":False},sort_keys=True))


if __name__=="__main__":
    try:main()
    except (BuildError,OSError,ValueError) as exc:raise SystemExit("NOT_READY: %s"%exc)
