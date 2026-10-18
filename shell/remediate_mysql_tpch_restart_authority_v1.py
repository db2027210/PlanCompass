#!/usr/bin/env python3
"""Restore only the frozen TPC-H MySQL cgroup controls after its restart.

This helper is intentionally host/container/PID specific.  It never invokes a
Docker mutation, never addresses a PostgreSQL cgroup, and publishes only one
append-only content-addressed authority directory after live read-back passes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile


HOSTNAME = "node04"
DOCKER_SOCKET_URI = "unix:///run/ra-docker-mysql-tpch-v1.sock"
CONTAINER_NAME = (
    "ra_mysql_tpch_v2_tpch_v2_20260731t144525z_"
    "6c3809b818ae9bbba8e7088_db"
)
CONTAINER_ID = (
    "7c395d290f115dbac9c1146ee8b7599de5967888b19965fa5c114692c1912250"
)
DATABASE_PID = 81330
BUNDLE_ID = "87ed507ed689c103781db5f96332ae7875e4773d04d69b98a7e63e3d4097b257"
ATTEMPT_TOKEN = "tpch-v2-20260731t144525z-6c3809b818ae9bbba8e70884"
DEVICE = "/dev/nvme0n1"
DEVICE_MAJOR_MINOR = "259:1"
READ_BPS = 293601280
WRITE_BPS = 293601280
CPU_QUOTA = 500000
CPU_PERIOD = 100000
CPUSET_CPUS = "18-20,54-56"
CPUSET_MEMS = "1"
MEMORY_BYTES = 6174015488
MEMORY_SWAP_BYTES = 6174015488
PIDS_LIMIT = 512
AUTHORITY_ROOT = Path(
    "/home/anonymous/resource_adaptive_mysql/authorities/"
    "mysql_tpch_restart_blkio_v1"
)


class RemediationError(RuntimeError):
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


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def sha_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def run(argv, label):
    completed = subprocess.run(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        check=False,
        timeout=20,
    )
    if completed.returncode != 0:
        raise RemediationError(
            "%s failed: %s" % (label, completed.stderr.strip())
        )
    return completed.stdout


def process_start_ticks(pid):
    raw = (Path("/proc") / str(pid) / "stat").read_text(encoding="ascii")
    boundary = raw.rfind(") ")
    if boundary < 0:
        raise RemediationError("database stat format differs")
    fields = raw[boundary + 2 :].split()
    value = int(fields[19])
    if value <= 0:
        raise RemediationError("database start ticks differ")
    return value


def cgroup_paths(pid):
    paths = {}
    raw = (Path("/proc") / str(pid) / "cgroup").read_text(encoding="ascii")
    for line in raw.splitlines():
        pieces = line.split(":", 2)
        if len(pieces) != 3:
            raise RemediationError("database cgroup format differs")
        controllers = pieces[1].split(",")
        for controller in controllers:
            paths[controller] = pieces[2]
    expected = "/docker/" + CONTAINER_ID
    if paths.get("blkio") != expected or paths.get("memory") != expected:
        raise RemediationError("database cgroup identity differs")
    return {
        "blkio": Path("/sys/fs/cgroup/blkio") / expected.lstrip("/"),
        "memory": Path("/sys/fs/cgroup/memory") / expected.lstrip("/"),
    }


def assert_no_collector_running():
    own = os.getpid()
    for item in Path("/proc").iterdir():
        if not item.name.isdigit() or int(item.name) == own:
            continue
        try:
            command = (item / "cmdline").read_bytes()
        except (OSError, PermissionError):
            continue
        if b"mysql_tpch_tpcc_shared_collector_entry_v1.py" in command:
            raise RemediationError("a shared qualification collector is running")


def inspect_target():
    raw = run(
        [
            "docker", "--host", DOCKER_SOCKET_URI, "inspect", CONTAINER_NAME,
        ],
        "target container inspect",
    )
    rows = json.loads(raw)
    if not isinstance(rows, list) or len(rows) != 1:
        raise RemediationError("target container inspect cardinality differs")
    row = rows[0]
    state = row.get("State") or {}
    host = row.get("HostConfig") or {}
    if (
        row.get("Id") != CONTAINER_ID
        or row.get("Name") != "/" + CONTAINER_NAME
        or state.get("Running") is not True
        or int(state.get("Pid") or 0) != DATABASE_PID
        or int(host.get("CpuQuota") or 0) != CPU_QUOTA
        or int(host.get("CpuPeriod") or 0) != CPU_PERIOD
        or host.get("CpusetCpus") != CPUSET_CPUS
        or host.get("CpusetMems") != CPUSET_MEMS
        or int(host.get("Memory") or 0) != MEMORY_BYTES
        or int(host.get("MemorySwap") or 0) != MEMORY_SWAP_BYTES
        or int(host.get("PidsLimit") or 0) != PIDS_LIMIT
        or host.get("NetworkMode") != "host"
    ):
        raise RemediationError("target container frozen envelope differs")
    return row


def exact_rate(path, expected):
    rows = []
    for line in path.read_text(encoding="ascii").splitlines():
        fields = line.split()
        if len(fields) != 2:
            raise RemediationError("effective throttle row is malformed")
        rows.append((fields[0], int(fields[1])))
    if rows != [(DEVICE_MAJOR_MINOR, int(expected))]:
        raise RemediationError("effective throttle read-back differs")


def write_cgroup(path, value):
    with path.open("w", encoding="ascii") as handle:
        handle.write(str(value) + "\n")


def verify_storage():
    device = Path(DEVICE)
    state = device.stat()
    observed = "%d:%d" % (os.major(state.st_rdev), os.minor(state.st_rdev))
    if not stat.S_ISBLK(state.st_mode) or observed != DEVICE_MAJOR_MINOR:
        raise RemediationError("target block device identity differs")
    mounted = run(
        [
            "findmnt", "-n", "-o", "SOURCE,FSTYPE,TARGET",
            "--target", "/mnt/nvme0n1",
        ],
        "target storage read-back",
    ).strip().split()
    if len(mounted) != 3 or mounted[0] != DEVICE or mounted[2] != "/mnt/nvme0n1":
        raise RemediationError("target storage mount differs")


def apply_and_readback(paths):
    blkio = paths["blkio"]
    memory = paths["memory"]
    for directory in (blkio, memory):
        if directory.is_symlink() or not directory.is_dir():
            raise RemediationError("target cgroup directory differs")
    read_path = blkio / "blkio.throttle.read_bps_device"
    write_path = blkio / "blkio.throttle.write_bps_device"
    swappiness_path = memory / "memory.swappiness"
    write_cgroup(read_path, "%s %d" % (DEVICE_MAJOR_MINOR, READ_BPS))
    write_cgroup(write_path, "%s %d" % (DEVICE_MAJOR_MINOR, WRITE_BPS))
    write_cgroup(swappiness_path, 0)
    exact_rate(read_path, READ_BPS)
    exact_rate(write_path, WRITE_BPS)
    if swappiness_path.read_text(encoding="ascii").strip() != "0":
        raise RemediationError("effective memory swappiness differs")
    total_swap = None
    for line in (memory / "memory.stat").read_text(encoding="ascii").splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[0] == "total_swap":
            total_swap = int(fields[1])
    if total_swap != 0:
        raise RemediationError("target database has non-zero swap usage")
    if int((memory / "memory.limit_in_bytes").read_text().strip()) != MEMORY_BYTES:
        raise RemediationError("effective memory limit differs")
    if int((memory / "memory.memsw.limit_in_bytes").read_text().strip()) != MEMORY_SWAP_BYTES:
        raise RemediationError("effective memory+swap limit differs")
    return {
        "blkio": blkio,
        "memory": memory,
        "read": read_path,
        "write": write_path,
        "swappiness": swappiness_path,
        "total_swap": total_swap,
    }


def publish(script_sha, live):
    cgroup_path = str(live["blkio"])
    unsigned = {
        "accepted_round_authorized": False,
        "attempt_token": ATTEMPT_TOKEN,
        "bundle_id": BUNDLE_ID,
        "cgroup_path": cgroup_path,
        "cgroup_version": "v1",
        "cross_engine_comparison_prohibited": True,
        "database_container": CONTAINER_NAME,
        "database_container_id": CONTAINER_ID,
        "database_or_client_started": True,
        "database_pid": DATABASE_PID,
        "database_pid_starttime_ticks": process_start_ticks(DATABASE_PID),
        "database_restarted_by_remediation": False,
        "device_major_minor": DEVICE_MAJOR_MINOR,
        "docker_hostconfig_is_declaration_not_effective_authority": True,
        "formal_launch_authorized": False,
        "formal_scope": "mysql_only",
        "host_effective_uid_gid": [0, 0],
        "memory_cgroup_path": str(live["memory"]),
        "memory_swappiness": 0,
        "postgresql_mutation_prohibited": True,
        "postgresql_mutation_performed": False,
        "read_bps": READ_BPS,
        "read_throttle_file": str(live["read"]),
        "remediation_script_sha256": script_sha,
        "schema_version": "mysql_tpch_blkio_v1_throttle_authority_v1",
        "status": "PASS_EFFECTIVE_CGROUP_V1_THROTTLE",
        "storage_mutation_performed": False,
        "total_swap_bytes": live["total_swap"],
        "workload_started": False,
        "write_bps": WRITE_BPS,
        "write_throttle_file": str(live["write"]),
    }
    internal = sha_bytes(canonical(unsigned))
    value = dict(unsigned)
    value["authority_sha256"] = internal
    raw = canonical(value)
    file_sha = sha_bytes(raw)
    destination = AUTHORITY_ROOT / internal
    authority_path = destination / "blkio_throttle_authority.json"
    sidecar_path = destination / "blkio_throttle_authority.json.sha256"
    AUTHORITY_ROOT.mkdir(parents=True, mode=0o700, exist_ok=True)
    if destination.exists():
        if (
            destination.is_symlink()
            or not authority_path.is_file()
            or authority_path.read_bytes() != raw
            or not sidecar_path.is_file()
            or sidecar_path.read_text(encoding="ascii")
            != file_sha + "  blkio_throttle_authority.json\n"
        ):
            raise RemediationError("existing authority destination differs")
    else:
        stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=str(AUTHORITY_ROOT)))
        try:
            staged_authority = stage / authority_path.name
            staged_sidecar = stage / sidecar_path.name
            staged_authority.write_bytes(raw)
            staged_sidecar.write_text(
                file_sha + "  blkio_throttle_authority.json\n",
                encoding="ascii",
            )
            os.chmod(str(staged_authority), 0o400)
            os.chmod(str(staged_sidecar), 0o400)
            os.chmod(str(stage), 0o500)
            os.rename(str(stage), str(destination))
        finally:
            if stage.exists():
                shutil.rmtree(str(stage))
    return {
        "status": "PASS_TPCH_RESTART_CGROUP_AUTHORITY_PUBLISHED",
        "authority_path": str(authority_path),
        "authority_file_sha256": file_sha,
        "authority_sha256": internal,
        "database_container_id": CONTAINER_ID,
        "database_pid": DATABASE_PID,
        "read_bps": READ_BPS,
        "write_bps": WRITE_BPS,
        "memory_swappiness": 0,
        "total_swap_bytes": live["total_swap"],
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "postgresql_mutation_performed": False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-script-sha256", required=True)
    args = parser.parse_args()
    source = Path(__file__).resolve()
    observed_script_sha = sha_file(source)
    if args.expected_script_sha256 != observed_script_sha:
        raise RemediationError("remediation script identity differs")
    if not args.apply:
        raise RemediationError("explicit --apply is required")
    if os.geteuid() != 0 or os.getegid() != 0:
        raise RemediationError("root execution is required")
    if os.uname()[1].split(".", 1)[0] != HOSTNAME:
        raise RemediationError("host identity differs")
    assert_no_collector_running()
    inspect_target()
    verify_storage()
    paths = cgroup_paths(DATABASE_PID)
    live = apply_and_readback(paths)
    print(json.dumps(publish(observed_script_sha, live), indent=2, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (RemediationError, OSError, ValueError, json.JSONDecodeError) as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)
