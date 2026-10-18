#!/usr/bin/env python3
"""Fail-closed live read-back and debug-only qualification launcher.

This file is copied into each content-addressed distributed successor.  It is
Python 3.6 compatible for node02.  ``--collect-only`` is read-only.
Qualification modes start only an exact hash-bound command authorized by a
fresh main-agent token document; they can never create capture, R0, formal, or
accepted-round authority.
"""

import sys
sys.dont_write_bytecode = True

import argparse
import threading
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import subprocess
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import runtime_tree_content_identity_v3 as runtime_tree
import mysql_distributed_workload_metric_adapter_v1 as metric_adapter
from mysql_tpch_client_immutable_identity_v1 import (
    client_container_immutable_projection,
)


QUALIFICATION_SECONDS = 180
DOCKER_SOCKET = "/run/ra-docker-mysql-tpch-v1.sock"
DOCKER_SOCKET_URI = "unix:///run/ra-docker-mysql-tpch-v1.sock"
DOCKER_CLI = ("docker", "--host", DOCKER_SOCKET_URI)
DOCKER_DATA_ROOT = "/mnt/nvme0n1/anonymous/resource_adaptive_mysql/docker_ra_tpch_v1"
DOCKER_UNIT = "ra-docker-mysql-tpch-v1.service"
DOCKER_UNIT_FRAGMENT = "/run/systemd/system/ra-docker-mysql-tpch-v1.service"
DOCKER_GRAPH_DRIVER = "overlay2"
DOCKER_ROOT_MAJOR_MINOR = "259:1"

MINIMUM_SAMPLES = 181
MAXIMUM_SAMPLE_GAP_SECONDS = 5.0
WORKLOAD_SAMPLE_TIMEOUT_SECONDS = 1.0
CPU_CONTENTION_POLICY_VERSION = "mysql_algorithm_c_cpu_contention_signal_v2"
REQUIRED_STREAMS = (
    "workload_throughput_or_completion_rate",
    "p50_p95_p99_or_query_duration",
    "cpu_usage_and_throttle_ratio",
    "cpu_pressure",
    "memory_current_swap_and_pressure",
    "per_declared_device_bytes_iops_latency_queue_and_utilization",
    "database_counters",
    "process_cpu_affinity_and_device_inventory",
)
DYNAMIC_TOLERANCES = {
    "throughput_degradation_fraction_max": 0.05,
    "latency_inflation_fraction_max": 0.10,
    "resource_utilization_absolute_drift_max": 0.05,
    "cpu_throttle_ratio_absolute_drift_max": 0.05,
    "foreign_device_io_attribution_bytes_max": 0,
    "new_oom_kill_events_max": 0,
    "new_unexpected_swap_events_max": 0,
}
FORBIDDEN_COMMAND_PATTERN = re.compile(
    r"(^|[^a-z0-9])(capture|formal|accepted|r0)([^a-z0-9]|$)",
    re.IGNORECASE,
)
FORBIDDEN_ENV_AUTHORITY_PATTERN = re.compile(
    r"(authorized|accepted_round|formal10|fresh_r0|outer_execution)",
    re.IGNORECASE,
)
DB_COUNTERS = (
    "Questions",
    "Com_select",
    "Com_insert",
    "Com_update",
    "Com_delete",
    "Threads_running",
    "Innodb_rows_read",
    "Innodb_rows_inserted",
    "Innodb_rows_updated",
    "Innodb_rows_deleted",
)
PROC_ROOT = Path("/proc")
ROOT_DOCKER_MOUNT_HELPER = "mysql_job_host_root_inventory_v1.py"
ROOT_DOCKER_MOUNT_HELPER_MAX_STDOUT_BYTES = 4 * 1024 * 1024
CORESIDENT_PROCESS_PATTERN = re.compile(
    r"(postgres|mysqld|mariadbd|mysql|pgbench|sysbench|benchbase|"
    r"hybench|tpch|tpcc|algorithm[_-]?[abc]|benchmark)",
    re.IGNORECASE,
)


class QualificationError(RuntimeError):
    pass


def canonical(value):
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def load_object(path, label):
    if path.is_symlink() or not path.is_file():
        raise QualificationError("%s absent or symlinked: %s" % (label, path))
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise QualificationError("%s is unreadable: %s" % (label, exc))
    if not isinstance(value, dict):
        raise QualificationError("%s must be a JSON object" % label)
    return value


def valid_sidecar(path):
    sidecar = path.with_name(path.name + ".sha256")
    return (
        sidecar.is_file()
        and not sidecar.is_symlink()
        and sidecar.read_text(encoding="ascii")
        == "%s  %s\n" % (sha256(path), path.name)
    )


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical(value)
    temporary = path.with_name(".%s.tmp.%d" % (path.name, os.getpid()))
    temporary.write_bytes(payload)
    os.replace(str(temporary), str(path))
    sidecar = path.with_name(path.name + ".sha256")
    sidecar_temporary = sidecar.with_name(
        ".%s.tmp.%d" % (sidecar.name, os.getpid())
    )
    sidecar_temporary.write_text(
        "%s  %s\n" % (sha_bytes(payload), path.name), encoding="ascii"
    )
    os.replace(str(sidecar_temporary), str(sidecar))


def expand_cpuset(spec):
    values = set()
    for piece in str(spec).split(","):
        piece = piece.strip()
        if not piece:
            raise QualificationError("empty cpuset component")
        if "-" in piece:
            first_text, last_text = piece.split("-", 1)
            first, last = int(first_text), int(last_text)
            if first > last:
                raise QualificationError("descending cpuset range")
            values.update(range(first, last + 1))
        else:
            values.add(int(piece))
    if not values:
        raise QualificationError("empty cpuset")
    return values


def run(command, timeout, label):
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise QualificationError("%s failed or timed out: %s" % (label, exc))
    if completed.returncode != 0:
        raise QualificationError(
            "%s failed rc=%d stderr=%s"
            % (label, completed.returncode, completed.stderr.strip())
        )
    return completed.stdout


def verify_bundle(bundle):
    manifest = load_object(bundle / "bundle_manifest.json", "bundle manifest")
    schema = manifest.get("schema_version")
    if (
        manifest.get("bundle_id") != bundle.name
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
    ):
        raise QualificationError("distributed bundle identity/scope differs")
    if schema in (
        "mysql_algorithm_c_distributed_successor_bundle_v1",
        "mysql_xp_distributed_formal10_launch_v3",
        "mysql_tpch_distributed_formal10_successor_v2",
        "mysql_tpch_v23_recalibrated_successor_v3",
    ):
        identity = dict(manifest)
        identity.pop("bundle_id", None)
        if sha_bytes(canonical(identity)) != bundle.name:
            raise QualificationError("distributed bundle content address differs")
        if not valid_sidecar(bundle / "bundle_manifest.json"):
            raise QualificationError("bundle manifest sidecar differs")
    elif schema == "mysql_distributed_successor_staged_bundle_v1":
        closure = bundle / "closure.json"
        if (
            manifest.get("launch_authorized") is not False
            or closure.is_symlink()
            or not closure.is_file()
            or sha256(closure) != bundle.name
            or (manifest.get("files") or {}).get("closure.json") != bundle.name
        ):
            raise QualificationError("staged bundle closure identity differs")
    else:
        raise QualificationError("distributed bundle schema differs")
    for relative, expected in (manifest.get("files") or {}).items():
        path = bundle / relative
        if path.is_symlink() or not path.is_file() or sha256(path) != expected:
            raise QualificationError("bundle closure differs: %s" % relative)
    runtime = load_object(
        bundle / "distributed_runtime_contract.json", "runtime contract"
    )
    if (
        runtime.get("data_plane_launch_authority") != "main_agent_only"
        or runtime.get("formal_launch_authorized") is not False
        or runtime.get("accepted_round_authorized") is not False
    ):
        raise QualificationError("runtime contract authority differs")
    return manifest, runtime


def tree_readback(root, tree_manifest):
    try:
        return runtime_tree.verify_tree_manifest(root, tree_manifest)
    except runtime_tree.RuntimeTreeError as exc:
        raise QualificationError(
            "runtime full tree inventory/hash/mode differs: %s" % exc
        )


def process_status(pid):
    status = {}
    path = PROC_ROOT / str(pid) / "status"
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            status[key] = value.strip()
    return status


def _proc_text(path, label, encoding="utf-8"):
    try:
        if path.is_symlink() or not path.is_file():
            raise OSError("absent or symlinked")
        return path.read_text(encoding=encoding, errors="replace")
    except (OSError, PermissionError) as exc:
        raise QualificationError("%s unreadable: %s" % (label, exc))


def _process_target_device_write_fds(pid, major_minor):
    major, minor = [_integer(value, "declared device number") for value in
                    major_minor.split(":", 1)]
    directory = PROC_ROOT / str(pid) / "fd"
    try:
        entries = list(directory.iterdir())
    except FileNotFoundError:
        raise
    except (OSError, PermissionError) as exc:
        raise QualificationError(
            "co-resident process fd inventory unreadable: %s" % exc
        )
    matched = 0
    writable = 0
    for entry in entries:
        try:
            info = os.stat(str(entry), follow_symlinks=True)
        except FileNotFoundError:
            continue
        except (OSError, PermissionError) as exc:
            raise QualificationError(
                "co-resident process fd attribution unreadable: %s" % exc
            )
        device = info.st_rdev if stat.S_ISBLK(info.st_mode) else info.st_dev
        if os.major(device) != major or os.minor(device) != minor:
            continue
        matched += 1
        flags = None
        try:
            for line in (PROC_ROOT / str(pid) / "fdinfo" / entry.name).read_text(
                encoding="ascii", errors="strict"
            ).splitlines():
                if line.startswith("flags:"):
                    flags = int(line.split(":", 1)[1].strip(), 8)
                    break
        except FileNotFoundError:
            continue
        except (OSError, PermissionError, UnicodeError, ValueError) as exc:
            raise QualificationError(
                "co-resident process fd flags unreadable: %s" % exc
            )
        if flags is None:
            raise QualificationError("co-resident process fd flags absent")
        if flags & os.O_ACCMODE != os.O_RDONLY:
            writable += 1
    return matched, writable


def _parse_proc_stat_starttime(raw):
    # /proc/<pid>/stat field 2 is parenthesized and may itself contain spaces.
    boundary = raw.rfind(") ")
    if boundary < 0:
        raise QualificationError("process stat identity coverage incomplete")
    trailing = raw[boundary + 2:].split()
    # trailing[0] is field 3 (state); starttime is field 22.
    if len(trailing) <= 19:
        raise QualificationError("process stat identity coverage incomplete")
    return _integer(trailing[19], "process starttime ticks")


def _cgroup_identity(raw):
    paths = []
    for line in raw.splitlines():
        pieces = line.split(":", 2)
        if len(pieces) != 3 or not pieces[2].startswith("/"):
            raise QualificationError("process cgroup identity malformed")
        paths.append(pieces[2])
    if not paths:
        raise QualificationError("process cgroup identity absent")
    normalized = "\n".join(sorted(set(paths)))
    container_ids = re.findall(
        r"(?<![0-9a-f])([0-9a-f]{64})(?![0-9a-f])", normalized
    )
    return {
        "cgroup_sha256": sha_bytes(raw.encode("utf-8")),
        "cgroup_paths_sha256": sha_bytes(normalized.encode("utf-8")),
        "container_identity": (
            sorted(set(container_ids))[0] if container_ids else None
        ),
        "_paths": set(paths),
    }


def _process_io_identity(directory):
    values = {}
    try:
        rows = (directory / "io").read_text(
            encoding="ascii", errors="strict"
        ).splitlines()
    except FileNotFoundError:
        raise
    except (OSError, PermissionError, UnicodeError) as exc:
        raise QualificationError(
            "process IO attribution unreadable: %s" % exc
        )
    for line in rows:
        pieces = line.split(":", 1)
        if len(pieces) == 2:
            values[pieces[0].strip()] = _integer(
                pieces[1].strip(), "process IO counter"
            )
    if "read_bytes" not in values or "write_bytes" not in values:
        raise QualificationError("process IO attribution incomplete")
    return {
        "read_bytes": values["read_bytes"],
        "write_bytes": values["write_bytes"],
    }


def _path_major_minor(path):
    try:
        info = os.stat(str(path), follow_symlinks=True)
    except (OSError, PermissionError) as exc:
        raise QualificationError(
            "container mount device attribution unreadable: %s" % exc
        )
    device = info.st_rdev if stat.S_ISBLK(info.st_mode) else info.st_dev
    return "%d:%d" % (os.major(device), os.minor(device))


def _root_docker_mount_inventory():
    """Read only the otherwise-unreadable Docker mount device identities.

    The helper is fixed beside this sealed collector, hash-bound at every
    invocation, and can only emit a bounded stdout document.  It is not a
    general root collector: process /proc attribution remains unprivileged
    and non-container permission failures remain fatal.
    """
    helper = Path(__file__).resolve().parent / ROOT_DOCKER_MOUNT_HELPER
    if helper.is_symlink() or not helper.is_file():
        raise QualificationError("sealed root Docker mount helper is absent")
    helper_hash = sha256(helper)
    command = [
        "sudo", "-n", sys.executable, "-B", str(helper),
        "--expected-helper-sha256", helper_hash,
    ]
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"PATH": "/usr/bin:/bin", "PYTHONPATH": "",
                 "PYTHONDONTWRITEBYTECODE": "1"},
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise QualificationError("root Docker mount helper failed or timed out: %s" % exc)
    if completed.returncode != 0 or completed.stderr:
        raise QualificationError("root Docker mount helper failed")
    if not completed.stdout or len(completed.stdout) > ROOT_DOCKER_MOUNT_HELPER_MAX_STDOUT_BYTES:
        raise QualificationError("root Docker mount helper stdout coverage differs")
    try:
        value = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise QualificationError("root Docker mount helper JSON invalid: %s" % exc)
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != "mysql_job_host_root_mount_inventory_v1"
        or value.get("status") != "PASS_READ_ONLY_ROOT_DOCKER_MOUNT_INVENTORY"
        or value.get("helper_sha256") != helper_hash
        or value.get("effective_uid_gid") != [0, 0]
        or value.get("mutation_performed") is not False
        or value.get("postgresql_read_only_inventory_allowed") is not True
        or not isinstance(value.get("containers"), list)
    ):
        raise QualificationError("root Docker mount helper authority differs")
    return value, {
        "helper_relative": ROOT_DOCKER_MOUNT_HELPER,
        "helper_sha256": helper_hash,
        "invocation": ["sudo", "-n", sys.executable, "-B",
                       ROOT_DOCKER_MOUNT_HELPER,
                       "--expected-helper-sha256", helper_hash],
        "output_sha256": sha_bytes(completed.stdout),
        "container_count": len(value["containers"]),
        "container_projection_sha256": sha_bytes(
            canonical(value["containers"])
        ),
        "effective_uid_gid": [0, 0],
        "mutation_performed": False,
    }


def _docker_container_inventory():
    """Inventory every running Docker cgroup with root-only mount stat help.

    On node02 the co-resident PostgreSQL container is owned by root, so the
    experiment user cannot enumerate its ``/proc/<pid>/fd`` or ``io`` files.
    Docker inspect, bind-mount device identities, and the readable container
    blkio cgroup provide a complete fail-closed fallback for that exact case.
    Non-container processes never receive this fallback.
    """
    identifiers = [
        value.strip()
        for value in run(
            [*DOCKER_CLI, "ps", "--no-trunc", "--quiet"],
            timeout=5,
            label="running Docker inventory",
        ).splitlines()
        if value.strip()
    ]
    if not identifiers:
        root_document, root_evidence = _root_docker_mount_inventory()
        if root_document.get("containers") != []:
            raise QualificationError(
                "empty Docker CLI inventory differs from root mount inventory"
            )
        return {}, root_evidence
    raw = run(
        [*DOCKER_CLI, "inspect"] + identifiers,
        timeout=10,
        label="running Docker inspect inventory",
    )
    try:
        inspected = json.loads(raw)
    except ValueError as exc:
        raise QualificationError(
            "running Docker inspect inventory JSON invalid: %s" % exc
        )
    if not isinstance(inspected, list) or len(inspected) != len(identifiers):
        raise QualificationError(
            "running Docker inspect inventory cardinality differs"
        )
    root_document, root_evidence = _root_docker_mount_inventory()
    root_rows = root_document["containers"]
    root_by_id = {}
    for root_row in root_rows:
        container_id = root_row.get("container_id") if isinstance(root_row, dict) else None
        if (not isinstance(container_id, str)
                or re.fullmatch(r"[0-9a-f]{64}", container_id) is None
                or container_id in root_by_id):
            raise QualificationError("root Docker mount helper container identity differs")
        root_by_id[container_id] = root_row
    result = {}
    for row in inspected:
        container_id = row.get("Id")
        state = row.get("State") or {}
        pid = int(state.get("Pid") or 0)
        if (
            not isinstance(container_id, str)
            or not re.fullmatch(r"[0-9a-f]{64}", container_id)
            or state.get("Running") is not True
            or pid <= 0
        ):
            raise QualificationError(
                "running Docker container immutable identity differs"
            )
        roots = _cgroup_roots(pid)
        if roots["version"] == 2:
            devices = _v2_io_values(roots["io"] / "io.stat")
        else:
            devices = _v1_io_values(roots["io"])
        root_row = root_by_id.pop(container_id, None)
        if (
            not isinstance(root_row, dict)
            or root_row.get("name") != str(row.get("Name") or "").lstrip("/")
            or root_row.get("init_pid") != pid
            or not isinstance(root_row.get("mounts"), list)
        ):
            raise QualificationError("root/unprivileged Docker identity differs")
        root_mounts = root_row["mounts"]
        mounts = []
        for mount in row.get("Mounts") or []:
            source = mount.get("Source")
            destination = mount.get("Destination")
            if (
                not isinstance(source, str)
                or not source.startswith("/")
                or not isinstance(destination, str)
                or not destination.startswith("/")
                or not isinstance(mount.get("RW"), bool)
            ):
                raise QualificationError(
                    "running Docker mount inventory differs"
                )
            mounts.append({
                "source": os.path.realpath(source),
                "destination": destination,
                "rw": mount["RW"],
            })
        expected_projection = sorted(
            mounts,
            key=lambda value: (value["source"], value["destination"], value["rw"]),
        )
        actual_projection = []
        for mount in root_mounts:
            if (
                not isinstance(mount, dict)
                or set(mount) != {"source", "destination", "major_minor", "rw"}
                or not isinstance(mount.get("source"), str)
                or not isinstance(mount.get("destination"), str)
                or not isinstance(mount.get("rw"), bool)
                or re.fullmatch(r"[0-9]+:[0-9]+", str(mount.get("major_minor"))) is None
            ):
                raise QualificationError("root Docker mount helper mount differs")
            actual_projection.append({
                "source": mount["source"], "destination": mount["destination"],
                "rw": mount["rw"], "major_minor": mount["major_minor"],
            })
        if [
            {key: value for key, value in mount.items() if key != "major_minor"}
            for mount in sorted(actual_projection, key=lambda value: (
                value["source"], value["destination"], value["rw"], value["major_minor"]
            ))
        ] != expected_projection:
            raise QualificationError("root/unprivileged Docker mount projection differs")
        mounts = sorted(
            actual_projection,
            key=lambda value: (
                value["source"], value["destination"], value["major_minor"], value["rw"]
            ),
        )
        result[container_id] = {
            "container_id": container_id,
            "name": str(row.get("Name") or "").lstrip("/"),
            "init_pid": pid,
            "mounts": mounts,
            "mounts_sha256": sha_bytes(canonical(mounts)),
            "cgroup_version": roots["version"],
            "cgroup_devices": devices,
        }
    if root_by_id:
        raise QualificationError("root Docker mount helper has surplus containers")
    return result, root_evidence


def rolling_process_inventory(
    target_pid,
    workload_pid,
    control_pid,
    major_minor,
    mysql_cpuset,
    workload_cpuset,
    workload_mems,
    docker_containers=None,
    root_docker_mount_evidence=None,
):
    # First inventory every PID.  Role filtering happens only after the full
    # workload/container descendant closure is known.
    all_processes = {}
    races = 0
    exit_candidates = []
    protected_roots = {
        int(target_pid), int(workload_pid), int(control_pid), os.getpid()
    }
    try:
        directories = list(PROC_ROOT.iterdir())
    except (OSError, PermissionError) as exc:
        raise QualificationError("full process inventory unreadable: %s" % exc)
    for directory in directories:
        if not directory.name.isdigit():
            continue
        pid = int(directory.name)
        try:
            status = process_status(pid)
            raw_cmdline = (directory / "cmdline").read_bytes()
            raw_stat = (directory / "stat").read_text(
                encoding="ascii", errors="replace"
            )
            cgroup = (directory / "cgroup").read_text(
                encoding="utf-8", errors="replace"
            )
        except FileNotFoundError:
            # A PID enumerated at the beginning may naturally exit before its
            # identity is read.  It is not silently ignored: snapshot
            # completion below must prove that the PID is still absent.
            if pid in protected_roots:
                races += 1
            else:
                exit_candidates.append({
                    "pid": pid,
                    "phase": "identity",
                    "starttime_ticks": None,
                    "cgroup_paths_sha256": None,
                })
            continue
        except (OSError, PermissionError) as exc:
            raise QualificationError(
                "full process identity inventory unreadable: %s" % exc
            )
        command = raw_cmdline.replace(b"\0", b" ").decode(
            "utf-8", errors="replace"
        ).strip()
        comm = status.get("Name", "")
        identity = _cgroup_identity(cgroup)
        all_processes[pid] = {
            "pid": pid,
            "ppid": _integer(status.get("PPid"), "process PPid"),
            "starttime_ticks": _parse_proc_stat_starttime(raw_stat),
            "comm": comm,
            "_command": command,
            "cmdline_sha256": sha_bytes(raw_cmdline),
            "cgroup_sha256": identity["cgroup_sha256"],
            "cgroup_paths_sha256": identity["cgroup_paths_sha256"],
            "container_identity": identity["container_identity"],
            "_cgroup_paths": identity["_paths"],
            "cpus_allowed_list": status.get("Cpus_allowed_list", ""),
            "mems_allowed_list": status.get("Mems_allowed_list", ""),
            "_directory": directory,
        }
    if int(target_pid) not in all_processes or int(workload_pid) not in all_processes:
        raise QualificationError("target/workload process inventory is absent")

    docker_inventory_enforced = docker_containers is not None
    if docker_containers is None:
        docker_containers = {}
    if not isinstance(docker_containers, dict):
        raise QualificationError("Docker container inventory differs")
    target_paths = all_processes[int(target_pid)]["_cgroup_paths"]
    workload_paths = all_processes[int(workload_pid)]["_cgroup_paths"]
    workload_tree = {int(workload_pid)}
    changed = True
    while changed:
        changed = False
        for pid, row in all_processes.items():
            if (
                row["ppid"] in workload_tree
                or row["_cgroup_paths"] == workload_paths
            ) and pid not in workload_tree:
                workload_tree.add(pid)
                changed = True
    target_tree = {
        pid for pid, row in all_processes.items()
        if row["_cgroup_paths"] == target_paths
    }
    # Only the exact hash-bound collector is control-plane.  Its ancestors are
    # neither exempted nor silently absorbed into the client workload role.
    control_plane = {os.getpid(), int(control_pid)}

    mysql_cpus = expand_cpuset(mysql_cpuset)
    client_cpus = expand_cpuset(workload_cpuset)
    expected_mems = expand_cpuset(workload_mems)
    rows = []
    conflicts = []
    permission_failures = 0
    foreign_writer_count = 0
    for pid, row in sorted(all_processes.items()):
        container_row = docker_containers.get(row["container_identity"])
        relevant = bool(
            pid in target_tree
            or pid in workload_tree
            or pid in control_plane
            or container_row is not None
            or CORESIDENT_PROCESS_PATTERN.search(
                row["comm"] + " " + row["_command"]
            )
        )
        if not relevant:
            continue
        if (
            docker_inventory_enforced
            and
            row["container_identity"] is not None
            and container_row is None
        ):
            permission_failures += 1
            conflicts.append("Docker_process_inventory_incomplete")
            continue
        fd_attribution = "host_proc_fd"
        try:
            matched_fds, writable_fds = _process_target_device_write_fds(
                pid, major_minor
            )
        except FileNotFoundError:
            if pid in protected_roots:
                races += 1
            else:
                exit_candidates.append({
                    "pid": pid,
                    "phase": "fd",
                    "starttime_ticks": row["starttime_ticks"],
                    "cgroup_paths_sha256": row["cgroup_paths_sha256"],
                })
            continue
        except QualificationError:
            if container_row is None:
                permission_failures += 1
                continue
            # The Docker fallback is exact only when the mount inventory and
            # the container's own blkio cgroup are both available.
            target_mounts = [
                mount for mount in container_row["mounts"]
                if mount["major_minor"] == major_minor
            ]
            matched_fds = 0
            writable_fds = sum(1 for mount in target_mounts if mount["rw"])
            fd_attribution = "docker_mount_and_cgroup_fallback"
        process_io_attribution = "host_proc_io"
        try:
            process_io = _process_io_identity(row["_directory"])
        except FileNotFoundError:
            if pid in protected_roots:
                races += 1
            else:
                exit_candidates.append({
                    "pid": pid,
                    "phase": "io",
                    "starttime_ticks": row["starttime_ticks"],
                    "cgroup_paths_sha256": row["cgroup_paths_sha256"],
                })
            continue
        except QualificationError:
            if container_row is None:
                permission_failures += 1
                continue
            device_io = container_row["cgroup_devices"].get(
                major_minor,
                {"read_bytes": 0, "write_bytes": 0},
            )
            process_io = {
                "read_bytes": int(device_io.get("read_bytes") or 0),
                "write_bytes": int(device_io.get("write_bytes") or 0),
            }
            process_io_attribution = "docker_cgroup_aggregate_fallback"
        if pid in target_tree:
            role = "target_mysql"
        elif pid in workload_tree:
            role = "qualification_workload"
        elif pid in control_plane:
            role = "qualification_control_plane"
        else:
            role = "co_resident"
        actual_cpus = expand_cpuset(row["cpus_allowed_list"])
        actual_mems = expand_cpuset(row["mems_allowed_list"])
        row.update(
            {
                "role": role,
                "declared_device_open_fds": matched_fds,
                "declared_device_writable_fds": writable_fds,
                "process_io": process_io,
                "fd_attribution": fd_attribution,
                "process_io_attribution": process_io_attribution,
                "container_mounts_sha256": (
                    container_row["mounts_sha256"]
                    if container_row is not None else None
                ),
                "container_declared_device_cgroup_io": (
                    container_row["cgroup_devices"].get(
                        major_minor,
                        {
                            "read_bytes": 0,
                            "write_bytes": 0,
                            "read_ios": 0,
                            "write_ios": 0,
                        },
                    )
                    if container_row is not None else None
                ),
            }
        )
        if role == "target_mysql":
            if (
                actual_cpus != mysql_cpus
                or actual_mems
                != expand_cpuset(
                    all_processes[int(target_pid)]["mems_allowed_list"]
                )
            ):
                conflicts.append("target_affinity_or_mems")
        elif role == "qualification_workload":
            if actual_cpus != client_cpus or actual_mems != expected_mems:
                conflicts.append("workload_affinity_or_mems")
        if role == "co_resident" and (
            not actual_cpus.isdisjoint(mysql_cpus | client_cpus)
            or writable_fds > 0
        ):
            conflicts.append("co_resident_cpu_or_device")
            if writable_fds > 0:
                foreign_writer_count += 1
        rows.append({
            key: value for key, value in row.items()
            if not key.startswith("_")
        })
    # Resolve every disappearance at the end of the same snapshot.
    # If a PID is present again (including reuse), the view is not complete and
    # the existing retry/fail-closed path remains mandatory.
    verified_exit_tombstones = []
    for event in exit_candidates:
        directory = PROC_ROOT / str(event["pid"])
        try:
            directory.stat()
        except FileNotFoundError:
            verified_exit_tombstones.append(event)
        except (OSError, PermissionError):
            permission_failures += 1
        else:
            races += 1
    if permission_failures:
        conflicts.append("process_fd_or_io_permission_failure")
    if races:
        conflicts.append("process_inventory_race")
    result = {
        "inventory_complete": not permission_failures and not races,
        "target_pid": int(target_pid),
        "workload_root_pid": int(workload_pid),
        "target_cgroup_paths_sha256":
            all_processes[int(target_pid)]["cgroup_paths_sha256"],
        "workload_cgroup_paths_sha256":
            all_processes[int(workload_pid)]["cgroup_paths_sha256"],
        "workload_tree_pids": sorted(workload_tree),
        "processes": rows,
        "permission_failure_count": permission_failures,
        "race_count": races,
        "verified_exit_tombstone_count": len(verified_exit_tombstones),
        "verified_exit_tombstones": verified_exit_tombstones,
        "verified_exit_tombstone_policy": {
            "schema_version":
                "mysql_shared_proc_verified_exit_tombstone_policy_v1",
            "snapshot_completion_absence_required": True,
            "target_workload_and_collector_roots_protected": True,
            "pid_reappearance_or_reuse_is_race": True,
            "permission_error_is_tombstone": False,
            "foreign_device_attribution_unchanged": True,
        },
        "foreign_writer_count": foreign_writer_count,
        "docker_container_count": len(docker_containers),
        "docker_inventory_sha256": sha_bytes(
            canonical(
                [
                    {
                        "container_id": value["container_id"],
                        "name": value["name"],
                        "init_pid": value["init_pid"],
                        "mounts_sha256": value["mounts_sha256"],
                    }
                    for value in sorted(
                        docker_containers.values(),
                        key=lambda item: item["container_id"],
                    )
                ]
            )
        ),
        "conflicts": sorted(set(conflicts)),
    }
    if docker_inventory_enforced:
        if not isinstance(root_docker_mount_evidence, dict):
            raise QualificationError("root Docker mount evidence is absent")
        result["root_docker_mount_inventory"] = root_docker_mount_evidence
    return result


