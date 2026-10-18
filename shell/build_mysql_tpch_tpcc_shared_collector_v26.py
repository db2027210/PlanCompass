#!/usr/bin/env python3
"""Build v26 with exact dedicated-daemon device-write attribution."""
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
v25 = importlib.util.module_from_spec(spec); spec.loader.exec_module(v25)
u = v25.u
PARENT_ID = "97e625ef164ab1b27f4b2edc647ec7f1a8b54ac196be97366d53fb38726a88ec"
PARENT_SHA = "59457555ecb3b24b18fb2b458b64ecb77588493a7a442de3f7c98ae92c6e4e26"
PARENT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v25" / PARENT_ID
OUT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v26"
CORE = "qualification/mysql_distributed_live_readback_qualification_v1.py"
BUILDER = "authority/build_mysql_tpch_tpcc_shared_collector_v26.py"
INVALIDATION = "authority/v25a_daemon_attribution_invalidation.json"
TEST = "tests/test_shared_collector_daemon_attribution_v26.py"
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")


def load_parent():
    path = PARENT / "bundle_manifest.json"
    manifest = json.loads(path.read_text()); unsigned = dict(manifest)
    unsigned.pop("bundle_id", None)
    if (manifest.get("bundle_id") != PARENT_ID or u.digest(path) != PARENT_SHA
            or u.sha_bytes(u.canonical(unsigned)) != PARENT_ID
            or manifest.get("schema_version") != "mysql_tpch_tpcc_shared_collector_bundle_v25"
            or manifest.get("formal_launch_authorized") is not False
            or manifest.get("accepted_round_authorized") is not False):
        raise u.BuildError("v25 parent differs")
    for relative, expected in manifest["files"].items():
        item = PARENT / relative
        if item.is_symlink() or not item.is_file() or u.digest(item) != expected:
            raise u.BuildError("v25 closure differs: " + relative)
    return manifest


def invalidation():
    return {
        "schema_version": "mysql_shared_v25a_daemon_attribution_invalidation_v1",
        "status": "INVALIDATED_ZERO_CREDIT_REPLACED_BY_V26",
        "failed_attempts": [
            "tpch-a-paired-v25a-20260801t233000p08-6c3809b8",
            "tpcc-b-paired-v25a-20260801t233000p08-7e2e13aa",
        ],
        "completed_sample_counts": {"tpch": 181, "tpcc": 181},
        "primary_failure": (
            "the exact manifest/readback-bound dedicated dockerd was classified "
            "as a foreign co-resident writer to its own target device"
        ),
        "repair": (
            "bind main PID, start ticks, cgroup identity and exact daemon CPU set "
            "from the fresh own readback; classify only that identity as target "
            "infrastructure while retaining all foreign writer and overlap gates"
        ),
        "qualification_credit": 0,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "postgresql_mutation": False,
    }


def test_source():
    return b'''from __future__ import annotations
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def test_v26_exact_daemon_attribution_is_fail_closed():
 m=json.loads((ROOT/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id")
 raw=(json.dumps(u,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\\n").encode()
 assert m["bundle_id"]==ROOT.name==hashlib.sha256(raw).hexdigest()
 assert m["schema_version"]=="mysql_tpch_tpcc_shared_collector_bundle_v26"
 assert m["exact_dedicated_daemon_attribution"] is True
 core=(ROOT/"qualification/mysql_distributed_live_readback_qualification_v1.py").read_text()
 assert 'role = "target_infrastructure"' in core
 assert 'dedicated daemon rolling identity differs' in core
 assert 'dedicated daemon rolling CPU set differs' in core
 assert 'if role == "co_resident" and (' in core
 assert m["formal_launch_authorized"] is False and m["accepted_round_authorized"] is False
'''


