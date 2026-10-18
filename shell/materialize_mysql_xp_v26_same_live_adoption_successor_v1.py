#!/usr/bin/env python3
"""Read-only node02 XP v26 same-live adoption/compatibility materializer.

Python 3.6 compatible. It never creates, starts, stops, restarts or updates a
container. A v24/v25 client bind-mounted to its predecessor bundle cannot be
relabelled or remounted in place; that case is sealed as requiring a fresh
v26 pair. A current v26 pair is accepted only after real PID/starttime,
Docker label/mount, systemd 219 and post-adoption Python readback.
"""
import sys
sys.dont_write_bytecode = True

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess


TARGET_ID = "b8e574ea5d2a47faae7cbe1776c7407c6e4b6122b4567c8a9b7dde5513382dcc"
TARGET_SHA = "1baf26d67479544c2f9aa13e4bd1d9a87fccaec9e6912d0570de3780d896df02"
V25_ID = "9783caeb6e5e610f2fa008388d7f82e9e30ee302b7a29af41d5c549a0f8da0db"
V24_ID = "156df4866ca2919e66086cfd0dd84f6093cbcc05716089b5a7cc70dcd2a829c4"
REMOTE_BUNDLE_PARENT = "/mnt/mysql_nvme0n1p1/anonymous/resource_adaptive_mysql/hybench_xp/runtime_algorithm_c_successor_20260729/bundles"
DOCKER = ["docker", "--host", "unix:///run/ra-docker-mysql-xp-v2.sock"]
CRITICAL = (
    "launch/mysql_xp_node02_adopted_service_v1.py",
    "launch/mysql_xp_capture_transition_v1.py",
    "launch/mysql_xp_raw_evidence_executor_v1.py",
    "launch/mysql_xp_fresh_r0_executor_v2.py",
    "launch/mysql_xp_formal_evidence_producer_v1.py",
    "launch/mysql_xp_node02_formal10_materializer_v1.py",
    "qualification/mysql_distributed_live_readback_qualification_v1.py",
    "qualification/mysql_xp_qualification_authority_v1.py",
)