def inspect_container(container):
    raw = run(
        [*DOCKER_CLI, "inspect", container],
        timeout=5,
        label="container inspect",
    )
    try:
        rows = json.loads(raw)
    except ValueError as exc:
        raise QualificationError("container inspect JSON invalid: %s" % exc)
    if not isinstance(rows, list) or len(rows) != 1:
        raise QualificationError("container inspect cardinality differs")
    return rows[0]


def _full_container_id(row, label):
    value = row.get("Id")
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise QualificationError("%s full 64-hex container ID differs" % label)
    return value


def _container_name(row, label):
    value = row.get("Name")
    if not isinstance(value, str) or not value.startswith("/") or len(value) <= 1:
        raise QualificationError("%s container name differs" % label)
    return value[1:]


def process_start_ticks(pid):
    """Return immutable-at-runtime /proc stat starttime for a positive PID."""
    try:
        raw = (PROC_ROOT / str(int(pid)) / "stat").read_text(
            encoding="ascii"
        ).strip()
    except (OSError, UnicodeError, ValueError) as exc:
        raise QualificationError("process startticks readback failed: %s" % exc)
    try:
        _comm, tail = raw.rsplit(")", 1)
        fields = tail.strip().split()
        # After the comm field, state is field 3.  starttime is field 22.
        value = int(fields[19])
    except (IndexError, TypeError, ValueError) as exc:
        raise QualificationError("process startticks format differs: %s" % exc)
    if value <= 0:
        raise QualificationError("process startticks is non-positive")
    return value


def _exact_rate_binding(rows, device, major_minor, expected):
    """Validate one Docker declaration against path and cgroup identity."""
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        return False
    row = rows[0]
    try:
        major, minor = (int(value) for value in str(major_minor).split(":", 1))
        rate = int(row.get("Rate"))
    except (TypeError, ValueError):
        return False
    if rate != int(expected):
        return False
    path = row.get("Path")
    has_major_minor = "Major" in row or "Minor" in row
    if has_major_minor:
        try:
            if int(row.get("Major")) != major or int(row.get("Minor")) != minor:
                return False
        except (TypeError, ValueError):
            return False
    if path == device:
        return set(row).issubset({"Path", "Major", "Minor", "Rate"})
    if path in (None, "") and has_major_minor:
        return set(row).issubset({"Path", "Major", "Minor", "Rate"})
    return False


def _v1_effective_throttle(root, major_minor, read_bps, write_bps):
    """Read the effective v1 throttle files, never the data partition stat."""
    def exact(path, label, expected):
        rows = []
        for line in _read_required(path, label).splitlines():
            fields = line.split()
            if len(fields) != 2:
                raise QualificationError("%s row malformed" % label)
            rows.append((fields[0], _integer(fields[1], label + " rate")))
        if rows != [(major_minor, int(expected))]:
            raise QualificationError("%s effective binding differs" % label)
        return {"major_minor": major_minor, "rate": int(expected), "path": str(path)}
    return {
        "read": exact(root / "blkio.throttle.read_bps_device", "cgroup v1 read BPS", read_bps),
        "write": exact(root / "blkio.throttle.write_bps_device", "cgroup v1 write BPS", write_bps),
    }



def validate_tpch_effective_blkio_authority(
    container_row, runtime, defaults_file, bundle_id
):
    defaults_file = Path(defaults_file)
    if (
        defaults_file.name != "mysql.cnf"
        or defaults_file.parent.name != "credentials"
    ):
        raise QualificationError("TPC-H defaults/attempt path binding differs")
    authority_path = (
        defaults_file.parent.parent / "blkio_throttle_authority.json"
    )
    if not valid_sidecar(authority_path):
        raise QualificationError("effective blkio authority sidecar differs")
    value = load_object(authority_path, "effective blkio authority")
    unsigned = dict(value)
    recorded_sha = unsigned.pop("authority_sha256", None)
    container_id = container_row.get("Id")
    pid = int((container_row.get("State") or {}).get("Pid") or 0)
    expected_cgroup = "/sys/fs/cgroup/blkio/docker/" + str(container_id)
    envelope = runtime["resource_envelope"]
    storage = runtime["storage"]
    if (
        recorded_sha != sha_bytes(canonical(unsigned))
        or value.get("schema_version")
        != "mysql_tpch_blkio_v1_throttle_authority_v1"
        or value.get("status") != "PASS_EFFECTIVE_CGROUP_V1_THROTTLE"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("postgresql_mutation_prohibited") is not True
        or value.get("database_container_id") != container_id
        or value.get("database_pid") != pid
        or value.get("host_effective_uid_gid") != [0, 0]
        or value.get("cgroup_version") != "v1"
        or value.get("cgroup_path") != expected_cgroup
        or value.get("device_major_minor")
        != storage["expected_live_major_minor"]
        or value.get("read_bps") != envelope["io_read_bps"]
        or value.get("write_bps") != envelope["io_write_bps"]
        or value.get("read_throttle_file")
        != expected_cgroup + "/blkio.throttle.read_bps_device"
        or value.get("write_throttle_file")
        != expected_cgroup + "/blkio.throttle.write_bps_device"
        or value.get(
            "docker_hostconfig_is_declaration_not_effective_authority"
        )
        is not True
        or value.get("database_or_client_started") is not True
        or value.get("workload_started") is not False
        or value.get("formal_launch_authorized") is not False
        or value.get("accepted_round_authorized") is not False
    ):
        raise QualificationError("effective cgroup-v1 blkio authority differs")
    expected_attempt = defaults_file.parent.parent.name
    if (
        value.get("bundle_id") != bundle_id
        or value.get("attempt_token") != expected_attempt
    ):
        raise QualificationError(
            "effective cgroup-v1 manifest/attempt binding differs"
        )
    return {
        "schema_version": value["schema_version"],
        "authority_sha256": recorded_sha,
        "bundle_id": bundle_id,
        "attempt_token": expected_attempt,
        "database_container_id": container_id,
        "database_pid": pid,
        "same_live_identity_verified": True,
        "cgroup_path": expected_cgroup,
        "device_major_minor": value["device_major_minor"],
        "read_bps": value["read_bps"],
        "write_bps": value["write_bps"],
        "docker_hostconfig_is_declaration_not_effective_authority": True,
    }


def _validate_docker_blkio_projection(
    read_rows,
    write_rows,
    device,
    major_minor,
    read_bps,
    write_bps,
    container_id,
    pid,
    bundle_id,
    attempt_token,
    effective_authority,
):
    """Accept []/[] only behind the exact same-live effective authority."""
    read_empty = read_rows == []
    write_empty = write_rows == []
    if read_empty or write_empty:
        if not (read_empty and write_empty):
            raise QualificationError(
                "asymmetric empty Docker blkio projection differs"
            )
        expected_cgroup = "/sys/fs/cgroup/blkio/docker/" + str(container_id)
        if (
            not isinstance(effective_authority, dict)
            or effective_authority.get("schema_version")
            != "mysql_tpch_blkio_v1_throttle_authority_v1"
            or re.fullmatch(
                r"[0-9a-f]{64}",
                str(effective_authority.get("authority_sha256") or ""),
            )
            is None
            or effective_authority.get("bundle_id") != bundle_id
            or effective_authority.get("attempt_token") != attempt_token
            or effective_authority.get("database_container_id") != container_id
            or effective_authority.get("database_pid") != pid
            or effective_authority.get("same_live_identity_verified") is not True
            or effective_authority.get("cgroup_path") != expected_cgroup
            or effective_authority.get("device_major_minor") != major_minor
            or effective_authority.get("read_bps") != int(read_bps)
            or effective_authority.get("write_bps") != int(write_bps)
            or effective_authority.get(
                "docker_hostconfig_is_declaration_not_effective_authority"
            )
            is not True
        ):
            raise QualificationError(
                "empty Docker blkio projection lacks exact effective authority"
            )
        return "empty_arrays_with_exact_effective_cgroup_v1_authority"
    if not _exact_rate_binding(read_rows, device, major_minor, read_bps):
        raise QualificationError("container read BPS binding differs")
    if not _exact_rate_binding(write_rows, device, major_minor, write_bps):
        raise QualificationError("container write BPS binding differs")
    return "exact_nonempty_docker_declaration"


def validate_container_readback(
    container_row,
    runtime,
    effective_blkio_authority=None,
    bundle_id=None,
    attempt_token=None,
):
    envelope = runtime["resource_envelope"]
    storage = runtime["storage"]
    host_config = container_row.get("HostConfig") or {}
    state = container_row.get("State") or {}
    pid = int(state.get("Pid") or 0)
    if state.get("Running") is not True or pid <= 0:
        raise QualificationError("target container has no running workload PID")
    expected_cpu = float(envelope["cpu_quota_cores"])
    quota = int(host_config.get("CpuQuota") or 0)
    period = int(host_config.get("CpuPeriod") or 0)
    if period <= 0 or abs((float(quota) / float(period)) - expected_cpu) > 1e-9:
        raise QualificationError("container CPU quota differs")
    exact = {
        "CpusetCpus": envelope["cpuset_cpus"],
        "CpusetMems": envelope["cpuset_mems"],
        "Memory": envelope["memory_bytes"],
        "MemorySwap": envelope["memory_swap_bytes"],
        "PidsLimit": envelope["pids_max"],
    }
    for key, expected in exact.items():
        actual = host_config.get(key)
        if key.startswith("Cpuset"):
            if expand_cpuset(actual) != expand_cpuset(expected):
                raise QualificationError("container %s differs" % key)
        elif int(actual or 0) != int(expected):
            raise QualificationError("container %s differs" % key)
    data_device = storage["device"]
    throttle_device = storage.get("io_throttle_parent_device", data_device)
    throttle_major_minor = storage.get(
        "io_throttle_parent_major_minor", storage.get("expected_live_major_minor")
    )
    if (
        not isinstance(throttle_device, str)
        or not isinstance(throttle_major_minor, str)
        or not throttle_major_minor
    ):
        raise QualificationError("container throttle-parent contract differs")
    docker_blkio_projection = _validate_docker_blkio_projection(
        host_config.get("BlkioDeviceReadBps"),
        host_config.get("BlkioDeviceWriteBps"),
        throttle_device,
        throttle_major_minor,
        envelope["io_read_bps"],
        envelope["io_write_bps"],
        container_row.get("Id"),
        pid,
        bundle_id,
        attempt_token,
        effective_blkio_authority,
    )
    effective_throttle = None
    if "io_throttle_parent_device" in storage:
        roots = _cgroup_roots(pid)
        if roots["version"] != 1:
            raise QualificationError("XP throttle-parent requires cgroup v1")
        effective_throttle = _v1_effective_throttle(
            roots["io"], throttle_major_minor,
            envelope["io_read_bps"], envelope["io_write_bps"],
        )
    command = (container_row.get("Config") or {}).get("Cmd") or []
    if (
        host_config.get("NetworkMode") != "host"
        or (host_config.get("PortBindings") or {}) != {}
        or "--bind-address=127.0.0.1" not in command
        or "--port=65103" not in command
    ):
        raise QualificationError("database host-network/port binding differs")
    status = process_status(pid)
    if expand_cpuset(status.get("Cpus_allowed_list", "")) != expand_cpuset(
        envelope["cpuset_cpus"]
    ):
        raise QualificationError("container PID CPU affinity differs")
    if expand_cpuset(status.get("Mems_allowed_list", "")) != expand_cpuset(
        envelope["cpuset_mems"]
    ):
        raise QualificationError("container PID NUMA policy differs")
    return {
        "container_id": container_row.get("Id"),
        "container_name": container_row.get("Name"),
        "pid": pid,
        "data_partition_device": data_device,
        "throttle_parent_device": throttle_device,
        "throttle_parent_major_minor": throttle_major_minor,
        "effective_cgroup_v1_throttle": effective_throttle,
        "docker_blkio_projection": docker_blkio_projection,
        "host_config": {
            key: host_config.get(key)
            for key in (
                "CpuQuota",
                "CpuPeriod",
                "CpusetCpus",
                "CpusetMems",
                "Memory",
                "MemorySwap",
                "PidsLimit",
                "BlkioDeviceReadBps",
                "BlkioDeviceWriteBps",
            )
        },
        "pid_cpus_allowed_list": status.get("Cpus_allowed_list"),
        "pid_mems_allowed_list": status.get("Mems_allowed_list"),
    }


def _tmpfs_size_bytes(host_config, expected_destination):
    tmpfs = host_config.get("Tmpfs")
    if (
        not isinstance(tmpfs, dict)
        or set(tmpfs) != {expected_destination}
    ):
        raise QualificationError("client container tmpfs is absent")
    sizes = []
    for options in tmpfs.values():
        if not isinstance(options, str):
            continue
        for option in options.split(","):
            if option.startswith("size="):
                raw = option.split("=", 1)[1]
                if raw.isdigit():
                    sizes.append(int(raw))
    if len(sizes) != 1:
        raise QualificationError("client container tmpfs size differs")
    return sizes[0]


def _client_container_immutable_projection(container_row):
    """Canonical client identity with no Docker runtime/transient fields.

    In particular, ``State.ExecIDs`` changes whenever the SQL bridge invokes
    ``docker exec``.  Hashing the complete inspect response therefore turns a
    successful bridge sample into a false identity-change.  Lifecycle state
    is checked separately by full ID, name, running PID and startticks.
    """
    try:
        return client_container_immutable_projection(container_row)
    except ValueError as exc:
        raise QualificationError(str(exc))


def _client_container_config_identity(container_row):
    return sha_bytes(canonical(_client_container_immutable_projection(container_row)))


_DOCKER_MANAGED_MUTABLE_BIND_TARGETS = {
    "/etc/hosts": "hosts",
    "/etc/hostname": "hostname",
    "/etc/resolv.conf": "resolv.conf",
}


def _is_docker_managed_mutable_bind(mount, container_id):
    """Accept only Docker's three per-container resolver metadata binds.

    Docker reports these files in ``Mounts`` as writable bind mounts even for
    a read-only-rootfs container.  They are generated under the daemon's
    container directory; treating every writable bind as an application mount
    made the client readback reject an otherwise exact launch.  Do not relax
    this to a destination-only allowlist: the source must be the exact
    container-ID directory and have the daemon-managed filename.
    """
    if (
        mount.get("Type") != "bind"
        or mount.get("RW") is not True
        or not isinstance(container_id, str)
        or not re.fullmatch(r"[0-9a-f]{64}", container_id)
    ):
        return False
    destination = mount.get("Destination")
    expected_name = _DOCKER_MANAGED_MUTABLE_BIND_TARGETS.get(destination)
    source = mount.get("Source")
    if expected_name is None or not isinstance(source, str):
        return False
    source_path = Path(source)
    return (
        source_path.name == expected_name
        and source_path.parent.name == container_id
        and "containers" in source_path.parts
    )



def validate_tpch_secure_measurement_bind(container_row, expected, destination):
    if expected.get("measurement_output_medium") != "host_tmpfs_bind":
        return
    config = container_row.get("Config") or {}
    host = container_row.get("HostConfig") or {}
    if (
        config.get("User") != expected.get("container_user")
        or host.get("Tmpfs") not in ({}, None)
    ):
        raise QualificationError("TPC-H client user/tmpfs contract differs")
    mounts = [
        row for row in (container_row.get("Mounts") or [])
        if row.get("Destination") == destination
    ]
    if len(mounts) != 1:
        raise QualificationError("TPC-H measurement bind cardinality differs")
    mount = mounts[0]
    source = mount.get("Source")
    if (
        mount.get("Type") != "bind"
        or mount.get("RW") is not True
        or not isinstance(source, str)
        or not Path(source).is_absolute()
    ):
        raise QualificationError("TPC-H measurement bind differs")
    source_path = Path(source)
    try:
        state = source_path.stat()
    except OSError as exc:
        raise QualificationError("TPC-H measurement bind source is unreadable") from exc
    if (
        state.st_uid != int(expected["measurement_directory_uid"])
        or state.st_gid != int(expected["measurement_directory_gid"])
        or stat.S_IMODE(state.st_mode)
        != int(str(expected["measurement_directory_mode"]), 8)
    ):
        raise QualificationError("TPC-H measurement bind ownership differs")
    raw = run(
        ["findmnt", "-n", "-o", "FSTYPE,OPTIONS", "--target", str(source_path)],
        timeout=5,
        label="TPC-H measurement bind tmpfs readback",
    ).strip().split(None, 1)
    if len(raw) != 2 or raw[0] != "tmpfs":
        raise QualificationError("TPC-H measurement bind is not tmpfs")
    sizes = [
        _parse_size_option(value.split("=", 1)[1])
        for value in raw[1].split(",")
        if value.startswith("size=")
    ]
    if len(sizes) != 1 or sizes[0] < int(expected["tmpfs_bytes"]):
        raise QualificationError("TPC-H measurement bind capacity differs")

def _validate_tpcc_host_tmpfs_bind(
    container_row, expected, destination, expected_source
):
    if expected.get("measurement_output_medium") != "tmpfs":
        return False
    host = container_row.get("HostConfig") or {}
    if host.get("Tmpfs") not in ({}, None):
        return False
    mounts = [
        row for row in (container_row.get("Mounts") or [])
        if row.get("Destination") == destination
    ]
    if len(mounts) != 1:
        raise QualificationError("TPC-C measurement bind cardinality differs")
    mount = mounts[0]
    source = mount.get("Source")
    if (
        mount.get("Type") != "bind"
        or mount.get("RW") is not True
        or not isinstance(source, str)
        or Path(source).resolve() != Path(expected_source).resolve()
    ):
        raise QualificationError("TPC-C measurement bind source differs")
    configured_user = str((container_row.get("Config") or {}).get("User") or "")
    if re.fullmatch(r"[0-9]+:[0-9]+", configured_user) is None:
        raise QualificationError("TPC-C client numeric execution user differs")
    expected_uid, expected_gid = [int(value) for value in configured_user.split(":")]
    state = Path(source).stat()
    if (
        state.st_uid != expected_uid
        or state.st_gid != expected_gid
        or stat.S_IMODE(state.st_mode) != 0o700
    ):
        raise QualificationError("TPC-C measurement bind ownership differs")
    raw = run(
        ["findmnt", "-n", "-o", "FSTYPE,OPTIONS", "--target", source],
        timeout=5,
        label="TPC-C measurement bind tmpfs readback",
    ).strip().split(None, 1)
    if len(raw) != 2 or raw[0] != "tmpfs":
        raise QualificationError("TPC-C measurement bind is not tmpfs")
    sizes = [
        _parse_size_option(value.split("=", 1)[1])
        for value in raw[1].split(",") if value.startswith("size=")
    ]
    if len(sizes) != 1 or sizes[0] < int(expected["tmpfs_bytes"]):
        raise QualificationError("TPC-C measurement bind capacity differs")
    return True


