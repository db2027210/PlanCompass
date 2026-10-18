#!/usr/bin/env python3
"""Build the immutable v13 shared collector with 120s graceful quiescence.

The v12 paired run proved that both native workloads and all fail-closed
readbacks remain healthy, but TPC-H's 240s guard process can end before 181
complete concurrent readbacks are collected.  V13 changes no sample gate,
resource envelope, workload, or algorithm.  It only extends the bounded
post-sampling natural-exit wait from 75s to 120s so a separately sealed
longer-lived debug SQL guard can quiesce without forced termination.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(__file__).resolve()
PARENT_ID = "e31976d50aa917ba2dfd353f181d6a908f5c1a4d94e289894f759c326536653c"
PARENT_SHA = "37f065c860d26f46a03e1f3bbdddd5ed22e67081912984f5b6cdf7ad28d04dc9"
PARENT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v12" / PARENT_ID
OUT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v13"
SCHEMA = "mysql_tpch_tpcc_shared_collector_bundle_v13"
CORE = "qualification/mysql_distributed_live_readback_qualification_v1.py"
BUILDER = "authority/build_mysql_tpch_tpcc_shared_collector_v13.py"
INVALIDATION = "authority/v12_guard_duration_invalidation.json"
TEST = "tests/test_shared_collector_graceful_quiescence_v13.py"
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")


class BuildError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def verified_parent():
    manifest_path = PARENT / "bundle_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    unsigned = dict(manifest)
    unsigned.pop("bundle_id", None)
    if (
        manifest.get("bundle_id") != PARENT_ID
        or digest(manifest_path) != PARENT_SHA
        or sha(canonical(unsigned)) != PARENT_ID
        or manifest.get("schema_version")
        != "mysql_tpch_tpcc_shared_collector_bundle_v12"
        or manifest.get("maximum_sample_gap_seconds") != 3.5
        or manifest.get("native_marker_maximum_age_seconds") != 1.5
        or manifest.get("native_marker_poll_timeout_seconds") != 2.5
        or manifest.get("database_counter_timeout_seconds") != 1.0
        or manifest.get("minimum_samples") != 181
        or manifest.get("proc_snapshot_maximum_attempts") != 16
        or manifest.get("proc_snapshot_acceptance_requires_zero_races") is not True
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("postgresql_mutation_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise BuildError("v12 parent identity, scope, or gates differ")
    for relative, expected in manifest["files"].items():
        path = PARENT / relative
        if path.is_symlink() or not path.is_file() or digest(path) != expected:
            raise BuildError("v12 parent closure differs: " + relative)
    return manifest


def replace_exact(data, old, new, label, count=1):
    if data.count(old) != count:
        raise BuildError(label + " patch cardinality differs")
    return data.replace(old, new)


def test_source():
    return b'''from __future__ import annotations
import hashlib,json,py_compile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def test_v13_extends_only_graceful_quiescence_and_retains_gates():
 m=json.loads((ROOT/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id")
 raw=(json.dumps(u,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\\n").encode()
 assert m["bundle_id"]==ROOT.name==hashlib.sha256(raw).hexdigest()
 assert m["schema_version"]=="mysql_tpch_tpcc_shared_collector_bundle_v13"
 assert m["graceful_quiescence_seconds"]==120
 assert m["maximum_sample_gap_seconds"]==3.5 and m["minimum_samples"]==181
 assert m["native_marker_maximum_age_seconds"]==1.5
 assert m["native_marker_poll_timeout_seconds"]==2.5
 assert m["database_counter_timeout_seconds"]==1.0
 assert m["proc_snapshot_maximum_attempts"]==16
 assert m["proc_snapshot_acceptance_requires_zero_races"] is True
 assert m["formal_launch_authorized"] is False and m["accepted_round_authorized"] is False
 for relative,expected in m["files"].items():assert hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()==expected
 core=(ROOT/"qualification/mysql_distributed_live_readback_qualification_v1.py").read_text()
 assert "process.wait(timeout=120)" in core
 assert 'workload_exit.get("graceful_within_seconds") != 120' in core
 assert '"graceful_within_seconds": 120' in core
 assert "for index in range(MINIMUM_SAMPLES)" in core
 assert 'timeout=1,\\n        label="database counter sample"' in core
 py_compile.compile(str(ROOT/"qualification/mysql_distributed_live_readback_qualification_v1.py"),doraise=True,cfile=str(Path("/tmp")/(ROOT.name+"-v13-core.pyc")))
'''


def invalidation():
    return {
        "schema_version": "mysql_shared_collector_guard_duration_invalidation_v1",
        "status": "INVALIDATED_ZERO_CREDIT_REPLACED_BY_V13",
        "invalidated_collector_bundle_id": PARENT_ID,
        "failed_attempts": [
            "tpch-a-paired-v12a-20260801t150500p08-6c3809b8",
            "tpcc-b-paired-v12a-20260801t150500p08-7e2e13aa",
        ],
        "observed_complete_samples": {"tpch": 155, "tpcc": 156},
        "observed_native_tpch_sequences": [0, 239],
        "old_tpch_debug_guard_seconds": 240,
        "required_complete_samples": 181,
        "old_graceful_quiescence_seconds": 75,
        "new_graceful_quiescence_seconds": 120,
        "sample_gates_unchanged": True,
        "database_counter_fail_closed_unchanged": True,
        "qualification_credit": 0,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "resource_workload_four_flow_algorithm_c_unchanged": True,
        "postgresql_mutation": False,
    }


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise BuildError("symlink in shared collector v13")
        os.chmod(path, stat.S_IMODE(path.stat().st_mode) & ~0o222)
    os.chmod(root, stat.S_IMODE(root.stat().st_mode) & ~0o222)


def remove_caches(root):
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_dir() and path.name in {"__pycache__", ".pytest_cache"}:
            shutil.rmtree(path)
        elif path.is_file() and path.suffix in {".pyc", ".pyo"}:
            path.unlink()


def run_tests(stage, bundle_id):
    scratch_parent = Path(tempfile.mkdtemp(prefix="mysql-shared-v13-test-"))
    scratch = scratch_parent / bundle_id
    try:
        shutil.copytree(stage, scratch)
        tests = sorted(str(path) for path in (scratch / "tests").glob("test_*.py"))
        result = subprocess.run(
            [str(PYTHON), "-m", "pytest", "-q", *tests], cwd=ROOT,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=180, check=False,
        )
        if result.returncode != 0:
            raise BuildError("v13 tests failed:\n" + result.stdout[-16000:])
        return result.stdout.strip()
    finally:
        shutil.rmtree(scratch_parent)


def relative_files(root):
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise BuildError("symlink in published v13")
        relative = path.relative_to(root).as_posix()
        if path.is_file() and relative not in {"bundle_manifest.json", "bundle_manifest.json.sha256"}:
            result[relative] = digest(path)
    return result


def main():
    parent = verified_parent()
    payload = {relative: (PARENT / relative).read_bytes() for relative in parent["files"]}
    modes = {relative: stat.S_IMODE((PARENT / relative).stat().st_mode) for relative in parent["files"]}
    core = payload[CORE]
    core = replace_exact(core, b'workload_exit.get("graceful_within_seconds") != 75', b'workload_exit.get("graceful_within_seconds") != 120', "exit proof")
    core = replace_exact(core, b'# waited 75 seconds and then replaced it', b'# waited for graceful exit and then replaced it', "cleanup comment")
    core = replace_exact(core, b'process.wait(timeout=75)', b'process.wait(timeout=120)', "graceful wait")
    core = replace_exact(core, b'"within 75s"', b'"within 120s"', "graceful error")
    core = replace_exact(core, b'"graceful_within_seconds": 75', b'"graceful_within_seconds": 120', "graceful evidence")
    payload[CORE] = core
    for relative, data in list(payload.items()):
        if relative.startswith("tests/") and relative.endswith(".py"):
            payload[relative] = data.replace(
                b"mysql_tpch_tpcc_shared_collector_bundle_v12", SCHEMA.encode("ascii")
            )
    payload[BUILDER] = SOURCE.read_bytes(); modes[BUILDER] = 0o444
    payload[INVALIDATION] = canonical(invalidation()); modes[INVALIDATION] = 0o444
    payload[TEST] = test_source(); modes[TEST] = 0o444
    unsigned = {key: value for key, value in parent.items() if key not in {"bundle_id", "files"}}
    unsigned.update({
        "schema_version": SCHEMA,
        "status": "SEALED_120S_GRACEFUL_QUIESCENCE_NOT_LAUNCH_AUTHORITY",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_SHA,
        "graceful_quiescence_seconds": 120,
        "maximum_sample_gap_seconds": 3.5,
        "native_marker_maximum_age_seconds": 1.5,
        "native_marker_poll_timeout_seconds": 2.5,
        "database_counter_timeout_seconds": 1.0,
        "minimum_samples": 181,
        "proc_snapshot_maximum_attempts": 16,
        "proc_snapshot_acceptance_requires_zero_races": True,
        "resource_workload_four_flow_algorithm_c_unchanged": True,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "files": {relative: sha(data) for relative, data in sorted(payload.items())},
    })
    bundle_id = sha(canonical(unsigned)); manifest = dict(unsigned, bundle_id=bundle_id)
    OUT.mkdir(parents=True, exist_ok=True); destination = OUT / bundle_id
    test_result = "existing immutable bundle reverified"
    if not destination.exists():
        stage = OUT / (".stage-" + bundle_id + "-" + secrets.token_hex(6)); stage.mkdir(mode=0o700)
        try:
            for relative, data in payload.items():
                path = stage / relative; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data); os.chmod(path, modes.get(relative, 0o444) & ~0o222)
            raw = canonical(manifest); (stage / "bundle_manifest.json").write_bytes(raw)
            (stage / "bundle_manifest.json.sha256").write_text(sha(raw) + "  bundle_manifest.json\n", encoding="ascii")
            test_result = run_tests(stage, bundle_id); remove_caches(stage); freeze(stage); os.rename(stage, destination)
        finally:
            if stage.exists(): shutil.rmtree(stage)
    published = json.loads((destination / "bundle_manifest.json").read_text(encoding="utf-8")); unsigned_check = dict(published); unsigned_check.pop("bundle_id", None)
    if (published != manifest or sha(canonical(unsigned_check)) != bundle_id
            or relative_files(destination) != manifest["files"]
            or any(stat.S_IMODE(path.stat().st_mode) & 0o222 for path in [destination, *destination.rglob("*")])):
        raise BuildError("published v13 identity, closure, or mode differs")
    print(json.dumps({"status":"PASS_IMMUTABLE_SHARED_COLLECTOR_V13","bundle":str(destination),"bundle_id":bundle_id,"bundle_manifest_sha256":digest(destination/"bundle_manifest.json"),"verified_files":len(manifest["files"]),"tests":test_result,"graceful_quiescence_seconds":120,"minimum_samples":181,"formal_launch_authorized":False,"accepted_round_authorized":False},indent=2,sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (BuildError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print("NOT_READY: " + str(exc), file=__import__("sys").stderr)
        raise SystemExit(2)
