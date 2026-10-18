#!/usr/bin/env python3
"""Rebind co-resident benchmark/control processes away from MySQL CPUs.

This helper is copied to a selected experiment host and run there.  It does not
stop processes.  It records every before/after affinity and attempts to migrate
existing anonymous pages from NUMA node 1 to node 0.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import pwd
import subprocess
from typing import Any


ROLE_PATTERNS = {
    "host01": (
        "/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/"
        "pg5_common_outer_bgallowed_20260722_v1/"
    ),
    "host02": "/mnt/nvme2n1/anonymous_resource_adaptive/",
    "host04_pg": (
        "/mnt/nvme2n1/anonymous/resource_adaptive/"
        "pg5_common_outer_bgallowed_20260722_v1/tpch_node04/"
    ),
}


def status_value(pid: int, key: str) -> str:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith(key + ":"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return ""


def cmdline(pid: int) -> str:
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


def comm(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/comm").read_text().strip()
    except OSError:
        return ""


def owner(pid: int) -> str:
    try:
        return pwd.getpwuid(Path(f"/proc/{pid}").stat().st_uid).pw_name
    except (KeyError, OSError):
        return ""


def numa_pages(pid: int) -> dict[str, int]:
    totals: dict[str, int] = {}
    try:
        lines = Path(f"/proc/{pid}/numa_maps").read_text().splitlines()
    except OSError:
        return totals
    for line in lines:
        for field in line.split():
            if field.startswith("N") and "=" in field:
                name, raw = field.split("=", 1)
                if name[1:].isdigit() and raw.isdigit():
                    totals[name] = totals.get(name, 0) + int(raw)
    return totals


def selected(pid: int, role: str, current_uid: int) -> bool:
    if pid in {1, os.getpid(), os.getppid()}:
        return False
    command = cmdline(pid)
    name = comm(pid)
    if not command or name in {"dockerd", "containerd", "sshd", "bash", "sh"}:
        return False
    if role in {"host01", "host02"}:
        try:
            if Path(f"/proc/{pid}").stat().st_uid != current_uid:
                return False
        except OSError:
            return False
        return ROLE_PATTERNS[role] in command
    return (
        ROLE_PATTERNS["host04_pg"] in command
        or "model4/src/batch_collector.py" in command
        or name in {"gsql", "psql"}
    )


def run(command: list[str], use_sudo: bool) -> dict[str, Any]:
    argv = ["sudo", "-n", *command] if use_sudo else command
    result = subprocess.run(
        argv,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    return {
        "argv": argv,
        "returncode": result.returncode,
        "stdout_sha256": hashlib.sha256(stdout.encode()).hexdigest(),
        "stdout_line_count": len(stdout.splitlines()),
        "stderr": stderr,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=("host01", "host02", "host04"), required=True)
    parser.add_argument("--cpus", required=True)
    parser.add_argument("--sudo", action="store_true")
    args = parser.parse_args()
    current_uid = os.getuid()
    candidates = sorted(
        int(path.name)
        for path in Path("/proc").iterdir()
        if path.name.isdigit() and selected(int(path.name), args.role, current_uid)
    )
    records: list[dict[str, Any]] = []
    for pid in candidates:
        command = cmdline(pid)
        record: dict[str, Any] = {
            "pid": pid,
            "owner": owner(pid),
            "comm": comm(pid),
            "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
            "before": {
                "cpus_allowed_list": status_value(pid, "Cpus_allowed_list"),
                "mems_allowed_list": status_value(pid, "Mems_allowed_list"),
                "numa_pages": numa_pages(pid),
            },
        }
        record["taskset"] = run(["taskset", "-apc", args.cpus, str(pid)], args.sudo)
        if Path(f"/proc/{pid}").is_dir():
            record["migratepages"] = run(
                ["migratepages", str(pid), "1", "0"],
                args.sudo,
            )
            record["after"] = {
                "cpus_allowed_list": status_value(pid, "Cpus_allowed_list"),
                "mems_allowed_list": status_value(pid, "Mems_allowed_list"),
                "numa_pages": numa_pages(pid),
            }
        records.append(record)
    passed = all(
        row["taskset"]["returncode"] == 0
        and (
            not row.get("after")
            or row["after"].get("cpus_allowed_list") == args.cpus
        )
        for row in records
    )
    payload = {
        "schema_version": "mysql_co_resident_rebind_v1",
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "hostname": os.uname().nodename,
        "role": args.role,
        "target_co_resident_cpus": args.cpus,
        "target_co_resident_numa_node": "0",
        "process_count": len(records),
        "passed": passed,
        "records": records,
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
