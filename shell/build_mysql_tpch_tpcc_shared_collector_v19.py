#!/usr/bin/env python3
"""Build immutable v19 with a deterministic paired sequence-zero barrier."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(__file__).resolve()
PARENT_ID = "010463771ab35cb375484089791e1554316328801fd7bc53f58e307e633992c4"
PARENT_SHA = "623204383639337074549f6ae2b8e064f773d5926d04afafe126cb8035445b49"
PARENT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v18" / PARENT_ID
OUT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v19"
SCHEMA = "mysql_tpch_tpcc_shared_collector_bundle_v19"
CORE = "qualification/mysql_distributed_live_readback_qualification_v1.py"
BUILDER = "authority/build_mysql_tpch_tpcc_shared_collector_v19.py"
INVALIDATION = "authority/v18c_paired_start_barrier_invalidation.json"
TEST = "tests/test_shared_collector_paired_epoch_barrier_v19.py"
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")


class BuildError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def replace_exact(data, old, new, label):
    if data.count(old) != 1:
        raise BuildError(label + " patch cardinality differs")
    return data.replace(old, new)


def load_parent():
    path = PARENT / "bundle_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    unsigned = dict(manifest); unsigned.pop("bundle_id", None)
    if (manifest.get("bundle_id") != PARENT_ID
            or digest(path) != PARENT_SHA
            or sha_bytes(canonical(unsigned)) != PARENT_ID
            or manifest.get("schema_version")
            != "mysql_tpch_tpcc_shared_collector_bundle_v18"
            or manifest.get("formal_scope") != "mysql_only"
            or manifest.get("formal_launch_authorized") is not False
            or manifest.get("accepted_round_authorized") is not False):
        raise BuildError("v18 parent differs")
    for relative, expected in manifest["files"].items():
        item = PARENT / relative
        if item.is_symlink() or not item.is_file() or digest(item) != expected:
            raise BuildError("v18 closure differs: " + relative)
    return manifest


def invalidation():
    return {
        "schema_version": "mysql_shared_v18c_paired_start_barrier_invalidation_v1",
        "status": "INVALIDATED_ZERO_CREDIT_REPLACED_BY_V19",
        "failed_attempts": [
            "tpch-a-paired-v18c-20260801t213816p08-6c3809b8",
            "tpcc-b-paired-v18c-20260801t213816p08-7e2e13aa",
        ],
        "failure": "the first peer read selected the newest row and could begin above sequence zero",
        "repair": "bind paired index zero to exact peer sequence zero, then require every exact successor",
        "missing_middle_sequence_remains_fail_closed": True,
        "sample_freshness_gate_unchanged": True,
        "qualification_credit": 0,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "postgresql_mutation": False,
    }


def test_source():
    return b'''from __future__ import annotations
import hashlib,json,py_compile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def test_v19_binds_peer_epoch_to_exact_zero(tmp_path):
 m=json.loads((ROOT/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id")
 raw=(json.dumps(u,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\\n").encode()
 assert m["bundle_id"]==ROOT.name==hashlib.sha256(raw).hexdigest()
 assert m["schema_version"]=="mysql_tpch_tpcc_shared_collector_bundle_v19"
 assert m["paired_peer_epoch_policy"]=="exact_sequence_zero_then_every_exact_successor"
 assert m["formal_launch_authorized"] is False and m["accepted_round_authorized"] is False
 for r,h in m["files"].items():assert hashlib.sha256((ROOT/r).read_bytes()).hexdigest()==h
 p=ROOT/"qualification/mysql_distributed_live_readback_qualification_v1.py";s=p.read_text()
 assert s.count("peer_previous_sequence = -1") == 1
 assert "expected_sequence = previous_sequence + 1" in s
 assert "workload metric exact next sequence is absent or ambiguous" in s
 py_compile.compile(str(p),doraise=True,cfile=str(tmp_path/"v19.pyc"))
'''


def clean(root):
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_dir() and path.name in {"__pycache__", ".pytest_cache"}:
            shutil.rmtree(path)
        elif path.is_file() and path.suffix in {".pyc", ".pyo"}:
            path.unlink()


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise BuildError("v19 symlink")
        os.chmod(path, stat.S_IMODE(path.stat().st_mode) & ~0o222)
    os.chmod(root, stat.S_IMODE(root.stat().st_mode) & ~0o222)


def file_map(root):
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise BuildError("v19 closure symlink")
        relative = path.relative_to(root).as_posix()
        if path.is_file() and relative not in {
            "bundle_manifest.json", "bundle_manifest.json.sha256"
        }:
            result[relative] = digest(path)
    return result


def main():
    parent = load_parent()
    payload = {r: (PARENT / r).read_bytes() for r in parent["files"]}
    modes = {r: stat.S_IMODE((PARENT / r).stat().st_mode) for r in parent["files"]}
    old = b'''                except metric_adapter.WorkloadMetricAdapterError as exc:\n                    raise QualificationError(\n                        "paired startup metric epoch reset failed: %s" % exc\n                    )\n            started = time.monotonic()\n'''
    new = b'''                except metric_adapter.WorkloadMetricAdapterError as exc:\n                    raise QualificationError(\n                        "paired startup metric epoch reset failed: %s" % exc\n                    )\n                # Both peers preserve sequence zero in their append-only stream.\n                # Bind the common window to that exact epoch even if scheduling\n                # lets the producer append sequence one before the first read.\n                peer_previous_sequence = -1\n            started = time.monotonic()\n'''
    payload[CORE] = replace_exact(payload[CORE], old, new, "paired epoch barrier")
    for relative, data in list(payload.items()):
        if relative.startswith("tests/") and relative.endswith(".py"):
            payload[relative] = data.replace(
                b"mysql_tpch_tpcc_shared_collector_bundle_v18", SCHEMA.encode()
            )
    payload[BUILDER] = SOURCE.read_bytes(); modes[BUILDER] = 0o444
    payload[INVALIDATION] = canonical(invalidation()); modes[INVALIDATION] = 0o444
    payload[TEST] = test_source(); modes[TEST] = 0o444
    unsigned = {k: v for k, v in parent.items() if k not in {"bundle_id", "files"}}
    unsigned.update({
        "schema_version": SCHEMA,
        "status": "SEALED_PAIRED_EPOCH_BARRIER_NOT_LAUNCH_AUTHORITY",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_SHA,
        "paired_peer_epoch_policy": "exact_sequence_zero_then_every_exact_successor",
        "workload_stream_consumption_policy": "exact_next_sequence_no_latest_row_skip",
        "maximum_sample_gap_seconds_unchanged": 3.5,
        "unknown_process_change_remains_fail_closed": True,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "files": {r: sha_bytes(d) for r, d in sorted(payload.items())},
    })
    bundle_id = sha_bytes(canonical(unsigned)); manifest = dict(unsigned, bundle_id=bundle_id)
    OUT.mkdir(parents=True, exist_ok=True); destination = OUT / bundle_id
    test_result = "existing immutable bundle reverified"
    if not destination.exists():
        stage = OUT / (".stage-" + bundle_id + "-" + secrets.token_hex(6)); stage.mkdir(mode=0o700)
        try:
            for relative, data in payload.items():
                path = stage / relative; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data); os.chmod(path, modes.get(relative, 0o444) & ~0o222)
            raw = canonical(manifest); (stage / "bundle_manifest.json").write_bytes(raw)
            (stage / "bundle_manifest.json.sha256").write_text(
                sha_bytes(raw) + "  bundle_manifest.json\n", encoding="ascii")
            temp = Path(tempfile.mkdtemp(prefix="mysql-shared-v19-test-")); scratch = temp / bundle_id
            try:
                shutil.copytree(stage, scratch)
                tests = sorted(str(p) for p in (scratch / "tests").glob("test_*.py"))
                result = subprocess.run([str(PYTHON), "-m", "pytest", "-q", *tests],
                                        cwd=ROOT, text=True, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, timeout=180)
                if result.returncode:
                    raise BuildError("v19 tests failed:\n" + result.stdout[-16000:])
                test_result = result.stdout.strip()
            finally:
                shutil.rmtree(temp)
            clean(stage); freeze(stage); os.rename(stage, destination)
        finally:
            if stage.exists(): shutil.rmtree(stage)
    published = json.loads((destination / "bundle_manifest.json").read_text()); check = dict(published); check.pop("bundle_id", None)
    if (published != manifest or sha_bytes(canonical(check)) != bundle_id
            or file_map(destination) != manifest["files"]
            or any(stat.S_IMODE(p.stat().st_mode) & 0o222
                   for p in [destination, *destination.rglob("*")])):
        raise BuildError("published v19 differs")
    print(json.dumps({"status":"PASS_IMMUTABLE_SHARED_COLLECTOR_V19",
                      "bundle":str(destination),"bundle_id":bundle_id,
                      "bundle_manifest_sha256":digest(destination/"bundle_manifest.json"),
                      "verified_files":len(manifest["files"]),"tests":test_result,
                      "formal_launch_authorized":False,"accepted_round_authorized":False},
                     indent=2,sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (BuildError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print("NOT_READY: " + str(exc), file=__import__("sys").stderr)
        raise SystemExit(2)