class MaterializerError(RuntimeError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sealed(path, label):
    path = Path(path)
    sidecar = path.with_name(path.name + ".sha256")
    if path.is_symlink() or not path.is_file() or sidecar.is_symlink() or not sidecar.is_file():
        raise MaterializerError(label + " is not sealed")
    expected = "%s  %s\n" % (sha256(path), path.name)
    if sidecar.read_text(encoding="ascii") != expected:
        raise MaterializerError(label + " sidecar differs")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise MaterializerError(label + " must be an object")
    return value


def atomic(path, value):
    path = Path(path)
    if path.exists() or path.is_symlink():
        raise MaterializerError("output must be fresh")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode("utf-8")
    temporary = path.with_name("." + path.name + ".%d" % os.getpid())
    with temporary.open("xb") as stream:
        stream.write(payload); stream.flush(); os.fsync(stream.fileno())
    os.replace(str(temporary), str(path))
    path.with_name(path.name + ".sha256").write_text(
        "%s  %s\n" % (sha256(path), path.name), encoding="ascii")


def run(argv):
    completed = subprocess.run(list(argv), stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE,
                               universal_newlines=True, check=False)
    if completed.returncode:
        raise MaterializerError("read-only compatibility command failed")
    return completed.stdout


def proc_starttime(pid):
    text = Path("/proc/%d/stat" % int(pid)).read_text(encoding="ascii")
    fields = text[text.rfind(")") + 2:].split()
    if len(fields) <= 19:
        raise MaterializerError("live proc stat differs")
    return int(fields[19])


def docker_inspect(container_id):
    value = json.loads(run(DOCKER + ["inspect", container_id]))
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise MaterializerError("docker inspect differs")
    return value[0]


def role_identity(adoption, role):
    row = adoption.get(role) or {}
    container_id = row.get("container_id")
    pid = int(row.get("pid") or 0)
    ticks = int(row.get("pid_starttime_ticks") or 0)
    if not isinstance(container_id, str) or len(container_id) != 64 or pid <= 1 or ticks <= 0:
        raise MaterializerError(role + " adoption identity is incomplete")
    if proc_starttime(pid) != ticks:
        raise MaterializerError(role + " PID/starttime is stale")
    inspect = docker_inspect(container_id)
    state = inspect.get("State") or {}
    if (inspect.get("Id") != container_id or state.get("Running") is not True
            or int(state.get("Pid") or 0) != pid):
        raise MaterializerError(role + " same-live Docker identity differs")
    return inspect


def client_bundle_binding(inspect):
    config = inspect.get("Config") or {}
    labels = config.get("Labels") or {}
    matches = [item for item in (inspect.get("Mounts") or [])
               if isinstance(item, dict)
               and item.get("Destination") == "/opt/mysql-xp-bundle"]
    if len(matches) != 1:
        raise MaterializerError("client bundle mount differs")
    mount = matches[0]
    return {"label_bundle_id": labels.get("ra.bundle_id"),
            "mount_source": mount.get("Source"), "mount_rw": mount.get("RW"),
            "mount_type": mount.get("Type")}


def python_readback(interpreter, bundle):
    resolved = shutil.which(str(interpreter)) if not Path(str(interpreter)).is_absolute() else str(interpreter)
    if not resolved:
        raise MaterializerError("post-adoption Python is absent")
    executable = Path(resolved).resolve()
    if executable.is_symlink() or not executable.is_file():
        raise MaterializerError("post-adoption Python identity differs")
    probe = "import json,sys;print(json.dumps(list(sys.version_info[:3])))"
    version = json.loads(run([str(executable), "-B", "-c", probe]).strip())
    version_pass = tuple(version) >= (3, 7, 0)
    compile_probe = "import sys;compile(open(sys.argv[1],'rb').read(),sys.argv[1],'exec')"
    compiled = {}
    if version_pass:
        for relative in CRITICAL:
            path = bundle / relative
            run([str(executable), "-B", "-c", compile_probe, str(path)])
            compiled[relative] = sha256(path)
    return {"path": str(executable), "sha256": sha256(executable),
            "version": version, "minimum_required": [3, 7, 0],
            "version_pass": version_pass,
            "critical_compile_sha256": compiled}


def systemd_readback():
    first = run(["systemd-run", "--version"]).splitlines()[0].split()
    if len(first) != 2 or first[0] != "systemd":
        raise MaterializerError("systemd version readback differs")
    help_text = run(["systemd-run", "--help"])
    required = ("--quiet", "--service-type=TYPE", "--remain-after-exit",
                "--unit=UNIT")
    help_pass = all(value in help_text for value in required)
    return {"version": int(first[1]), "expected_version": 219,
            "version_pass": int(first[1]) == 219 and help_pass,
            "required_help_options": list(required),
            "required_help_options_pass": help_pass,
            "help_sha256": hashlib.sha256(help_text.encode("utf-8")).hexdigest(),
            "wait_collect_pipe_prohibited": True,
            "oneshot_remain_after_exit_controller_poll_required": True}


def materialize(bundle_path, adoption_path, interpreter, output):
    if socket.gethostname().split(".")[0] != "node02":
        raise MaterializerError("same-live materializer is host-bound to node02")
    bundle = Path(bundle_path).resolve(strict=True)
    if bundle.name != TARGET_ID or sha256(bundle / "bundle_manifest.json") != TARGET_SHA:
        raise MaterializerError("target v26 bundle identity differs")
    adoption = sealed(adoption_path, "candidate adoption")
    source_id = adoption.get("bundle_id")
    if (adoption.get("schema_version") != "mysql_xp_node02_adopted_service_authority_v1"
            or adoption.get("status") != "PASS_ADOPTED_SAME_LIVE_SERVICE"
            or source_id not in (V24_ID, V25_ID, TARGET_ID)):
        raise MaterializerError("candidate adoption lineage differs")
    database = role_identity(adoption, "database")
    client = role_identity(adoption, "client")
    binding = client_bundle_binding(client)
    database_label = (((database.get("Config") or {}).get("Labels") or {})
                      .get("ra.bundle_id"))
    expected_source = REMOTE_BUNDLE_PARENT + "/" + source_id
    old = source_id in (V24_ID, V25_ID)
    if (database_label != source_id
            or binding.get("label_bundle_id") != source_id
            or binding.get("mount_source") != expected_source
            or binding.get("mount_rw") is not False
            or binding.get("mount_type") != "bind"):
        raise MaterializerError("candidate client bundle label/mount differs")
    common = {
        "schema_version": "mysql_xp_v26_same_live_adoption_successor_v1",
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True, "target_bundle_id": TARGET_ID,
        "target_manifest_sha256": TARGET_SHA, "source_adoption_bundle_id": source_id,
        "source_adoption_sha256": sha256(adoption_path),
        "database_container_id": database.get("Id"),
        "database_label_bundle_id": database_label,
        "client_container_id": client.get("Id"), "client_bundle_binding": binding,
        "same_live_pid_starttime_pass": True, "container_restart_performed": False,
        "container_update_performed": False, "remote_mutation_performed": False,
        "formal_launch_authorized": False, "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
    }
    if old:
        result = dict(common)
        result.update({
            "status": "REQUIRES_FRESH_V26_PAIR_NO_ZERO_RESTART_UPGRADE",
            "zero_restart_upgrade_possible": False,
            "reason": "Docker cannot change immutable container labels or bind-mount source in place",
            "v26_adoption_materialized": False,
            "fresh_v26_database_and_client_required": True,
        })
    else:
        python = python_readback(interpreter, bundle)
        systemd = systemd_readback()
        ready = python["version_pass"] and systemd["version_pass"]
        result = dict(common)
        result.update({
            "status": ("PASS_STRICT_SAME_LIVE_V26_ADOPTION_COMPATIBILITY"
                       if ready else "V26_SAME_LIVE_HOST_COMPATIBILITY_BLOCKED"),
            "zero_restart_upgrade_possible": True,
            "v26_adoption_materialized": ready,
            "fresh_v26_database_and_client_required": False,
            "post_adoption_python": python, "systemd": systemd,
            "capture_r0_compatibility_ready": ready,
        })
    atomic(output, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--candidate-adoption", type=Path, required=True)
    parser.add_argument("--post-adoption-python", default="python3")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = materialize(args.bundle, args.candidate_adoption,
                         args.post_adoption_python, args.output)
    print(json.dumps({"status": result["status"],
                      "zero_restart_upgrade_possible": result["zero_restart_upgrade_possible"],
                      "remote_mutation_performed": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (MaterializerError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise SystemExit("NOT_READY: " + str(exc))
