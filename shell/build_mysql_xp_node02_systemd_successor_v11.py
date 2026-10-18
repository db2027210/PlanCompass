#!/usr/bin/env python3
"""Seal the node02 XP v11 systemd-219 compatibility successor offline."""
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
PARENT_ID = "e2b809f690375604bf9bc8b767009ed43dfd8baec2fc75c0ddfe7d72f8447b9b"
PARENT_SHA = "da3c4b6b0996ef4bc7ca98085ee0c0c661297b0d5b13c315075a9a26ce1d2b5c"
PARENT = ROOT / "artifacts/mysql_xp_node02_successor_v10" / PARENT_ID
OUT = ROOT / "artifacts/mysql_xp_node02_successor_v11"
SCHEMA = "mysql_xp_node02_successor_bundle_v11"
MATERIALIZER = "test_support/mysql_xp_qualification_bootstrap_materials_v5.py"
SERVICE = "launch/mysql_xp_node02_adopted_service_v1.py"
SYSTEMD_TEST = "tests/test_mysql_xp_systemd_oneshot_v1.py"
NEW_TEST = "tests/test_mysql_xp_systemd_compatibility_v11.py"
INVALIDATION = "authority/mysql_xp_v10_systemd_cpuaffinity_invalidation_20260801.json"


class BuildError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def digest(path):
    return sha_bytes(Path(path).read_bytes())


def replace_once(source, old, new, label):
    if source.count(old) != 1:
        raise BuildError(label + " patch cardinality differs")
    return source.replace(old, new)


def verify_parent():
    manifest_path = PARENT / "bundle_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    unsigned = dict(manifest); unsigned.pop("bundle_id", None)
    if (manifest.get("bundle_id") != PARENT_ID or digest(manifest_path) != PARENT_SHA
            or sha_bytes(canonical(unsigned)) != PARENT_ID
            or manifest.get("schema_version") != "mysql_xp_node02_successor_bundle_v10"
            or manifest.get("formal_scope") != "mysql_only"
            or manifest.get("cross_engine_comparison_prohibited") is not True):
        raise BuildError("XP v10 parent identity/scope differs")
    for relative, expected in manifest.get("files", {}).items():
        path = PARENT / relative
        if path.is_symlink() or not path.is_file() or digest(path) != expected:
            raise BuildError("XP v10 parent closure differs: " + relative)
    return manifest


def patch_materializer(payload):
    source = payload.decode("utf-8")
    if source.count("ra-mysql-xp-v8-") != 3:
        raise BuildError("XP transient unit prefix cardinality differs")
    source = source.replace("ra-mysql-xp-v8-", "ra-mysql-xp-v11-")
    source = replace_once(
        source,
        '        "--property=CPUAffinity=" + HOST_CONTROL_CPUSET.replace(",", " "),\n',
        "",
        "unsupported transient CPUAffinity property",
    )
    return source.encode("utf-8")


def patch_service(payload):
    source = payload.decode("utf-8")
    source = replace_once(source, "ra-mysql-xp-v8-", "ra-mysql-xp-v11-",
                          "executor unit identity")
    anchor = '''    if os.geteuid() != 0:\n        raise ServiceError("direct host root executor is required")\n'''
    replacement = anchor + '''    if _current_cpu_affinity() != DB_CPUS:\n        raise ServiceError("direct host root executor CPU affinity differs")\n'''
    source = replace_once(source, anchor, replacement,
                          "root executor affinity readback")
    return source.encode("utf-8")


def patch_systemd_test(payload):
    source = payload.decode("utf-8")
    if source.count("ra-mysql-xp-v8-") != 2:
        raise BuildError("systemd test unit prefix cardinality differs")
    source = source.replace("ra-mysql-xp-v8-", "ra-mysql-xp-v11-")
    anchor = '''    assert "--unit=" + UNIT in argv\n'''
    replacement = anchor + '''    assert not any(item.startswith("--property=CPUAffinity=") for item in argv)\n'''
    return replace_once(source, anchor, replacement,
                        "systemd compatibility assertion").encode("utf-8")


