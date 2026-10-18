#!/usr/bin/env python3
"""Capture a JSON inventory for MySQL-only static isolation verification."""

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import pwd
import subprocess


PROCESS_MARKERS = (
    "mysqld",
    "postgres",
    "gsql",
    "psql",
    "sysbench",
    "mysqlslap",
    "hybench",
    "tpch",
    "tpcc",
    "benchmark",
    "candidate_once",
    "ranked_replays",
    "outer_supervisor",
    "parameter_optimizer",
    "batch_collector",
)


def read_status(pid, key):
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith(key + ":"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return ""


def read_cmdline(pid):
    try:
        return (
            Path(f"/proc/{pid}/cmdline")
            .read_bytes()
            .replace(b"\0", b" ")
            .decode("utf-8", "replace")
            .strip()
        )
    except OSError:
        return ""


def read_comm(pid):
    try:
        return Path(f"/proc/{pid}/comm").read_text().strip()
    except OSError:
        return ""


def owner(pid):
    try:
        return pwd.getpwuid(Path(f"/proc/{pid}").stat().st_uid).pw_name
    except (KeyError, OSError):
        return ""


def numa_pages(pid):
    result = {}
    try:
        lines = Path(f"/proc/{pid}/numa_maps").read_text().splitlines()
    except OSError:
        return result
    for line in lines:
        for field in line.split():
            if field.startswith("N") and "=" in field:
                name, raw = field.split("=", 1)
                if name[1:].isdigit() and raw.isdigit():
                    result[name] = result.get(name, 0) + int(raw)
    return result


def process_inventory():
    rows = []
    for path in Path("/proc").iterdir():
        if not path.name.isdigit():
            continue
        pid = int(path.name)
        command = read_cmdline(pid)
        name = read_comm(pid)
        searchable = f"{name} {command}".lower()
        if not any(marker in searchable for marker in PROCESS_MARKERS):
            continue
        rows.append(
            {
                "pid": pid,
                "owner": owner(pid),
                "comm": name,
                "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
                "cpus_allowed_list": read_status(pid, "Cpus_allowed_list"),
                "mems_allowed_list": read_status(pid, "Mems_allowed_list"),
                "numa_pages": numa_pages(pid),
            }
        )
    return sorted(rows, key=lambda row: row["pid"])


def docker_inventory():
    listed = subprocess.run(
        ["docker", "ps", "-q"],
        universal_newlines=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    ids = [line for line in listed.stdout.splitlines() if line]
    if not ids:
        return []
    inspected = subprocess.run(
        ["docker", "inspect", *ids],
        universal_newlines=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if inspected.returncode != 0:
        return [{"inventory_error": inspected.stderr.strip()}]
    values = json.loads(inspected.stdout)
    rows = []
    for value in values:
        host = value.get("HostConfig") or {}
        state = value.get("State") or {}
        rows.append(
            {
                "id": str(value.get("Id") or ""),
                "name": str(value.get("Name") or "").lstrip("/"),
                "image": str((value.get("Config") or {}).get("Image") or ""),
                "status": state.get("Status"),
                "pid": state.get("Pid"),
                "cpuset_cpus": host.get("CpusetCpus"),
                "cpuset_mems": host.get("CpusetMems"),
                "cpu_quota": host.get("CpuQuota"),
                "cpu_period": host.get("CpuPeriod"),
                "memory": host.get("Memory"),
                "memory_swap": host.get("MemorySwap"),
                "pids_limit": host.get("PidsLimit"),
                "blkio_read_bps": host.get("BlkioDeviceReadBps"),
                "blkio_write_bps": host.get("BlkioDeviceWriteBps"),
                "mount_sources": sorted(
                    str(item.get("Source") or "")
                    for item in value.get("Mounts") or []
                    if item.get("Source")
                ),
            }
        )
    return sorted(rows, key=lambda row: row["name"])


def storage_inventory(containers):
    paths = {"/", "/var/lib/mysql"}
    for container in containers:
        for source in container.get("mount_sources") or []:
            paths.add(str(source))
    filesystems = []
    for path in sorted(paths):
        try:
            path_exists = Path(path).exists()
        except OSError:
            path_exists = None
        result = subprocess.run(
            ["findmnt", "-T", path, "-n", "-o", "SOURCE,TARGET,FSTYPE"],
            universal_newlines=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        entries = []
        if result.returncode == 0:
            fields = result.stdout.strip().split(None, 2)
            if len(fields) == 3:
                entries = [
                    {
                        "source": fields[0],
                        "target": fields[1],
                        "fstype": fields[2],
                    }
                ]
        filesystems.append(
            {
                "path": path,
                "exists": path_exists,
                "findmnt_returncode": result.returncode,
                "filesystems": entries,
            }
        )
    block_result = subprocess.run(
        [
            "lsblk",
            "--json",
            "-o",
            "NAME,KNAME,PKNAME,TYPE,MAJ:MIN,MOUNTPOINTS",
        ],
        universal_newlines=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    block_devices = {}
    if block_result.returncode == 0:
        try:
            block_devices = json.loads(block_result.stdout)
        except json.JSONDecodeError:
            block_devices = {}
    return {
        "path_filesystems": filesystems,
        "block_devices_returncode": block_result.returncode,
        "block_devices": block_devices,
    }


def main():
    containers = docker_inventory()
    payload = {
        "schema_version": "mysql_static_host_inventory_v1",
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "hostname": os.uname().nodename,
        "online_cpus": sorted(os.sched_getaffinity(0)),
        "processes": process_inventory(),
        "running_containers": containers,
        "storage": storage_inventory(containers),
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