def validate_client_container_readback(
    container_row, expected, binding, tmpfs_destination,
    host_tmpfs_directory, target_major_minor
):
    host_config = container_row.get("HostConfig") or {}
    state = container_row.get("State") or {}
    container_id = _full_container_id(container_row, "client")
    container_name = _container_name(container_row, "client")
    pid = int(state.get("Pid") or 0)
    if state.get("Running") is not True or pid <= 0:
        raise QualificationError("client container has no running PID")
    if (
        container_id != binding["container_id"]
        or not isinstance(binding.get("container_id"), str)
        or re.fullmatch(r"[0-9a-f]{64}", binding["container_id"]) is None
        or binding.get("container_name") not in (None, container_name)
        or container_row.get("Image") != binding["image_id"]
        or container_row.get("Created") != binding["created"]
        or _client_container_config_identity(container_row)
        != binding["inspect_config_sha256"]
    ):
        raise QualificationError("client container immutable identity differs")
    quota = int(host_config.get("CpuQuota") or 0)
    period = int(host_config.get("CpuPeriod") or 0)
    if (
        period <= 0
        or abs(
            float(quota) / float(period)
            - float(expected["cpu_quota_cores"])
        ) > 1e-9
    ):
        raise QualificationError("client container CPU quota differs")
    exact = {
        "CpusetCpus": expected["cpuset_cpus"],
        "CpusetMems": expected["cpuset_mems"],
        "Memory": expected["memory_bytes"],
        "MemorySwap": expected["memory_swap_bytes"],
        "PidsLimit": expected["pids_max"],
    }
    for key, wanted in exact.items():
        actual = host_config.get(key)
        if key.startswith("Cpuset"):
            if expand_cpuset(actual) != expand_cpuset(wanted):
                raise QualificationError("client container %s differs" % key)
        elif int(actual or 0) != int(wanted):
            raise QualificationError("client container %s differs" % key)
    if (
        host_config.get("NetworkMode") != expected["network_mode"]
        or host_config.get("ReadonlyRootfs") is not True
        or ((host_config.get("LogConfig") or {}).get("Type") !=
            expected["docker_log_driver"])
        or "ALL" not in (host_config.get("CapDrop") or [])
        or not any(
            str(value).replace("=", ":").lower()
            in ("no-new-privileges", "no-new-privileges:true")
            for value in (host_config.get("SecurityOpt") or [])
        )
        or (
            expected.get("measurement_output_medium") != "host_tmpfs_bind"
            and host_config.get("Tmpfs") not in ({}, None)
            and _tmpfs_size_bytes(host_config, tmpfs_destination)
            != int(expected["tmpfs_bytes"])
        )
    ):
        raise QualificationError("client container hardening differs")
    raw_client_mounts = container_row.get("Mounts") or []
    tpcc_host_bind = _validate_tpcc_host_tmpfs_bind(
        container_row, expected, tmpfs_destination, host_tmpfs_directory
    )
    if (
        expected.get("measurement_output_medium") == "tmpfs"
        and not tpcc_host_bind
        and _tmpfs_size_bytes(host_config, tmpfs_destination)
        != int(expected["tmpfs_bytes"])
    ):
        raise QualificationError("TPC-C measurement tmpfs differs")
    validate_tpch_secure_measurement_bind(
        container_row, expected, tmpfs_destination
    )
    allowed_tpcc_volume_sources = set()
    for mount in raw_client_mounts:
        if (
            expected.get("measurement_output_medium") == "tmpfs"
            and expected.get("target_mysql_device_write_prohibited") is True
            and mount.get("Type") == "volume"
            and mount.get("RW") is True
            and mount.get("Destination") == "/var/lib/mysql"
            and isinstance(mount.get("Source"), str)
        ):
            source_state = Path(mount["Source"]).stat()
            source_device = "%d:%d" % (
                os.major(source_state.st_dev), os.minor(source_state.st_dev)
            )
            if source_device != target_major_minor:
                raise QualificationError("TPC-C client volume device differs")
            allowed_tpcc_volume_sources.add(mount["Source"])
        elif mount.get("Type") == "volume":
            raise QualificationError("unexpected client volume is prohibited")
    observed_mounts = {}
    for mount in raw_client_mounts:
        if (
            mount.get("Type") in ("bind", "volume")
            and mount.get("RW") is not False
            and mount.get("Destination") == "/measurement"
            and (
                expected.get("measurement_output_medium") == "host_tmpfs_bind"
                or tpcc_host_bind
            )
        ):
            continue
        if (
            mount.get("Type") == "volume"
            and mount.get("Source") in allowed_tpcc_volume_sources
        ):
            continue
        if mount.get("Type") in ("bind", "volume") and mount.get("RW") is not False:
            if not _is_docker_managed_mutable_bind(
                mount, container_row.get("Id")
            ):
                raise QualificationError("client container mutable mount differs")
        if mount.get("Type") in ("bind", "volume"):
            observed_mounts[(mount.get("Source"), mount.get("Destination"))] = (
                mount.get("RW")
            )
    expected_mounts = {
        (row["source"], row["destination"]): False
        for row in binding["required_read_only_mounts"]
    }
    if not expected_mounts or any(
        observed_mounts.get(pair) is not False for pair in expected_mounts
    ):
        raise QualificationError("client container required read-only mount differs")
    status = process_status(pid)
    if (
        expand_cpuset(status.get("Cpus_allowed_list", ""))
        != expand_cpuset(expected["cpuset_cpus"])
        or expand_cpuset(status.get("Mems_allowed_list", ""))
        != expand_cpuset(expected["cpuset_mems"])
    ):
        raise QualificationError("client container PID affinity differs")
    return {
        "container_id": container_id,
        "container_name": container_name,
        "container_short_id": container_id[:12],
        "pid": pid,
        "process_start_ticks": process_start_ticks(pid),
        "host_config_sha256": sha_bytes(
            canonical({
                "CpuQuota": host_config.get("CpuQuota"),
                "CpuPeriod": host_config.get("CpuPeriod"),
                "CpusetCpus": host_config.get("CpusetCpus"),
                "CpusetMems": host_config.get("CpusetMems"),
                "Memory": host_config.get("Memory"),
                "MemorySwap": host_config.get("MemorySwap"),
                "PidsLimit": host_config.get("PidsLimit"),
                "NetworkMode": host_config.get("NetworkMode"),
                "ReadonlyRootfs": host_config.get("ReadonlyRootfs"),
                "LogConfig": host_config.get("LogConfig"),
                "CapDrop": host_config.get("CapDrop"),
                "SecurityOpt": host_config.get("SecurityOpt"),
                "Tmpfs": host_config.get("Tmpfs"),
            })
        ),
        "inspect_config_sha256": _client_container_config_identity(
            container_row
        ),
    }


def resolve_device(device):
    try:
        mode = device.stat().st_mode
        device_number = device.stat().st_rdev
    except OSError as exc:
        raise QualificationError("device readback failed: %s" % exc)
    if not stat_is_block(mode):
        raise QualificationError("declared device is not a block device")
    return "%d:%d" % (os.major(device_number), os.minor(device_number))


def stat_is_block(mode):
    # Kept separate for unit tests and Python 3.6 compatibility.
    import stat as stat_module
    return stat_module.S_ISBLK(mode)


def mount_readback(mount):
    output = run(
        ["findmnt", "-n", "-o", "SOURCE,FSTYPE,TARGET", "--target", mount],
        timeout=5,
        label="mount readback",
    ).strip()
    pieces = output.split()
    if len(pieces) != 3:
        raise QualificationError("mount readback cardinality differs")
    return {"source": pieces[0], "filesystem": pieces[1], "target": pieces[2]}


def _parse_size_option(raw):
    match = re.match(r"^([0-9]+)([kKmMgG]?)$", raw)
    if not match:
        raise QualificationError("tmpfs size option differs")
    scale = {
        "": 1,
        "k": 1024,
        "m": 1024 ** 2,
        "g": 1024 ** 3,
    }[match.group(2).lower()]
    return int(match.group(1)) * scale


def validate_host_tmpfs_output(path, expected_directory, minimum_bytes):
    expected = Path(expected_directory).resolve()
    candidate = Path(path).resolve()
    if candidate != expected and expected not in candidate.parents:
        raise QualificationError("measurement output escaped frozen tmpfs")
    raw = run(
        ["findmnt", "-n", "-o", "FSTYPE,TARGET,OPTIONS", "--target",
         str(candidate)],
        timeout=5,
        label="measurement tmpfs readback",
    ).strip()
    pieces = raw.split(None, 2)
    if len(pieces) != 3 or pieces[0] != "tmpfs":
        raise QualificationError("measurement output is not tmpfs")
    mount_target = Path(pieces[1]).resolve()
    if mount_target != expected:
        raise QualificationError("measurement tmpfs mount target differs")
    size_values = [
        _parse_size_option(option.split("=", 1)[1])
        for option in pieces[2].split(",")
        if option.startswith("size=")
    ]
    if len(size_values) != 1 or size_values[0] < int(minimum_bytes):
        raise QualificationError("measurement tmpfs capacity differs")
    return {
        "filesystem": pieces[0],
        "target": str(mount_target),
        "size_bytes": size_values[0],
    }


def db_counter_sample(
    mysql_binary,
    defaults_file,
    port,
    database_counter_transport=None,
):
    if defaults_file.is_symlink() or not defaults_file.is_file():
        raise QualificationError("mysql defaults file absent or symlinked")
    # MySQL 8 installations may expose statement counters through SHOW STATUS
    # while a filtered performance_schema.global_status query silently omits
    # the Com_* rows (notably with restricted performance_schema views).  The
    # protocol command is still one exact client-container stdout read; fetch
    # the server's canonical global-status stream and retain only DB_COUNTERS
    # below.  Missing counters remain a hard failure.
    sql = "SHOW GLOBAL STATUS"
    if database_counter_transport is None:
        if not mysql_binary.is_file():
            raise QualificationError("mysql client is absent")
        argv = [
            str(mysql_binary),
            "--defaults-extra-file=%s" % defaults_file,
            "--protocol=TCP",
            "--host=127.0.0.1",
            "--port=%d" % int(port),
            "--batch",
            "--raw",
            "--skip-column-names",
            "--execute",
            sql,
        ]
    else:
        transport = database_counter_transport
        current = inspect_container(transport["client_container"])
        current_id = _full_container_id(current, "database-counter client")
        current_name = _container_name(current, "database-counter client")
        current_pid = int((current.get("State") or {}).get("Pid") or 0)
        current_start_ticks = process_start_ticks(current_pid)
        current_identity_sha = _client_container_config_identity(current)
        if (
            current_id != transport["client_container_id"]
            or current_name != transport["client_container_name"]
            or current_id[:12] != transport["client_container_short_id"]
            or current_pid != int(transport["client_pid"])
            or current_start_ticks
            != int(transport["client_process_start_ticks"])
            or current_identity_sha != transport["client_inspect_config_sha256"]
        ):
            raise QualificationError(
                "database-counter client container identity changed: "
                "expected_full_id=%s observed_full_id=%s "
                "expected_name=%s observed_name=%s "
                "expected_short_id=%s observed_short_id=%s "
                "expected_pid=%s observed_pid=%s "
                "expected_start_ticks=%s observed_start_ticks=%s "
                "expected_immutable_identity_sha256=%s "
                "observed_immutable_identity_sha256=%s"
                % (
                    transport["client_container_id"], current_id,
                    transport["client_container_name"], current_name,
                    transport["client_container_short_id"], current_id[:12],
                    transport["client_pid"], current_pid,
                    transport["client_process_start_ticks"], current_start_ticks,
                    transport["client_inspect_config_sha256"], current_identity_sha,
                )
            )
        argv = [
            *DOCKER_CLI,
            "exec", "--user", transport["docker_exec_user"],
            transport["client_container_id"],
            transport["mysql_binary_in_client"],
            "--defaults-extra-file=%s"
            % transport.get("mysql_defaults_file_in_client", defaults_file),
            "--protocol=TCP",
            "--host=127.0.0.1",
            "--port=%d" % int(port),
            "--batch",
            "--raw",
            "--skip-column-names",
            "--execute",
            sql,
        ]
    output = run(
        argv,
        timeout=1,
        label="database counter sample",
    )
    observed = {}
    required_lower = {name.lower() for name in DB_COUNTERS}
    for line in output.splitlines():
        pieces = line.split("\t", 1)
        if len(pieces) != 2:
            continue
        key = pieces[0].strip().lower()
        # SHOW GLOBAL STATUS is intentionally a full MySQL 8 stream.  It may
        # contain non-numeric informational rows such as
        # Caching_sha2_password_rsa_public_key; those are neither requested
        # counters nor evidence.  Only required names are parsed as integers.
        if key not in required_lower:
            continue
        if key in observed:
            raise QualificationError("duplicate database counter: %s" % pieces[0])
        try:
            observed[key] = int(pieces[1].strip())
        except ValueError as exc:
            raise QualificationError(
                "database counter value is not an integer: %s" % pieces[0]
            ) from exc
    counters = {
        name: observed[name.lower()]
        for name in DB_COUNTERS
        if name.lower() in observed
    }
    if set(counters) != set(DB_COUNTERS):
        missing = sorted(set(DB_COUNTERS) - set(counters))
        raise QualificationError(
            "database counter coverage incomplete: %s" % ",".join(missing)
        )
    return counters


def prepare_database_counter_transport(
    runtime,
    bundle,
    database_container,
    database_row,
    defaults_file,
    mysql_client_container,
    mysql_binary_in_client,
):
    contract = runtime.get("database_counter_transport")
    if contract is None:
        if mysql_client_container is not None:
            raise QualificationError(
                "client SQL bridge supplied without runtime contract"
            )
        return None
    legacy_contract = {
            "schema_version":
                "mysql_algorithm_c_database_counter_transport_v1",
            "kind": "exact_client_container_docker_exec_stdout_v1",
            "mysql_binary_in_client": "/usr/bin/mysql",
            "docker_exec_user": "0",
            "stdout_only": True,
            "host_mysql_cli_required": False,
            "docker_socket_mount_prohibited": True,
            "target_datadir_mount_prohibited": True,
        }
    sealed_config_contract = {
        "schema_version":
            "mysql_algorithm_c_database_counter_transport_v2",
        "kind": "exact_client_container_docker_exec_stdout_v1",
        "mysql_binary_in_client": "/usr/bin/mysql",
        "mysql_defaults_file_relative":
            "qualification/config/mysql-client.cnf",
        "mysql_defaults_file_in_client":
            "/opt/mysql-xp-config/mysql-client.cnf",
        "client_bundle_destination": "/opt/mysql-xp-bundle",
        "client_runtime_destination": "/opt/mysql-xp-jdk",
        "client_config_source_relative": "qualification/config",
        "client_config_destination": "/opt/mysql-xp-config",
        "stdout_only": True,
        "host_mysql_cli_required": False,
        "docker_socket_mount_prohibited": True,
        "target_datadir_mount_prohibited": True,
    }
    if (
        contract not in (legacy_contract, sealed_config_contract)
        or not mysql_client_container
        or mysql_binary_in_client != contract["mysql_binary_in_client"]
        or not str(database_container).endswith("_db")
        or mysql_client_container
        != str(database_container)[:-3] + "_client"
    ):
        raise QualificationError(
            "database-counter client SQL bridge authority differs"
        )
    client = inspect_container(mysql_client_container)
    client_id = _full_container_id(client, "database-counter client")
    client_name = _container_name(client, "database-counter client")
    if client_name != mysql_client_container:
        raise QualificationError("database-counter client container name differs")
    mounts = client.get("Mounts") or []
    database_sources = {
        row.get("Source")
        for row in database_row.get("Mounts") or []
        if row.get("Destination") == "/var/lib/mysql"
    }
    if (
        any(
            "/var/run/docker.sock"
            in (str(row.get("Source")) + " " + str(row.get("Destination")))
            for row in mounts
        )
        or any(row.get("Source") in database_sources for row in mounts)
    ):
        raise QualificationError(
            "database-counter client SQL bridge mount isolation differs"
        )
    if contract == legacy_contract:
        required_mounts = [
            {"source": str(bundle), "destination": str(bundle)},
            {
                "source": runtime["toolchain"]["python_runtime_root"],
                "destination": runtime["toolchain"]["python_runtime_root"],
            },
            {
                "source": str(defaults_file.parent),
                "destination": str(defaults_file.parent),
            },
            {
                "source": str(
                    bundle
                    / "qualification/client_volume_masks/mysql_datadir"
                ),
                "destination": "/var/lib/mysql",
            },
        ]
        defaults_file_in_client = str(defaults_file)
    else:
        expected_defaults = (
            bundle / contract["mysql_defaults_file_relative"]
        ).resolve()
        config_source = (
            bundle / contract["client_config_source_relative"]
        ).resolve()
        runtime_source = Path(
            runtime["toolchain"]["deployment_target"]
        ).resolve()
        if (
            defaults_file.resolve() != expected_defaults
            or defaults_file.is_symlink()
            or not defaults_file.is_file()
        ):
            raise QualificationError(
                "manifest-bound MySQL defaults file differs"
            )
        required_mounts = [
            {
                "source": str(bundle),
                "destination": contract["client_bundle_destination"],
            },
            {
                "source": str(runtime_source),
                "destination": contract["client_runtime_destination"],
            },
            {
                "source": str(config_source),
                "destination": contract["client_config_destination"],
            },
        ]
        defaults_file_in_client = contract["mysql_defaults_file_in_client"]
    binding = {
        "container_id": client_id,
        "container_name": client_name,
        "image_id": client.get("Image"),
        "created": client.get("Created"),
        "inspect_config_sha256": _client_container_config_identity(client),
        "required_read_only_mounts": required_mounts,
    }
    checked = validate_client_container_readback(
        client,
        runtime["client_resource_envelope"],
        binding,
        "/measurement",
    )
    version = run(
        [
            *DOCKER_CLI,
            "exec", "--user", contract["docker_exec_user"],
            client_id,
            mysql_binary_in_client,
            "--version",
        ],
        timeout=5,
        label="client-container native MySQL CLI readback",
    ).strip()
    if not version or "mysql" not in version.lower():
        raise QualificationError(
            "client-container native MySQL CLI is absent"
        )
    return {
        "schema_version":
            "mysql_algorithm_c_database_counter_transport_readback_v1",
        "status": "PASS_EXACT_CLIENT_DOCKER_EXEC_SQL_STDOUT_BRIDGE",
        "kind": contract["kind"],
        "client_container": mysql_client_container,
        "client_container_name": client_name,
        "client_container_id": checked["container_id"],
        "client_container_short_id": checked["container_short_id"],
        "client_pid": checked["pid"],
        "client_process_start_ticks": checked["process_start_ticks"],
        "client_inspect_config_sha256":
            checked["inspect_config_sha256"],
        "mysql_binary_in_client": mysql_binary_in_client,
        "docker_exec_user": contract["docker_exec_user"],
        "mysql_defaults_file_in_client": defaults_file_in_client,
        "mysql_version_stdout": version,
        "stdout_only": True,
        "host_mysql_cli_required": False,
        "docker_socket_mounted": False,
        "target_datadir_mounted": False,
        "target_device_write_authorized": False,
        "accepted_round_authorized": False,
    }




def _verify_live_daemon_affinity_remediation(bundle, contract, pid):
    path = Path(bundle) / 'authority/tpch_daemon_affinity_remediation_v1.json'
    sidecar = Path(str(path) + ".sha256")
    try:
        payload = path.read_bytes()
        authority = load_object(path, "daemon affinity remediation authority")
    except (OSError, UnicodeError, ValueError) as exc:
        raise QualificationError("daemon affinity remediation authority unreadable") from exc
    if (
        hashlib.sha256(payload).hexdigest() != 'c6906e3580f88b6e21fcaa41250b51cc76952656e7a20fe08efed4370ce0e853'
        or sidecar.is_symlink()
        or not sidecar.is_file()
        or sidecar.read_text(encoding="ascii")
        != 'c6906e3580f88b6e21fcaa41250b51cc76952656e7a20fe08efed4370ce0e853' + "  " + path.name + "\n"
    ):
        raise QualificationError("daemon affinity remediation file identity differs")
    unsigned = dict(authority)
    recorded = unsigned.pop("authority_sha256", None)
    expected_effective = "32-33,68-69"
    expected_peer = "34-35,70-71"
    try:
        tids = sorted(
            int(entry.name)
            for entry in (Path("/proc") / str(pid) / "task").iterdir()
            if entry.name.isdigit()
        )
        task_sets = {tid: set(os.sched_getaffinity(tid)) for tid in tids}
        peer_pid = int(authority.get("peer_main_pid") or 0)
        peer_tids = sorted(
            int(entry.name)
            for entry in (Path("/proc") / str(peer_pid) / "task").iterdir()
            if entry.name.isdigit()
        )
        peer_sets = {tid: set(os.sched_getaffinity(tid)) for tid in peer_tids}
        shown = run(
            ["systemctl", "show", str(authority.get("peer_unit") or ""),
             "--property=ActiveState", "--property=SubState", "--property=MainPID"],
            timeout=30, label="peer daemon systemd readback",
        )
        properties = {}
        for line in shown.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                properties[key] = value
    except (OSError, PermissionError, ValueError) as exc:
        raise QualificationError("daemon affinity remediation live readback failed") from exc
    wanted = set((32, 33, 68, 69))
    peer_wanted = set((34, 35, 70, 71))
    if (
        recorded != '915d27c99be693866bb31f0260727e2012bf58351bd7d9d5da9a8cdb4433528c'
        or recorded != hashlib.sha256(canonical(unsigned)).hexdigest()
        or authority.get("schema_version")
        != "mysql_dedicated_daemon_live_affinity_remediation_v1"
        or authority.get("status") != "PASS_EFFECTIVE_DISJOINT_DAEMON_AFFINITY"
        or authority.get("formal_scope") != "mysql_only"
        or authority.get("cross_engine_comparison_prohibited") is not True
        or authority.get("postgresql_mutation_prohibited") is not True
        or authority.get("unit") != contract["unit"]
        or int(authority.get("main_pid") or 0) != pid
        or int(authority.get("main_pid_startticks") or 0)
        != contract["main_pid_starttime_ticks"]
        or authority.get("cmdline_sha256") != contract["main_pid_cmdline_sha256"]
        or authority.get("declared_exec_affinity")
        != contract["main_pid_cpus_allowed_list"]
        or authority.get("effective_affinity_after") != expected_effective
        or authority.get("peer_effective_affinity") != expected_peer
        or authority.get("peer_unit") != "ra-docker-mysql-tpcc-v1.service"
        or authority.get("all_threads_exact") is not True
        or authority.get("effective_sets_disjoint") is not True
        or authority.get("container_mutation_performed") is not False
        or authority.get("storage_mutation_performed") is not False
        or authority.get("network_mutation_performed") is not False
        or authority.get("systemd_unit_mutation_performed") is not False
        or authority.get("formal_launch_authorized") is not False
        or authority.get("accepted_round_authorized") is not False
        or properties.get("ActiveState") != "active"
        or properties.get("SubState") != "running"
        or int(properties.get("MainPID") or 0) != peer_pid
        or not task_sets
        or any(values != wanted for values in task_sets.values())
        or not peer_sets
        or any(values != peer_wanted for values in peer_sets.values())
        or any(values & peer for values in task_sets.values() for peer in peer_sets.values())
    ):
        raise QualificationError("daemon affinity remediation authority differs")
    return {
        "file_sha256": 'c6906e3580f88b6e21fcaa41250b51cc76952656e7a20fe08efed4370ce0e853',
        "authority_sha256": recorded,
        "declared_affinity": contract["main_pid_cpus_allowed_list"],
        "effective_affinity": expected_effective,
        "all_current_threads_exact": True,
        "peer_main_pid": peer_pid,
        "peer_all_current_threads_exact": True,
        "effective_sets_disjoint": True,
    }


def _live_daemon_process_readback(pid, contract):
    remediation = _verify_live_daemon_affinity_remediation(
        bundle_from_script(), contract, pid
    )
    try:
        status_lines = (Path("/proc") / str(pid) / "status").read_text(
            encoding="ascii"
        ).splitlines()
        cmdline = (Path("/proc") / str(pid) / "cmdline").read_bytes()
        cgroup_lines = (Path("/proc") / str(pid) / "cgroup").read_text(
            encoding="ascii"
        ).splitlines()
    except (OSError, UnicodeError) as exc:
        raise QualificationError("live daemon process readback failed") from exc
    status = {}
    for line in status_lines:
        if ":" in line:
            key, value = line.split(":", 1)
            status[key] = value.strip()
    argv = [
        item.decode("utf-8")
        for item in cmdline.rstrip(b"\0").split(b"\0")
        if item
    ]
    namespaces = {}
    try:
        for name in contract["mandatory_namespaces"]:
            namespaces[name] = os.readlink("/proc/%d/ns/%s" % (pid, name))
    except OSError as exc:
        raise QualificationError("live daemon mandatory namespace readback failed") from exc
    cgroup_ns_path = "/proc/%d/ns/cgroup" % pid
    cgroup_ns_supported = os.path.lexists(cgroup_ns_path)
    if (
        int(status.get("PPid") or 0) != contract["main_pid_ppid"]
        or status.get("Cpus_allowed_list") != remediation["effective_affinity"]
        or process_start_ticks(pid) != contract["main_pid_starttime_ticks"]
        or hashlib.sha256(cmdline).hexdigest() != contract["main_pid_cmdline_sha256"]
        or argv != contract["main_pid_argv"]
        or cgroup_lines != contract["cgroup_v1_lines"]
        or namespaces != contract["mandatory_namespace_identities"]
        or cgroup_ns_supported != contract["optional_cgroup_namespace"]["supported"]
        or (not cgroup_ns_supported) != contract["optional_cgroup_namespace"]["path_absent"]
    ):
        raise QualificationError("live daemon process identity differs")
    return {
        "ppid": int(status["PPid"]),
        "cpus_allowed_list": status["Cpus_allowed_list"],
        "declared_cpus_allowed_list": contract["main_pid_cpus_allowed_list"],
        "affinity_remediation": remediation,
        "starttime_ticks": contract["main_pid_starttime_ticks"],
        "cmdline_sha256": hashlib.sha256(cmdline).hexdigest(),
        "argv": argv,
        "cgroup_v1_lines": cgroup_lines,
        "mandatory_namespaces": namespaces,
        "optional_cgroup_namespace": {
            "supported": cgroup_ns_supported,
            "path_absent": not cgroup_ns_supported,
        },
    }

