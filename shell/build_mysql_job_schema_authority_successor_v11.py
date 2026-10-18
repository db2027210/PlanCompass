#!/usr/bin/env python3
"""Seal a JOB v11 successor that recognizes its own immutable bundle schema."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import sys


ROOT = Path(__file__).resolve().parents[1]
PARENT_ID = "2ecc360556bb7d492e602e2ce058fcdf6bbdeea76c1320d70eb968ec82848d18"
PARENT_SHA = "479555bb8826aa719391f2301c244aba9adafffea81e3c168314f8ec2dfbbcc4"
PARENT = ROOT / "artifacts/mysql_job_raw_all_subset_three_level_successor_v10" / PARENT_ID
OUT = ROOT / "artifacts/mysql_job_raw_all_subset_three_level_successor_v11"
SCHEMA = "mysql_job_raw_all_subset_three_level_successor_bundle_v11"
QUALIFIER = "qualification/mysql_job_distributed_live_readback_qualification_v2.py"
MATERIALIZER = "qualification/mysql_job_dedicated_daemon_materializer_v5.py"
TESTS = (
    "tests/test_mysql_job_graphdriver_readback_v9.py",
    "tests/test_mysql_job_daemon_namespace_readback_v10.py",
    "tests/test_mysql_job_module_closure_successor_v5.py",
)
NEW_TEST = "tests/test_mysql_job_schema_authority_v11.py"
INVALIDATION = "authority/mysql_job_v10_schema_authority_invalidation_20260801.json"


class BuildError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def digest_bytes(value):
    return hashlib.sha256(value).hexdigest()


def digest(path):
    return digest_bytes(Path(path).read_bytes())


def replace_once(source, old, new, label):
    if source.count(old) != 1:
        raise BuildError(label + " patch cardinality differs")
    return source.replace(old, new)


def verify_parent():
    manifest_path = PARENT / "bundle_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    unsigned = dict(manifest); unsigned.pop("bundle_id", None)
    if (manifest.get("bundle_id") != PARENT_ID or digest(manifest_path) != PARENT_SHA
            or digest_bytes(canonical(unsigned)) != PARENT_ID
            or manifest.get("schema_version")
            != "mysql_job_raw_all_subset_three_level_successor_bundle_v10"):
        raise BuildError("JOB v10 parent identity differs")
    for relative, expected in manifest.get("files", {}).items():
        path = PARENT / relative
        if path.is_symlink() or not path.is_file() or digest(path) != expected:
            raise BuildError("JOB v10 parent closure differs: " + relative)
    return manifest


def patch_qualifier(payload):
    source = payload.decode("utf-8")
    old = '''    if schema in (\n        "mysql_algorithm_c_distributed_successor_bundle_v1",\n        "mysql_xp_distributed_formal10_launch_v3",\n    ):'''
    new = '''    if schema in (\n        "mysql_algorithm_c_distributed_successor_bundle_v1",\n        "mysql_xp_distributed_formal10_launch_v3",\n        "mysql_job_raw_all_subset_three_level_successor_bundle_v11",\n    ):'''
    return replace_once(source, old, new, "qualification schema authority").encode("utf-8")


def patch_materializer(payload):
    source = payload.decode("utf-8")
    source = replace_once(source,
        'SCHEMA = "mysql_job_raw_all_subset_three_level_successor_bundle_v10"',
        'SCHEMA = "mysql_job_raw_all_subset_three_level_successor_bundle_v11"',
        "materializer schema")
    source = replace_once(source,
        '"mysql_job_dedicated_daemon_materials_v10"',
        '"mysql_job_dedicated_daemon_materials_v11"',
        "materials schema")
    return source.encode("utf-8")


def patch_test(payload):
    source = payload.decode("utf-8")
    return source.replace(
        "mysql_job_raw_all_subset_three_level_successor_bundle_v10",
        SCHEMA,
    ).replace("JOB v10 successor", "JOB v11 successor").encode("utf-8")


def test_source():
    return b'''from __future__ import annotations\nimport hashlib,json\nfrom pathlib import Path\nROOT=Path(__file__).resolve().parents[1]\ndef test_schema_authority_v11():\n m=json.loads((ROOT/"bundle_manifest.json").read_text()); assert m["bundle_id"]==ROOT.name\n assert m["schema_version"]=="mysql_job_raw_all_subset_three_level_successor_bundle_v11"\n q=(ROOT/"qualification/mysql_job_distributed_live_readback_qualification_v2.py").read_text()\n assert '"mysql_job_raw_all_subset_three_level_successor_bundle_v11"' in q\n assert 'raise QualificationError("distributed bundle schema differs")' in q\n for r,h in m["files"].items(): assert hashlib.sha256((ROOT/r).read_bytes()).hexdigest()==h\n'''


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise BuildError("symlink in JOB v11")
        os.chmod(path, 0o444 if path.is_file() else 0o555)
    os.chmod(root, 0o555)


def main():
    parent = verify_parent()
    payload = {}
    modes = {}
    for relative in parent["files"]:
        if relative == "closure.json":
            continue
        path = PARENT / relative
        payload[relative] = path.read_bytes()
        modes[relative] = stat.S_IMODE(path.stat().st_mode)
    payload[QUALIFIER] = patch_qualifier(payload[QUALIFIER]); modes[QUALIFIER] = 0o555
    payload[MATERIALIZER] = patch_materializer(payload[MATERIALIZER]); modes[MATERIALIZER] = 0o555
    for relative in TESTS:
        payload[relative] = patch_test(payload[relative]); modes[relative] = 0o444
    payload[NEW_TEST] = test_source(); modes[NEW_TEST] = 0o444
    payload[INVALIDATION] = canonical({
        "schema_version": "mysql_job_schema_authority_invalidation_v1",
        "status": "INVALID_ZERO_CREDIT",
        "predecessor_bundle_id": PARENT_ID,
        "attempt_token": "job-v2-20260731t181410z-2f6a67754d42136aee334e7a",
        "failure": "QUALIFICATION_REJECTED_SUCCESSOR_SCHEMA",
        "workload_started": False,
        "r0_credit": 0,
        "candidate_credit": 0,
        "top5_replay_credit": 0,
        "accepted_round_credit": 0,
        "fresh_attempt_required": True,
        "postgresql_mutation": False,
    }); modes[INVALIDATION] = 0o444
    closure = json.loads((PARENT / "closure.json").read_text(encoding="utf-8"))
    closure.update({
        "schema_version": "mysql_job_module_closure_v11",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_bundle_manifest_sha256": PARENT_SHA,
        "schema_authority": SCHEMA,
        "invalidated_v10_attempt": INVALIDATION,
    })
    closure["files"] = {name: digest_bytes(data) for name, data in sorted(payload.items())}
    payload["closure.json"] = canonical(closure); modes["closure.json"] = 0o444
    identity = dict(parent); identity.pop("bundle_id", None)
    identity.update({
        "schema_version": SCHEMA,
        "status": "SEALED_SCHEMA_AUTHORITY_SUCCESSOR_DEBUG_BOOTSTRAP_NOT_R0_OR_FORMAL_AUTHORITY",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_bundle_manifest_sha256": PARENT_SHA,
        "formal_launch_authorized": False,
        "formal10_authorized": False,
        "accepted_round_authorized": False,
        "files": {name: digest_bytes(data) for name, data in sorted(payload.items())},
    })
    bundle_id = digest_bytes(canonical(identity)); manifest = {**identity, "bundle_id": bundle_id}
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / bundle_id
    if target.exists() or target.is_symlink():
        raise BuildError("immutable JOB v11 already exists")
    stage = OUT / (".stage-" + bundle_id + "-" + secrets.token_hex(6)); stage.mkdir(mode=0o700)
    try:
        for relative, data in payload.items():
            path = stage / relative; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data); os.chmod(path, modes[relative] & ~0o222)
        raw = canonical(manifest)
        (stage / "bundle_manifest.json").write_bytes(raw)
        (stage / "bundle_manifest.json.sha256").write_text(
            digest_bytes(raw) + "  bundle_manifest.json\n", encoding="ascii")
        freeze(stage); os.rename(stage, target)
    except BaseException:
        if stage.exists(): shutil.rmtree(stage)
        raise
    print(json.dumps({"status": manifest["status"], "bundle": str(target),
                      "bundle_id": bundle_id,
                      "manifest_sha256": digest(target / "bundle_manifest.json"),
                      "formal_launch_authorized": False,
                      "accepted_round_authorized": False}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (BuildError, OSError, ValueError, KeyError) as exc:
        sys.stderr.write("NOT_READY: " + str(exc) + "\n")
        raise SystemExit(2)