def new_test_source():
    return b'''from __future__ import annotations\nimport hashlib,importlib.util,json\nfrom pathlib import Path\nROOT=Path(__file__).resolve().parents[1]\nMAT=ROOT/"test_support/mysql_xp_qualification_bootstrap_materials_v5.py"\nSERVICE=ROOT/"launch/mysql_xp_node02_adopted_service_v1.py"\ndef load():\n s=importlib.util.spec_from_file_location("xp_v11_materials",MAT);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m\ndef test_v11_content_and_old_systemd_plan():\n manifest=json.loads((ROOT/"bundle_manifest.json").read_text()); assert manifest["bundle_id"]==ROOT.name\n assert manifest["schema_version"]=="mysql_xp_node02_successor_bundle_v11"\n for r,h in manifest["files"].items(): assert hashlib.sha256((ROOT/r).read_bytes()).hexdigest()==h\n m=load();unit="ra-mysql-xp-v11-bootstrap-0123456789abcdef.service"\n argv=m.systemd_run_argv(unit,["taskset","-c",m.HOST_CONTROL_CPUSET,"true"]); assert not any(x.startswith("--property=CPUAffinity=") for x in argv)\n assert ["taskset","-c",m.HOST_CONTROL_CPUSET]==argv[-4:-1]\n text=SERVICE.read_text(); assert "ra-mysql-xp-v11-" in text and "ra-mysql-xp-v8-" not in text\n assert "direct host root executor CPU affinity differs" in text\n'''


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise BuildError("symlink in XP v11")
        os.chmod(path, stat.S_IMODE(path.stat().st_mode) & ~0o222)
    os.chmod(root, stat.S_IMODE(root.stat().st_mode) & ~0o222)


def main():
    parent = verify_parent()
    payload = {relative: (PARENT / relative).read_bytes()
               for relative in parent["files"]}
    modes = {relative: stat.S_IMODE((PARENT / relative).stat().st_mode)
             for relative in parent["files"]}
    payload[MATERIALIZER] = patch_materializer(payload[MATERIALIZER]); modes[MATERIALIZER] = 0o555
    payload[SERVICE] = patch_service(payload[SERVICE]); modes[SERVICE] = 0o555
    payload[SYSTEMD_TEST] = patch_systemd_test(payload[SYSTEMD_TEST]); modes[SYSTEMD_TEST] = 0o444
    payload[NEW_TEST] = new_test_source(); modes[NEW_TEST] = 0o444
    payload[INVALIDATION] = canonical({
        "schema_version": "mysql_xp_systemd_cpuaffinity_invalidation_v1",
        "status": "INVALID_ZERO_CREDIT",
        "predecessor_bundle_id": PARENT_ID,
        "attempt_token": "xp-formal-qual-20260731T181812Z-4be99c642f9ca6f80415c167",
        "host_id": "node02",
        "systemd_version": "219",
        "failure": "UNKNOWN_ASSIGNMENT_CPUAFFINITY_TRANSIENT_PROPERTY",
        "data_plane_started": False,
        "r0_credit": 0,
        "candidate_credit": 0,
        "top5_replay_credit": 0,
        "accepted_round_credit": 0,
        "fresh_attempt_required": True,
        "replacement_control_affinity": {
            "inner_taskset": "21-33,57-69",
            "root_executor_proc_readback_required": True,
            "database_and_client_descendant_affinity_readback_required": True,
        },
        "postgresql_mutation": False,
    }); modes[INVALIDATION] = 0o444
    identity = dict(parent); identity.pop("bundle_id", None)
    identity.update({
        "schema_version": SCHEMA,
        "status": "SEALED_SYSTEMD_219_COMPATIBILITY_SUCCESSOR_NOT_LAUNCHED",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_SHA,
        "systemd_transient_cpuaffinity_property_prohibited": True,
        "host_control_inner_taskset": "21-33,57-69",
        "root_executor_proc_affinity_readback_required": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "offline_build_only": True,
        "remote_actions": 0,
        "builder_revision": 3,
        "files": {relative: sha_bytes(data) for relative, data in sorted(payload.items())},
    })
    bundle_id = sha_bytes(canonical(identity)); manifest = {**identity, "bundle_id": bundle_id}
    OUT.mkdir(parents=True, exist_ok=True); target = OUT / bundle_id
    if target.exists() or target.is_symlink():
        raise BuildError("immutable XP v11 already exists")
    stage = OUT / (".stage-" + bundle_id + "-" + secrets.token_hex(6)); stage.mkdir(mode=0o700)
    try:
        for relative, data in payload.items():
            path = stage / relative; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data); os.chmod(path, modes[relative] & ~0o222)
        raw = canonical(manifest)
        (stage / "bundle_manifest.json").write_bytes(raw)
        (stage / "bundle_manifest.json.sha256").write_text(
            sha_bytes(raw) + "  bundle_manifest.json\n", encoding="ascii")
        freeze(stage); os.rename(stage, target)
    except BaseException:
        if stage.exists(): shutil.rmtree(stage)
        raise
    print(json.dumps({"status": manifest["status"], "bundle": str(target),
                      "bundle_id": bundle_id,
                      "bundle_manifest_sha256": digest(target / "bundle_manifest.json"),
                      "formal_launch_authorized": False,
                      "accepted_round_authorized": False}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (BuildError, OSError, ValueError, KeyError) as exc:
        sys.stderr.write("NOT_READY: " + str(exc) + "\n")
        raise SystemExit(2)
