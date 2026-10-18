#!/usr/bin/env python3
"""Seal the node02 XP v12 systemd-219 transient-identity successor offline."""
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
PARENT_ID = "f910d2246b38b9719136ea015ad843733a16bbdd6b9666133b997d73bfc71c78"
PARENT_SHA = "67773059a5a5a11424ea22f3bcab82a3d39113913a61ab4e3181e91c629abfd6"
PARENT = ROOT / "artifacts/mysql_xp_node02_successor_v11" / PARENT_ID
OUT = ROOT / "artifacts/mysql_xp_node02_successor_v12"
SCHEMA = "mysql_xp_node02_successor_bundle_v12"
MATERIALIZER = "test_support/mysql_xp_qualification_bootstrap_materials_v5.py"
SERVICE = "launch/mysql_xp_node02_adopted_service_v1.py"
SYSTEMD_TEST = "tests/test_mysql_xp_systemd_oneshot_v1.py"
OLD_COMPAT_TEST = "tests/test_mysql_xp_systemd_compatibility_v11.py"
NEW_TEST = "tests/test_mysql_xp_transient_identity_v12.py"
INVALIDATION = "authority/mysql_xp_v11_transient_fragment_identity_invalidation_20260801.json"


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
    unsigned = dict(manifest)
    unsigned.pop("bundle_id", None)
    if (manifest.get("bundle_id") != PARENT_ID
            or digest(manifest_path) != PARENT_SHA
            or sha_bytes(canonical(unsigned)) != PARENT_ID
            or manifest.get("schema_version") != "mysql_xp_node02_successor_bundle_v11"
            or manifest.get("formal_scope") != "mysql_only"
            or manifest.get("cross_engine_comparison_prohibited") is not True):
        raise BuildError("XP v11 parent identity/scope differs")
    for relative, expected in manifest.get("files", {}).items():
        path = PARENT / relative
        if path.is_symlink() or not path.is_file() or digest(path) != expected:
            raise BuildError("XP v11 parent closure differs: " + relative)
    return manifest


def patch_prefix(payload, label):
    source = payload.decode("utf-8")
    count = source.count("ra-mysql-xp-v11-")
    if count < 1:
        raise BuildError(label + " unit prefix is absent")
    return source.replace("ra-mysql-xp-v11-", "ra-mysql-xp-v12-").encode("utf-8")


def patch_service(payload):
    source = patch_prefix(payload, "service").decode("utf-8")
    old_query = '''        "--property=MainPID,ActiveState,SubState,ControlGroup,FragmentPath",
'''
    new_query = '''        "--property=MainPID,ActiveState,SubState,ControlGroup,FragmentPath,Transient,SourcePath,UnitFileState",
'''
    source = replace_once(source, old_query, new_query,
                          "systemd transient identity properties")
    old_gate = '''        or unit.get("ControlGroup") != "/system.slice/" + expected_unit
        or unit.get("FragmentPath") not in ("", None)
'''
    new_gate = '''        or unit.get("ControlGroup") != "/system.slice/" + expected_unit
        or unit.get("Transient") != "yes"
        or unit.get("FragmentPath") != "/run/systemd/system/" + expected_unit
        or unit.get("SourcePath") not in ("", None)
        or unit.get("UnitFileState") != "static"
'''
    source = replace_once(source, old_gate, new_gate,
                          "systemd-219 transient fragment identity")
    return source.encode("utf-8")


def patch_old_compat_test(payload):
    source = patch_prefix(payload, "v11 compatibility test").decode("utf-8")
    source = replace_once(
        source,
        'assert manifest["schema_version"]=="mysql_xp_node02_successor_bundle_v11"',
        'assert manifest["schema_version"]=="mysql_xp_node02_successor_bundle_v12"',
        "compatibility test schema",
    )
    source = source.replace("xp_v11_materials", "xp_v12_materials")
    source = source.replace("test_v11_content_and_old_systemd_plan",
                            "test_v12_content_and_old_systemd_plan")
    return source.encode("utf-8")


