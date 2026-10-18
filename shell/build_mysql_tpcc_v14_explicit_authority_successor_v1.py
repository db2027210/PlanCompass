#!/usr/bin/env python3
"""Build a minimal immutable successor that makes both collector bindings explicit."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BASE_ID = "5da0b262350a97c6cacdfb7cd1aabfc3d703b8d398053214f6503f78307aff56"
BASE_SHA = "5290618e050285f4a4eb2f06e33e97bfabe9b0340d6b6383236fa170249f0988"
BASE = ROOT / "artifacts/mysql_tpcc_qualification_fresh_r0_v14" / BASE_ID
COLLECTOR_ID = "ba98dab53beabbf2fefee60c0fe6795e6c2271524fdab335c01679b7a98693ec"
COLLECTOR_SHA = "a38915982e408c32f6e94ad4e88fbbdd323030190b3b525e6a570c9e2dbfd127"
FAILED = "v13-20260804T082319Z-68011af90ec6"
OUT = ROOT / "artifacts/mysql_tpcc_v14_explicit_authority_successor_v1"
OUTPUT_ROOT = "/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729/tpcc-v16-7e2e13aa-20260801t121500p08/measurement/v14-explicit-authority-qualification-fresh-r0"


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + "\n").encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        os.chmod(path, 0o555 if path.is_dir() else 0o444)
    os.chmod(root, 0o555)


TEST = r'''import ast,hashlib,importlib.util,json
from pathlib import Path
B=Path(__file__).resolve().parents[1];M=B/"bundle_manifest.json";C=B/"control/mysql_tpcc_qualification_fresh_r0_v14.py";L=B/"lib/mysql_tpcc_inventory_controller_v1.py"
NEW="ba98dab53beabbf2fefee60c0fe6795e6c2271524fdab335c01679b7a98693ec";SHA="a38915982e408c32f6e94ad4e88fbbdd323030190b3b525e6a570c9e2dbfd127";FAILED="v13-20260804T082319Z-68011af90ec6"
def can(v):return (json.dumps(v,sort_keys=True,indent=2)+"\n").encode()
def test_identity_scope_zero_credit():
 m=json.loads(M.read_text());u=dict(m);u.pop("bundle_id");assert hashlib.sha256(can(u)).hexdigest()==m["bundle_id"]==B.name;assert m["formal_scope"]=="mysql_only" and m["cross_engine_comparison_prohibited"] is True and m["postgresql_mutation_prohibited"] is True and m["accepted_rounds_credit"]==0
def test_library_api_requires_explicit_dual_binding():
 s=L.read_text();ast.parse(s);assert "collector_bundle_id: str, collector_manifest_sha: str" in s and '"collector_bundle_id": collector_bundle_id' in s
def test_controller_passes_exact_dual_binding():
 s=C.read_text();ast.parse(s);assert "L.authority(command, command_sha, hashlib.sha256(token.encode()).hexdigest(), COLLECTOR_ID, COLLECTOR_MANIFEST_SHA" in s
def test_authority_result_exact():
 q=importlib.util.spec_from_file_location("v14_explicit",L);m=importlib.util.module_from_spec(q);q.loader.exec_module(m);a=m.authority({"bundle_id":"b","mode":"qualification_solo","qualification_attempt_id":"a"},"c","t",NEW,SHA,1,1800);assert a["collector_bundle_id"]==NEW and a["collector_manifest_sha256"]==SHA
def test_pre_and_post_seal_readback():
 s=C.read_text();assert "def assert_collector_authority" in s and "assert_collector_authority(authority)" in s and "assert_collector_authority(sealed_authority)" in s and s.index("assert_collector_authority(authority)")<s.index("write_sealed(authority_path")<s.index("assert_collector_authority(sealed_authority)")
def test_failed_v13_is_zero_credit_and_never_reused():
 f=json.loads((B/"authority/v13_failure_explicit_successor.json").read_text());assert f["failed_attempt_id"]==FAILED and f["rerun_prohibited"] is True and f["reusable"] is False and f["accepted_rounds_credit"]==0 and f["measurement_samples_before_failure"]==2
def test_new_root_and_no_execution():
 r=json.loads((B/"runbook/one_click.json").read_text());assert "v14-explicit-authority-qualification-fresh-r0" in r["argv"][-1] and r["build_executes_workload"] is False and r["main_agent_execution_required"] is True
def test_preflight_qualification_r0_order():
 s=C.read_text();assert "DURATION = 180" in s and "SAMPLES = 181" in s and s.index("run_collector_preflight(plan, run_root, collector)")<s.index('run_stage(plan, "qualification"')<s.index('run_stage(plan, "fresh_r0"')
'''


def verify_base():
    manifest_path = BASE / "bundle_manifest.json"
    if sha(manifest_path.read_bytes()) != BASE_SHA:
        raise RuntimeError("base manifest differs")
    manifest = json.loads(manifest_path.read_text())
    unsigned = dict(manifest)
    if unsigned.pop("bundle_id", None) != BASE_ID or sha(canonical(unsigned)) != BASE_ID:
        raise RuntimeError("base content address differs")
    for relative, want in manifest["files"].items():
        if sha((BASE / relative).read_bytes()) != want:
            raise RuntimeError("base closure differs: " + relative)
    return manifest


def patched_library():
    source = (BASE / "lib/mysql_tpcc_inventory_controller_v1.py").read_text()
    old = "def authority(command: Mapping[str, Any], command_sha: str, token_sha: str,\n              collector_manifest_sha: str, now: float, ttl: int) -> dict[str, Any]:"
    new = "def authority(command: Mapping[str, Any], command_sha: str, token_sha: str,\n              collector_bundle_id: str, collector_manifest_sha: str, now: float, ttl: int) -> dict[str, Any]:"
    if source.count(old) != 1 or source.count('"collector_bundle_id": COLLECTOR_ID,') != 1:
        raise RuntimeError("base authority API differs")
    return source.replace(old, new).replace('"collector_bundle_id": COLLECTOR_ID,', '"collector_bundle_id": collector_bundle_id,')


def patched_controller():
    source = (BASE / "control/mysql_tpcc_qualification_fresh_r0_v14.py").read_text()
    old = "L.authority(command, command_sha, hashlib.sha256(token.encode()).hexdigest(), sha(collector_manifest_path), time.time(), 1800)"
    new = "L.authority(command, command_sha, hashlib.sha256(token.encode()).hexdigest(), COLLECTOR_ID, COLLECTOR_MANIFEST_SHA, time.time(), 1800)"
    if source.count(old) != 1:
        raise RuntimeError("base controller authority call differs")
    source = source.replace(old, new)
    marker = "\ndef materialize(output_root):\n"
    helper = '''\n\ndef assert_collector_authority(value):\n    if (value.get("collector_bundle_id") != COLLECTOR_ID\n            or value.get("collector_manifest_sha256") != COLLECTOR_MANIFEST_SHA):\n        raise NotReady("explicit collector authority binding differs")\n    return value\n'''
    if source.count(marker) != 1:
        raise RuntimeError("helper insertion marker differs")
    source = source.replace(marker, helper + marker)
    old_assign = '        authority["accepted_rounds_credit"] = 0\n        authority_path = root / (phase + ".authority.json")'
    new_assign = '        authority["accepted_rounds_credit"] = 0\n        assert_collector_authority(authority)\n        authority_path = root / (phase + ".authority.json")'
    if source.count(old_assign) != 1:
        raise RuntimeError("authority validation marker differs")
    source = source.replace(old_assign, new_assign)
    old_seal = '        write_sealed(authority_path, authority, 0o600)'
    new_seal = '        write_sealed(authority_path, authority, 0o600)\n        _, sealed_authority = load_sealed(authority_path, phase + " explicit collector authority")\n        assert_collector_authority(sealed_authority)'
    if source.count(old_seal) != 1:
        raise RuntimeError("authority readback marker differs")
    return source.replace(old_seal, new_seal)


def main():
    base_manifest = verify_base()
    contents = {}
    for relative in sorted(base_manifest["files"]):
        if relative in {"control/mysql_tpcc_qualification_fresh_r0_v14.py", "lib/mysql_tpcc_inventory_controller_v1.py", "runbook/one_click.json"} or relative.startswith(("builder/", "tests/")):
            continue
        contents[relative] = (BASE / relative).read_bytes()
    failure = {
        "schema_version": "mysql_tpcc_v13_signed_collector_authority_failure_successor_v1",
        "failed_attempt_id": FAILED,
        "status": "FAIL_CLOSED_PAIRED_COLLECTOR_SIGNED_AUTHORITY_DIFFERS",
        "preflight_status": "PASS_READ_ONLY_RECREATED_AUTHORITY_PREFLIGHT",
        "real_java_pid": 3218,
        "measurement_samples_before_failure": 2,
        "expected_collector_bundle_id": COLLECTOR_ID,
        "expected_collector_manifest_sha256": COLLECTOR_SHA,
        "observed_authority_collector_bundle_id": "f5a7a688f1c981b6c92e6dae0cf6aac580d0326f7f4d1f46c7493af993bc8a23",
        "observed_authority_collector_manifest_sha256": COLLECTOR_SHA,
        "root_cause": "v13 inherited authority API did not accept collector_bundle_id and signed its obsolete module constant",
        "rerun_prohibited": True,
        "reusable": False,
        "accepted_rounds_credit": 0,
        "postgresql_mutation_performed": False,
    }
    runbook = json.loads((BASE / "runbook/one_click.json").read_text())
    runbook.update({
        "schema_version": "mysql_tpcc_v14_explicit_authority_successor_runbook_v1",
        "collector_bundle_id": COLLECTOR_ID,
        "collector_manifest_sha256": COLLECTOR_SHA,
        "explicit_dual_authority_binding_required": True,
        "failed_v13_attempt": FAILED,
        "failed_v13_attempt_rerun_prohibited": True,
    })
    runbook["argv"][-1] = OUTPUT_ROOT
    contents.update({
        "control/mysql_tpcc_qualification_fresh_r0_v14.py": patched_controller().encode(),
        "lib/mysql_tpcc_inventory_controller_v1.py": patched_library().encode(),
        "authority/v13_failure_explicit_successor.json": canonical(failure),
        "runbook/one_click.json": canonical(runbook),
        "tests/test_mysql_tpcc_v14_explicit_authority_successor_v1.py": TEST.encode(),
        "builder/build_mysql_tpcc_v14_explicit_authority_successor_v1.py": Path(__file__).read_bytes(),
    })
    files = {relative: sha(raw) for relative, raw in sorted(contents.items())}
    unsigned = {
        "schema_version": "mysql_tpcc_v14_explicit_authority_successor_bundle_v1",
        "status": "SEALED_NOT_PUBLISHED_NOT_EXECUTED",
        "immutable": True,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "predecessor_bundle_id": BASE_ID,
        "predecessor_manifest_sha256": BASE_SHA,
        "collector_bundle_id": COLLECTOR_ID,
        "collector_manifest_sha256": COLLECTOR_SHA,
        "explicit_dual_authority_binding_required": True,
        "sealed_authority_readback_required": True,
        "failed_v13_attempt": FAILED,
        "failed_v13_rerun_prohibited": True,
        "formal_outer_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
        "remote_actions_performed": 0,
        "data_plane_actions_performed": 0,
        "files": files,
    }
    identity = sha(canonical(unsigned))
    manifest = dict(unsigned, bundle_id=identity)
    raw_manifest = canonical(manifest)
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / identity
    if target.exists():
        if (target / "bundle_manifest.json").read_bytes() != raw_manifest:
            raise RuntimeError("existing manifest differs")
        for relative, raw in contents.items():
            if (target / relative).read_bytes() != raw:
                raise RuntimeError("existing closure differs: " + relative)
    else:
        stage = Path(tempfile.mkdtemp(prefix=".tpcc-v14-explicit-", dir=OUT))
        try:
            for relative, raw in contents.items():
                path = stage / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            (stage / "bundle_manifest.json").write_bytes(raw_manifest)
            (stage / "bundle_manifest.json.sha256").write_text(sha(raw_manifest) + "  bundle_manifest.json\n")
            freeze(stage)
            os.rename(stage, target)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    print(identity)
    print(sha(raw_manifest))
    print(target)


if __name__ == "__main__":
    main()
