#!/usr/bin/env python3
"""Build immutable shared collector v14 with exact /proc exit-race handling.

V13 collected all 181 paired samples on both workloads, but a short-lived
process could disappear between the full identity snapshot and enumeration of
``/proc/<pid>/fd``.  The core caught that ENOENT as a generic qualification
failure, counted it as a permission failure, and therefore never exercised
the already sealed bounded zero-race retry policy.  V14 classifies only that
exact ENOENT as a race.  EACCES and every other FD/IO error remain fail-closed.
No resource, workload, sampling, algorithm, or authorization setting changes.
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
PARENT_ID = "dc0fbc2b68b82491347d2820188315297f400e19605afbaed5c78d78d222bedc"
PARENT_SHA = "653791a8c130a2f5f9322c6bef761978b79212eb51b030c0c1d18b52e583436d"
PARENT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v13" / PARENT_ID
OUT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v14"
SCHEMA = "mysql_tpch_tpcc_shared_collector_bundle_v14"
CORE = "qualification/mysql_distributed_live_readback_qualification_v1.py"
BUILDER = "authority/build_mysql_tpch_tpcc_shared_collector_v14.py"
INVALIDATION = "authority/v13c_proc_fd_exit_race_invalidation.json"
TEST = "tests/test_shared_collector_proc_fd_exit_race_v14.py"
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
        != "mysql_tpch_tpcc_shared_collector_bundle_v13"
        or manifest.get("graceful_quiescence_seconds") != 120
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
        raise BuildError("v13 parent identity, scope, or gates differ")
    for relative, expected in manifest["files"].items():
        path = PARENT / relative
        if path.is_symlink() or not path.is_file() or digest(path) != expected:
            raise BuildError("v13 parent closure differs: " + relative)
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
def test_v14_classifies_only_proc_fd_enoent_as_retryable_race():
 m=json.loads((ROOT/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id")
 raw=(json.dumps(u,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\\n").encode()
 assert m["bundle_id"]==ROOT.name==hashlib.sha256(raw).hexdigest()
 assert m["schema_version"]=="mysql_tpch_tpcc_shared_collector_bundle_v14"
 assert m["proc_fd_exit_enoent_classification"]=="race_retry_requires_final_zero_races"
 assert m["permission_errors_remain_fail_closed"] is True
 assert m["graceful_quiescence_seconds"]==120 and m["minimum_samples"]==181
 assert m["maximum_sample_gap_seconds"]==3.5
 assert m["native_marker_maximum_age_seconds"]==1.5
 assert m["native_marker_poll_timeout_seconds"]==2.5
 assert m["database_counter_timeout_seconds"]==1.0
 assert m["proc_snapshot_maximum_attempts"]==16
 assert m["proc_snapshot_acceptance_requires_zero_races"] is True
 assert m["formal_launch_authorized"] is False and m["accepted_round_authorized"] is False
 for relative,expected in m["files"].items():assert hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()==expected
 core=(ROOT/"qualification/mysql_distributed_live_readback_qualification_v1.py").read_text()
 assert "entries = list(directory.iterdir())\\n    except FileNotFoundError:\\n        raise\\n    except (OSError, PermissionError)" in core
 assert "matched_fds, writable_fds = _process_target_device_write_fds(\\n                pid, major_minor\\n            )\\n        except FileNotFoundError:\\n            races += 1\\n            continue\\n        except QualificationError:" in core
 assert 'conflicts.append("process_fd_or_io_permission_failure")' in core
 entry=(ROOT/"qualification/mysql_tpch_tpcc_shared_collector_entry_v1.py").read_text()
 assert "snapshot_attempts < 16" in entry
 assert '"accepted_snapshot_requires_zero_races": True' in entry
 py_compile.compile(str(ROOT/"qualification/mysql_distributed_live_readback_qualification_v1.py"),doraise=True,cfile=str(Path("/tmp")/(ROOT.name+"-v14-core.pyc")))
'''


def invalidation():
    return {
        "schema_version": "mysql_shared_collector_proc_fd_exit_race_invalidation_v1",
        "status": "INVALIDATED_ZERO_CREDIT_REPLACED_BY_V14",
        "invalidated_collector_bundle_id": PARENT_ID,
        "failed_attempts": [
            "tpch-a-paired-v13c-20260801t192000p08-6c3809b8",
            "tpcc-b-paired-v13c-20260801t192000p08-7e2e13aa",
        ],
        "observed_complete_samples": {"tpch": 181, "tpcc": 181},
        "primary_failure": "process_fd_or_io_permission_failure",
        "observed_permission_failure_count": 2,
        "observed_race_count": 0,
        "foreign_writer_count": 0,
        "root_cause": "proc_fd_directory_enoent_was_folded_into_permission_failure",
        "repair": "classify_only_proc_fd_directory_enoent_as_race_then_bounded_retry",
        "true_permission_errors_remain_fail_closed": True,
        "accepted_snapshot_requires_zero_races": True,
        "qualification_credit": 0,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "resource_workload_sample_gates_algorithm_c_unchanged": True,
        "postgresql_mutation": False,
    }


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise BuildError("symlink in shared collector v14")
        os.chmod(path, stat.S_IMODE(path.stat().st_mode) & ~0o222)
    os.chmod(root, stat.S_IMODE(root.stat().st_mode) & ~0o222)


def remove_caches(root):
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_dir() and path.name in {"__pycache__", ".pytest_cache"}:
            shutil.rmtree(path)
        elif path.is_file() and path.suffix in {".pyc", ".pyo"}:
            path.unlink()


def run_tests(stage, bundle_id):
    scratch_parent = Path(tempfile.mkdtemp(prefix="mysql-shared-v14-test-"))
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
            raise BuildError("v14 tests failed:\n" + result.stdout[-16000:])
        return result.stdout.strip()
    finally:
        shutil.rmtree(scratch_parent)


def relative_files(root):
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise BuildError("symlink in published v14")
        relative = path.relative_to(root).as_posix()
        if path.is_file() and relative not in {"bundle_manifest.json", "bundle_manifest.json.sha256"}:
            result[relative] = digest(path)
    return result


def main():
    parent = verified_parent()
    payload = {relative: (PARENT / relative).read_bytes() for relative in parent["files"]}
    modes = {relative: stat.S_IMODE((PARENT / relative).stat().st_mode) for relative in parent["files"]}
    core = payload[CORE]
    core = replace_exact(
        core,
        b'''    try:\n        entries = list(directory.iterdir())\n    except (OSError, PermissionError) as exc:\n''',
        b'''    try:\n        entries = list(directory.iterdir())\n    except FileNotFoundError:\n        raise\n    except (OSError, PermissionError) as exc:\n''',
        "proc fd directory ENOENT classification",
    )
    core = replace_exact(
        core,
        b'''        try:\n            matched_fds, writable_fds = _process_target_device_write_fds(\n                pid, major_minor\n            )\n        except QualificationError:\n''',
        b'''        try:\n            matched_fds, writable_fds = _process_target_device_write_fds(\n                pid, major_minor\n            )\n        except FileNotFoundError:\n            races += 1\n            continue\n        except QualificationError:\n''',
        "proc fd caller race classification",
    )
    payload[CORE] = core
    for relative, data in list(payload.items()):
        if relative.startswith("tests/") and relative.endswith(".py"):
            payload[relative] = data.replace(
                b"mysql_tpch_tpcc_shared_collector_bundle_v13", SCHEMA.encode("ascii")
            )
    payload[BUILDER] = SOURCE.read_bytes(); modes[BUILDER] = 0o444
    payload[INVALIDATION] = canonical(invalidation()); modes[INVALIDATION] = 0o444
    payload[TEST] = test_source(); modes[TEST] = 0o444
    unsigned = {key: value for key, value in parent.items() if key not in {"bundle_id", "files"}}
    unsigned.update({
        "schema_version": SCHEMA,
        "status": "SEALED_PROC_FD_EXIT_RACE_FIX_NOT_LAUNCH_AUTHORITY",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_SHA,
        "proc_fd_exit_enoent_classification": "race_retry_requires_final_zero_races",
        "permission_errors_remain_fail_closed": True,
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
        raise BuildError("published v14 identity, closure, or mode differs")
    print(json.dumps({"status":"PASS_IMMUTABLE_SHARED_COLLECTOR_V14","bundle":str(destination),"bundle_id":bundle_id,"bundle_manifest_sha256":digest(destination/"bundle_manifest.json"),"verified_files":len(manifest["files"]),"tests":test_result,"graceful_quiescence_seconds":120,"minimum_samples":181,"formal_launch_authorized":False,"accepted_round_authorized":False},indent=2,sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (BuildError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print("NOT_READY: " + str(exc), file=__import__("sys").stderr)
        raise SystemExit(2)
