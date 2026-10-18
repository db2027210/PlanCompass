#!/usr/bin/env python3
"""Build immutable v17 with ordered workload-stream consumption.

The v16 collectors sampled the newest peer row.  A two-second producer jitter
could therefore skip one still-valid sequence and exhaust the peer's fixed
181-row stream at the final sample.  v17 consumes exactly the next sequence;
freshness and the 3.5-second maximum gap remain fail-closed.
"""
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
PARENT_ID = "a4146fdd02f6e79ddc5518fe3939de50b107090d1ea6d03fd7930900df3c3af7"
PARENT_SHA = "84800e20d6a79d398d2fbb3f005f018b7d1ed859ceb802159e93ff5518bffa3b"
PARENT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v16" / PARENT_ID
OUT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v17"
SCHEMA = "mysql_tpch_tpcc_shared_collector_bundle_v17"
CORE = "qualification/mysql_distributed_live_readback_qualification_v1.py"
BUILDER = "authority/build_mysql_tpch_tpcc_shared_collector_v17.py"
INVALIDATION = "authority/v16a_ordered_stream_and_swap_invalidation.json"
TEST = "tests/test_shared_collector_ordered_stream_v17.py"
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
    unsigned = dict(manifest)
    unsigned.pop("bundle_id", None)
    if (
        manifest.get("bundle_id") != PARENT_ID
        or digest(path) != PARENT_SHA
        or sha_bytes(canonical(unsigned)) != PARENT_ID
        or manifest.get("schema_version")
        != "mysql_tpch_tpcc_shared_collector_bundle_v16"
        or manifest.get("paired_peer_client_policy")
        != "signed_container_id_exact_cpu_zero_target_writer"
        or manifest.get("unknown_process_change_remains_fail_closed") is not True
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise BuildError("v16 parent differs")
    for relative, expected in manifest["files"].items():
        path = PARENT / relative
        if path.is_symlink() or not path.is_file() or digest(path) != expected:
            raise BuildError("v16 closure differs: " + relative)
    return manifest


def invalidation():
    return {
        "schema_version": "mysql_shared_v16a_invalidation_v1",
        "status": "INVALIDATED_ZERO_CREDIT_REPLACED_BY_V17",
        "invalidated_collector_bundle_id": PARENT_ID,
        "failed_attempts": [
            "tpch-a-paired-v16a-20260801t202300p08-6c3809b8",
            "tpcc-b-paired-v16a-20260801t202300p08-7e2e13aa",
        ],
        "observed_complete_metric_rows": {"tpch": 181, "tpcc": 181},
        "tpcc_failure": "newest-row consumption skipped a peer sequence after producer jitter",
        "tpch_failures": [
            "stale shared revenue0 view collision",
            "8192 bytes live cgroup-v1 swap with zero swap capacity",
        ],
        "repair_in_this_bundle": "consume exact next sequence without weakening freshness",
        "separate_repairs_required": [
            "unique Q15 view identity in a new immutable SQL adapter",
            "restart TPC-H database into a clean zero-swap cgroup and reseal live authority",
        ],
        "qualification_credit": 0,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "postgresql_mutation": False,
    }


def test_source():
    return b'''from __future__ import annotations
import hashlib,importlib.util,json,py_compile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def test_v17_consumes_next_row_without_skipping_fresh_sequence(tmp_path):
 m=json.loads((ROOT/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id")
 raw=(json.dumps(u,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\\n").encode()
 assert m["bundle_id"]==ROOT.name==hashlib.sha256(raw).hexdigest()
 assert m["schema_version"]=="mysql_tpch_tpcc_shared_collector_bundle_v17"
 assert m["workload_stream_consumption_policy"]=="exact_next_sequence_no_latest_row_skip"
 assert m["maximum_sample_gap_seconds_unchanged"]==3.5
 assert m["formal_launch_authorized"] is False and m["accepted_round_authorized"] is False
 for r,h in m["files"].items():assert hashlib.sha256((ROOT/r).read_bytes()).hexdigest()==h
 p=ROOT/"qualification/mysql_distributed_live_readback_qualification_v1.py"
 s=importlib.util.spec_from_file_location("v17core",str(p));x=importlib.util.module_from_spec(s);s.loader.exec_module(x)
 now=time.time();stream=tmp_path/"metric.jsonl"
 rows=[{"schema_version":"mysql_algorithm_c_debug_workload_metric_sample_v1","qualification_attempt_id":"attempt-abcdefghijklmnopqrstuvwxyz0123456789","sequence":i,"epoch_seconds":now,"completed_total":i+1,"query_duration_ms":1.0} for i in range(3)]
 stream.write_text("".join(json.dumps(v)+"\\n" for v in rows))
 assert x.workload_metric_sample(stream,rows[0]["qualification_attempt_id"],0,0.1)["sequence"]==1
 py_compile.compile(str(p),doraise=True,cfile=str(tmp_path/"v17.pyc"))
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
            raise BuildError("v17 symlink")
        os.chmod(path, stat.S_IMODE(path.stat().st_mode) & ~0o222)
    os.chmod(root, stat.S_IMODE(root.stat().st_mode) & ~0o222)


def file_map(root):
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise BuildError("v17 closure symlink")
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
    old = b'''            value = json.loads(rows[-1])\n            if not isinstance(value, dict):\n'''
    new = b'''            values = [json.loads(row) for row in rows]\n            if previous_sequence is None:\n                value = values[-1]\n            else:\n                expected_sequence = previous_sequence + 1\n                matches = [\n                    row for row in values\n                    if isinstance(row, dict)\n                    and row.get("sequence") == expected_sequence\n                ]\n                if len(matches) != 1:\n                    raise QualificationError(\n                        "workload metric exact next sequence is absent or ambiguous"\n                    )\n                value = matches[0]\n            if not isinstance(value, dict):\n'''
    payload[CORE] = replace_exact(payload[CORE], old, new, "ordered stream")
    for relative, data in list(payload.items()):
        if relative.startswith("tests/") and relative.endswith(".py"):
            payload[relative] = data.replace(
                b"mysql_tpch_tpcc_shared_collector_bundle_v16", SCHEMA.encode()
            )
    payload[BUILDER] = SOURCE.read_bytes(); modes[BUILDER] = 0o444
    payload[INVALIDATION] = canonical(invalidation()); modes[INVALIDATION] = 0o444
    payload[TEST] = test_source(); modes[TEST] = 0o444
    unsigned = {k: v for k, v in parent.items() if k not in {"bundle_id", "files"}}
    unsigned.update({
        "schema_version": SCHEMA,
        "status": "SEALED_ORDERED_FRESH_STREAM_CONSUMPTION_NOT_LAUNCH_AUTHORITY",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_SHA,
        "workload_stream_consumption_policy": "exact_next_sequence_no_latest_row_skip",
        "maximum_sample_gap_seconds_unchanged": 3.5,
        "fresh_sequence_at_every_sample_required": True,
        "unknown_process_change_remains_fail_closed": True,
        "resource_workload_sample_gates_algorithm_c_unchanged": True,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "files": {r: sha_bytes(d) for r, d in sorted(payload.items())},
    })
    bundle_id = sha_bytes(canonical(unsigned))
    manifest = dict(unsigned, bundle_id=bundle_id)
    OUT.mkdir(parents=True, exist_ok=True)
    destination = OUT / bundle_id
    test_result = "existing immutable bundle reverified"
    if not destination.exists():
        stage = OUT / (".stage-" + bundle_id + "-" + secrets.token_hex(6))
        stage.mkdir(mode=0o700)
        try:
            for relative, data in payload.items():
                path = stage / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                os.chmod(path, modes.get(relative, 0o444) & ~0o222)
            raw = canonical(manifest)
            (stage / "bundle_manifest.json").write_bytes(raw)
            (stage / "bundle_manifest.json.sha256").write_text(
                sha_bytes(raw) + "  bundle_manifest.json\n", encoding="ascii"
            )
            temp = Path(tempfile.mkdtemp(prefix="mysql-shared-v17-test-"))
            scratch = temp / bundle_id
            try:
                shutil.copytree(stage, scratch)
                tests = sorted(str(p) for p in (scratch / "tests").glob("test_*.py"))
                result = subprocess.run(
                    [str(PYTHON), "-m", "pytest", "-q", *tests], cwd=ROOT,
                    text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    timeout=180,
                )
                if result.returncode:
                    raise BuildError("v17 tests failed:\n" + result.stdout[-16000:])
                test_result = result.stdout.strip()
            finally:
                shutil.rmtree(temp)
            clean(stage); freeze(stage); os.rename(stage, destination)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    published = json.loads((destination / "bundle_manifest.json").read_text())
    check = dict(published); check.pop("bundle_id", None)
    if (
        published != manifest
        or sha_bytes(canonical(check)) != bundle_id
        or file_map(destination) != manifest["files"]
        or any(stat.S_IMODE(p.stat().st_mode) & 0o222
               for p in [destination, *destination.rglob("*")])
    ):
        raise BuildError("published v17 differs")
    print(json.dumps({
        "status": "PASS_IMMUTABLE_SHARED_COLLECTOR_V17",
        "bundle": str(destination),
        "bundle_id": bundle_id,
        "bundle_manifest_sha256": digest(destination / "bundle_manifest.json"),
        "verified_files": len(manifest["files"]),
        "tests": test_result,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (BuildError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print("NOT_READY: " + str(exc), file=__import__("sys").stderr)
        raise SystemExit(2)
