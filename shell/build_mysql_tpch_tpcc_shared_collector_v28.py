#!/usr/bin/env python3
"""Seal v28 with auditable /proc exit tombstones for transient children.

The v27 collector retries a complete process inventory sixteen times, but a
busy paired workload continuously creates short-lived docker-exec/mysql
children.  A child can therefore disappear during every retry even though
there is no permission gap, PID reuse, affinity overlap, or foreign writer.

V28 preserves fail-closed inventory for live processes.  A FileNotFoundError
is accepted only when the PID is still absent at snapshot completion and is
recorded as an explicit tombstone.  Target mysqld, workload-root, and collector
root exits remain unconditionally fatal; a PID that reappears/reuses is still
an unverified race and fails the sample.
"""
from __future__ import annotations

import importlib.util
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
spec = importlib.util.spec_from_file_location(
    "v25", ROOT / "shell/build_mysql_tpch_tpcc_shared_collector_v25.py"
)
v25 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v25)
u = v25.u

PARENT_ID = "7adb292783ca1a3cf68744519e0e60e0081f2b2e2ffbe46d2476062d564b7868"
PARENT_SHA = "c404a3c5ed273066379e7d6bdb923c54b23deef688de969da50314cacfdcf5b3"
PARENT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v27" / PARENT_ID
OUT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v28"
CORE = "qualification/mysql_distributed_live_readback_qualification_v1.py"
BUILDER = "authority/build_mysql_tpch_tpcc_shared_collector_v28.py"
INVALIDATION = "authority/tpcc_v27b_proc_exit_race_invalidation.json"
TEST = "tests/test_shared_collector_verified_exit_tombstones_v28.py"
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")