def _unix_listener_identity(
    socket_path: str, main_pid: int
):
    try:
        lines = Path("/proc/net/unix").read_text(
            encoding="ascii"
        ).splitlines()
    except (OSError, UnicodeError) as exc:
        raise QualificationError("Unix socket table readback failed") from exc
    path_rows = []
    listeners = []
    for line in lines:
        fields = line.split()
        if len(fields) < 8 or fields[7] != socket_path:
            continue
        row = {
            "raw": line,
            "flags": fields[3],
            "type": fields[4],
            "state": fields[5],
            "inode": int(fields[6]),
            "path": fields[7],
        }
        path_rows.append(row)
        if (
            row["flags"] == "00010000"
            and row["type"] == "0001"
            and row["state"] == "01"
        ):
            listeners.append(row)
    if len(listeners) != 1:
        raise QualificationError(
            "Unix socket exact-path listener cardinality differs"
        )
    listener = listeners[0]
    try:
        fd_targets = {
            entry.name: os.readlink(entry)
            for entry in (
                Path("/proc") / str(main_pid) / "fd"
            ).iterdir()
        }
    except (OSError, PermissionError) as exc:
        raise QualificationError(
            "dedicated daemon socket FD inventory is unreadable"
        ) from exc
    expected_target = "socket:[%d]" % listener["inode"]
    owners = sorted(
        name for name, target in fd_targets.items()
        if target == expected_target
    )
    if not owners:
        raise QualificationError(
            "dedicated daemon MainPID does not own listener inode"
        )
    return {
        "schema_version": "mysql_tpch_unix_listener_identity_v1",
        "socket_path": socket_path,
        "path_row_count": len(path_rows),
        "listener_count": 1,
        "listener_flags": listener["flags"],
        "listener_type": listener["type"],
        "listener_state": listener["state"],
        "listener_kernel_inode": listener["inode"],
        "listener_fd_target": expected_target,
        "main_pid": main_pid,
        "main_pid_owner_fds": owners,
        "main_pid_holds_listener": True,
    }

def legacy_dedicated_daemon_readback_must_be_overridden():
    """The current shared entry installs a same-live readback implementation."""
    raise QualificationError("current shared entry did not install daemon readback")

def _obsolete_dedicated_daemon_readback_never_installed():
    shown = run(
        [
            "systemctl", "show", DOCKER_UNIT,
            "--property=ActiveState", "--property=SubState",
            "--property=MainPID", "--property=FragmentPath",
            "--property=ExecStart", "--property=ControlGroup",
            "--property=CPUShares", "--property=MemoryLimit",
            "--property=TasksMax", "--property=Id",
            "--property=Transient",
        ],
        timeout=30,
        label="dedicated daemon systemd readback",
    )
    properties = {}
    for line in shown.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            properties[key] = value
    contract = {"entry_override_required": True}
    main_pid = int(properties.get("MainPID") or 0)
    expected_exec = "argv[]=" + " ".join(contract["exec_argv"]) + " ;"
    socket_state = os.stat(DOCKER_SOCKET, follow_symlinks=False)
    listener_identity = _unix_listener_identity(
        DOCKER_SOCKET, main_pid
    )
    if (
        properties.get("ActiveState") != "active"
        or properties.get("SubState") != "running"
        or properties.get("Id") != contract["unit"]
        or properties.get("FragmentPath") != contract["unit_fragment"]
        or properties.get("Transient") != "yes"
        or main_pid != contract["main_pid"]
        or properties.get("ControlGroup") != contract["control_group"]
        or int(properties.get("CPUShares") or 0) != contract["cpu_shares"]
        or int(properties.get("MemoryLimit") or 0)
        != contract["memory_limit_bytes"]
        or int(properties.get("TasksMax") or 0) != contract["tasks_max"]
        or expected_exec not in properties.get("ExecStart", "")
        or not stat.S_ISSOCK(socket_state.st_mode)
    ):
        raise QualificationError("dedicated daemon systemd/socket differs")
    process_readback = _live_daemon_process_readback(
        main_pid, contract
    )
    raw_info = run(
            [*DOCKER_CLI, "info", "--format", "{{json .}}"],
            timeout=30,
            label="dedicated daemon info readback",
    )
    try:
        info = json.loads(raw_info)
    except ValueError as exc:
        raise QualificationError("dedicated daemon info JSON differs")
    data_state = os.stat(DOCKER_DATA_ROOT)
    mounted = run(
        ["findmnt", "-n", "-o", "SOURCE,FSTYPE,TARGET", "--target",
         DOCKER_DATA_ROOT],
        timeout=30,
        label="dedicated daemon root findmnt",
    ).strip().split()
    if (
        info.get("DockerRootDir") != DOCKER_DATA_ROOT
        or info.get("Driver") != DOCKER_GRAPH_DRIVER
        or info.get("CgroupDriver") != "cgroupfs"
        or "%d:%d" % (os.major(data_state.st_dev), os.minor(data_state.st_dev))
        != DOCKER_ROOT_MAJOR_MINOR
        or len(mounted) != 3
        or mounted[0] != "/dev/nvme0n1"
        or mounted[2] != "/mnt/nvme0n1"
    ):
        raise QualificationError("dedicated daemon root/driver/findmnt differs")
    return {
        "schema_version": "mysql_tpch_dedicated_daemon_readback_v1",
        "status": "PASS_DEDICATED_DAEMON_EXACT_NON_PG",
        "unit": DOCKER_UNIT,
        "unit_fragment": contract["unit_fragment"],
        "transient": True,
        "control_group": contract["control_group"],
        "cpu_shares": contract["cpu_shares"],
        "memory_limit_bytes": contract["memory_limit_bytes"],
        "tasks_max": contract["tasks_max"],
        "main_pid": main_pid,
        "main_pid_process_readback": process_readback,
        "socket": DOCKER_SOCKET,
        "socket_uri": DOCKER_SOCKET_URI,
        "socket_filesystem_inode": socket_state.st_ino,
        "socket_listener_identity": listener_identity,
        "docker_root_dir": info["DockerRootDir"],
        "docker_root_device_major_minor": DOCKER_ROOT_MAJOR_MINOR,
        "graph_driver": info["Driver"],
        "cgroup_driver": info["CgroupDriver"],
        "bridge": "none",
        "iptables": False,
        "postgresql_mutation_prohibited": True,
        "accepted_round_authorized": False,
    }

def port_probe(port):
    connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    connection.settimeout(1.0)
    try:
        result = connection.connect_ex(("127.0.0.1", int(port)))
    finally:
        connection.close()
    if result != 0:
        raise QualificationError("declared MySQL port is not reachable")


def collect_readback(
    bundle,
    container,
    mysql_binary,
    defaults_file,
    mysql_client_container=None,
    mysql_binary_in_client=None,
):
    manifest, runtime = verify_bundle(bundle)
    expected_host = runtime["host"]["hostname"]
    actual_host = socket.gethostname()
    if actual_host != expected_host:
        raise QualificationError(
            "hostname differs: expected=%s actual=%s"
            % (expected_host, actual_host)
        )
    storage = runtime["storage"]
    device = Path(storage["device"])
    major_minor = resolve_device(device)
    if major_minor != storage["expected_live_major_minor"]:
        raise QualificationError("device major:minor differs")
    mounted = mount_readback(storage["mount"])
    if (
        mounted["target"] != storage["mount"]
        or mounted["source"] != storage["device"]
    ):
        raise QualificationError("mount source/target differs")
    daemon_readback = dedicated_daemon_readback()
    database_row = inspect_container(container)
    effective_blkio_authority = validate_tpch_effective_blkio_authority(
        database_row, runtime, defaults_file, manifest["bundle_id"]
    )
    attempt_token = Path(defaults_file).parent.parent.name
    container_readback = validate_container_readback(
        database_row,
        runtime,
        effective_blkio_authority,
        manifest["bundle_id"],
        attempt_token,
    )
    container_readback["effective_blkio_authority"] = (
        effective_blkio_authority
    )
    port = runtime["resource_envelope"]["mysql_port"]
    port_probe(port)
    database_counter_transport = prepare_database_counter_transport(
        runtime,
        bundle,
        container,
        database_row,
        defaults_file,
        mysql_client_container,
        mysql_binary_in_client,
    )
    counters = db_counter_sample(
        mysql_binary,
        defaults_file,
        port,
        database_counter_transport,
    )
    toolchain_contract = runtime["toolchain"]
    java_required = toolchain_contract.get("java_required")
    python_required = toolchain_contract.get("python_required")
    if (
        java_required not in (True, False)
        or python_required not in (True, False)
        or not (java_required or python_required)
    ):
        raise QualificationError("toolchain required-runtime flags differ")
    toolchain = {}
    if java_required:
        jdk_manifest = load_object(
            bundle / toolchain_contract["tree_manifest"], "JDK tree manifest"
        )
        jdk_root = Path(toolchain_contract["deployment_target"])
        java_tree = tree_readback(jdk_root, jdk_manifest)
        expected_tree = toolchain_contract["tree_content_sha256"]
        if (
            java_tree["tree_content_sha256"] != expected_tree
            or java_tree["algorithm"]
            != toolchain_contract["tree_content_identity_algorithm"]
        ):
            raise QualificationError("live JDK tree identity differs")
        java_binary = jdk_root / toolchain_contract["java_binary_relative"]
        java_sha = sha256(java_binary)
        release_sha = sha256(jdk_root / "release")
        if (
            java_sha != toolchain_contract["java_binary_sha256"]
            or release_sha != toolchain_contract["release_sha256"]
        ):
            raise QualificationError("live JDK executable/release differs")
        java_tree["java_binary"] = str(java_binary)
        java_tree["java_binary_sha256"] = java_sha
        java_tree["release_sha256"] = release_sha
        toolchain["java"] = java_tree
    if python_required:
        python_manifest = load_object(
            bundle / toolchain_contract["python_tree_manifest"],
            "Python tree manifest",
        )
        python_root = Path(toolchain_contract["python_runtime_root"])
        python_relative = toolchain_contract.get("python_binary_relative")
        if (
            not isinstance(python_relative, str)
            or not python_relative
            or Path(python_relative).is_absolute()
            or ".." in Path(python_relative).parts
            or python_manifest.get("schema_version")
            != "mysql_algorithm_c_runtime_tree_manifest_v3"
            or (python_manifest.get("artifacts") or {}).get(
                python_relative
            )
            != toolchain_contract["python_binary_sha256"]
        ):
            raise QualificationError(
                "Python manifest-bound executable differs"
            )
        python_tree = tree_readback(python_root, python_manifest)
        if (
            python_tree["tree_content_sha256"]
            != toolchain_contract["python_tree_content_sha256"]
            or python_tree["algorithm"]
            != toolchain_contract[
                "python_tree_content_identity_algorithm"
            ]
        ):
            raise QualificationError("live Python tree identity differs")
        python_binary = python_root / python_relative
        if python_binary != Path(toolchain_contract["python_runtime"]):
            raise QualificationError(
                "Python executable path is not manifest-bound"
            )
        python_sha = sha256(python_binary)
        if python_sha != toolchain_contract["python_binary_sha256"]:
            raise QualificationError("live Python executable differs")
        python_version = run(
            [str(python_binary), "--version"],
            timeout=5,
            label="Python runtime readback",
        ).strip()
        if toolchain_contract["python_version_expected"] not in python_version:
            raise QualificationError("Python runtime version differs")
        python_tree["python_binary"] = str(python_binary)
        python_tree["python_binary_sha256"] = python_sha
        python_tree["python_version"] = python_version
        toolchain["python"] = python_tree
    return {
        "schema_version": "mysql_algorithm_c_distributed_live_readback_v1",
        "status": "PASS_LIVE_READBACK_NOT_LAUNCH_AUTHORITY",
        "collected_epoch": time.time(),
        "bundle_id": manifest["bundle_id"],
        "bundle_manifest_sha256": sha256(bundle / "bundle_manifest.json"),
        "scenario": runtime["scenario"],
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "hostname": actual_host,
        "storage": {
            "device": storage["device"],
            "major_minor": major_minor,
            "mount": mounted,
        },
        "resource_envelope": runtime["resource_envelope"],
        "client_resource_envelope": runtime["client_resource_envelope"],
        "container": container_readback,
        "mysql_port": port,
        "database_counters": counters,
        "database_counter_transport": database_counter_transport,
        "toolchain": toolchain,
        "dedicated_daemon": daemon_readback,
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }


def _mapped_host_path(container_path, mounts):
    candidate = Path(container_path)
    matches = []
    for row in mounts:
        destination = Path(row["destination"])
        if candidate == destination or destination in candidate.parents:
            matches.append((len(destination.parts), destination, Path(row["source"])))
    if not matches:
        raise QualificationError(
            "container path is outside frozen read-only mount closure"
        )
    _, destination, source = sorted(matches, reverse=True)[0]
    return source / candidate.relative_to(destination)


def validate_command_spec(path, bundle_id, mode, expected_client_envelope):
    if not valid_sidecar(path):
        raise QualificationError("qualification command sidecar differs")
    spec = load_object(path, "qualification command")
    argv = spec.get("argv")
    attempt_id = spec.get("qualification_attempt_id")
    metric_stream = spec.get("workload_metric_stream")
    native_adapter = spec.get("workload_metric_adapter")
    workload_isolation = spec.get("workload_process_isolation")
    client_container = spec.get("client_container")
    measurement_output = spec.get("measurement_output")
    if (
        spec.get("schema_version")
        != "mysql_algorithm_c_debug_qualification_command_v1"
        or spec.get("bundle_id") != bundle_id
        or spec.get("mode") != mode
        or spec.get("debug_only") is not True
        or spec.get("capture_authorized") is not False
        or spec.get("formal_r0_authorized") is not False
        or spec.get("formal_authorized") is not False
        or spec.get("accepted_round_authorized") is not False
        or not isinstance(argv, list)
        or not argv
        or not all(isinstance(value, str) and value for value in argv)
        or not isinstance(attempt_id, str)
        or len(attempt_id) < 32
        or not isinstance(metric_stream, dict)
        or not isinstance(native_adapter, dict)
        or not isinstance(workload_isolation, dict)
        or not isinstance(client_container, dict)
        or not isinstance(measurement_output, dict)
    ):
        raise QualificationError("qualification command authority differs")
    if set(metric_stream) != {"schema_version", "path"} or (
        metric_stream.get("schema_version")
        != "mysql_algorithm_c_debug_workload_metric_stream_v1"
    ):
        raise QualificationError("workload metric stream contract differs")
    if (
        set(native_adapter) != {"schema_version", "kind"}
        or native_adapter.get("schema_version")
        != "mysql_algorithm_c_debug_workload_metric_adapter_v1"
        or native_adapter.get("kind")
        not in ("hybench_xp", "benchbase_tpcc", "sql_query_native")
    ):
        raise QualificationError("native workload metric adapter differs")
    if (
        set(workload_isolation)
        != {
            "cpuset_cpus",
            "cpuset_mems",
            "root_and_descendants_exact",
        }
        or not isinstance(workload_isolation.get("cpuset_cpus"), str)
        or not expand_cpuset(workload_isolation["cpuset_cpus"])
        or not isinstance(workload_isolation.get("cpuset_mems"), str)
        or not expand_cpuset(workload_isolation["cpuset_mems"])
        or workload_isolation.get("root_and_descendants_exact") is not True
    ):
        raise QualificationError("workload process isolation differs")
    if (
        expand_cpuset(workload_isolation["cpuset_cpus"])
        != expand_cpuset(expected_client_envelope["cpuset_cpus"])
        or expand_cpuset(workload_isolation["cpuset_mems"])
        != expand_cpuset(expected_client_envelope["cpuset_mems"])
        or expected_client_envelope.get("execution_mode")
        != "dedicated_read_only_docker_client_cgroup"
        or (
            expected_client_envelope.get("measurement_output_medium")
            == "host_tmpfs_bind"
            and set(client_container) != {
                "name", "container_id", "image_id", "created",
                "inspect_config_sha256", "execution_user",
                "required_read_only_mounts",
            }
        )
        or (
            expected_client_envelope.get("measurement_output_medium")
            == "tmpfs"
            and set(client_container) != {
                "name", "container_id", "image_id", "created",
                "inspect_config_sha256", "required_read_only_mounts",
            }
        )
        or expected_client_envelope.get("measurement_output_medium")
        not in ("host_tmpfs_bind", "tmpfs")
        or not isinstance(client_container.get("name"), str)
        or not client_container["name"]
    ):
        raise QualificationError("workload isolation differs from frozen client envelope")
    for key in (
        "container_id",
        "image_id",
        "created",
        "inspect_config_sha256",
    ):
        if not isinstance(client_container.get(key), str) or not client_container[key]:
            raise QualificationError("client container immutable identity is absent")
    if (
        expected_client_envelope.get("measurement_output_medium")
        == "host_tmpfs_bind"
        and client_container.get("execution_user")
        != expected_client_envelope.get("container_user")
    ):
        raise QualificationError("TPC-H client execution user differs")
    mounts = client_container.get("required_read_only_mounts")
    if (
        not isinstance(mounts, list)
        or not mounts
        or any(
            not isinstance(row, dict)
            or set(row) != {"source", "destination"}
            or not all(
                isinstance(row.get(key), str)
                and Path(row[key]).is_absolute()
                for key in ("source", "destination")
            )
            for row in mounts
        )
    ):
        raise QualificationError("client container read-only mount closure differs")
    measurement_medium = expected_client_envelope.get(
        "measurement_output_medium"
    )
    expected_measurement_keys = (
        {"host_tmpfs_directory", "host_bind_source",
         "container_tmpfs_destination"}
        if measurement_medium == "host_tmpfs_bind"
        else {"host_tmpfs_directory", "container_tmpfs_destination"}
    )
    if (
        set(measurement_output) != expected_measurement_keys
        or not all(
            isinstance(measurement_output.get(key), str)
            and Path(measurement_output[key]).is_absolute()
            for key in measurement_output
        )
    ):
        raise QualificationError("measurement tmpfs binding differs")
    if measurement_medium == "host_tmpfs_bind":
        host_bind_source = Path(measurement_output["host_bind_source"])
        if (
            host_bind_source.parent
            != Path(measurement_output["host_tmpfs_directory"])
            or host_bind_source.name != "client_measurement"
        ):
            raise QualificationError("TPC-H command measurement bind differs")
    metric_path = Path(str(metric_stream.get("path") or ""))
    host_tmpfs_directory = Path(
        measurement_output["host_tmpfs_directory"]
    )
    container_tmpfs_destination = Path(
        measurement_output["container_tmpfs_destination"]
    )
    if (
        not metric_path.is_absolute()
        or not metric_path.parent.is_dir()
        or metric_path.is_symlink()
        or (metric_path.exists() and not metric_path.is_file())
        or not (
            metric_path == host_tmpfs_directory
            or host_tmpfs_directory in metric_path.parents
        )
    ):
        raise QualificationError("workload metric stream path is unsafe")
    paired_peer = spec.get("paired_peer_workload")
    if mode == "qualification_solo":
        if paired_peer is not None:
            raise QualificationError("solo command unexpectedly binds a peer")
    else:
        if not isinstance(paired_peer, dict) or set(paired_peer) != {
            "schema_version",
            "scenario",
            "qualification_attempt_id",
            "workload_metric_stream_path",
        }:
            raise QualificationError("paired peer workload contract is absent")
        peer_path = Path(str(paired_peer.get("workload_metric_stream_path") or ""))
        if (
            paired_peer.get("schema_version")
            != "mysql_algorithm_c_paired_peer_workload_binding_v1"
            or not isinstance(paired_peer.get("scenario"), str)
            or not paired_peer.get("scenario")
            or not isinstance(
                paired_peer.get("qualification_attempt_id"), str
            )
            or len(paired_peer.get("qualification_attempt_id")) < 32
            or paired_peer.get("qualification_attempt_id") == attempt_id
            or not peer_path.is_absolute()
            or not peer_path.parent.is_dir()
            or peer_path.is_symlink()
            or (peer_path.exists() and not peer_path.is_file())
        ):
            raise QualificationError("paired peer workload binding differs")
    executable = Path(argv[0])
    executable_host = _mapped_host_path(executable, mounts)
    if (
        not executable.is_absolute()
        or executable_host.is_symlink()
        or not executable_host.is_file()
        or sha256(executable_host) != spec.get("executable_sha256")
    ):
        raise QualificationError("qualification executable identity differs")
    code_files = spec.get("code_files")
    if not isinstance(code_files, dict) or not code_files:
        raise QualificationError("qualification code-file closure is absent")
    expected_code_files = {}
    for raw_path, expected_sha in code_files.items():
        code_path = Path(str(raw_path))
        if (
            not code_path.is_absolute()
            or code_path.is_symlink()
            or not code_path.is_file()
            or not isinstance(expected_sha, str)
            or sha256(code_path) != expected_sha
        ):
            raise QualificationError(
                "qualification code-file identity differs: %s" % raw_path
            )
        expected_code_files[str(code_path)] = expected_sha
    if str(executable_host) not in expected_code_files:
        raise QualificationError(
            "qualification executable missing from code-file closure"
        )
    for raw_path in expected_code_files:
        code_path = Path(raw_path)
        if not any(
            code_path == Path(row["source"])
            or Path(row["source"]) in code_path.parents
            for row in mounts
        ):
            raise QualificationError(
                "qualification code file is outside read-only mount closure"
            )
    classpath_argument_indexes = {
        index + 1
        for index, value in enumerate(argv[:-1])
        if value in ("-cp", "-classpath", "--class-path")
    }
    output_argument_indexes = set()
    if native_adapter["kind"] == "sql_query_native":
        switches = [
            index for index, value in enumerate(argv)
            if value == "--metric-stream"
        ]
        if (
            len(switches) != 1
            or switches[0] + 1 >= len(argv)
            or any(
                value.startswith("--metric-stream=")
                for value in argv
            )
        ):
            raise QualificationError(
                "native SQL metric output argument differs"
            )
        output_index = switches[0] + 1
        container_metric_path = Path(argv[output_index])
        if (
            not container_metric_path.is_absolute()
            or container_metric_path == container_tmpfs_destination
            or container_tmpfs_destination
            not in container_metric_path.parents
        ):
            raise QualificationError(
                "native SQL metric output is outside client tmpfs"
            )
        output_argument_indexes.add(output_index)
    elif "--metric-stream" in argv or any(
        value.startswith("--metric-stream=") for value in argv
    ):
        raise QualificationError(
            "non-SQL native workload cannot bind SQL metric output"
        )
    for index, value in enumerate(argv[1:], start=1):
        if (
            index in classpath_argument_indexes
            or index in output_argument_indexes
        ):
            continue
        candidate = Path(value)
        if (
            candidate.is_absolute()
            and index not in classpath_argument_indexes
        ):
            mapped = _mapped_host_path(candidate, mounts)
            if mapped.is_file():
                covered = str(mapped) in expected_code_files
            elif mapped.is_dir() and not mapped.is_symlink():
                descendants = []
                for descendant in mapped.rglob("*"):
                    if descendant.is_symlink():
                        raise QualificationError(
                            "qualification argv directory contains symlink"
                        )
                    if descendant.is_file():
                        descendants.append(str(descendant))
                    elif not descendant.is_dir():
                        raise QualificationError(
                            "qualification argv directory has special entry"
                        )
                covered = (
                    bool(descendants)
                    and all(
                        descendant in expected_code_files
                        for descendant in descendants
                    )
                )
            else:
                covered = False
            if not covered:
                raise QualificationError(
                    "qualification argv file is outside hash/mount closure"
                )
    classpath_pieces = []
    for index, value in enumerate(argv[:-1]):
        if value not in ("-cp", "-classpath", "--class-path"):
            continue
        classpath_pieces.extend(argv[index + 1].split(os.pathsep))
        for classpath_piece in argv[index + 1].split(os.pathsep):
            candidate = _mapped_host_path(
                Path(classpath_piece), mounts
            )
            if (
                candidate.is_file()
                and str(candidate) not in expected_code_files
            ):
                raise QualificationError(
                    "qualification classpath file is outside hash closure"
                )
    if native_adapter["kind"] in ("hybench_xp", "benchbase_tpcc"):
        classpath_switches = [
            index for index, value in enumerate(argv)
            if value in ("-cp", "-classpath", "--class-path")
        ]
        expected_main_class = (
            "com.hybench.HyBenchFormalMain"
            if native_adapter["kind"] == "hybench_xp"
            else "com.oltpbenchmark.DBWorkload"
        )
        if (
            "-jar" in argv
            or len(classpath_switches) != 1
            or classpath_switches[0] != 1
            or len(argv) <= 3
            or argv[3] != expected_main_class
        ):
            raise QualificationError(
                "native Java classpath/main launch shape differs"
            )
        suffixes = (
            ("com/hybench/HyBenchFormalMain.class",)
            if native_adapter["kind"] == "hybench_xp"
            else (
                "com/oltpbenchmark/FormalControl.class",
                "com/oltpbenchmark/ThreadBench.class",
                "com/oltpbenchmark/api/Worker.class",
                "com/oltpbenchmark/api/Worker$1.class",
            )
        )
        bound = {}
        for suffix in suffixes:
            matches = [
                raw for raw in expected_code_files
                if raw.replace(os.sep, "/").endswith(suffix)
            ]
            if len(matches) != 1:
                raise QualificationError(
                    "native latency producer class closure differs"
                )
            bound[suffix] = Path(matches[0])
        first_classpath = (
            _mapped_host_path(Path(classpath_pieces[0]), mounts)
            if classpath_pieces else None
        )
        if (
            first_classpath is None
            or not first_classpath.is_absolute()
            or any(
                not str(path).startswith(str(first_classpath) + os.sep)
                for path in bound.values()
            )
        ):
            raise QualificationError(
                "native latency producer overlay is not first on classpath"
            )
    for index, value in enumerate(argv):
        if index == 0 or index in classpath_argument_indexes:
            continue
        if Path(value).is_absolute():
            continue
        if FORBIDDEN_COMMAND_PATTERN.search(value):
            raise QualificationError(
                "capture/R0/formal token in qualification argv"
            )
    environment = spec.get("environment") or {}
    if not isinstance(environment, dict):
        raise QualificationError("qualification environment must be an object")
    for key, value in environment.items():
        if (
            not isinstance(key, str)
            or not isinstance(value, str)
            or FORBIDDEN_ENV_AUTHORITY_PATTERN.search(key)
            or FORBIDDEN_ENV_AUTHORITY_PATTERN.search(value)
        ):
            raise QualificationError("unsafe qualification environment")
    if native_adapter["kind"] == "benchbase_tpcc":
        required_benchbase = {
            "BENCHBASE_FORMAL_STOP_FILE":
                str(
                    Path(
                        measurement_output[
                            "container_tmpfs_destination"
                        ]
                    )
                    / (attempt_id + ".benchbase-stop-request")
                ),
            "BENCHBASE_FORMAL_WARMUP_SECONDS": "60",
            "BENCHBASE_FORMAL_MAXIMUM_MEASUREMENT_SECONDS": "600",
        }
        observed_benchbase = {
            key: value
            for key, value in environment.items()
            if key.startswith("BENCHBASE_FORMAL_")
        }
        if observed_benchbase != required_benchbase:
            raise QualificationError(
                "BenchBase formal timing environment differs"
            )
    cwd = Path(str(spec.get("cwd") or ""))
    tmpfs_destination = Path(
        measurement_output["container_tmpfs_destination"]
    )
    if (
        not cwd.is_absolute()
        or not (
            cwd == tmpfs_destination
            or tmpfs_destination in cwd.parents
        )
    ):
        raise QualificationError(
            "qualification cwd is outside container tmpfs"
        )
    return spec