def new_test_source():
    return b'''from __future__ import annotations
import importlib.util
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SERVICE=ROOT/"launch/mysql_xp_node02_adopted_service_v1.py"
def load():
 s=importlib.util.spec_from_file_location("xp_v12_service",SERVICE);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
def test_v12_accepts_exact_systemd219_transient_identity(monkeypatch):
 m=load(); unit="ra-mysql-xp-v12-copy-0123456789abcdef.service"
 text=SERVICE.read_text()
 assert "FragmentPath,Transient,SourcePath,UnitFileState" in text
 assert 'unit.get("Transient") != "yes"' in text
 assert '"/run/systemd/system/" + expected_unit' in text
 assert 'unit.get("SourcePath") not in ("", None)' in text
 assert 'unit.get("UnitFileState") != "static"' in text
 assert "ra-mysql-xp-v11-" not in text
def test_v12_keeps_fail_closed_host_executor_checks():
 text=SERVICE.read_text()
 for required in ("direct host root executor is required","direct host root executor CPU affinity differs","executor is not in the host %s namespace","executor cgroup readback differs","executor is not a PID1-owned transient host service"):
  assert required in text
'''


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise BuildError("symlink in XP v12")
        os.chmod(path, stat.S_IMODE(path.stat().st_mode) & ~0o222)
    os.chmod(root, stat.S_IMODE(root.stat().st_mode) & ~0o222)


def main():
    parent = verify_parent()
    payload = {relative: (PARENT / relative).read_bytes()
               for relative in parent["files"]}
    modes = {relative: stat.S_IMODE((PARENT / relative).stat().st_mode)
             for relative in parent["files"]}
    payload[MATERIALIZER] = patch_prefix(payload[MATERIALIZER], "materializer")
    payload[SERVICE] = patch_service(payload[SERVICE])
    payload[SYSTEMD_TEST] = patch_prefix(payload[SYSTEMD_TEST], "systemd test")
    payload[OLD_COMPAT_TEST] = patch_old_compat_test(payload[OLD_COMPAT_TEST])
    payload[NEW_TEST] = new_test_source()
    modes[NEW_TEST] = 0o444
    payload[INVALIDATION] = canonical({
        "schema_version": "mysql_xp_transient_fragment_identity_invalidation_v1",
        "status": "INVALID_ZERO_CREDIT",
        "predecessor_bundle_id": PARENT_ID,
        "attempt_token": "xp-formal-qual-20260731T185806Z-42dea92b14aede01780d431a",
        "host_id": "node02",
        "systemd_version": "219",
        "failure": "TRANSIENT_FRAGMENT_PATH_REJECTED_AS_PERSISTENT_UNIT",
        "observed_identity": {
            "Transient": "yes",
            "FragmentPath": "/run/systemd/system/ra-mysql-xp-v11-copy-164c09c55cdec22a.service",
            "SourcePath": "",
            "UnitFileState": "static",
        },
        "data_plane_started": False,
        "r0_credit": 0,
        "candidate_credit": 0,
        "top5_replay_credit": 0,
        "accepted_round_credit": 0,
        "fresh_attempt_required": True,
        "postgresql_mutation": False,
    })
    modes[INVALIDATION] = 0o444
    identity = dict(parent)
    identity.pop("bundle_id", None)
    identity.update({
        "schema_version": SCHEMA,
        "status": "SEALED_SYSTEMD_219_TRANSIENT_IDENTITY_SUCCESSOR_NOT_LAUNCHED",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_SHA,
        "systemd_transient_identity_contract": {
            "Transient": "yes",
            "FragmentPath": "/run/systemd/system/<exact-unit>",
            "SourcePath": "",
            "UnitFileState": "static",
        },
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "offline_build_only": True,
        "remote_actions": 0,
        "builder_revision": 1,
        "files": {relative: sha_bytes(data)
                  for relative, data in sorted(payload.items())},
    })
    bundle_id = sha_bytes(canonical(identity))
    manifest = {**identity, "bundle_id": bundle_id}
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / bundle_id
    if target.exists() or target.is_symlink():
        raise BuildError("immutable XP v12 already exists")
    stage = OUT / (".stage-" + bundle_id + "-" + secrets.token_hex(6))
    stage.mkdir(mode=0o700)
    try:
        for relative, data in payload.items():
            path = stage / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            os.chmod(path, modes[relative] & ~0o222)
        raw = canonical(manifest)
        (stage / "bundle_manifest.json").write_bytes(raw)
        (stage / "bundle_manifest.json.sha256").write_text(
            sha_bytes(raw) + "  bundle_manifest.json\n", encoding="ascii")
        freeze(stage)
        os.rename(stage, target)
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    print(json.dumps({
        "status": manifest["status"], "bundle": str(target),
        "bundle_id": bundle_id,
        "bundle_manifest_sha256": digest(target / "bundle_manifest.json"),
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (BuildError, OSError, ValueError, KeyError) as exc:
        sys.stderr.write("NOT_READY: " + str(exc) + "\n")
        raise SystemExit(2)
