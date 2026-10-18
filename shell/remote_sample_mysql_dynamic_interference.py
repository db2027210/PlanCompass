#!/usr/bin/env python3
"""Emit seven JSON snapshots over six intervals on an experiment host.

This file deliberately remains compatible with the Python 3.6 installations
on the remote hosts.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import subprocess
import time


DATABASE_COMMS = {"mysqld", "postgres"}
DRIVER_COMMS = {"gsql", "psql", "sysbench", "mysqlslap", "java"}
DRIVER_MARKERS = (
    "hybench",
    "benchbase",
    "tpch-seed-agent",
    "candidate_once",
    "ranked_replays",
    "outer_supervisor",
    "parameter_optimizer",
    "batch_collector",
)


def status_value(pid, key):
    try:
        for line in Path("/proc/{}/status".format(pid)).read_text().splitlines():
            if line.startswith(key + ":"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return ""


def command(pid):
    try:
        return (
            Path("/proc/{}/cmdline".format(pid))
            .read_bytes()
            .replace(b"\0", b" ")
            .decode("utf-8", "replace")
            .strip()
        )
    except OSError:
        return ""


def comm(pid):
    try:
        return Path("/proc/{}/comm".format(pid)).read_text().strip()
    except OSError:
        return ""


def process_owner(pid):
    try:
        return pwd.getpwuid(Path("/proc/{}".format(pid)).stat().st_uid).pw_name
    except (KeyError, OSError):
        return ""


def cpu_ticks(pid):
    try:
        raw = Path("/proc/{}/stat".format(pid)).read_text()
        suffix = raw[raw.rfind(")") + 2 :].split()
        return int(suffix[11]) + int(suffix[12])
    except (OSError, ValueError, IndexError):
        return None


def io_bytes(pid):
    result = {"read_bytes": None, "write_bytes": None}
    try:
        for line in Path("/proc/{}/io".format(pid)).read_text().splitlines():
            key, value = line.split(":", 1)
            if key in result:
                result[key] = int(value.strip())
    except (OSError, ValueError):
        pass
    return result


def process_rows():
    databases = []
    drivers = []
    for path in Path("/proc").iterdir():
        if not path.name.isdigit():
            continue
        pid = int(path.name)
        name = comm(pid)
        cmd = command(pid)
        lower = cmd.lower()
        is_database_healthcheck = (
            name == "pg_isready" or "pg_isready" in lower
        )
        if name in DATABASE_COMMS:
            io = io_bytes(pid)
            databases.append(
                {
                    "pid": pid,
                    "owner": process_owner(pid),
                    "comm": name,
                    "command_sha256": hashlib.sha256(
                        cmd.encode("utf-8")
                    ).hexdigest(),
                    "cpus_allowed_list": status_value(pid, "Cpus_allowed_list"),
                    "mems_allowed_list": status_value(pid, "Mems_allowed_list"),
                    "cpu_ticks": cpu_ticks(pid),
                    "read_bytes": io["read_bytes"],
                    "write_bytes": io["write_bytes"],
                }
            )
        elif not is_database_healthcheck and (
            name in DRIVER_COMMS
            or any(marker in lower for marker in DRIVER_MARKERS)
        ):
            drivers.append(
                {
                    "pid": pid,
                    "owner": process_owner(pid),
                    "comm": name,
                    "command_sha256": hashlib.sha256(
                        cmd.encode("utf-8")
                    ).hexdigest(),
                    "cpus_allowed_list": status_value(pid, "Cpus_allowed_list"),
                    "mems_allowed_list": status_value(pid, "Mems_allowed_list"),
                }
            )
    return (
        sorted(databases, key=lambda row: row["pid"]),
        sorted(drivers, key=lambda row: row["pid"]),
    )


def system_cpu_ticks():
    try:
        fields = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
        return sum(int(value) for value in fields)
    except (OSError, ValueError, IndexError):
        return None


def containers():
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
        ["docker", "inspect"] + ids,
        universal_newlines=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if inspected.returncode != 0:
        return [{"inventory_error": inspected.stderr.strip()}]
    rows = []
    for value in json.loads(inspected.stdout):
        host = value.get("HostConfig") or {}
        state = value.get("State") or {}
        rows.append(
            {
                "id": str(value.get("Id") or ""),
                "name": str(value.get("Name") or "").lstrip("/"),
                "status": state.get("Status"),
                "pid": state.get("Pid"),
                "cpuset_cpus": host.get("CpusetCpus"),
                "cpuset_mems": host.get("CpusetMems"),
                "memory": host.get("Memory"),
                "memory_swap": host.get("MemorySwap"),
                "pids_limit": host.get("PidsLimit"),
            }
        )
    return sorted(rows, key=lambda row: row["name"])


def snapshot(sequence):
    databases, drivers = process_rows()
    try:
        loadavg = Path("/proc/loadavg").read_text().strip()
    except OSError:
        loadavg = ""
    return {
        "schema_version": "mysql_dynamic_interference_remote_sample_v1",
        "sequence": sequence,
        "epoch_seconds": time.time(),
        "monotonic_seconds": time.monotonic(),
        "hostname": os.uname().nodename,
        "online_cpu_count": len(os.sched_getaffinity(0)),
        "system_cpu_ticks": system_cpu_ticks(),
        "loadavg": loadavg,
        "database_processes": databases,
        "driver_processes": drivers,
        "running_containers": containers(),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval-seconds", type=int, default=30)
    parser.add_argument("--interval-count", type=int, default=6)
    args = parser.parse_args()
    if args.interval_seconds < 1 or args.interval_count < 1:
        return 2
    deadline = time.monotonic()
    for sequence in range(args.interval_count + 1):
        print(
            json.dumps(snapshot(sequence), ensure_ascii=False, sort_keys=True),
            flush=True,
        )
        if sequence == args.interval_count:
            break
        deadline += args.interval_seconds
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