def validate_main_agent_authority(
    path, token, bundle_id, mode, command_spec
):
    if not token or len(token) < 32:
        raise QualificationError("explicit main-agent token is absent or short")
    if not valid_sidecar(path):
        raise QualificationError("main-agent authority sidecar differs")
    authority = load_object(path, "main-agent qualification authority")
    now = time.time()
    if (
        authority.get("schema_version")
        != "mysql_algorithm_c_main_agent_qualification_authority_v1"
        or authority.get("authorized_by") != "main_agent"
        or authority.get("bundle_id") != bundle_id
        or authority.get("mode") != mode
        or authority.get("qualification_attempt_id")
        != load_object(command_spec, "qualification command").get(
            "qualification_attempt_id"
        )
        or authority.get("main_agent_token_sha256")
        != sha_bytes(token.encode("utf-8"))
        or authority.get("command_spec_sha256") != sha256(command_spec)
        or authority.get("debug_only") is not True
        or authority.get("capture_authorized") is not False
        or authority.get("formal_r0_authorized") is not False
        or authority.get("formal_authorized") is not False
        or authority.get("accepted_round_authorized") is not False
        or float(authority.get("not_before_epoch") or 0) > now
        or float(authority.get("expires_epoch") or 0) <= now
        or float(authority.get("expires_epoch") or 0) - now > 3600
    ):
        raise QualificationError("main-agent qualification authority differs")
    return authority


def validate_peer_readback(path, own_readback):
    if not valid_sidecar(path):
        raise QualificationError("peer readback sidecar differs")
    peer = load_object(path, "peer live readback")
    if (
        peer.get("schema_version")
        != "mysql_algorithm_c_distributed_live_readback_v1"
        or peer.get("status") != "PASS_LIVE_READBACK_NOT_LAUNCH_AUTHORITY"
        or peer.get("hostname") != own_readback["hostname"]
        or peer.get("capture_authorized") is not False
        or peer.get("formal_r0_authorized") is not False
        or peer.get("accepted_round_authorized") is not False
    ):
        raise QualificationError("peer live readback authority differs")
    own_cpus = expand_cpuset(
        own_readback["resource_envelope"]["cpuset_cpus"]
    )
    peer_cpus = expand_cpuset(peer["resource_envelope"]["cpuset_cpus"])
    if not own_cpus.isdisjoint(peer_cpus):
        raise QualificationError("paired qualification CPU sets overlap")
    if (
        own_readback["storage"]["major_minor"]
        == peer["storage"]["major_minor"]
        or own_readback["mysql_port"] == peer["mysql_port"]
    ):
        raise QualificationError("paired qualification device or port overlaps")
    return peer


def process_sample(pid):
    stat_fields = (
        (Path("/proc") / str(pid) / "stat")
        .read_text(encoding="ascii", errors="replace")
        .split()
    )
    io_values = {}
    for line in (
        (Path("/proc") / str(pid) / "io")
        .read_text(encoding="ascii", errors="replace")
        .splitlines()
    ):
        if ":" in line:
            key, value = line.split(":", 1)
            io_values[key] = int(value.strip())
    return {
        "pid": pid,
        "cpu_ticks": int(stat_fields[13]) + int(stat_fields[14]),
        "read_bytes": io_values.get("read_bytes", 0),
        "write_bytes": io_values.get("write_bytes", 0),
    }


def _integer(value, label, minimum=0):
    if isinstance(value, bool):
        raise QualificationError("%s is not an integer" % label)
    try:
        converted = int(value)
    except (TypeError, ValueError):
        raise QualificationError("%s is not an integer" % label)
    if converted < minimum:
        raise QualificationError("%s is below its minimum" % label)
    return converted


def _finite_number(value, label, minimum=0.0):
    if isinstance(value, bool):
        raise QualificationError("%s is not numeric" % label)
    try:
        converted = float(value)
    except (TypeError, ValueError):
        raise QualificationError("%s is not numeric" % label)
    if converted != converted or converted in (float("inf"), float("-inf")):
        raise QualificationError("%s is non-finite" % label)
    if converted < minimum:
        raise QualificationError("%s is below its minimum" % label)
    return converted


def workload_metric_sample(
    path,
    attempt_id,
    previous_sequence,
    timeout_seconds=WORKLOAD_SAMPLE_TIMEOUT_SECONDS,
    allow_fresh_repeat=False,
):
    deadline = time.monotonic() + float(timeout_seconds)
    last_error = None
    while True:
        try:
            if path.is_symlink() or not path.is_file():
                raise QualificationError("workload metric stream is absent")
            rows = path.read_text(
                encoding="utf-8", errors="strict"
            ).splitlines()
            if not rows:
                raise QualificationError("workload metric stream is empty")
            values = [json.loads(row) for row in rows]
            if previous_sequence is None:
                value = values[-1]
            else:
                expected_sequence = previous_sequence + 1
                forward = []
                for row in values:
                    if not isinstance(row, dict):
                        raise QualificationError(
                            "workload metric row is not an object"
                        )
                    raw_sequence = row.get("sequence")
                    if (isinstance(raw_sequence, bool)
                            or not isinstance(raw_sequence, int)
                            or raw_sequence < 0):
                        raise QualificationError(
                            "workload metric sequence closure is malformed"
                        )
                    if raw_sequence > previous_sequence:
                        if (row.get("schema_version")
                                != "mysql_algorithm_c_debug_workload_metric_sample_v1"
                                or row.get("qualification_attempt_id")
                                != attempt_id):
                            raise QualificationError(
                                "workload metric suffix authority differs"
                            )
                        forward.append(row)
                if not forward:
                    repeats = [
                        row for row in values
                        if isinstance(row, dict)
                        and row.get("sequence") == previous_sequence
                    ]
                    if not allow_fresh_repeat or len(repeats) != 1:
                        raise QualificationError(
                            "workload metric contiguous suffix is absent"
                        )
                    value = repeats[0]
                    forward = None
                if forward is not None:
                    newest_sequence = max(row["sequence"] for row in forward)
                    counts = {}
                    for row in forward:
                        sequence_key = row["sequence"]
                        counts[sequence_key] = counts.get(sequence_key, 0) + 1
                    expected = list(range(expected_sequence, newest_sequence + 1))
                    if (sorted(counts) != expected
                            or any(counts[key] != 1 for key in expected)):
                        raise QualificationError(
                            "workload metric suffix is not unique and contiguous"
                        )
                    value = forward[-1]
            if not isinstance(value, dict):
                raise QualificationError("workload metric row is not an object")
            if (
                value.get("schema_version")
                != "mysql_algorithm_c_debug_workload_metric_sample_v1"
                or value.get("qualification_attempt_id") != attempt_id
            ):
                raise QualificationError(
                    "workload metric sample authority differs"
                )
            sequence = _integer(value.get("sequence"), "metric sequence")
            if (previous_sequence is not None
                    and (sequence < previous_sequence
                         or (sequence == previous_sequence
                             and not allow_fresh_repeat))):
                raise QualificationError("workload metric sample is stale")
            epoch = _finite_number(
                value.get("epoch_seconds"), "metric epoch"
            )
            if abs(time.time() - epoch) > MAXIMUM_SAMPLE_GAP_SECONDS:
                raise QualificationError("workload metric sample age exceeded")
            completed = _integer(
                value.get("completed_total"), "completed total"
            )
            latency = {}
            percentile_keys = (
                "latency_p50_ms",
                "latency_p95_ms",
                "latency_p99_ms",
            )
            present = [key in value for key in percentile_keys]
            if all(present):
                for key in percentile_keys:
                    latency[key] = _finite_number(value[key], key)
            elif not any(present) and "query_duration_ms" in value:
                latency["query_duration_ms"] = _finite_number(
                    value["query_duration_ms"], "query duration"
                )
            else:
                raise QualificationError(
                    "p50/p95/p99 or query duration coverage is incomplete"
                )
            native = {}
            native_present = (
                "native_adapter_kind" in value,
                "native_marker_epoch_ms" in value,
            )
            if all(native_present):
                native_kind = value["native_adapter_kind"]
                if native_kind not in (
                    "hybench_xp",
                    "benchbase_tpcc",
                    "sql_query_native",
                ):
                    raise QualificationError(
                        "native workload adapter kind differs"
                    )
                # Current native SQL uses elapsed duration before the first
                # completion and a complete percentile tuple afterwards.
                if native_kind == "sql_query_native" and set(latency) not in (
                    {"query_duration_ms"},
                    set(percentile_keys),
                ):
                    raise QualificationError(
                        "native SQL latency shape differs"
                    )
                if (
                    native_kind in ("hybench_xp", "benchbase_tpcc")
                    and set(latency) == {"query_duration_ms"}
                ):
                    raise QualificationError(
                        "native transactional workload requires p50/p95/p99"
                    )
                native_epoch_ms = _integer(
                    value["native_marker_epoch_ms"],
                    "native workload marker epoch",
                )
                if abs(native_epoch_ms / 1000.0 - epoch) > 0.001:
                    raise QualificationError(
                        "native workload marker/sample epoch differs"
                    )
                native = {
                    "native_adapter_kind": native_kind,
                    "native_marker_epoch_ms": native_epoch_ms,
                }
            elif any(native_present):
                raise QualificationError(
                    "native workload marker provenance is incomplete"
                )
            return {
                "schema_version":
                    "mysql_algorithm_c_debug_workload_metric_sample_v1",
                "qualification_attempt_id": attempt_id,
                "sequence": sequence,
                "epoch_seconds": epoch,
                "completed_total": completed,
                "latency": latency,
                **latency,
                **native,
            }
        except (OSError, ValueError, UnicodeError, QualificationError) as exc:
            last_error = exc
        if time.monotonic() >= deadline:
            raise QualificationError(
                "workload metric sample missing/stale/timed out: %s"
                % last_error
            )
        time.sleep(0.02)


def _read_required(path, label):
    try:
        if path.is_symlink() or not path.is_file():
            raise OSError("absent or symlinked")
        return path.read_text(encoding="ascii", errors="strict")
    except (OSError, UnicodeError) as exc:
        raise QualificationError("%s read failed: %s" % (label, exc))


def _key_value_rows(text, label):
    values = {}
    for line in text.splitlines():
        pieces = line.split()
        if len(pieces) != 2:
            raise QualificationError("%s row malformed" % label)
        values[pieces[0]] = _integer(pieces[1], "%s %s" % (label, pieces[0]))
    if not values:
        raise QualificationError("%s is empty" % label)
    return values


def _pressure_sample(path, label):
    rows = {}
    for line in _read_required(path, label).splitlines():
        pieces = line.split()
        if not pieces:
            continue
        fields = {}
        for piece in pieces[1:]:
            if "=" not in piece:
                raise QualificationError("%s row malformed" % label)
            key, raw = piece.split("=", 1)
            fields[key] = raw
        if "total" not in fields:
            raise QualificationError("%s total is absent" % label)
        rows[pieces[0]] = _integer(
            fields["total"], "%s %s total" % (label, pieces[0])
        )
    if "some" not in rows:
        raise QualificationError("%s some pressure is absent" % label)
    return {
        "source": str(path),
        "some_total_usec": rows["some"],
        "full_total_usec": rows.get("full"),
    }


def _psi_file_or_none(name):
    """Return a regular PSI file, or None only for a wholly absent PSI tree.

    A partial, unreadable, or symlinked PSI installation is evidence damage,
    not permission to silently select the fallback.  This makes PSI preferred
    whenever the kernel exposes it and reserves the fallback for old kernels
    with no /proc/pressure directory at all.
    """
    directory = PROC_ROOT / "pressure"
    path = directory / name
    try:
        directory_mode = os.lstat(str(directory)).st_mode
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise QualificationError("host PSI directory cannot be read") from exc
    if stat.S_ISLNK(directory_mode):
        raise QualificationError("host PSI path is symlinked")
    if not stat.S_ISDIR(directory_mode):
        raise QualificationError("host PSI directory is not a directory")
    try:
        file_mode = os.lstat(str(path)).st_mode
    except FileNotFoundError:
        raise QualificationError("host PSI directory is partial")
    except OSError as exc:
        raise QualificationError("host PSI file cannot be read") from exc
    if stat.S_ISLNK(file_mode):
        raise QualificationError("host PSI path is symlinked")
    if not stat.S_ISREG(file_mode):
        raise QualificationError("host PSI path is not a regular file")
    return path


def _proc_cpu_fallback_sample():
    """Versioned no-PSI CPU contention evidence from kernel accounting.

    Counters are deliberately retained raw so the rolling summary can reject
    any regression, incomplete row, or non-positive total-clock delta.
    """
    lines = _read_required(PROC_ROOT / "stat", "host proc stat").splitlines()
    row = next((line for line in lines if line.startswith("cpu ")), None)
    if row is None:
        raise QualificationError("host proc stat aggregate CPU row is absent")
    fields = row.split()[1:]
    if len(fields) < 8:
        raise QualificationError("host proc stat aggregate CPU coverage incomplete")
    counters = [_integer(value, "host proc stat CPU counter") for value in fields[:8]]
    user, nice, system, idle, iowait, irq, softirq, steal = counters
    total = sum(counters)
    busy = user + nice + system + irq + softirq + steal
    if total <= 0 or busy < 0 or iowait < 0 or steal < 0:
        raise QualificationError("host proc stat CPU counters differ")
    load = _read_required(PROC_ROOT / "loadavg", "host proc loadavg").split()
    if len(load) < 4 or "/" not in load[3]:
        raise QualificationError("host proc loadavg coverage incomplete")
    try:
        load_1, load_5, load_15 = [float(value) for value in load[:3]]
    except ValueError as exc:
        raise QualificationError("host proc loadavg is not numeric") from exc
    runnable, total_tasks = load[3].split("/", 1)
    runnable = _integer(runnable, "host runnable tasks")
    total_tasks = _integer(total_tasks, "host total tasks")
    if (load_1 < 0 or load_5 < 0 or load_15 < 0 or runnable < 0
            or total_tasks <= 0 or runnable > total_tasks):
        raise QualificationError("host proc loadavg values differ")
    return {
        "policy_version": CPU_CONTENTION_POLICY_VERSION,
        "mode": "proc_stat_loadavg_fallback_v1",
        "psi_preferred_when_available": True,
        "proc_stat": {
            "total_ticks": total,
            "busy_ticks": busy,
            "iowait_ticks": iowait,
            "steal_ticks": steal,
        },
        "loadavg": {
            "load_1": load_1,
            "load_5": load_5,
            "load_15": load_15,
            "runnable_tasks": runnable,
            "total_tasks": total_tasks,
        },
    }


def _cpu_contention_sample():
    psi = _psi_file_or_none("cpu")
    if psi is None:
        return _proc_cpu_fallback_sample()
    sample = _pressure_sample(psi, "host CPU pressure")
    sample.update({
        "policy_version": CPU_CONTENTION_POLICY_VERSION,
        "mode": "psi_cpu_v1",
        "psi_preferred_when_available": True,
    })
    return sample


def _memory_pressure_sample(memory_current, memory_limit, swap_current, memswap_limit):
    """Keep no-PSI kernels observable through cgroup occupancy and swap/OOM.

    The fallback does not pretend to manufacture PSI time; its separate mode
    causes the summary to derive an explicit occupancy/swap proxy.
    """
    psi = _psi_file_or_none("memory")
    if psi is not None:
        sample = _pressure_sample(psi, "host memory pressure")
        sample.update({"mode": "psi_memory_v1", "policy_version": CPU_CONTENTION_POLICY_VERSION})
        return sample
    if memory_limit <= 0 or memswap_limit < memory_limit:
        raise QualificationError("memory fallback envelope differs")
    return {
        "policy_version": CPU_CONTENTION_POLICY_VERSION,
        "mode": "cgroup_memory_occupancy_fallback_v1",
        "memory_current_bytes": memory_current,
        "swap_current_bytes": swap_current,
        "memory_limit_bytes": memory_limit,
        "memory_swap_limit_bytes": memswap_limit,
    }


def _cgroup_roots(pid):
    lines = _read_required(
        Path("/proc") / str(pid) / "cgroup", "process cgroup inventory"
    ).splitlines()
    unified = None
    controller_paths = {}
    for line in lines:
        pieces = line.split(":", 2)
        if len(pieces) != 3:
            raise QualificationError("process cgroup row malformed")
        controllers = pieces[1]
        relative = pieces[2]
        if not relative.startswith("/"):
            raise QualificationError("process cgroup path is not absolute")
        if not controllers:
            unified = relative
        else:
            for controller in controllers.split(","):
                controller_paths[controller] = relative
    if unified is not None:
        root = Path("/sys/fs/cgroup") / unified.lstrip("/")
        if not root.is_dir() or root.is_symlink():
            raise QualificationError("cgroup v2 root is absent")
        return {"version": 2, "cpu": root, "memory": root, "io": root}

    def locate(controller, mount_names):
        if controller not in controller_paths:
            raise QualificationError("cgroup %s controller is absent" % controller)
        relative = controller_paths[controller].lstrip("/")
        for mount_name in mount_names:
            candidate = Path("/sys/fs/cgroup") / mount_name / relative
            if candidate.is_dir() and not candidate.is_symlink():
                return candidate
        raise QualificationError("cgroup %s path is absent" % controller)

    return {
        "version": 1,
        "cpu": locate("cpu", ("cpu,cpuacct", "cpuacct,cpu", "cpu")),
        "memory": locate("memory", ("memory",)),
        "io": locate("blkio", ("blkio",)),
    }


def _v1_device_values(path, label):
    devices = {}
    for line in _read_required(path, label).splitlines():
        pieces = line.split()
        if not pieces or pieces[0] == "Total":
            continue
        if ":" not in pieces[0] or len(pieces) not in (2, 3):
            raise QualificationError("%s row malformed" % label)
        device = pieces[0]
        operation = "total" if len(pieces) == 2 else pieces[1].lower()
        if operation == "total":
            continue
        value = _integer(pieces[-1], "%s value" % label)
        row = devices.setdefault(device, {})
        row[operation] = row.get(operation, 0) + value
    return devices


def _v2_io_values(path):
    devices = {}
    for line in _read_required(path, "cgroup io.stat").splitlines():
        pieces = line.split()
        if not pieces or ":" not in pieces[0]:
            raise QualificationError("cgroup io.stat row malformed")
        row = {}
        for piece in pieces[1:]:
            if "=" not in piece:
                raise QualificationError("cgroup io.stat field malformed")
            key, raw = piece.split("=", 1)
            row[key] = _integer(raw, "cgroup io.stat %s" % key)
        required = {"rbytes", "wbytes", "rios", "wios"}
        if not required.issubset(set(row)):
            raise QualificationError("cgroup io.stat coverage incomplete")
        devices[pieces[0]] = {
            "read_bytes": row["rbytes"],
            "write_bytes": row["wbytes"],
            "read_ios": row["rios"],
            "write_ios": row["wios"],
        }
    return devices


def _v1_io_values(root):
    byte_rows = _v1_device_values(
        root / "blkio.throttle.io_service_bytes",
        "cgroup blkio service bytes",
    )
    op_rows = _v1_device_values(
        root / "blkio.throttle.io_serviced",
        "cgroup blkio serviced",
    )
    devices = {}
    for device in sorted(set(byte_rows) | set(op_rows)):
        byte_row = byte_rows.get(device, {})
        op_row = op_rows.get(device, {})
        devices[device] = {
            "read_bytes": byte_row.get("read", 0),
            "write_bytes": byte_row.get("write", 0),
            "read_ios": op_row.get("read", 0),
            "write_ios": op_row.get("write", 0),
        }
    return devices


def _block_device_sample(major_minor):
    fields = _read_required(
        Path("/sys/dev/block") / major_minor / "stat",
        "declared block-device stat",
    ).split()
    if len(fields) < 11:
        raise QualificationError("declared block-device stat coverage incomplete")
    values = [_integer(value, "block-device stat") for value in fields]
    return {
        "read_ios": values[0],
        "read_bytes": values[2] * 512,
        "read_ticks_ms": values[3],
        "write_ios": values[4],
        "write_bytes": values[6] * 512,
        "write_ticks_ms": values[7],
        "in_flight": values[8],
        "io_ticks_ms": values[9],
        "weighted_io_ticks_ms": values[10],
    }


def _v1_memory_oom_sample(memory_root):
    """Read the actual cgroup-v1 OOM shape without inventing ``oom_kill``.

    cgroup v1's ``memory.oom_control`` exposes ``under_oom`` and
    ``oom_kill_disable``; unlike v2 ``memory.events`` it has no cumulative
    ``oom_kill`` row.  ``memory.failcnt`` is the only monotonic local signal
    available during a still-running qualification.  Treat any increase as a
    fail-closed OOM-or-limit-failure proxy, while retaining its exact source in
    the evidence instead of misparsing a missing key as an integer.
    """
    oom_control = _key_value_rows(
        _read_required(
            memory_root / "memory.oom_control",
            "cgroup memory oom control",
        ),
        "cgroup memory oom control",
    )
    under_oom = _integer(
        oom_control.get("under_oom"), "memory under_oom"
    )
    oom_kill_disable = _integer(
        oom_control.get("oom_kill_disable"), "memory oom_kill_disable"
    )
    if under_oom != 0:
        raise QualificationError("cgroup memory is under OOM")
    return {
        "events": _integer(
            _read_required(
                memory_root / "memory.failcnt",
                "cgroup memory failcnt",
            ).strip(),
            "memory failcnt",
        ),
        "source": "cgroup_v1_memory.failcnt_fail_closed_proxy",
        "under_oom": under_oom,
        "oom_kill_disable": oom_kill_disable,
    }


