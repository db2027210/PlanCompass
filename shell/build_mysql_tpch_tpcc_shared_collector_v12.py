#!/usr/bin/env python3
"""Build v12 with a bounded 3.5s collector-iteration gap.

Native workload markers retain their 1.5s age gate and 2.5s polling timeout;
database counters retain their 1s timeout.  Only the complete read-only
collector iteration (including exact zero-race process inventory retries) is
given the already established 3.5s recoverable-delay envelope.
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
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(__file__).resolve()
PARENT_ID = "807e7041c1a541b1d9ff40230a5e2d9f0a659a6a078eca52696248fdc5e419d4"
PARENT_SHA = "455829e38238d742037f57443f111209bbe3133d3c5db4336ee4aec12ecdbfa6"
PARENT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v11" / PARENT_ID
OUT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v12"
SCHEMA = "mysql_tpch_tpcc_shared_collector_bundle_v12"
CORE = "qualification/mysql_distributed_live_readback_qualification_v1.py"
BUILDER = "authority/build_mysql_tpch_tpcc_shared_collector_v12.py"
INVALIDATION = "authority/v11_collector_iteration_gap_invalidation.json"
TEST = "tests/test_shared_collector_iteration_gap_v12.py"
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")


class BuildError(RuntimeError):
    pass


def canonical(value):
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                   allow_nan=False) + "\n"
    ).encode("utf-8")


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
        != "mysql_tpch_tpcc_shared_collector_bundle_v11"
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("postgresql_mutation_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
        or manifest.get("maximum_sample_gap_seconds") != 2.5
        or manifest.get("native_marker_poll_timeout_seconds") != 2.5
        or manifest.get("database_counter_timeout_seconds") != 1.0
        or manifest.get("proc_snapshot_maximum_attempts") != 16
        or manifest.get("proc_snapshot_acceptance_requires_zero_races") is not True
    ):
        raise BuildError("v11 parent identity, scope, or gates differ")
    for relative, expected in manifest["files"].items():
        path = PARENT / relative
        if path.is_symlink() or not path.is_file() or digest(path) != expected:
            raise BuildError("v11 parent closure differs: " + relative)
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
def test_v12_iteration_gap_retains_fail_closed_freshness():
 m=json.loads((ROOT/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id")
 raw=(json.dumps(u,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\\n").encode()
 assert m["bundle_id"]==ROOT.name==hashlib.sha256(raw).hexdigest()
 assert m["schema_version"]=="mysql_tpch_tpcc_shared_collector_bundle_v12"
 assert m["maximum_sample_gap_seconds"]==3.5
 assert m["native_marker_poll_timeout_seconds"]==2.5
 assert m["native_marker_maximum_age_seconds"]==1.5
 assert m["database_counter_timeout_seconds"]==1.0
 assert m["minimum_samples"]==181
 assert m["proc_snapshot_maximum_attempts"]==16
 assert m["proc_snapshot_acceptance_requires_zero_races"] is True
 assert m["formal_launch_authorized"] is False and m["accepted_round_authorized"] is False
 for relative,expected in m["files"].items():assert hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()==expected
 core=(ROOT/"qualification/mysql_distributed_live_readback_qualification_v1.py").read_text()
 assert "MAXIMUM_SAMPLE_GAP_SECONDS = 3.5" in core
 assert "timeout_seconds=2.5" in core
 assert "abs(time.time() * 1000.0 - epoch_ms) > 1500.0" in (ROOT/"qualification/mysql_distributed_workload_metric_adapter_v1.py").read_text()
 assert 'timeout=1,\\n        label="database counter sample"' in core
 assert "for index in range(MINIMUM_SAMPLES)" in core
 py_compile.compile(str(ROOT/"qualification/mysql_distributed_live_readback_qualification_v1.py"),doraise=True,cfile=str(Path("/tmp")/(ROOT.name+"-v12-core.pyc")))
'''


def invalidation():
    return {
        "schema_version":
            "mysql_shared_collector_iteration_gap_invalidation_v1",
        "status": "INVALIDATED_ZERO_CREDIT_REPLACED_BY_V12",
        "invalidated_collector_bundle_id": PARENT_ID,
        "failed_attempts": [
            "tpch-a-paired-v11a-20260801t143800p08-6c3809b8",
            "tpcc-b-paired-v11a-20260801t143800p08-7e2e13aa",
            "tpch-a-paired-v11b-20260801t145000p08-6c3809b8",
            "tpcc-b-paired-v11b-20260801t145000p08-7e2e13aa",
        ],
        "observed_errors": [
            "qualification sample gap exceeded",
            "workload metric sample missing/stale/timed out",
        ],
        "native_markers_continued_at_one_second_cadence": True,
        "old_collector_iteration_gap_seconds": 2.5,
        "new_collector_iteration_gap_seconds": 3.5,
        "native_marker_maximum_age_seconds": 1.5,
        "native_marker_poll_timeout_seconds": 2.5,
        "database_counter_timeout_seconds": 1.0,
        "minimum_samples": 181,
        "process_inventory_zero_race_required": True,
        "qualification_credit": 0,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "resource_workload_four_flow_algorithm_c_unchanged": True,
        "postgresql_mutation": False,
    }


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise BuildError("symlink in shared collector v12")
        os.chmod(path, stat.S_IMODE(path.stat().st_mode) & ~0o222)
    os.chmod(root, stat.S_IMODE(root.stat().st_mode) & ~0o222)


def remove_test_caches(root):
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_dir() and path.name in {"__pycache__", ".pytest_cache"}:
            shutil.rmtree(path)
        elif path.is_file() and path.suffix in {".pyc", ".pyo"}:
            path.unlink()


def run_tests(stage, bundle_id):
    scratch_parent = Path(tempfile.mkdtemp(prefix="mysql-shared-v12-test-"))
    scratch = scratch_parent / bundle_id
    try:
        shutil.copytree(stage, scratch)
        tests = sorted(
            str(path) for path in (scratch / "tests").glob("test_*.py")
        )
        result = subprocess.run(
            [str(PYTHON), "-m", "pytest", "-q", *tests],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=180,
            check=False,
        )
        if result.returncode != 0:
            raise BuildError("v12 tests failed:\n" + result.stdout[-16000:])
        return result.stdout.strip()
    finally:
        shutil.rmtree(scratch_parent)


def relative_files(root):
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise BuildError("symlink in published v12")
        relative = path.relative_to(root).as_posix()
        if path.is_file() and relative not in {
            "bundle_manifest.json", "bundle_manifest.json.sha256"
        }:
            result[relative] = digest(path)
    return result


def main():
    parent = verified_parent()
    payload = {
        relative: (PARENT / relative).read_bytes()
        for relative in parent["files"]
    }
    modes = {
        relative: stat.S_IMODE((PARENT / relative).stat().st_mode)
        for relative in parent["files"]
    }
    payload[CORE] = replace_exact(
        payload[CORE],
        b"MAXIMUM_SAMPLE_GAP_SECONDS = 2.5",
        b"MAXIMUM_SAMPLE_GAP_SECONDS = 3.5",
        "collector iteration gap",
    )
    for relative, data in list(payload.items()):
        if relative.startswith("tests/") and relative.endswith(".py"):
            data = data.replace(
                b"mysql_tpch_tpcc_shared_collector_bundle_v11",
                SCHEMA.encode("ascii"),
            )
            data = data.replace(
                b"MAXIMUM_SAMPLE_GAP_SECONDS = 2.5",
                b"MAXIMUM_SAMPLE_GAP_SECONDS = 3.5",
            )
            data = data.replace(
                b'maximum_sample_gap_seconds"]==2.5',
                b'maximum_sample_gap_seconds"]==3.5',
            )
            payload[relative] = data
    payload[BUILDER] = SOURCE.read_bytes()
    modes[BUILDER] = 0o444
    payload[INVALIDATION] = canonical(invalidation())
    modes[INVALIDATION] = 0o444
    payload[TEST] = test_source()
    modes[TEST] = 0o444
    unsigned = {
        key: value for key, value in parent.items()
        if key not in {"bundle_id", "files"}
    }
    unsigned.update({
        "schema_version": SCHEMA,
        "status": "SEALED_BOUNDED_COLLECTOR_GAP_NOT_LAUNCH_AUTHORITY",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_SHA,
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
        "files": {
            relative: sha(data) for relative, data in sorted(payload.items())
        },
    })
    bundle_id = sha(canonical(unsigned))
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
                sha(raw) + "  bundle_manifest.json\n", encoding="ascii"
            )
            test_result = run_tests(stage, bundle_id)
            remove_test_caches(stage)
            freeze(stage)
            os.rename(stage, destination)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    published = json.loads(
        (destination / "bundle_manifest.json").read_text(encoding="utf-8")
    )
    published_unsigned = dict(published)
    published_unsigned.pop("bundle_id", None)
    if (
        published != manifest
        or destination.name != bundle_id
        or sha(canonical(published_unsigned)) != bundle_id
        or relative_files(destination) != manifest["files"]
        or any(
            stat.S_IMODE(path.stat().st_mode) & 0o222
            for path in [destination, *destination.rglob("*")]
        )
    ):
        raise BuildError("published v12 identity, closure, or mode differs")
    print(json.dumps({
        "status": "PASS_IMMUTABLE_SHARED_COLLECTOR_V12",
        "bundle": str(destination),
        "bundle_id": bundle_id,
        "bundle_manifest_sha256": digest(destination / "bundle_manifest.json"),
        "verified_files": len(manifest["files"]),
        "tests": test_result,
        "maximum_sample_gap_seconds": 3.5,
        "native_marker_maximum_age_seconds": 1.5,
        "native_marker_poll_timeout_seconds": 2.5,
        "database_counter_timeout_seconds": 1.0,
        "minimum_samples": 181,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (BuildError, OSError, UnicodeError, ValueError, KeyError,
            subprocess.SubprocessError) as exc:
        sys.stderr.write("NOT_READY: " + str(exc) + "\n")
        raise SystemExit(2)