def load_parent():
    path = PARENT / "bundle_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    unsigned = dict(manifest)
    unsigned.pop("bundle_id", None)
    if (
        manifest.get("bundle_id") != PARENT_ID
        or u.digest(path) != PARENT_SHA
        or u.sha_bytes(u.canonical(unsigned)) != PARENT_ID
        or manifest.get("schema_version")
        != "mysql_tpch_tpcc_shared_collector_bundle_v27"
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise u.BuildError("v27 parent differs")
    for relative, expected in manifest["files"].items():
        item = PARENT / relative
        if item.is_symlink() or not item.is_file() or u.digest(item) != expected:
            raise u.BuildError("v27 closure differs: " + relative)
    return manifest


def patch_core(payload):
    payload = u.replace_exact(
        payload,
        b'''    all_processes = {}\n    races = 0\n''',
        b'''    all_processes = {}\n    races = 0\n    exit_candidates = []\n    protected_roots = {\n        int(target_pid), int(workload_pid), int(control_pid), os.getpid()\n    }\n''',
        "inventory tombstone state",
    )
    payload = u.replace_exact(
        payload,
        b'''        except FileNotFoundError:\n            races += 1\n            continue\n        except (OSError, PermissionError) as exc:\n            raise QualificationError(\n                "full process identity inventory unreadable: %s" % exc\n            )\n''',
        b'''        except FileNotFoundError:\n            # A PID enumerated at the beginning may naturally exit before its\n            # identity is read.  It is not silently ignored: snapshot\n            # completion below must prove that the PID is still absent.\n            if pid in protected_roots:\n                races += 1\n            else:\n                exit_candidates.append({\n                    "pid": pid,\n                    "phase": "identity",\n                    "starttime_ticks": None,\n                    "cgroup_paths_sha256": None,\n                })\n            continue\n        except (OSError, PermissionError) as exc:\n            raise QualificationError(\n                "full process identity inventory unreadable: %s" % exc\n            )\n''',
        "identity exit candidate",
    )
    old_fd = b'''            matched_fds, writable_fds = _process_target_device_write_fds(\n                pid, major_minor\n            )\n        except FileNotFoundError:\n            races += 1\n            continue\n        except QualificationError:\n'''
    new_fd = b'''            matched_fds, writable_fds = _process_target_device_write_fds(\n                pid, major_minor\n            )\n        except FileNotFoundError:\n            if pid in protected_roots:\n                races += 1\n            else:\n                exit_candidates.append({\n                    "pid": pid,\n                    "phase": "fd",\n                    "starttime_ticks": row["starttime_ticks"],\n                    "cgroup_paths_sha256": row["cgroup_paths_sha256"],\n                })\n            continue\n        except QualificationError:\n'''
    payload = u.replace_exact(payload, old_fd, new_fd, "fd exit candidate")
    old_io = b'''            process_io = _process_io_identity(row["_directory"])\n        except FileNotFoundError:\n            races += 1\n            continue\n        except QualificationError:\n'''
    new_io = b'''            process_io = _process_io_identity(row["_directory"])\n        except FileNotFoundError:\n            if pid in protected_roots:\n                races += 1\n            else:\n                exit_candidates.append({\n                    "pid": pid,\n                    "phase": "io",\n                    "starttime_ticks": row["starttime_ticks"],\n                    "cgroup_paths_sha256": row["cgroup_paths_sha256"],\n                })\n            continue\n        except QualificationError:\n'''
    payload = u.replace_exact(payload, old_io, new_io, "io exit candidate")
    payload = u.replace_exact(
        payload,
        b'''    if permission_failures:\n        conflicts.append("process_fd_or_io_permission_failure")\n    if races:\n        conflicts.append("process_inventory_race")\n    result = {\n''',
        b'''    # Resolve every disappearance at the end of the same snapshot.\n    # If a PID is present again (including reuse), the view is not complete and\n    # the existing retry/fail-closed path remains mandatory.\n    verified_exit_tombstones = []\n    for event in exit_candidates:\n        directory = PROC_ROOT / str(event["pid"])\n        try:\n            directory.stat()\n        except FileNotFoundError:\n            verified_exit_tombstones.append(event)\n        except (OSError, PermissionError):\n            permission_failures += 1\n        else:\n            races += 1\n    if permission_failures:\n        conflicts.append("process_fd_or_io_permission_failure")\n    if races:\n        conflicts.append("process_inventory_race")\n    result = {\n''',
        "tombstone completion verification",
    )
    payload = u.replace_exact(
        payload,
        b'''        "race_count": races,\n        "foreign_writer_count": foreign_writer_count,\n''',
        b'''        "race_count": races,\n        "verified_exit_tombstone_count": len(verified_exit_tombstones),\n        "verified_exit_tombstones": verified_exit_tombstones,\n        "verified_exit_tombstone_policy": {\n            "schema_version":\n                "mysql_shared_proc_verified_exit_tombstone_policy_v1",\n            "snapshot_completion_absence_required": True,\n            "target_workload_and_collector_roots_protected": True,\n            "pid_reappearance_or_reuse_is_race": True,\n            "permission_error_is_tombstone": False,\n            "foreign_device_attribution_unchanged": True,\n        },\n        "foreign_writer_count": foreign_writer_count,\n''',
        "tombstone evidence",
    )
    return payload


def invalidation():
    return {
        "schema_version": "mysql_tpcc_v27b_proc_exit_race_invalidation_v1",
        "status": "INVALIDATED_ZERO_CREDIT_REPLACED_BY_V28",
        "attempt": "tpcc-b-paired-v27b-20260802t021200p08-7e2e13aa",
        "sample_index": 93,
        "failure": "process_inventory_race_after_16_complete_snapshot_retries",
        "foreign_writer_count": 0,
        "permission_failure_count": 0,
        "repair": (
            "record only PIDs proven absent at snapshot completion as explicit "
            "exit tombstones; protected roots, PID reuse, permission failures, "
            "and foreign-device attribution remain fail closed"
        ),
        "qualification_credit": 0,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "postgresql_mutation": False,
    }


def test_source():
    return b'''from __future__ import annotations\nimport hashlib,json,py_compile\nfrom pathlib import Path\nROOT=Path(__file__).resolve().parents[1]\ndef test_v28_verified_exit_tombstones_preserve_fail_closed_roots():\n m=json.loads((ROOT/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id")\n raw=(json.dumps(u,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\\n").encode()\n assert m["bundle_id"]==ROOT.name==hashlib.sha256(raw).hexdigest()\n assert m["schema_version"]=="mysql_tpch_tpcc_shared_collector_bundle_v28"\n assert m["proc_exit_tombstone_policy"]=="snapshot_completion_absence_required"\n assert m["protected_root_exit_fail_closed"] is True\n assert m["pid_reappearance_or_reuse_fail_closed"] is True\n assert m["permission_errors_remain_fail_closed"] is True\n assert m["foreign_device_attribution_unchanged"] is True\n assert m["formal_launch_authorized"] is False and m["accepted_round_authorized"] is False\n core=(ROOT/"qualification/mysql_distributed_live_readback_qualification_v1.py").read_text()\n assert "verified_exit_tombstones" in core\n assert "snapshot_completion_absence_required" in core\n assert "target_workload_and_collector_roots_protected" in core\n assert "pid_reappearance_or_reuse_is_race" in core\n assert 'conflicts.append("process_inventory_race")' in core\n assert 'conflicts.append("process_fd_or_io_permission_failure")' in core\n py_compile.compile(str(ROOT/"qualification/mysql_distributed_live_readback_qualification_v1.py"),doraise=True,cfile=str(Path("/tmp")/(ROOT.name+"-v28-core.pyc")))\n'''


def main():
    parent = load_parent()
    payload = {r: (PARENT / r).read_bytes() for r in parent["files"]}
    modes = {r: stat.S_IMODE((PARENT / r).stat().st_mode) for r in parent["files"]}
    payload[CORE] = patch_core(payload[CORE])
    for relative, content in list(payload.items()):
        if relative.startswith("tests/") and relative.endswith(".py"):
            payload[relative] = content.replace(
                b"mysql_tpch_tpcc_shared_collector_bundle_v27",
                b"mysql_tpch_tpcc_shared_collector_bundle_v28",
            )
    # V14's old static assertion encoded the predecessor behavior.  Preserve
    # its other fail-closed checks while binding it to the superseding policy.
    v14 = "tests/test_shared_collector_proc_fd_exit_race_v14.py"
    payload[v14] = payload[v14].replace(
        b''' assert "matched_fds, writable_fds = _process_target_device_write_fds(\\n                pid, major_minor\\n            )\\n        except FileNotFoundError:\\n            races += 1\\n            continue\\n        except QualificationError:" in core\n''',
        b''' assert "matched_fds, writable_fds = _process_target_device_write_fds(" in core\n assert "snapshot_completion_absence_required" in core\n assert "pid_reappearance_or_reuse_is_race" in core\n''',
    )
    payload[BUILDER] = SOURCE.read_bytes()
    modes[BUILDER] = 0o444
    payload[INVALIDATION] = u.canonical(invalidation())
    modes[INVALIDATION] = 0o444
    payload[TEST] = test_source()
    modes[TEST] = 0o444
    unsigned = {k: v for k, v in parent.items() if k not in {"bundle_id", "files"}}
    unsigned.update({
        "schema_version": "mysql_tpch_tpcc_shared_collector_bundle_v28",
        "status": "SEALED_VERIFIED_PROC_EXIT_TOMBSTONES_NOT_LAUNCH_AUTHORITY",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_SHA,
        "collector_core_changed_from_v27": True,
        "proc_exit_tombstone_policy": "snapshot_completion_absence_required",
        "protected_root_exit_fail_closed": True,
        "pid_reappearance_or_reuse_fail_closed": True,
        "permission_errors_remain_fail_closed": True,
        "foreign_device_attribution_unchanged": True,
        "race_rows_ignored": False,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "files": {r: u.sha_bytes(d) for r, d in sorted(payload.items())},
    })
    bundle_id = u.sha_bytes(u.canonical(unsigned))
    manifest = dict(unsigned, bundle_id=bundle_id)
    OUT.mkdir(parents=True, exist_ok=True)
    destination = OUT / bundle_id
    result_text = "existing immutable bundle reverified"
    if not destination.exists():
        stage = OUT / (".stage-" + bundle_id + "-" + secrets.token_hex(6))
        stage.mkdir(mode=0o700)
        try:
            for relative, content in payload.items():
                item = stage / relative
                item.parent.mkdir(parents=True, exist_ok=True)
                item.write_bytes(content)
                os.chmod(item, modes.get(relative, 0o444) & ~0o222)
            raw = u.canonical(manifest)
            (stage / "bundle_manifest.json").write_bytes(raw)
            (stage / "bundle_manifest.json.sha256").write_text(
                u.sha_bytes(raw) + "  bundle_manifest.json\n", encoding="ascii"
            )
            temp = Path(tempfile.mkdtemp(prefix="mysql-shared-v28-test-"))
            scratch = temp / bundle_id
            try:
                shutil.copytree(stage, scratch)
                tests = sorted(str(p) for p in (scratch / "tests").glob("test_*.py"))
                result = subprocess.run(
                    [str(PYTHON), "-m", "pytest", "-q", *tests],
                    cwd=ROOT,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=180,
                )
                if result.returncode:
                    raise u.BuildError("v28 tests failed:\n" + result.stdout[-16000:])
                result_text = result.stdout.strip()
            finally:
                shutil.rmtree(temp)
            u.clean(stage)
            u.freeze(stage)
            os.rename(stage, destination)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    published = json.loads((destination / "bundle_manifest.json").read_text())
    check = dict(published)
    check.pop("bundle_id", None)
    if (
        published != manifest
        or u.sha_bytes(u.canonical(check)) != bundle_id
        or u.file_map(destination) != manifest["files"]
        or any(
            stat.S_IMODE(path.stat().st_mode) & 0o222
            for path in [destination, *destination.rglob("*")]
        )
    ):
        raise u.BuildError("published v28 differs")
    print(json.dumps({
        "status": "PASS_IMMUTABLE_SHARED_COLLECTOR_V28",
        "bundle": str(destination),
        "bundle_id": bundle_id,
        "bundle_manifest_sha256": u.digest(destination / "bundle_manifest.json"),
        "verified_files": len(manifest["files"]),
        "tests": result_text,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (u.BuildError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print("NOT_READY: " + str(exc), file=__import__("sys").stderr)
        raise SystemExit(2)
