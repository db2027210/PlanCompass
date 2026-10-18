#!/usr/bin/env python3
"""Seal JOB v13 with a dedicated daemon lane disjoint from MySQL XP."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat


ROOT = Path(__file__).resolve().parents[1]
PARENT_ID = "01e6d81e5e59ca28eaa5497286fbb1cf23d8d434de6077bf6f12119ca81d986b"
PARENT_SHA = "b6f34b74a67a33cfa61543dd365ea7e91cc3022e9b8d525d12c11bfe788936f6"
PARENT = ROOT / "artifacts/mysql_job_raw_all_subset_three_level_successor_v12" / PARENT_ID
OUT = ROOT / "artifacts/mysql_job_raw_all_subset_three_level_successor_v13"
SCHEMA = "mysql_job_raw_all_subset_three_level_successor_bundle_v13"
QUALIFIER = "qualification/mysql_job_distributed_live_readback_qualification_v2.py"
MONITOR = "qualification/mysql_job_distributed_v2_rolling_monitor.py"
MATERIALIZER = "qualification/mysql_job_dedicated_daemon_materializer_v5.py"
SCHEMA_TESTS = (
    "tests/test_mysql_job_graphdriver_readback_v9.py",
    "tests/test_mysql_job_daemon_namespace_readback_v10.py",
    "tests/test_mysql_job_module_closure_successor_v5.py",
    "tests/test_mysql_job_schema_authority_v11.py",
    "tests/test_mysql_job_counter_timeout_v12.py",
)
DAEMON_BINDINGS = (
    "qualification/fixtures/mysql_job_dedicated_daemon_readback_fixture_v1.json",
    "qualification/mysql_job_dedicated_daemon_contract_v1.py",
    "authority/build_mysql_job_graphdriver_readback_successor_v9.py",
    "authority/mysql_job_dedicated_daemon_contract_v1.py",
)
OLD_DAEMON_CPUSET = "34-35,70-71"
NEW_DAEMON_CPUSET = "16-17,52-53"
NEW_TEST = "tests/test_mysql_job_xp_daemon_lane_disjoint_v13.py"
INVALIDATION = "authority/mysql_job_v12_daemon_lane_overlap_invalidation_20260803.json"


class BuildError(RuntimeError): pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha(value): return hashlib.sha256(value).hexdigest()
def digest(path): return sha(Path(path).read_bytes())


def replace_once(source, old, new, label):
    if source.count(old) != 1: raise BuildError(label + " patch cardinality differs")
    return source.replace(old, new)


def verify_parent():
    path = PARENT / "bundle_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8")); unsigned = dict(manifest); unsigned.pop("bundle_id", None)
    if (manifest.get("bundle_id") != PARENT_ID or digest(path) != PARENT_SHA
            or sha(canonical(unsigned)) != PARENT_ID
            or manifest.get("schema_version") != "mysql_job_raw_all_subset_three_level_successor_bundle_v12"):
        raise BuildError("JOB v12 parent identity differs")
    for relative, expected in manifest.get("files", {}).items():
        candidate = PARENT / relative
        if candidate.is_symlink() or not candidate.is_file() or digest(candidate) != expected:
            raise BuildError("JOB v12 parent closure differs: " + relative)
    return manifest


def patch_qualifier(payload):
    source = payload.decode("utf-8")
    return replace_once(
        source,
        '        "mysql_job_raw_all_subset_three_level_successor_bundle_v12",\n',
        '        "mysql_job_raw_all_subset_three_level_successor_bundle_v13",\n',
        "qualification schema",
    ).encode("utf-8")


def patch_materializer(payload):
    source = payload.decode("utf-8")
    source = replace_once(source,
        'SCHEMA = "mysql_job_raw_all_subset_three_level_successor_bundle_v12"',
        'SCHEMA = "mysql_job_raw_all_subset_three_level_successor_bundle_v13"',
        "materializer schema")
    return replace_once(source, '"mysql_job_dedicated_daemon_materials_v12"',
                        '"mysql_job_dedicated_daemon_materials_v13"',
                        "materials schema").encode("utf-8")


def patch_schema_test(payload):
    return payload.decode("utf-8").replace(
        "mysql_job_raw_all_subset_three_level_successor_bundle_v12", SCHEMA
    ).replace("JOB v12 successor", "JOB v13 successor").encode("utf-8")


TEST_SOURCE = b'''from __future__ import annotations\nimport hashlib,json\nfrom pathlib import Path\nROOT=Path(__file__).resolve().parents[1]\ndef test_job_v13_daemon_lane_is_disjoint_from_xp():\n m=json.loads((ROOT/"bundle_manifest.json").read_text()); assert m["bundle_id"]==ROOT.name\n assert m["schema_version"]=="mysql_job_raw_all_subset_three_level_successor_bundle_v13"\n for r,h in m["files"].items(): assert hashlib.sha256((ROOT/r).read_bytes()).hexdigest()==h\n job={16,17,52,53}; xp={34,35,70,71}; assert not (job & xp)\n assert m["job_dedicated_daemon_cpuset"]=="16-17,52-53"\n assert m["mysql_xp_dedicated_daemon_cpuset"]=="34-35,70-71"\n for r in ("qualification/mysql_job_dedicated_daemon_contract_v1.py","authority/mysql_job_dedicated_daemon_contract_v1.py"):\n  s=(ROOT/r).read_text(); assert 'CPUS="16-17,52-53"' in s; assert "34-35,70-71" not in s\n'''


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink(): raise BuildError("symlink in JOB v13")
        os.chmod(path, stat.S_IMODE(path.stat().st_mode) & ~0o222)
    os.chmod(root, stat.S_IMODE(root.stat().st_mode) & ~0o222)


def main():
    parent = verify_parent()
    payload = {relative: (PARENT / relative).read_bytes() for relative in parent["files"] if relative != "closure.json"}
    modes = {relative: stat.S_IMODE((PARENT / relative).stat().st_mode) for relative in payload}
    payload[QUALIFIER] = patch_qualifier(payload[QUALIFIER]); modes[QUALIFIER] = 0o555
    payload[MATERIALIZER] = patch_materializer(payload[MATERIALIZER]); modes[MATERIALIZER] = 0o555
    for relative in SCHEMA_TESTS:
        payload[relative] = patch_schema_test(payload[relative]); modes[relative] = 0o444
    for relative in DAEMON_BINDINGS:
        source = payload[relative].decode("utf-8")
        count = source.count(OLD_DAEMON_CPUSET)
        if count != 1:
            raise BuildError(relative + " daemon cpuset cardinality differs")
        payload[relative] = source.replace(
            OLD_DAEMON_CPUSET, NEW_DAEMON_CPUSET
        ).encode("utf-8")
    payload[NEW_TEST] = TEST_SOURCE; modes[NEW_TEST] = 0o444
    payload[INVALIDATION] = canonical({"schema_version":"mysql_job_daemon_lane_overlap_invalidation_v1","status":"INVALID_ZERO_CREDIT","predecessor_bundle_id":PARENT_ID,"failure":"JOB_AND_XP_DEDICATED_DAEMONS_SHARED_CPUSET","old_job_daemon_cpuset":OLD_DAEMON_CPUSET,"mysql_xp_daemon_cpuset":OLD_DAEMON_CPUSET,"new_job_daemon_cpuset":NEW_DAEMON_CPUSET,"database_or_client_started":False,"r0_credit":0,"candidate_credit":0,"top5_replay_credit":0,"accepted_round_credit":0,"fresh_attempt_required":True,"postgresql_mutation":False}); modes[INVALIDATION] = 0o444
    closure = json.loads((PARENT / "closure.json").read_text(encoding="utf-8"))
    closure.update({"schema_version":"mysql_job_module_closure_v13","predecessor_bundle_id":PARENT_ID,"predecessor_bundle_manifest_sha256":PARENT_SHA,"database_counter_command_timeout_seconds":3,"database_counter_missing_or_timeout_policy":"FAIL_CLOSED","job_dedicated_daemon_cpuset":NEW_DAEMON_CPUSET,"mysql_xp_dedicated_daemon_cpuset":OLD_DAEMON_CPUSET,"daemon_cpuset_intersection":[]})
    closure["files"] = {relative:sha(data) for relative,data in sorted(payload.items())}
    payload["closure.json"] = canonical(closure); modes["closure.json"] = 0o444
    identity = dict(parent); identity.pop("bundle_id", None)
    identity.update({"schema_version":SCHEMA,"status":"SEALED_JOB_XP_DAEMON_LANE_DISJOINT_SUCCESSOR_DEBUG_BOOTSTRAP_NOT_R0_OR_FORMAL_AUTHORITY","predecessor_bundle_id":PARENT_ID,"predecessor_bundle_manifest_sha256":PARENT_SHA,"database_counter_command_timeout_seconds":3,"database_counter_missing_or_timeout_policy":"FAIL_CLOSED","job_dedicated_daemon_cpuset":NEW_DAEMON_CPUSET,"mysql_xp_dedicated_daemon_cpuset":OLD_DAEMON_CPUSET,"daemon_cpuset_intersection":[],"formal_launch_authorized":False,"formal10_authorized":False,"accepted_round_authorized":False,"files":{relative:sha(data) for relative,data in sorted(payload.items())}})
    bundle_id = sha(canonical(identity)); manifest = {**identity,"bundle_id":bundle_id}
    OUT.mkdir(parents=True,exist_ok=True); target=OUT/bundle_id
    if target.exists() or target.is_symlink(): raise BuildError("immutable JOB v13 already exists")
    stage=OUT/(".stage-"+bundle_id+"-"+secrets.token_hex(6)); stage.mkdir(mode=0o700)
    try:
        for relative,data in payload.items():
            path=stage/relative; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(data); os.chmod(path,modes[relative]&~0o222)
        raw=canonical(manifest); (stage/"bundle_manifest.json").write_bytes(raw); (stage/"bundle_manifest.json.sha256").write_text(sha(raw)+"  bundle_manifest.json\n",encoding="ascii")
        freeze(stage); os.rename(stage,target)
    except BaseException:
        if stage.exists(): shutil.rmtree(stage)
        raise
    print(json.dumps({"status":manifest["status"],"bundle":str(target),"bundle_id":bundle_id,"manifest_sha256":digest(target/"bundle_manifest.json"),"formal_launch_authorized":False,"accepted_round_authorized":False},sort_keys=True))


if __name__ == "__main__":
    try: main()
    except (BuildError,OSError,ValueError,KeyError) as exc: raise SystemExit("NOT_READY: %s" % exc)