def resource_stream_sample(
    pid,
    workload_pid,
    control_pid,
    major_minor,
    envelope,
    workload_isolation,
):
    roots = _cgroup_roots(pid)
    workload_roots = _cgroup_roots(workload_pid)
    if workload_roots["version"] == 2:
        workload_devices = _v2_io_values(
            workload_roots["io"] / "io.stat"
        )
    else:
        workload_devices = _v1_io_values(workload_roots["io"])
    if roots["version"] == 2:
        cpu = _key_value_rows(
            _read_required(roots["cpu"] / "cpu.stat", "cgroup cpu.stat"),
            "cgroup cpu.stat",
        )
        memory_current = _integer(
            _read_required(
                roots["memory"] / "memory.current", "cgroup memory.current"
            ).strip(),
            "memory current",
        )
        swap_current = _integer(
            _read_required(
                roots["memory"] / "memory.swap.current",
                "cgroup memory.swap.current",
            ).strip(),
            "swap current",
        )
        memory_events = _key_value_rows(
            _read_required(
                roots["memory"] / "memory.events", "cgroup memory.events"
            ),
            "cgroup memory.events",
        )
        devices = _v2_io_values(roots["io"] / "io.stat")
        usage_usec = _integer(cpu.get("usage_usec"), "cpu usage_usec")
        throttled_usec = _integer(
            cpu.get("throttled_usec"), "cpu throttled_usec"
        )
        nr_throttled = _integer(
            cpu.get("nr_throttled"), "cpu nr_throttled"
        )
        oom_kill = _integer(
            memory_events.get("oom_kill"), "memory oom_kill"
        )
    else:
        usage_usec = _integer(
            _read_required(
                roots["cpu"] / "cpuacct.usage", "cgroup cpuacct usage"
            ).strip(),
            "cpuacct usage",
        ) // 1000
        cpu = _key_value_rows(
            _read_required(roots["cpu"] / "cpu.stat", "cgroup cpu.stat"),
            "cgroup cpu.stat",
        )
        throttled_usec = _integer(
            cpu.get("throttled_time"), "cpu throttled_time"
        ) // 1000
        nr_throttled = _integer(
            cpu.get("nr_throttled"), "cpu nr_throttled"
        )
        memory_current = _integer(
            _read_required(
                roots["memory"] / "memory.usage_in_bytes",
                "cgroup memory usage",
            ).strip(),
            "memory usage",
        )
        # memory.usage_in_bytes and memory.memsw.usage_in_bytes are
        # independently changing counters.  Subtracting two non-atomic reads
        # creates false positive swap usage under allocation churn even when
        # the cgroup has a zero-swap envelope.  cgroup v1 memory.stat exposes
        # the hierarchical swap charge directly and is the fail-closed source
        # for the target container and all of its descendants.
        memory_stat = _key_value_rows(
            _read_required(
                roots["memory"] / "memory.stat",
                "cgroup v1 memory.stat",
            ),
            "cgroup v1 memory.stat",
        )
        swap_current = _integer(
            memory_stat.get("total_swap"),
            "cgroup v1 hierarchical swap",
        )
        oom_v1 = _v1_memory_oom_sample(roots["memory"])
        oom_kill = oom_v1["events"]
        devices = _v1_io_values(roots["io"])

    status = process_status(pid)
    expected_cpus = expand_cpuset(envelope["cpuset_cpus"])
    expected_mems = expand_cpuset(envelope["cpuset_mems"])
    actual_cpus = expand_cpuset(status.get("Cpus_allowed_list", ""))
    actual_mems = expand_cpuset(status.get("Mems_allowed_list", ""))
    docker_containers, root_docker_mount_evidence = _docker_container_inventory()
    return {
        "cpu_usage_and_throttle_ratio": {
            "usage_usec": usage_usec,
            "throttled_usec": throttled_usec,
            "nr_throttled": nr_throttled,
            "cpu_quota_cores": _finite_number(
                envelope["cpu_quota_cores"], "CPU quota", 0.000001
            ),
        },
        "cpu_pressure": _cpu_contention_sample(),
        "memory_current_swap_and_pressure": {
            "memory_current_bytes": memory_current,
            "swap_current_bytes": swap_current,
            "memory_limit_bytes": _integer(
                envelope["memory_bytes"], "memory limit", 1
            ),
            "memory_swap_limit_bytes": _integer(
                envelope["memory_swap_bytes"], "memory+swap limit", 1
            ),
            "oom_kill_events": oom_kill,
            "oom_kill_event_source": (
                "cgroup_v2_memory.events.oom_kill"
                if roots["version"] == 2 else oom_v1["source"]
            ),
            "pressure": _memory_pressure_sample(
                memory_current,
                _integer(envelope["memory_bytes"], "memory limit", 1),
                swap_current,
                _integer(envelope["memory_swap_bytes"], "memory+swap limit", 1),
            ),
        },
        "per_declared_device_bytes_iops_latency_queue_and_utilization": {
            "declared_major_minor": major_minor,
            "cgroup_devices": devices,
            "block_device": _block_device_sample(major_minor),
        },
        "process_cpu_affinity_and_device_inventory": {
            "pid": pid,
            "cpus_allowed_list": status.get("Cpus_allowed_list"),
            "mems_allowed_list": status.get("Mems_allowed_list"),
            "expected_cpuset_cpus": envelope["cpuset_cpus"],
            "expected_cpuset_mems": envelope["cpuset_mems"],
            "affinity_exact": (
                actual_cpus == expected_cpus and actual_mems == expected_mems
            ),
            "declared_major_minor": major_minor,
            "observed_cgroup_devices": sorted(devices),
            "cgroup_version": roots["version"],
            "rolling_full_host_inventory": rolling_process_inventory(
                pid,
                workload_pid,
                control_pid,
                major_minor,
                envelope["cpuset_cpus"],
                workload_isolation["cpuset_cpus"],
                workload_isolation["cpuset_mems"],
                docker_containers=docker_containers,
                root_docker_mount_evidence=root_docker_mount_evidence,
            ),
            "client_cgroup_devices": workload_devices,
        },
    }


def collect_dynamic_sample(
    index,
    sampled_monotonic,
    started_monotonic,
    own,
    target_pid,
    workload_pid,
    control_pid,
    workload_isolation,
    mysql_binary,
    defaults_file,
    metric_path,
    attempt_id,
    previous_sequence,
):
    workload = workload_metric_sample(
        metric_path, attempt_id, previous_sequence
    )
    resources = resource_stream_sample(
        target_pid,
        workload_pid,
        control_pid,
        own["storage"]["major_minor"],
        own["resource_envelope"],
        workload_isolation,
    )
    streams = {
        "workload_throughput_or_completion_rate": {
            "sequence": workload["sequence"],
            "epoch_seconds": workload["epoch_seconds"],
            "completed_total": workload["completed_total"],
        },
        "p50_p95_p99_or_query_duration": workload["latency"],
        "database_counters": db_counter_sample(
            mysql_binary,
            defaults_file,
            own["mysql_port"],
            own.get("database_counter_transport"),
        ),
    }
    streams.update(resources)
    if set(streams) != set(REQUIRED_STREAMS):
        raise QualificationError("dynamic required-stream coverage differs")
    return {
        "index": index,
        "monotonic": sampled_monotonic,
        "elapsed_seconds": sampled_monotonic - started_monotonic,
        "required_streams": streams,
    }


def _delta(first, last, label):
    difference = _finite_number(last, "%s last" % label) - _finite_number(
        first, "%s first" % label
    )
    if difference < 0:
        raise QualificationError("%s counter regressed" % label)
    return difference


def _mean(values, label):
    if not values:
        raise QualificationError("%s samples are absent" % label)
    converted = [
        _finite_number(value, "%s sample" % label) for value in values
    ]
    return sum(converted) / float(len(converted))


def _cpu_contention_summary(samples, elapsed):
    rows = [sample["required_streams"]["cpu_pressure"] for sample in samples]
    if not all(isinstance(row, dict) for row in rows):
        raise QualificationError("CPU contention sample differs")
    modes = {row.get("mode") for row in rows}
    if len(modes) != 1 or any(
        row.get("policy_version") != CPU_CONTENTION_POLICY_VERSION
        or row.get("psi_preferred_when_available") is not True
        for row in rows
    ):
        raise QualificationError("CPU contention source changed/incomplete")
    mode = next(iter(modes))
    if mode == "psi_cpu_v1":
        for previous, current in zip(rows, rows[1:]):
            _delta(previous.get("some_total_usec"), current.get("some_total_usec"), "CPU PSI pressure")
        ratio = _delta(rows[0].get("some_total_usec"), rows[-1].get("some_total_usec"), "CPU PSI pressure") / (elapsed * 1000000.0)
        return {
            "policy_version": CPU_CONTENTION_POLICY_VERSION,
            "mode": mode,
            "cpu_pressure_ratio": ratio,
        }
    if mode != "proc_stat_loadavg_fallback_v1":
        raise QualificationError("CPU contention mode is unsupported")
    for previous, current in zip(rows, rows[1:]):
        for field in ("total_ticks", "busy_ticks", "iowait_ticks", "steal_ticks"):
            _delta(
                (previous.get("proc_stat") or {}).get(field),
                (current.get("proc_stat") or {}).get(field),
                "host proc stat %s" % field,
            )
    first = rows[0]["proc_stat"]
    last = rows[-1]["proc_stat"]
    total_delta = _delta(first.get("total_ticks"), last.get("total_ticks"), "host proc stat total")
    if total_delta <= 0:
        raise QualificationError("host proc stat total did not advance")
    runnable_ratios = []
    loads = []
    for row in rows:
        load = row.get("loadavg")
        if not isinstance(load, dict):
            raise QualificationError("host loadavg is absent")
        runnable = _integer(load.get("runnable_tasks"), "host runnable tasks")
        total_tasks = _integer(load.get("total_tasks"), "host total tasks", 1)
        if runnable > total_tasks:
            raise QualificationError("host runnable count exceeds total")
        runnable_ratios.append(runnable / float(total_tasks))
        loads.append(_finite_number(load.get("load_1"), "host load_1"))
    return {
        "policy_version": CPU_CONTENTION_POLICY_VERSION,
        "mode": mode,
        "cpu_pressure_ratio": _delta(first.get("busy_ticks"), last.get("busy_ticks"), "host proc stat busy") / float(total_delta),
        "cpu_steal_ratio": _delta(first.get("steal_ticks"), last.get("steal_ticks"), "host proc stat steal") / float(total_delta),
        "cpu_iowait_ratio": _delta(first.get("iowait_ticks"), last.get("iowait_ticks"), "host proc stat iowait") / float(total_delta),
        "runnable_ratio_mean": _mean(runnable_ratios, "host runnable ratio"),
        "load_1_mean": _mean(loads, "host load_1"),
    }


def _memory_pressure_summary(rows, memory_utilization, swap_utilization, elapsed):
    pressure_rows = [row.get("pressure") for row in rows]
    if not all(isinstance(row, dict) for row in pressure_rows):
        raise QualificationError("memory pressure sample differs")
    modes = {row.get("mode") for row in pressure_rows}
    if len(modes) != 1 or any(
        row.get("policy_version") != CPU_CONTENTION_POLICY_VERSION
        for row in pressure_rows
    ):
        raise QualificationError("memory pressure source changed/incomplete")
    mode = next(iter(modes))
    if mode == "psi_memory_v1":
        for previous, current in zip(pressure_rows, pressure_rows[1:]):
            _delta(previous.get("some_total_usec"), current.get("some_total_usec"), "memory PSI pressure")
        ratio = _delta(pressure_rows[0].get("some_total_usec"), pressure_rows[-1].get("some_total_usec"), "memory PSI pressure") / (elapsed * 1000000.0)
    elif mode == "cgroup_memory_occupancy_fallback_v1":
        for row in pressure_rows:
            if (_integer(row.get("memory_current_bytes"), "fallback memory current") < 0
                    or _integer(row.get("swap_current_bytes"), "fallback swap current") < 0):
                raise QualificationError("memory fallback counters differ")
        ratio = max(memory_utilization, swap_utilization)
    else:
        raise QualificationError("memory pressure mode is unsupported")
    return {"policy_version": CPU_CONTENTION_POLICY_VERSION, "mode": mode,
            "memory_pressure_ratio": ratio}


