#!/usr/bin/env python3
"""Build immutable v18: keep paired-tree proof, restore sealed solo mode."""
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
PARENT_ID = "68fbb0c46154d8e6e7fc3061dea54754a0939033dc7f0fce4e6044863841c989"
PARENT_SHA = "608d5d5df5c68340255f691952b754fdb12c40c3759037663bbd4348e98df10f"
PARENT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v17" / PARENT_ID
OUT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v18"
SCHEMA = "mysql_tpch_tpcc_shared_collector_bundle_v18"
ENTRY = "qualification/mysql_tpch_tpcc_shared_collector_entry_v1.py"
BUILDER = "authority/build_mysql_tpch_tpcc_shared_collector_v18.py"
INVALIDATION = "authority/v17a_solo_dispatch_invalidation.json"
TEST = "tests/test_shared_collector_solo_dispatch_v18.py"
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
            != "mysql_tpch_tpcc_shared_collector_bundle_v17"
            or manifest.get("workload_stream_consumption_policy")
            != "exact_next_sequence_no_latest_row_skip"
            or manifest.get("formal_launch_authorized") is not False
            or manifest.get("accepted_round_authorized") is not False):
        raise BuildError("v17 parent differs")
    for relative, expected in manifest["files"].items():
        path = PARENT / relative
        if path.is_symlink() or not path.is_file() or digest(path) != expected:
            raise BuildError("v17 closure differs: " + relative)
    return manifest


def invalidation():
    return {
        "schema_version": "mysql_shared_v17a_solo_dispatch_invalidation_v1",
        "status": "INVALIDATED_ZERO_CREDIT_REPLACED_BY_V18",
        "invalidated_collector_bundle_id": PARENT_ID,
        "failed_attempt": "tpcc-b-solo-v17a-20260801t205358p08-7e2e13aa",
        "failure": "paired-only control-tree verifier was invoked in sealed solo mode",
        "repair": "run reciprocal collector-tree proof only in qualification_paired mode",
        "paired_control_tree_proof_unchanged": True,
        "solo_original_fail_closed_inventory_unchanged": True,
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
def test_v18_dispatches_paired_tree_proof_only_for_paired_mode(tmp_path):
 m=json.loads((ROOT/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id")
 raw=(json.dumps(u,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\\n").encode()
 assert m["bundle_id"]==ROOT.name==hashlib.sha256(raw).hexdigest()
 assert m["schema_version"]=="mysql_tpch_tpcc_shared_collector_bundle_v18"
 assert m["solo_dispatch_policy"]=="original_inventory_without_reciprocal_peer_proof"
 assert m["paired_dispatch_policy"]=="exact_reciprocal_collector_tree_proof_unchanged"
 assert m["formal_launch_authorized"] is False and m["accepted_round_authorized"] is False
 for r,h in m["files"].items():assert hashlib.sha256((ROOT/r).read_bytes()).hexdigest()==h
 p=ROOT/"qualification/mysql_tpch_tpcc_shared_collector_entry_v1.py";s=p.read_text()
 assert 'if "--qualification-paired" in sys.argv:' in s
 assert s.count("_classify_verified_collector_trees(result, args[4], args[5])")==1
 assert 'argv.count("--qualification-paired") != 1' in s
 py_compile.compile(str(p),doraise=True,cfile=str(tmp_path/"v18.pyc"))
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
            raise BuildError("v18 symlink")
        os.chmod(path, stat.S_IMODE(path.stat().st_mode) & ~0o222)
    os.chmod(root, stat.S_IMODE(root.stat().st_mode) & ~0o222)


def file_map(root):
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise BuildError("v18 closure symlink")
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
    old = b'''    _classify_verified_collector_trees(result, args[4], args[5])\n    profile = _active_profile\n'''
    new = b'''    if "--qualification-paired" in sys.argv:\n        _classify_verified_collector_trees(result, args[4], args[5])\n    profile = _active_profile\n'''
    payload[ENTRY] = replace_exact(payload[ENTRY], old, new, "solo dispatch")
    for relative, data in list(payload.items()):
        if relative.startswith("tests/") and relative.endswith(".py"):
            payload[relative] = data.replace(
                b"mysql_tpch_tpcc_shared_collector_bundle_v17", SCHEMA.encode()
            )
    payload[BUILDER] = SOURCE.read_bytes(); modes[BUILDER] = 0o444
    payload[INVALIDATION] = canonical(invalidation()); modes[INVALIDATION] = 0o444
    payload[TEST] = test_source(); modes[TEST] = 0o444
    unsigned = {k: v for k, v in parent.items() if k not in {"bundle_id", "files"}}
    unsigned.update({
        "schema_version": SCHEMA,
        "status": "SEALED_SOLO_PAIRED_DISPATCH_NOT_LAUNCH_AUTHORITY",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_SHA,
        "solo_dispatch_policy": "original_inventory_without_reciprocal_peer_proof",
        "paired_dispatch_policy": "exact_reciprocal_collector_tree_proof_unchanged",
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
            temp = Path(tempfile.mkdtemp(prefix="mysql-shared-v18-test-")); scratch = temp / bundle_id
            try:
                shutil.copytree(stage, scratch)
                tests = sorted(str(p) for p in (scratch / "tests").glob("test_*.py"))
                result = subprocess.run([str(PYTHON), "-m", "pytest", "-q", *tests],
                                        cwd=ROOT, text=True, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, timeout=180)
                if result.returncode:
                    raise BuildError("v18 tests failed:\n" + result.stdout[-16000:])
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
        raise BuildError("published v18 differs")
    print(json.dumps({"status":"PASS_IMMUTABLE_SHARED_COLLECTOR_V18",
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