def main():
    parent = load_parent()
    payload = {r: (PARENT / r).read_bytes() for r in parent["files"]}
    modes = {r: stat.S_IMODE((PARENT / r).stat().st_mode) for r in parent["files"]}
    core = payload[CORE]
    core = u.replace_exact(core, b'''    workload_mems,\n    docker_containers=None,\n''', b'''    workload_mems,\n    dedicated_daemon,\n    docker_containers=None,\n''', "rolling daemon argument")
    core = u.replace_exact(core, b'''    control_plane = {os.getpid(), int(control_pid)}\n\n    mysql_cpus = expand_cpuset(mysql_cpuset)\n''', b'''    control_plane = {os.getpid(), int(control_pid)}\n    if not isinstance(dedicated_daemon, dict):\n        raise QualificationError("dedicated daemon rolling identity is absent")\n    daemon_pid = _integer(\n        dedicated_daemon.get("main_pid"), "dedicated daemon PID", 1\n    )\n    daemon_row = all_processes.get(daemon_pid)\n    if (\n        daemon_row is None\n        or daemon_row["starttime_ticks"]\n        != _integer(\n            dedicated_daemon.get("main_pid_starttime_ticks"),\n            "dedicated daemon start ticks", 1,\n        )\n        or daemon_row["cgroup_sha256"]\n        != dedicated_daemon.get("cgroup_sha256")\n    ):\n        raise QualificationError("dedicated daemon rolling identity differs")\n    daemon_cpus = expand_cpuset(\n        dedicated_daemon.get("effective_daemon_cpuset") or ""\n    )\n    if not daemon_cpus:\n        raise QualificationError("dedicated daemon rolling CPU set is absent")\n\n    mysql_cpus = expand_cpuset(mysql_cpuset)\n''', "daemon identity validation")
    core = u.replace_exact(core, b'''        elif pid in control_plane:\n            role = "qualification_control_plane"\n        else:\n            role = "co_resident"\n''', b'''        elif pid in control_plane:\n            role = "qualification_control_plane"\n        elif pid == daemon_pid:\n            role = "target_infrastructure"\n        else:\n            role = "co_resident"\n''', "daemon role")
    core = u.replace_exact(core, b'''        elif role == "qualification_workload":\n            if actual_cpus != client_cpus or actual_mems != expected_mems:\n                conflicts.append("workload_affinity_or_mems")\n        if role == "co_resident" and (\n''', b'''        elif role == "qualification_workload":\n            if actual_cpus != client_cpus or actual_mems != expected_mems:\n                conflicts.append("workload_affinity_or_mems")\n        elif role == "target_infrastructure":\n            if actual_cpus != daemon_cpus:\n                conflicts.append("dedicated daemon rolling CPU set differs")\n        if role == "co_resident" and (\n''', "daemon CPU validation")
    core = u.replace_exact(core, b'''    workload_isolation,\n):\n    roots = _cgroup_roots(pid)\n''', b'''    workload_isolation,\n    dedicated_daemon,\n):\n    roots = _cgroup_roots(pid)\n''', "resource daemon argument")
    core = u.replace_exact(core, b'''                workload_isolation["cpuset_mems"],\n                docker_containers=docker_containers,\n''', b'''                workload_isolation["cpuset_mems"],\n                dedicated_daemon,\n                docker_containers=docker_containers,\n''', "rolling daemon call")
    core = u.replace_exact(core, b'''        own["resource_envelope"],\n        workload_isolation,\n    )\n''', b'''        own["resource_envelope"],\n        workload_isolation,\n        own["dedicated_daemon"],\n    )\n''', "resource daemon call")
    payload[CORE] = core
    for relative, content in list(payload.items()):
        if relative.startswith("tests/") and relative.endswith(".py"):
            payload[relative] = content.replace(
                b"mysql_tpch_tpcc_shared_collector_bundle_v25",
                b"mysql_tpch_tpcc_shared_collector_bundle_v26",
            )
    payload[BUILDER] = SOURCE.read_bytes(); modes[BUILDER] = 0o444
    payload[INVALIDATION] = u.canonical(invalidation()); modes[INVALIDATION] = 0o444
    payload[TEST] = test_source(); modes[TEST] = 0o444
    unsigned = {k: v for k, v in parent.items() if k not in {"bundle_id", "files"}}
    unsigned.update({
        "schema_version": "mysql_tpch_tpcc_shared_collector_bundle_v26",
        "status": "SEALED_EXACT_DAEMON_ATTRIBUTION_NOT_LAUNCH_AUTHORITY",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_SHA,
        "exact_dedicated_daemon_attribution": True,
        "foreign_writer_gate_unchanged": True,
        "co_resident_cpu_overlap_gate_unchanged": True,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "files": {r: u.sha_bytes(d) for r, d in sorted(payload.items())},
    })
    bundle_id = u.sha_bytes(u.canonical(unsigned)); manifest = dict(unsigned, bundle_id=bundle_id)
    OUT.mkdir(parents=True, exist_ok=True); destination = OUT / bundle_id
    result_text = "existing immutable bundle reverified"
    if not destination.exists():
        stage = OUT / (".stage-" + bundle_id + "-" + secrets.token_hex(6)); stage.mkdir(mode=0o700)
        try:
            for relative, content in payload.items():
                item = stage / relative; item.parent.mkdir(parents=True, exist_ok=True)
                item.write_bytes(content); os.chmod(item, modes.get(relative, 0o444) & ~0o222)
            raw = u.canonical(manifest); (stage / "bundle_manifest.json").write_bytes(raw)
            (stage / "bundle_manifest.json.sha256").write_text(u.sha_bytes(raw) + "  bundle_manifest.json\n")
            temp = Path(tempfile.mkdtemp(prefix="mysql-shared-v26-test-")); scratch = temp / bundle_id
            try:
                shutil.copytree(stage, scratch)
                tests = sorted(str(p) for p in (scratch / "tests").glob("test_*.py"))
                result = subprocess.run([str(PYTHON), "-m", "pytest", "-q", *tests], cwd=ROOT,
                    text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
                if result.returncode:
                    raise u.BuildError("v26 tests failed:\n" + result.stdout[-16000:])
                result_text = result.stdout.strip()
            finally:
                shutil.rmtree(temp)
            u.clean(stage); u.freeze(stage); os.rename(stage, destination)
        finally:
            if stage.exists(): shutil.rmtree(stage)
    published = json.loads((destination / "bundle_manifest.json").read_text())
    check = dict(published); check.pop("bundle_id", None)
    if (published != manifest or u.sha_bytes(u.canonical(check)) != bundle_id
            or u.file_map(destination) != manifest["files"]
            or any(stat.S_IMODE(p.stat().st_mode) & 0o222 for p in [destination, *destination.rglob("*")])):
        raise u.BuildError("published v26 differs")
    print(json.dumps({"status": "PASS_IMMUTABLE_SHARED_COLLECTOR_V26",
        "bundle": str(destination), "bundle_id": bundle_id,
        "bundle_manifest_sha256": u.digest(destination / "bundle_manifest.json"),
        "verified_files": len(manifest["files"]), "tests": result_text,
        "formal_launch_authorized": False, "accepted_round_authorized": False}, indent=2, sort_keys=True))


if __name__ == "__main__":
    try: main()
    except (u.BuildError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print("NOT_READY: " + str(exc), file=__import__("sys").stderr); raise SystemExit(2)