def summarize_dynamic_samples(samples, minimum_samples=MINIMUM_SAMPLES):
    if not isinstance(samples, list) or len(samples) < int(minimum_samples):
        raise QualificationError("qualification sample coverage incomplete")
    for expected_index, sample in enumerate(samples):
        if not isinstance(sample, dict) or sample.get("index") != expected_index:
            raise QualificationError("qualification sample sequence differs")
        streams = sample.get("required_streams")
        if not isinstance(streams, dict) or set(streams) != set(REQUIRED_STREAMS):
            raise QualificationError("qualification required stream is missing")
        inventory = streams[
            "process_cpu_affinity_and_device_inventory"
        ]
        if inventory.get("affinity_exact") is not True:
            raise QualificationError("process affinity changed during gate")
        rolling = inventory.get("rolling_full_host_inventory")
        if (
            not isinstance(rolling, dict)
            or rolling.get("inventory_complete") is not True
            or rolling.get("conflicts") != []
            or not isinstance(rolling.get("processes"), list)
            or rolling.get("permission_failure_count") != 0
            or rolling.get("race_count") != 0
            or rolling.get("foreign_writer_count") != 0
        ):
            diagnostic = {
                "sample_index": expected_index,
                "inventory_complete": (
                    rolling.get("inventory_complete")
                    if isinstance(rolling, dict) else None
                ),
                "conflicts": (
                    rolling.get("conflicts")
                    if isinstance(rolling, dict) else None
                ),
                "permission_failure_count": (
                    rolling.get("permission_failure_count")
                    if isinstance(rolling, dict) else None
                ),
                "race_count": (
                    rolling.get("race_count")
                    if isinstance(rolling, dict) else None
                ),
                "foreign_writer_count": (
                    rolling.get("foreign_writer_count")
                    if isinstance(rolling, dict) else None
                ),
                "relevant_processes": (
                    [
                        row for row in rolling.get("processes", [])
                        if row.get("role") in (
                            "qualification_workload", "co_resident"
                        )
                    ][:24]
                    if isinstance(rolling, dict) else []
                ),
            }
            raise QualificationError(
                "rolling co-resident process inventory conflicts/incomplete: %s"
                % json.dumps(diagnostic, sort_keys=True, separators=(",", ":"))
            )
        root_inventory = rolling.get("root_docker_mount_inventory")
        if (
            not isinstance(root_inventory, dict)
            or root_inventory.get("helper_relative")
            != ROOT_DOCKER_MOUNT_HELPER
            or not isinstance(root_inventory.get("helper_sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", root_inventory["helper_sha256"])
            is None
            or not isinstance(root_inventory.get("output_sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", root_inventory["output_sha256"])
            is None
            or root_inventory.get("effective_uid_gid") != [0, 0]
            or root_inventory.get("mutation_performed") is not False
            or not isinstance(root_inventory.get("container_count"), int)
            or root_inventory["container_count"]
            != rolling.get("docker_container_count")
        ):
            raise QualificationError("root Docker mount inventory evidence differs")
    first = samples[0]
    last = samples[-1]
    elapsed = _delta(
        first["monotonic"], last["monotonic"], "sample monotonic time"
    )
    if elapsed < QUALIFICATION_SECONDS:
        raise QualificationError("qualification duration coverage incomplete")
    for prior, current in zip(samples, samples[1:]):
        gap = current["monotonic"] - prior["monotonic"]
        if gap <= 0 or gap > MAXIMUM_SAMPLE_GAP_SECONDS:
            raise QualificationError("qualification sample gap exceeded")

    first_streams = first["required_streams"]
    last_streams = last["required_streams"]
    workload_first = first_streams[
        "workload_throughput_or_completion_rate"
    ]
    workload_last = last_streams[
        "workload_throughput_or_completion_rate"
    ]
    completion_delta = _delta(
        workload_first["completed_total"],
        workload_last["completed_total"],
        "workload completion",
    )
    if completion_delta <= 0:
        raise QualificationError("workload made no measured progress")
    latency_keys = set(
        first_streams["p50_p95_p99_or_query_duration"]
    )
    if latency_keys not in (
        {"latency_p50_ms", "latency_p95_ms", "latency_p99_ms"},
        {"query_duration_ms"},
    ):
        raise QualificationError("latency stream shape differs")
    latency = {}
    for key in sorted(latency_keys):
        values = []
        for sample in samples:
            row = sample["required_streams"][
                "p50_p95_p99_or_query_duration"
            ]
            if set(row) != latency_keys:
                raise QualificationError("latency stream changed shape")
            values.append(row[key])
        latency[key] = _mean(values, key)

    cpu_first = first_streams["cpu_usage_and_throttle_ratio"]
    cpu_last = last_streams["cpu_usage_and_throttle_ratio"]
    quota = _finite_number(
        cpu_first["cpu_quota_cores"], "CPU quota", 0.000001
    )
    if (
        _finite_number(
            cpu_last["cpu_quota_cores"], "last CPU quota", 0.000001
        )
        != quota
    ):
        raise QualificationError("CPU quota changed during gate")
    cpu_usage_ratio = _delta(
        cpu_first["usage_usec"], cpu_last["usage_usec"], "CPU usage"
    ) / (elapsed * 1000000.0 * quota)
    throttle_ratio = _delta(
        cpu_first["throttled_usec"],
        cpu_last["throttled_usec"],
        "CPU throttled time",
    ) / (elapsed * 1000000.0)
    cpu_contention = _cpu_contention_summary(samples, elapsed)
    cpu_pressure_ratio = cpu_contention["cpu_pressure_ratio"]

    memory_rows = [
        sample["required_streams"][
            "memory_current_swap_and_pressure"
        ]
        for sample in samples
    ]
    memory_limit = _integer(
        memory_rows[0]["memory_limit_bytes"], "memory limit", 1
    )
    memswap_limit = _integer(
        memory_rows[0]["memory_swap_limit_bytes"], "memory+swap limit", 1
    )
    if any(
        _integer(row["memory_limit_bytes"], "memory limit") != memory_limit
        or _integer(
            row["memory_swap_limit_bytes"], "memory+swap limit"
        )
        != memswap_limit
        for row in memory_rows
    ):
        raise QualificationError("memory envelope changed during gate")
    memory_utilization = _mean(
        [row["memory_current_bytes"] for row in memory_rows],
        "memory current",
    ) / float(memory_limit)
    swap_capacity = max(0, memswap_limit - memory_limit)
    swap_values = [
        _integer(row["swap_current_bytes"], "swap current")
        for row in memory_rows
    ]
    if swap_capacity == 0:
        if any(swap_values):
            raise QualificationError("swap used despite zero swap capacity")
        swap_utilization = 0.0
    else:
        swap_utilization = _mean(
            swap_values, "swap current"
        ) / float(swap_capacity)
    memory_contention = _memory_pressure_summary(
        memory_rows, memory_utilization, swap_utilization, elapsed
    )
    memory_pressure_ratio = memory_contention["memory_pressure_ratio"]
    oom_kill_events = _delta(
        memory_rows[0]["oom_kill_events"],
        memory_rows[-1]["oom_kill_events"],
        "OOM kill",
    )
    unexpected_swap_events = sum(
        1 for before, after in zip(swap_values, swap_values[1:])
        if after > before
    )

    device_first = first_streams[
        "per_declared_device_bytes_iops_latency_queue_and_utilization"
    ]
    device_last = last_streams[
        "per_declared_device_bytes_iops_latency_queue_and_utilization"
    ]
    declared = device_first["declared_major_minor"]
    if device_last["declared_major_minor"] != declared:
        raise QualificationError("declared device changed during gate")
    block_first = device_first["block_device"]
    block_last = device_last["block_device"]
    read_bytes = _delta(
        block_first["read_bytes"], block_last["read_bytes"], "device read bytes"
    )
    write_bytes = _delta(
        block_first["write_bytes"],
        block_last["write_bytes"],
        "device write bytes",
    )
    read_ios = _delta(
        block_first["read_ios"], block_last["read_ios"], "device read IOs"
    )
    write_ios = _delta(
        block_first["write_ios"], block_last["write_ios"], "device write IOs"
    )
    io_count = read_ios + write_ios
    service_ticks = _delta(
        block_first["read_ticks_ms"] + block_first["write_ticks_ms"],
        block_last["read_ticks_ms"] + block_last["write_ticks_ms"],
        "device service ticks",
    )
    io_ticks = _delta(
        block_first["io_ticks_ms"],
        block_last["io_ticks_ms"],
        "device utilization ticks",
    )
    weighted_ticks = _delta(
        block_first["weighted_io_ticks_ms"],
        block_last["weighted_io_ticks_ms"],
        "device weighted queue ticks",
    )

    all_devices = set(device_first["cgroup_devices"]) | set(
        device_last["cgroup_devices"]
    )
    target_wrong_device_bytes = 0.0
    declared_cgroup_bytes = 0.0
    for device in all_devices:
        before = device_first["cgroup_devices"].get(
            device, {"read_bytes": 0, "write_bytes": 0}
        )
        after = device_last["cgroup_devices"].get(
            device, {"read_bytes": 0, "write_bytes": 0}
        )
        attributed = _delta(
            before.get("read_bytes", 0),
            after.get("read_bytes", 0),
            "cgroup device read bytes",
        ) + _delta(
            before.get("write_bytes", 0),
            after.get("write_bytes", 0),
            "cgroup device write bytes",
        )
        if device != declared:
            target_wrong_device_bytes += attributed
        else:
            declared_cgroup_bytes += attributed
    foreign_declared_device_bytes = max(
        0.0,
        read_bytes + write_bytes - declared_cgroup_bytes,
    )
    foreign_bytes = (
        target_wrong_device_bytes + foreign_declared_device_bytes
    )

    first_inventory_stream = first_streams[
        "process_cpu_affinity_and_device_inventory"
    ]
    last_inventory_stream = last_streams[
        "process_cpu_affinity_and_device_inventory"
    ]
    first_inventory = first_inventory_stream[
        "rolling_full_host_inventory"
    ]
    client_first = first_inventory_stream["client_cgroup_devices"].get(
        declared, {"read_bytes": 0, "write_bytes": 0}
    )
    client_last = last_inventory_stream["client_cgroup_devices"].get(
        declared, {"read_bytes": 0, "write_bytes": 0}
    )
    client_declared_device_bytes = _delta(
        client_first.get("read_bytes", 0),
        client_last.get("read_bytes", 0),
        "client cgroup declared-device read bytes",
    ) + _delta(
        client_first.get("write_bytes", 0),
        client_last.get("write_bytes", 0),
        "client cgroup declared-device write bytes",
    )
    if client_declared_device_bytes != 0:
        raise QualificationError(
            "client cgroup wrote/read target MySQL device"
        )

    stable_keys = (
        "pid",
        "ppid",
        "starttime_ticks",
        "comm",
        "cmdline_sha256",
        "cgroup_sha256",
        "cgroup_paths_sha256",
        "container_identity",
        "cpus_allowed_list",
        "mems_allowed_list",
        "role",
        "fd_attribution",
        "process_io_attribution",
        "container_mounts_sha256",
    )

    def stable_process_projection(inventory):
        # The sealed workload root must remain stable, but its short-lived
        # children are part of the authorized workload implementation.  SQL
        # qualification intentionally invokes one mysql CLI child per query,
        # so child PID churn is not co-resident interference.  Continue to
        # compare every target, control-plane, and co-resident process.
        workload_root_pid = _integer(
            inventory.get("workload_root_pid"), "workload root PID", 1
        )
        return [
            {key: row.get(key) for key in stable_keys}
            for row in inventory["processes"]
            if (
                row.get("role") != "qualification_workload"
                or row.get("pid") == workload_root_pid
            )
        ]

    baseline_processes = stable_process_projection(first_inventory)
    baseline_target_cgroup = first_inventory[
        "target_cgroup_paths_sha256"
    ]
    baseline_workload_cgroup = first_inventory[
        "workload_cgroup_paths_sha256"
    ]
    baseline_docker_inventory = first_inventory[
        "docker_inventory_sha256"
    ]
    for sample_index, sample in enumerate(samples[1:], 1):
        current_inventory = sample["required_streams"][
            "process_cpu_affinity_and_device_inventory"
        ]["rolling_full_host_inventory"]
        current_processes = stable_process_projection(current_inventory)
        if (
            current_processes != baseline_processes
            or current_inventory["target_cgroup_paths_sha256"]
            != baseline_target_cgroup
            or current_inventory["workload_cgroup_paths_sha256"]
            != baseline_workload_cgroup
            or current_inventory["docker_inventory_sha256"]
            != baseline_docker_inventory
        ):
            baseline_by_pid = {
                (row.get("pid"), row.get("starttime_ticks")): row
                for row in baseline_processes
            }
            current_by_pid = {
                (row.get("pid"), row.get("starttime_ticks")): row
                for row in current_processes
            }
            diagnostics = {
                "sample_index": sample_index,
                "added_processes": [
                    current_by_pid[key]
                    for key in sorted(set(current_by_pid) - set(baseline_by_pid))
                ][:16],
                "removed_processes": [
                    baseline_by_pid[key]
                    for key in sorted(set(baseline_by_pid) - set(current_by_pid))
                ][:16],
                "changed_processes": [
                    {
                        "before": baseline_by_pid[key],
                        "after": current_by_pid[key],
                    }
                    for key in sorted(set(baseline_by_pid) & set(current_by_pid))
                    if baseline_by_pid[key] != current_by_pid[key]
                ][:16],
                "target_cgroup_changed": (
                    current_inventory["target_cgroup_paths_sha256"]
                    != baseline_target_cgroup
                ),
                "workload_cgroup_changed": (
                    current_inventory["workload_cgroup_paths_sha256"]
                    != baseline_workload_cgroup
                ),
                "docker_inventory_changed": (
                    current_inventory["docker_inventory_sha256"]
                    != baseline_docker_inventory
                ),
            }
            raise QualificationError(
                "co-resident process appeared/disappeared/changed: %s"
                % json.dumps(diagnostics, sort_keys=True, separators=(",", ":"))
            )

    counter_rates = {}
    database_first = first_streams["database_counters"]
    database_last = last_streams["database_counters"]
    if set(database_first) != set(DB_COUNTERS) or set(database_last) != set(
        DB_COUNTERS
    ):
        raise QualificationError("database counter coverage incomplete")
    for name in DB_COUNTERS:
        if name == "Threads_running":
            continue
        counter_rates[name] = _delta(
            database_first[name], database_last[name], "database %s" % name
        ) / elapsed
    threads_running_average = _mean(
        [
            sample["required_streams"]["database_counters"][
                "Threads_running"
            ]
            for sample in samples
        ],
        "Threads_running",
    )

    return {
        "elapsed_seconds": elapsed,
        "sample_count": len(samples),
        "workload": {
            "completion_delta": completion_delta,
            "completion_rate_per_second": completion_delta / elapsed,
            "latency_ms": latency,
        },
        "resource_utilization": {
            "cpu_usage_ratio": cpu_usage_ratio,
            "cpu_pressure_ratio": cpu_pressure_ratio,
            "memory_utilization_ratio": memory_utilization,
            "swap_utilization_ratio": swap_utilization,
            "memory_pressure_ratio": memory_pressure_ratio,
            "device_utilization_ratio": io_ticks / (elapsed * 1000.0),
        },
        "cpu_contention_signal": cpu_contention,
        "memory_pressure_signal": memory_contention,
        "cpu_throttle_ratio": throttle_ratio,
        "device": {
            "declared_major_minor": declared,
            "read_bytes_per_second": read_bytes / elapsed,
            "write_bytes_per_second": write_bytes / elapsed,
            "read_iops": read_ios / elapsed,
            "write_iops": write_ios / elapsed,
            "latency_ms_per_io": (
                service_ticks / io_count if io_count else 0.0
            ),
            "average_queue_depth": weighted_ticks / (elapsed * 1000.0),
            "utilization_ratio": io_ticks / (elapsed * 1000.0),
            "foreign_device_io_attribution_bytes": foreign_bytes,
            "target_cgroup_wrong_device_bytes":
                target_wrong_device_bytes,
            "foreign_declared_device_bytes":
                foreign_declared_device_bytes,
            "target_cgroup_declared_device_bytes":
                declared_cgroup_bytes,
            "client_cgroup_declared_device_bytes":
                client_declared_device_bytes,
        },
        "database_counter_rates_per_second": counter_rates,
        "database_gauges": {
            "Threads_running_average": threads_running_average,
        },
        "new_oom_kill_events": oom_kill_events,
        "new_unexpected_swap_events": unexpected_swap_events,
        "rolling_inventory": {
            "permission_failure_count": 0,
            "race_count": 0,
            "foreign_writer_count": 0,
            "target_cgroup_paths_sha256": baseline_target_cgroup,
            "workload_cgroup_paths_sha256": baseline_workload_cgroup,
            "docker_inventory_sha256": baseline_docker_inventory,
        },
        "affinity_and_inventory_stable": True,
    }


def validate_qualification_evidence(path, expected_mode):
    if not valid_sidecar(path):
        raise QualificationError("qualification evidence sidecar differs")
    evidence = load_object(path, "qualification evidence")
    scenario = evidence.get("scenario")
    hostname = evidence.get("hostname")
    initial = evidence.get("initial_live_readback")
    peer = evidence.get("peer_live_readback")
    peer_scenario = evidence.get("peer_scenario")
    peer_workload = evidence.get("paired_peer_workload_proof")
    if (
        evidence.get("schema_version")
        != "mysql_algorithm_c_debug_qualification_evidence_v2"
        or evidence.get("status")
        != "PASS_COMPLETE_8_STREAM_DEBUG_QUALIFICATION_NOT_GATE_AUTHORITY"
        or evidence.get("mode") != expected_mode
        or evidence.get("duration_seconds") != QUALIFICATION_SECONDS
        or evidence.get("minimum_samples") != MINIMUM_SAMPLES
        or evidence.get("required_streams") != list(REQUIRED_STREAMS)
        or not isinstance(evidence.get("summary"), dict)
        or evidence.get("debug_only") is not True
        or evidence.get("capture_authorized") is not False
        or evidence.get("formal_r0_authorized") is not False
        or evidence.get("formal_authorized") is not False
        or evidence.get("accepted_round_authorized") is not False
        or evidence.get("concurrent_capture_r0_or_debug_authorized") is not False
        or not isinstance(scenario, str)
        or not scenario
        or not isinstance(hostname, str)
        or not hostname
        or not isinstance(initial, dict)
        or initial.get("status")
        != "PASS_LIVE_READBACK_NOT_LAUNCH_AUTHORITY"
        or initial.get("scenario") != scenario
        or initial.get("hostname") != hostname
    ):
        raise QualificationError("qualification evidence authority differs")
    workload_exit = evidence.get("workload_exit")
    stop_request = evidence.get("qualification_stop_request")
    if (
        not isinstance(workload_exit, dict)
        or workload_exit.get("returncode") != 0
        or workload_exit.get("graceful_within_seconds") != 120
        or workload_exit.get("forced_termination") is not False
    ):
        raise QualificationError("qualification workload exit proof differs")
    if scenario == "tpcc_cpu":
        if (
            not isinstance(stop_request, dict)
            or stop_request.get("schema_version")
            != "mysql_algorithm_c_benchbase_stop_request_evidence_v1"
            or stop_request.get("payload_utf8") != "180\n"
            or stop_request.get("atomic_exclusive_create") is not True
            or stop_request.get("sha256") != sha_bytes(b"180\n")
            or not str(stop_request.get("container_path") or "").startswith(
                "/measurement/"
            )
            or not isinstance(stop_request.get("host_path"), str)
        ):
            raise QualificationError("BenchBase stop-request proof differs")
        stop_path = Path(stop_request["host_path"])
        if (
            stop_path.is_symlink()
            or not stop_path.is_file()
            or sha256(stop_path) != stop_request["sha256"]
        ):
            raise QualificationError("BenchBase stop-request artifact differs")
    elif stop_request is not None:
        raise QualificationError("non-BenchBase evidence has a stop request")
    if expected_mode == "qualification_solo":
        if (
            peer is not None
            or peer_scenario is not None
            or peer_workload is not None
        ):
            raise QualificationError("solo evidence unexpectedly binds a peer")
    elif (
        not isinstance(peer, dict)
        or not isinstance(peer_scenario, str)
        or peer.get("status") != "PASS_LIVE_READBACK_NOT_LAUNCH_AUTHORITY"
        or peer.get("scenario") != peer_scenario
        or peer.get("hostname") != hostname
        or not isinstance(peer_workload, dict)
        or peer_workload.get("schema_version")
        != "mysql_algorithm_c_paired_peer_workload_proof_v1"
        or peer_workload.get("scenario") != peer_scenario
        or not isinstance(
            peer_workload.get("qualification_attempt_id"), str
        )
        or len(peer_workload.get("qualification_attempt_id")) < 32
        or peer_workload.get("sample_count") != MINIMUM_SAMPLES
        or _integer(
            peer_workload.get("completion_delta"),
            "paired peer completion delta",
            1,
        )
        < 1
        or peer_workload.get("fresh_sequence_at_every_sample") is not True
    ):
        raise QualificationError("paired evidence peer binding differs")
    samples = evidence.get("samples")
    summary = summarize_dynamic_samples(samples)
    if summary != evidence["summary"]:
        raise QualificationError("qualification evidence summary differs")
    token = evidence.get("attempt_token_sha256")
    collector = evidence.get("collector_sha256")
    if (
        not isinstance(token, str)
        or len(token) != 64
        or not isinstance(collector, str)
        or len(collector) != 64
    ):
        raise QualificationError("qualification evidence identity is absent")
    return evidence


def _fraction_degradation(solo, paired):
    solo = _finite_number(solo, "solo throughput", 0.000000001)
    paired = _finite_number(paired, "paired throughput")
    return max(0.0, (solo - paired) / solo)


def _fraction_inflation(solo, paired, label):
    solo = _finite_number(solo, "%s solo" % label)
    paired = _finite_number(paired, "%s paired" % label)
    if solo == 0:
        return 0.0 if paired == 0 else float("inf")
    return max(0.0, (paired - solo) / solo)


def compare_direction(solo, paired, direction):
    solo_summary = solo["summary"]
    paired_summary = paired["summary"]
    violations = []
    solo_cpu_signal = solo_summary.get("cpu_contention_signal")
    paired_cpu_signal = paired_summary.get("cpu_contention_signal")
    solo_memory_signal = solo_summary.get("memory_pressure_signal")
    paired_memory_signal = paired_summary.get("memory_pressure_signal")
    for label, left, right in (
        ("cpu_contention", solo_cpu_signal, paired_cpu_signal),
        ("memory_pressure", solo_memory_signal, paired_memory_signal),
    ):
        if (
            not isinstance(left, dict) or not isinstance(right, dict)
            or left.get("policy_version") != CPU_CONTENTION_POLICY_VERSION
            or right.get("policy_version") != CPU_CONTENTION_POLICY_VERSION
            or left.get("mode") != right.get("mode")
        ):
            raise QualificationError("%s source differs across direction" % label)
    throughput_degradation = _fraction_degradation(
        solo_summary["workload"]["completion_rate_per_second"],
        paired_summary["workload"]["completion_rate_per_second"],
    )
    if (
        throughput_degradation
        > DYNAMIC_TOLERANCES["throughput_degradation_fraction_max"]
    ):
        violations.append("throughput_degradation_exceeded")
    solo_latency = solo_summary["workload"]["latency_ms"]
    paired_latency = paired_summary["workload"]["latency_ms"]
    if set(solo_latency) != set(paired_latency):
        raise QualificationError("solo/paired latency stream shape differs")
    latency_inflation = {}
    for key in sorted(solo_latency):
        latency_inflation[key] = _fraction_inflation(
            solo_latency[key], paired_latency[key], key
        )
        if (
            latency_inflation[key]
            > DYNAMIC_TOLERANCES["latency_inflation_fraction_max"]
        ):
            violations.append("%s_inflation_exceeded" % key)
    solo_resources = solo_summary["resource_utilization"]
    paired_resources = paired_summary["resource_utilization"]
    if set(solo_resources) != set(paired_resources):
        raise QualificationError("solo/paired resource stream shape differs")
    resource_drift = {}
    for key in sorted(solo_resources):
        resource_drift[key] = abs(
            _finite_number(paired_resources[key], "%s paired" % key)
            - _finite_number(solo_resources[key], "%s solo" % key)
        )
        if (
            resource_drift[key]
            > DYNAMIC_TOLERANCES[
                "resource_utilization_absolute_drift_max"
            ]
        ):
            violations.append("%s_absolute_drift_exceeded" % key)
    throttle_drift = abs(
        _finite_number(
            paired_summary["cpu_throttle_ratio"], "paired throttle"
        )
        - _finite_number(
            solo_summary["cpu_throttle_ratio"], "solo throttle"
        )
    )
    if (
        throttle_drift
        > DYNAMIC_TOLERANCES[
            "cpu_throttle_ratio_absolute_drift_max"
        ]
    ):
        violations.append("cpu_throttle_ratio_absolute_drift_exceeded")
    foreign = _finite_number(
        paired_summary["device"]["foreign_device_io_attribution_bytes"],
        "foreign device bytes",
    )
    if (
        foreign
        > DYNAMIC_TOLERANCES["foreign_device_io_attribution_bytes_max"]
    ):
        violations.append("foreign_device_io_attribution_detected")
    oom = _integer(
        paired_summary["new_oom_kill_events"], "new OOM kill events"
    )
    if oom > DYNAMIC_TOLERANCES["new_oom_kill_events_max"]:
        violations.append("new_oom_kill_events_detected")
    swap = _integer(
        paired_summary["new_unexpected_swap_events"],
        "new unexpected swap events",
    )
    if swap > DYNAMIC_TOLERANCES["new_unexpected_swap_events_max"]:
        violations.append("new_unexpected_swap_events_detected")
    if (
        solo_summary.get("affinity_and_inventory_stable") is not True
        or paired_summary.get("affinity_and_inventory_stable") is not True
    ):
        violations.append("affinity_or_device_inventory_changed")
    return {
        "direction": direction,
        "solo_evidence_sha256": solo["_evidence_sha256"],
        "paired_evidence_sha256": paired["_evidence_sha256"],
        "throughput_degradation_fraction": throughput_degradation,
        "latency_inflation_fraction": latency_inflation,
        "resource_utilization_absolute_drift": resource_drift,
        "cpu_throttle_ratio_absolute_drift": throttle_drift,
        "cpu_contention_mode": solo_cpu_signal["mode"],
        "memory_pressure_mode": solo_memory_signal["mode"],
        "foreign_device_io_attribution_bytes": foreign,
        "new_oom_kill_events": oom,
        "new_unexpected_swap_events": swap,
        "violations": sorted(set(violations)),
        "passed": not violations,
    }


def compare_bidirectional_gate(
    a_solo_path,
    a_paired_path,
    b_solo_path,
    b_paired_path,
    primary_scenario=None,
    host_id=None,
    attempt_token=None,
    captured_at_utc=None,
    expires_at_utc=None,
    raw_artifacts=None,
):
    inputs = []
    for path, mode in (
        (a_solo_path, "qualification_solo"),
        (a_paired_path, "qualification_paired"),
        (b_solo_path, "qualification_solo"),
        (b_paired_path, "qualification_paired"),
    ):
        value = validate_qualification_evidence(path, mode)
        value = dict(value)
        value["_evidence_sha256"] = sha256(path)
        inputs.append(value)
    a_solo, a_paired, b_solo, b_paired = inputs
    if (
        a_solo["scenario"] != a_paired["scenario"]
        or b_solo["scenario"] != b_paired["scenario"]
        or a_solo["scenario"] == b_solo["scenario"]
        or len({row["hostname"] for row in inputs}) != 1
    ):
        raise QualificationError("bidirectional scenario/host pairing differs")
    if (
        a_paired.get("peer_scenario") != b_solo["scenario"]
        or b_paired.get("peer_scenario") != a_solo["scenario"]
    ):
        raise QualificationError("bidirectional peer binding differs")
    if (
        a_paired["paired_peer_workload_proof"][
            "qualification_attempt_id"
        ]
        != b_paired.get("qualification_attempt_id")
        or b_paired["paired_peer_workload_proof"][
            "qualification_attempt_id"
        ]
        != a_paired.get("qualification_attempt_id")
    ):
        raise QualificationError(
            "paired directions were not measured in one concurrent window"
        )
    collectors = {row["collector_sha256"] for row in inputs}
    if len(collectors) != 1:
        raise QualificationError("both directions must use one collector version")
    attempts = [row["attempt_token_sha256"] for row in inputs]
    if len(set(attempts)) != 4:
        raise QualificationError(
            "qualification authorization reused across attempts"
        )
    a = compare_direction(
        a_solo, a_paired, "A_solo_vs_A_while_B_runs"
    )
    b = compare_direction(
        b_solo, b_paired, "B_solo_vs_B_while_A_runs"
    )
    if (a["cpu_contention_mode"] != b["cpu_contention_mode"]
            or a["memory_pressure_mode"] != b["memory_pressure_mode"]):
        raise QualificationError("contention source differs across bidirectional gate")
    passed = a["passed"] and b["passed"]
    scenario = primary_scenario or a_solo["scenario"]
    if scenario not in (a_solo["scenario"], b_solo["scenario"]):
        raise QualificationError("primary scenario is outside the measured pair")
    peer_scenario = (
        b_solo["scenario"]
        if scenario == a_solo["scenario"]
        else a_solo["scenario"]
    )
    resolved_host_id = host_id or a_solo["hostname"]
    resolved_attempt = attempt_token or a_solo["qualification_attempt_id"]
    if (
        not isinstance(resolved_host_id, str)
        or not resolved_host_id
        or not isinstance(resolved_attempt, str)
        or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{15,127}", resolved_attempt)
    ):
        raise QualificationError("canonical dynamic gate identity is invalid")
    captured = captured_at_utc or datetime.now(timezone.utc)
    expires = expires_at_utc or (captured + timedelta(minutes=15))
    if (
        not isinstance(captured, datetime)
        or captured.tzinfo is None
        or not isinstance(expires, datetime)
        or expires.tzinfo is None
        or expires <= captured
        or (expires - captured).total_seconds() > 900
    ):
        raise QualificationError("canonical dynamic gate validity window differs")
    closure = {
        "A_solo": a_solo["_evidence_sha256"],
        "A_paired": a_paired["_evidence_sha256"],
        "B_solo": b_solo["_evidence_sha256"],
        "B_paired": b_paired["_evidence_sha256"],
    }
    return {
        "schema_version":
            "mysql_algorithm_c_distributed_dynamic_gate_evidence_v1",
        "policy_version":
            "mysql_algorithm_c_same_host_bidirectional_interference_gate_v2",
        "status": "PASS" if passed else "FAIL",
        "scenario": scenario,
        "peer_scenario": peer_scenario,
        "host_id": resolved_host_id,
        "attempt_token": resolved_attempt,
        "captured_at_utc": captured.astimezone(timezone.utc).isoformat(),
        "expires_at_utc": expires.astimezone(timezone.utc).isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "accepted_round_authorized": False,
        "formal10_authorized": False,
        "duration_seconds": QUALIFICATION_SECONDS,
        "sample_period_seconds": 1,
        "directions_passed": (
            [
                "A_solo_vs_A_while_B_runs",
                "B_solo_vs_B_while_A_runs",
            ]
            if passed else []
        ),
        "streams_complete": list(REQUIRED_STREAMS),
        "database_counter_missing_samples": 0,
        "database_counter_timeout_count": 0,
        "all_tolerances_passed": passed,
        "ongoing_monitor_required": True,
        "authorization_reuse": "PROHIBITED_ACROSS_ATTEMPTS",
        "qualification_capture_r0_formal_accounting": "PROHIBITED",
        "raw_artifacts": dict(raw_artifacts or {}),
        "hostname": a_solo["hostname"],
        "scenarios": [a_solo["scenario"], b_solo["scenario"]],
        "collector_sha256": list(collectors)[0],
        "minimum_samples_per_stream": MINIMUM_SAMPLES,
        "maximum_sample_gap_seconds": MAXIMUM_SAMPLE_GAP_SECONDS,
        "required_streams": list(REQUIRED_STREAMS),
        "cpu_contention_policy_version": CPU_CONTENTION_POLICY_VERSION,
        "cpu_contention_mode": a["cpu_contention_mode"],
        "memory_pressure_mode": a["memory_pressure_mode"],
        "tolerances": dict(DYNAMIC_TOLERANCES),
        "directions": {
            "A_solo_vs_A_while_B_runs": a,
            "B_solo_vs_B_while_A_runs": b,
        },
        "both_directions_same_version_verified": True,
        "authorization_reuse_verified_absent": True,
        "ongoing_rolling_window_required": True,
        "raw_evidence_closure_sha256": closure,
        "debug_only": True,
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
        "concurrent_capture_r0_or_debug_eligible_for_main_agent_decision":
            passed,
        "serial_fallback_required": not passed,
    }


def run_qualification(
    bundle,
    mode,
    container,
    mysql_binary,
    defaults_file,
    output,
    command_spec_path,
    authority_path,
    token,
    mysql_client_container=None,
    mysql_binary_in_client=None,
    peer_readback_path=None,
    peer_container=None,
    duration_seconds=QUALIFICATION_SECONDS,
):
    if mode not in ("qualification_solo", "qualification_paired"):
        raise QualificationError("invalid qualification mode")
    if int(duration_seconds) != QUALIFICATION_SECONDS:
        raise QualificationError("qualification duration must be exactly 180s")
    own = collect_readback(
        bundle,
        container,
        mysql_binary,
        defaults_file,
        mysql_client_container,
        mysql_binary_in_client,
    )
    spec = validate_command_spec(
        command_spec_path,
        own["bundle_id"],
        mode,
        own["client_resource_envelope"],
    )
    authority = validate_main_agent_authority(
        authority_path,
        token,
        own["bundle_id"],
        mode,
        command_spec_path,
    )
    peer = None
    if mode == "qualification_paired":
        if peer_readback_path is None or not peer_container:
            raise QualificationError(
                "paired qualification requires peer readback and container"
            )
        peer = validate_peer_readback(peer_readback_path, own)
        peer_live = inspect_container(peer_container)
        if int((peer_live.get("State") or {}).get("Pid") or 0) != int(
            peer["container"]["pid"]
        ):
            raise QualificationError("peer container PID differs from readback")
    elif peer_readback_path is not None or peer_container:
        raise QualificationError("solo qualification cannot bind a peer")

    stdout_path = output.with_name(output.name + ".workload.stdout")
    stderr_path = output.with_name(output.name + ".workload.stderr")
    environment = dict(os.environ)
    environment.update(spec.get("environment") or {})
    environment.update(
        {
            "MYSQL_ALGORITHM_C_QUALIFICATION_ONLY": "1",
            "MYSQL_CAPTURE_AUTHORIZED": "0",
            "MYSQL_FORMAL_R0_AUTHORIZED": "0",
            "MYSQL_FORMAL_AUTHORIZED": "0",
            "MYSQL_ACCEPTED_ROUND_AUTHORIZED": "0",
            "MYSQL_QUALIFICATION_ATTEMPT_ID":
                spec["qualification_attempt_id"],
            "MYSQL_QUALIFICATION_METRIC_STREAM":
                spec["workload_metric_stream"]["path"],
        }
    )
    client_container_name = spec["client_container"]["name"]
    tmpfs_readback = validate_host_tmpfs_output(
        output.parent,
        spec["measurement_output"]["host_tmpfs_directory"],
        own["client_resource_envelope"]["tmpfs_bytes"],
    )
    validate_host_tmpfs_output(
        Path(spec["workload_metric_stream"]["path"]).parent,
        spec["measurement_output"]["host_tmpfs_directory"],
        own["client_resource_envelope"]["tmpfs_bytes"],
    )
    client_readback = validate_client_container_readback(
        inspect_container(client_container_name),
        own["client_resource_envelope"],
        spec["client_container"],
        spec["measurement_output"]["container_tmpfs_destination"],
        spec["measurement_output"]["host_tmpfs_directory"],
        own["storage"]["major_minor"],
    )
    client_pid = client_readback["pid"]
    client_id = client_readback["container_id"]
    client_start_ticks = client_readback["process_start_ticks"]
    samples = []
    previous_sequence = None
    metric_path = Path(spec["workload_metric_stream"]["path"])
    native_adapter_kind = spec["workload_metric_adapter"]["kind"]
    expected_native_adapter = {
        "hybench_xp_io": "hybench_xp",
        "tpcc_cpu": "benchbase_tpcc",
        "job_memory": "sql_query_native",
        "tpch_memory": "sql_query_native",
    }.get(own["scenario"])
    if expected_native_adapter != native_adapter_kind:
        raise QualificationError(
            "native workload adapter does not match scenario"
        )
    native_previous_epoch_ms = None
    native_metric_producer = None
    native_metric_producer_stop = threading.Event()
    native_metric_producer_state = {"error": None}
    peer_metric_binding = spec.get("paired_peer_workload")
    peer_previous_sequence = None
    workload_isolation = spec["workload_process_isolation"]
    workload_cpus = expand_cpuset(workload_isolation["cpuset_cpus"])
    own_cpus = expand_cpuset(
        own["resource_envelope"]["cpuset_cpus"]
    )
    if not workload_cpus.isdisjoint(own_cpus):
        raise QualificationError("workload and target MySQL CPUs overlap")
    if peer is not None and not workload_cpus.isdisjoint(
        expand_cpuset(peer["resource_envelope"]["cpuset_cpus"])
    ):
        raise QualificationError("workload and paired MySQL CPUs overlap")
    started = None
    process = None
    workload_environment = {
        key: value
        for key, value in environment.items()
        if key in (spec.get("environment") or {})
        or key.startswith("MYSQL_")
    }
    docker_exec_argv = [
        *DOCKER_CLI,
        "exec",
        "-i",
        "--workdir",
        spec["cwd"],
    ]
    for key, value in sorted(workload_environment.items()):
        docker_exec_argv.extend(["--env", "%s=%s" % (key, value)])
    docker_exec_argv.extend([client_container_name] + spec["argv"])
    qualification_stop_request = None
    with stdout_path.open("wb") as stdout_stream, stderr_path.open(
        "wb"
    ) as stderr_stream:
        try:
            process = subprocess.Popen(
                docker_exec_argv,
                env=environment,
                stdout=stdout_stream,
                stderr=stderr_stream,
                start_new_session=True,
            )
            if native_adapter_kind in ("hybench_xp", "benchbase_tpcc"):
                warmup_deadline = time.monotonic() + 75.0
                while True:
                    if process.poll() is not None:
                        raise QualificationError(
                            "debug qualification workload exited during warmup"
                        )
                    try:
                        native_previous_epoch_ms = (
                            metric_adapter.emit_fresh_sample(
                                native_adapter_kind,
                                stdout_path,
                                metric_path,
                                spec["qualification_attempt_id"],
                                0,
                                None,
                            )
                        )
                        metric_adapter.validate_measurement_transition(
                            native_adapter_kind, stdout_path
                        )
                        break
                    except metric_adapter.WorkloadMetricAdapterError as exc:
                        message = str(exc)
                        if not any(
                            marker in message
                            for marker in (
                                "marker is absent",
                                "outside measurement phase",
                                "marker is stale",
                            )
                        ):
                            raise QualificationError(
                                "native workload warmup adapter failed: %s"
                                % exc
                            )
                    if time.monotonic() >= warmup_deadline:
                        raise QualificationError(
                            "native workload did not enter measurement phase"
                        )
                    time.sleep(0.1)
            elif native_adapter_kind == "sql_query_native":
                try:
                    native_previous_epoch_ms = (
                        metric_adapter.emit_fresh_sample(
                            native_adapter_kind,
                            stdout_path,
                            metric_path,
                            spec["qualification_attempt_id"],
                            0,
                            None,
                            timeout_seconds=2.0,
                        )
                    )
                except metric_adapter.WorkloadMetricAdapterError as exc:
                    raise QualificationError(
                        "native SQL workload metric adapter failed: %s" % exc
                    )
            # Paired workloads can have intentionally different warmup
            # durations (TPC-C is 60 seconds while TPC-H starts immediately).
            # Establish presence/freshness before starting the common 180-second
            # window.  Do not consume the sequence: index zero must bind the
            # same current marker and later indexes still require strict growth.
            if peer_metric_binding is not None:
                workload_metric_sample(
                    Path(
                        peer_metric_binding[
                            "workload_metric_stream_path"
                        ]
                    ),
                    peer_metric_binding["qualification_attempt_id"],
                    None,
                    timeout_seconds=90.0,
                )
                # The first marker is only a startup-barrier probe.  Paired
                # workloads deliberately have asymmetric warmup (TPC-C 60s,
                # TPC-H immediate), so that probe can be old by the time the
                # peer appears.  Start the common window from a newly emitted
                # sequence zero without weakening the 2.5s freshness gate.
                if metric_path.is_symlink() or not metric_path.is_file():
                    raise QualificationError(
                        "paired startup metric stream changed before epoch reset"
                    )
                metric_path.unlink()
                try:
                    native_previous_epoch_ms = (
                        metric_adapter.emit_fresh_sample(
                            native_adapter_kind,
                            stdout_path,
                            metric_path,
                            spec["qualification_attempt_id"],
                            0,
                            native_previous_epoch_ms,
                        )
                    )
                except metric_adapter.WorkloadMetricAdapterError as exc:
                    raise QualificationError(
                        "paired startup metric epoch reset failed: %s" % exc
                    )
                # Both peers preserve sequence zero in their append-only stream.
                # Bind the common window to that exact epoch even if scheduling
                # lets the producer append sequence one before the first read.
                peer_previous_sequence = -1
            def produce_native_metrics():
                sequence = 1
                previous_epoch_ms = native_previous_epoch_ms
                while not native_metric_producer_stop.is_set():
                    try:
                        previous_epoch_ms = metric_adapter.emit_fresh_sample(
                            native_adapter_kind,
                            stdout_path,
                            metric_path,
                            spec["qualification_attempt_id"],
                            sequence,
                            previous_epoch_ms,
                            timeout_seconds=2.0,
                        )
                        sequence += 1
                    except metric_adapter.WorkloadMetricAdapterError as exc:
                        if not native_metric_producer_stop.is_set():
                            native_metric_producer_state["error"] = str(exc)
                        return
            native_metric_producer = threading.Thread(
                target=produce_native_metrics,
                name="native-metric-producer",
            )
            native_metric_producer.daemon = True
            native_metric_producer.start()
            started = time.monotonic()
            for index in range(MINIMUM_SAMPLES):
                target = started + float(index)
                delay = target - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                sampled = time.monotonic()
                if process.poll() is not None:
                    raise QualificationError(
                        "debug qualification workload exited before 180s"
                    )
                if samples and sampled - samples[-1]["monotonic"] > (
                    MAXIMUM_SAMPLE_GAP_SECONDS
                ):
                    raise QualificationError("qualification sample gap exceeded")
                container_row = inspect_container(container)
                container_state = container_row.get("State") or {}
                target_pid = int(container_state.get("Pid") or 0)
                if target_pid != own["container"]["pid"]:
                    raise QualificationError(
                        "target container PID changed during qualification"
                    )
                current_client = validate_client_container_readback(
                    inspect_container(client_container_name),
                    own["client_resource_envelope"],
                    spec["client_container"],
                    spec["measurement_output"][
                        "container_tmpfs_destination"
                    ],
                )
                if (
                    current_client["pid"] != client_pid
                    or current_client["container_id"] != client_id
                    or current_client["process_start_ticks"]
                    != client_start_ticks
                    or current_client["host_config_sha256"]
                    != client_readback["host_config_sha256"]
                ):
                    raise QualificationError(
                        "client container changed during qualification"
                    )
                if peer is not None:
                    peer_state = inspect_container(peer_container).get("State") or {}
                    if int(peer_state.get("Pid") or 0) != peer["container"]["pid"]:
                        raise QualificationError(
                            "peer container changed during qualification"
                        )
                if native_metric_producer_state["error"] is not None:
                    raise QualificationError(
                        "native workload metric producer failed: %s"
                        % native_metric_producer_state["error"]
                    )
                dynamic_sample = collect_dynamic_sample(
                    index,
                    sampled,
                    started,
                    own,
                    target_pid,
                    client_pid,
                    process.pid,
                    spec["workload_process_isolation"],
                    mysql_binary,
                    defaults_file,
                    metric_path,
                    spec["qualification_attempt_id"],
                    previous_sequence,
                )
                previous_sequence = dynamic_sample["required_streams"][
                    "workload_throughput_or_completion_rate"
                ]["sequence"]
                if peer_metric_binding is not None:
                    peer_workload = workload_metric_sample(
                        Path(
                            peer_metric_binding[
                                "workload_metric_stream_path"
                            ]
                        ),
                        peer_metric_binding["qualification_attempt_id"],
                        peer_previous_sequence,
                        timeout_seconds=2.5,
                        allow_fresh_repeat=True,
                    )
                    peer_previous_sequence = peer_workload["sequence"]
                    dynamic_sample["paired_peer_workload"] = peer_workload
                observed_gap = (
                    None if not samples else
                    sampled - samples[-1]["monotonic"]
                )
                print(
                    "DEBUG_COMPLETE_SAMPLE_GAP_JSON="
                    + json.dumps(
                        {"index": index, "gap_seconds": observed_gap,
                         "sampled_monotonic": sampled},
                        sort_keys=True, separators=(",", ":"),
                    ),
                    flush=True,
                )
                samples.append(dynamic_sample)
            if peer_metric_binding is not None:
                own_completion_path = Path(
                    str(metric_path) + ".collector-complete-v25.json"
                )
                peer_metric_path = Path(
                    peer_metric_binding["workload_metric_stream_path"]
                )
                peer_completion_path = Path(
                    str(peer_metric_path) + ".collector-complete-v25.json"
                )
                own_completion = {
                    "schema_version":
                        "mysql_algorithm_c_paired_collector_completion_v25",
                    "qualification_attempt_id":
                        spec["qualification_attempt_id"],
                    "sample_count": len(samples),
                    "last_index": MINIMUM_SAMPLES - 1,
                }
                completion_payload = canonical(own_completion)
                completion_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                if hasattr(os, "O_NOFOLLOW"):
                    completion_flags |= os.O_NOFOLLOW
                try:
                    completion_descriptor = os.open(
                        str(own_completion_path), completion_flags, 0o600
                    )
                    try:
                        os.write(completion_descriptor, completion_payload)
                        os.fsync(completion_descriptor)
                    finally:
                        os.close(completion_descriptor)
                except OSError as exc:
                    raise QualificationError(
                        "own paired collector completion marker failed: %s" % exc
                    )
                completion_deadline = time.monotonic() + 60.0
                while True:
                    if native_metric_producer_state["error"] is not None:
                        raise QualificationError(
                            "native workload metric producer failed during paired "
                            "completion: %s"
                            % native_metric_producer_state["error"]
                        )
                    if process.poll() is not None:
                        raise QualificationError(
                            "debug qualification workload exited during paired "
                            "completion barrier"
                        )
                    if peer_completion_path.is_symlink():
                        raise QualificationError(
                            "peer paired collector completion marker is unsafe"
                        )
                    if peer_completion_path.is_file():
                        try:
                            peer_completion = json.loads(
                                peer_completion_path.read_text(encoding="utf-8")
                            )
                        except (OSError, UnicodeError, ValueError) as exc:
                            raise QualificationError(
                                "peer paired collector completion marker is invalid: "
                                "%s" % exc
                            )
                        expected_completion = {
                            "schema_version":
                                "mysql_algorithm_c_paired_collector_completion_v25",
                            "qualification_attempt_id":
                                peer_metric_binding["qualification_attempt_id"],
                            "sample_count": MINIMUM_SAMPLES,
                            "last_index": MINIMUM_SAMPLES - 1,
                        }
                        if peer_completion != expected_completion:
                            raise QualificationError(
                                "peer paired collector completion authority differs"
                            )
                        workload_metric_sample(
                            peer_metric_path,
                            peer_metric_binding["qualification_attempt_id"],
                            peer_previous_sequence,
                            timeout_seconds=2.5,
                            allow_fresh_repeat=True,
                        )
                        break
                    if time.monotonic() >= completion_deadline:
                        raise QualificationError(
                            "paired collector completion marker timed out"
                        )
                    time.sleep(0.05)
        finally:
            producer_cleanup_error = None
            if native_metric_producer is not None:
                native_metric_producer_stop.set()
                native_metric_producer.join(timeout=3.0)
                if native_metric_producer.is_alive():
                    producer_cleanup_error = (
                        "native metric producer did not quiesce within 3s"
                    )
                elif native_metric_producer_state["error"] is not None:
                    producer_cleanup_error = native_metric_producer_state["error"]
            # Preserve the primary sampling/readback failure.  The v7 cleanup
            # waited for graceful exit and then replaced it with a generic graceful-
            # exit timeout, hiding the actionable cause.
            primary_exception_active = sys.exc_info()[1] is not None
            if process is not None and process.poll() is None:
                stop_error = None
                if native_adapter_kind == "benchbase_tpcc":
                    try:
                        container_root = str(
                            spec["measurement_output"][
                                "container_tmpfs_destination"
                            ]
                        ).rstrip("/")
                        stop_container = str(
                            (spec.get("environment") or {}).get(
                                "BENCHBASE_FORMAL_STOP_FILE"
                            ) or ""
                        )
                        required_prefix = container_root + "/"
                        if (
                            not stop_container.startswith(required_prefix)
                            or "/" in stop_container[len(required_prefix):]
                            or stop_container.endswith("/")
                        ):
                            raise QualificationError(
                                "BenchBase stop-file binding differs"
                            )
                        stop_host = Path(
                            spec["measurement_output"][
                                "host_tmpfs_directory"
                            ]
                        ) / stop_container[len(required_prefix):]
                        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                        if hasattr(os, "O_NOFOLLOW"):
                            flags |= os.O_NOFOLLOW
                        descriptor = os.open(str(stop_host), flags, 0o444)
                        try:
                            os.write(descriptor, b"180\n")
                            os.fsync(descriptor)
                        finally:
                            os.close(descriptor)
                        qualification_stop_request = {
                            "schema_version":
                                "mysql_algorithm_c_benchbase_stop_request_evidence_v1",
                            "container_path": stop_container,
                            "host_path": str(stop_host),
                            "payload_utf8": "180\n",
                            "sha256": sha256(stop_host),
                            "atomic_exclusive_create": True,
                        }
                    except (OSError, KeyError, TypeError, ValueError) as exc:
                        stop_error = str(exc)
                timed_out = False
                try:
                    # SQL runs for 240 seconds.  BenchBase receives the
                    # manifest-bound atomic 180-second stop request above.
                    # Both must exit naturally before immutable log hashing.
                    if stop_error is not None or primary_exception_active:
                        raise subprocess.TimeoutExpired(process.args, 0)
                    process.wait(timeout=120)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    # Killing the host-side docker-exec CLI does not kill its
                    # process in the client container.  Bind the exact sealed
                    # argv and client cgroup before terminating one process
                    # group; never use a name-wide or container-wide kill.
                    expected_cmdline = [
                        os.fsencode(str(value)) for value in spec["argv"]
                    ]
                    workload_roots = []
                    for proc_entry in Path("/proc").iterdir():
                        if not proc_entry.name.isdigit():
                            continue
                        try:
                            raw_cmdline = (
                                proc_entry / "cmdline"
                            ).read_bytes().split(b"\0")
                            if raw_cmdline and raw_cmdline[-1] == b"":
                                raw_cmdline.pop()
                            cgroup_text = (
                                proc_entry / "cgroup"
                            ).read_text(encoding="ascii", errors="strict")
                            if (
                                raw_cmdline == expected_cmdline
                                and ("/docker/%s" % client_id) in cgroup_text
                            ):
                                workload_roots.append(
                                    (
                                        int(proc_entry.name),
                                        process_start_ticks(int(proc_entry.name)),
                                    )
                                )
                        except (OSError, UnicodeError, ValueError):
                            continue
                    container_cleanup_ok = len(workload_roots) == 1
                    if container_cleanup_ok:
                        workload_pid, workload_start_ticks = workload_roots[0]
                        try:
                            os.killpg(os.getpgid(workload_pid), signal.SIGTERM)
                        except OSError:
                            try:
                                os.kill(workload_pid, signal.SIGTERM)
                            except OSError:
                                pass
                        cleanup_deadline = time.monotonic() + 10.0
                        while time.monotonic() < cleanup_deadline:
                            try:
                                if (
                                    process_start_ticks(workload_pid)
                                    != workload_start_ticks
                                ):
                                    break
                            except (OSError, QualificationError):
                                break
                            time.sleep(0.05)
                        try:
                            still_same = (
                                process_start_ticks(workload_pid)
                                == workload_start_ticks
                            )
                        except (OSError, QualificationError):
                            still_same = False
                        if still_same:
                            try:
                                os.killpg(
                                    os.getpgid(workload_pid), signal.SIGKILL
                                )
                            except OSError:
                                try:
                                    os.kill(workload_pid, signal.SIGKILL)
                                except OSError:
                                    pass
                            time.sleep(0.1)
                        try:
                            container_cleanup_ok = (
                                process_start_ticks(workload_pid)
                                != workload_start_ticks
                            )
                        except (OSError, QualificationError):
                            container_cleanup_ok = True
                    try:
                        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                        process.wait(timeout=120)
                    except (OSError, subprocess.TimeoutExpired):
                        try:
                            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                        except OSError:
                            pass
                        process.wait()
                if stop_error is not None:
                    raise QualificationError(
                        "BenchBase atomic stop request failed: %s" % stop_error
                    )
                if timed_out and not container_cleanup_ok:
                    raise QualificationError(
                        "timed-out workload exact container cleanup failed"
                    )
                if timed_out and not primary_exception_active:
                    raise QualificationError(
                        "debug qualification workload did not exit gracefully "
                        "within 120s"
                    )
            if (producer_cleanup_error is not None
                    and not primary_exception_active):
                raise QualificationError(
                    "native metric producer cleanup failed: %s"
                    % producer_cleanup_error
                )
    if len(samples) < MINIMUM_SAMPLES:
        raise QualificationError("qualification sample coverage incomplete")
    summary = summarize_dynamic_samples(samples)
    paired_peer_workload_proof = None
    if peer_metric_binding is not None:
        peer_rows = [sample.get("paired_peer_workload") for sample in samples]
        if any(not isinstance(row, dict) for row in peer_rows):
            raise QualificationError("paired peer workload samples are absent")
        peer_completion_delta = _delta(
            peer_rows[0]["completed_total"],
            peer_rows[-1]["completed_total"],
            "paired peer workload completion",
        )
        if peer_completion_delta <= 0:
            raise QualificationError(
                "paired peer workload made no measured progress"
            )
        paired_peer_workload_proof = {
            "schema_version":
                "mysql_algorithm_c_paired_peer_workload_proof_v1",
            "scenario": peer_metric_binding["scenario"],
            "qualification_attempt_id":
                peer_metric_binding["qualification_attempt_id"],
            "workload_metric_stream_path":
                peer_metric_binding["workload_metric_stream_path"],
            "sample_count": len(peer_rows),
            "first_sequence": peer_rows[0]["sequence"],
            "last_sequence": peer_rows[-1]["sequence"],
            "completion_delta": peer_completion_delta,
            "fresh_sequence_at_every_sample": True,
        }
    attempt_token_sha256 = sha_bytes(
        canonical(
            {
                "main_agent_token_sha256":
                    sha_bytes(token.encode("utf-8")),
                "qualification_attempt_id":
                    spec["qualification_attempt_id"],
                "authority_sha256": sha256(authority_path),
            }
        )
    )
    result = {
        "schema_version": "mysql_algorithm_c_debug_qualification_evidence_v2",
        "status":
            "PASS_COMPLETE_8_STREAM_DEBUG_QUALIFICATION_NOT_GATE_AUTHORITY",
        "mode": mode,
        "direction": (
            "solo_baseline"
            if mode == "qualification_solo"
            else "own_affected_by_peer"
        ),
        "bundle_id": own["bundle_id"],
        "scenario": own["scenario"],
        "hostname": own["hostname"],
        "main_agent_authority_sha256": sha256(authority_path),
        "command_spec_sha256": sha256(command_spec_path),
        "qualification_attempt_id": spec["qualification_attempt_id"],
        "attempt_token_sha256": attempt_token_sha256,
        "collector_sha256": sha256(
            Path(__file__).resolve().parents[1] / "bundle_manifest.json"
        ),
        "duration_seconds": QUALIFICATION_SECONDS,
        "minimum_samples": MINIMUM_SAMPLES,
        "maximum_sample_gap_seconds": MAXIMUM_SAMPLE_GAP_SECONDS,
        "required_streams": list(REQUIRED_STREAMS),
        "samples": samples,
        "summary": summary,
        "initial_live_readback": own,
        "peer_live_readback": peer,
        "peer_scenario": peer.get("scenario") if peer is not None else None,
        "paired_peer_workload_proof": paired_peer_workload_proof,
        "workload_stdout": str(stdout_path),
        "workload_stdout_sha256": sha256(stdout_path),
        "workload_stderr": str(stderr_path),
        "workload_stderr_sha256": sha256(stderr_path),
        "qualification_stop_request": qualification_stop_request,
        "workload_exit": {
            "returncode": process.returncode,
            "graceful_within_seconds": 120,
            "forced_termination": False,
        },
        "debug_only": True,
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
        "concurrent_capture_r0_or_debug_authorized": False,
        "bidirectional_comparison_still_required": True,
        "main_agent_token_not_persisted": True,
    }
    atomic_json(output, result)
    return result



def read_main_agent_token_file(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise QualificationError("main-agent token file must be regular")
    state = path.stat()
    if stat.S_IMODE(state.st_mode) != 0o600 or state.st_uid != os.geteuid():
        raise QualificationError("main-agent token file ownership/mode differs")
    token = path.read_text(encoding="ascii").strip()
    if len(token) < 32 or any(character.isspace() for character in token):
        raise QualificationError("main-agent token file content differs")
    return token

def bundle_from_script():
    # In a successor this file is qualification/<name>.py.
    return Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--collect-only", action="store_true")
    modes.add_argument("--qualification-solo", action="store_true")
    modes.add_argument("--qualification-paired", action="store_true")
    modes.add_argument("--compare-bidirectional", action="store_true")
    parser.add_argument("--bundle", type=Path, default=bundle_from_script())
    parser.add_argument("--container")
    parser.add_argument("--mysql-binary", type=Path, default=Path("/usr/bin/mysql"))
    parser.add_argument("--mysql-client-container")
    parser.add_argument(
        "--mysql-binary-in-client", default="/usr/bin/mysql"
    )
    parser.add_argument("--mysql-defaults-file", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--main-agent-token-file", type=Path)
    parser.add_argument("--main-agent-authority", type=Path)
    parser.add_argument("--command-spec", type=Path)
    parser.add_argument("--peer-readback", type=Path)
    parser.add_argument("--peer-container")
    parser.add_argument("--a-solo-evidence", type=Path)
    parser.add_argument("--a-paired-evidence", type=Path)
    parser.add_argument("--b-solo-evidence", type=Path)
    parser.add_argument("--b-paired-evidence", type=Path)
    parser.add_argument("--gate-scenario")
    parser.add_argument("--host-id")
    parser.add_argument("--attempt-token")
    args = parser.parse_args()
    if args.compare_bidirectional:
        comparison_paths = (
            args.a_solo_evidence,
            args.a_paired_evidence,
            args.b_solo_evidence,
            args.b_paired_evidence,
        )
        if any(path is None for path in comparison_paths):
            raise QualificationError(
                "bidirectional comparison requires four evidence paths"
            )
        if (
            not args.gate_scenario
            or not args.host_id
            or not args.attempt_token
        ):
            raise QualificationError(
                "canonical dynamic gate requires scenario, host ID, "
                "and promotion attempt token"
            )
        if any(
            value is not None
            for value in (
                args.container,
                args.mysql_defaults_file,
                args.main_agent_token_file,
                args.main_agent_authority,
                args.command_spec,
                args.peer_readback,
                args.peer_container,
            )
        ):
            raise QualificationError(
                "bidirectional comparison cannot launch or inspect data plane"
            )
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.output.name):
            raise QualificationError("dynamic gate output filename is unsafe")
        raw_path = args.output.with_name(
            args.output.name + ".raw_evidence.json"
        )
        raw_value = {
            "schema_version":
                "mysql_algorithm_c_dynamic_gate_raw_evidence_closure_v1",
            "inputs": {
                label: {
                    "path": str(path.resolve()),
                    "sha256": sha256(path),
                    "evidence": load_object(path, label),
                }
                for label, path in zip(
                    ("A_solo", "A_paired", "B_solo", "B_paired"),
                    comparison_paths,
                )
            },
        }
        atomic_json(raw_path, raw_value)
        result = compare_bidirectional_gate(
            *comparison_paths,
            primary_scenario=args.gate_scenario,
            host_id=args.host_id,
            attempt_token=args.attempt_token,
            raw_artifacts={raw_path.name: sha256(raw_path)}
        )
        atomic_json(args.output, result)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if not result["serial_fallback_required"] else 2

    if not args.container or args.mysql_defaults_file is None:
        raise QualificationError(
            "collect/qualification requires container and MySQL defaults file"
        )
    bundle = args.bundle.resolve(strict=True)
    if args.collect_only:
        if any(
            value is not None
            for value in (
                args.main_agent_token_file,
                args.main_agent_authority,
                args.command_spec,
                args.peer_readback,
                args.peer_container,
                args.a_solo_evidence,
                args.a_paired_evidence,
                args.b_solo_evidence,
                args.b_paired_evidence,
                args.gate_scenario,
                args.host_id,
                args.attempt_token,
            )
        ):
            raise QualificationError(
                "collect-only does not accept launch or peer authority"
            )
        result = collect_readback(
            bundle,
            args.container,
            args.mysql_binary,
            args.mysql_defaults_file,
            args.mysql_client_container,
            args.mysql_binary_in_client,
        )
        atomic_json(args.output, result)
    else:
        if any(
            value is not None
            for value in (
                args.a_solo_evidence,
                args.a_paired_evidence,
                args.b_solo_evidence,
                args.b_paired_evidence,
                args.gate_scenario,
                args.host_id,
                args.attempt_token,
            )
        ):
            raise QualificationError(
                "qualification cannot accept comparison evidence"
            )
        if (
            not args.main_agent_token_file
            or args.main_agent_authority is None
            or args.command_spec is None
        ):
            raise QualificationError(
                "qualification requires explicit main-agent token, authority, "
                "and command spec"
            )
        main_agent_token = read_main_agent_token_file(
            args.main_agent_token_file
        )
        mode = (
            "qualification_solo"
            if args.qualification_solo
            else "qualification_paired"
        )
        result = run_qualification(
            bundle,
            mode,
            args.container,
            args.mysql_binary,
            args.mysql_defaults_file,
            args.output,
            args.command_spec,
            args.main_agent_authority,
            main_agent_token,
            mysql_client_container=args.mysql_client_container,
            mysql_binary_in_client=args.mysql_binary_in_client,
            peer_readback_path=args.peer_readback,
            peer_container=args.peer_container,
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except QualificationError as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)
