#!/usr/bin/env python3
"""Process-backed fallback for inactive restarting external DB containers.

The Docker list endpoint remains authoritative for container identity.  A
non-target database container whose inspect endpoint times out may be treated
as inactive only when the list snapshot says it is restarting and two direct
/proc cgroup scans prove that it owns no live process.  Intended MySQL worker
containers remain fail-closed on every inspect error.
"""

from __future__ import annotations

import copy
import http.client
import json
from pathlib import Path
import re
import time
from typing import Any
from urllib.parse import quote

from scripts import preflight_mysql_four_scene_v5 as predecessor


_process_inventory = predecessor._process_inventory
_cpus = predecessor._cpus
_compress = predecessor._compress
DATABASE_WORKLOAD_COMMAND = re.compile(
    r"(?:^|/)(?:gaussdb|mongod|mysqld|postgres|redis-server)(?:\s|$)",
    re.I,
)


def _container_process_scan(container_id: str) -> dict[str, Any]:
    identities = {container_id, container_id[:12]}
    processes: list[dict[str, Any]] = []
    unreadable_cgroups: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cgroup = (entry / "cgroup").read_text(
                encoding="utf-8", errors="replace",
            )
        except FileNotFoundError:
            continue
        except (PermissionError, OSError):
            unreadable_cgroups.append(int(entry.name))
            continue
        if not any(identity and identity in cgroup for identity in identities):
            continue
        try:
            command = (
                (entry / "cmdline").read_bytes().replace(b"\0", b" ")
                .decode("utf-8", errors="replace").strip()
            )
            status = (entry / "status").read_text(
                encoding="utf-8", errors="replace",
            )
        except FileNotFoundError:
            continue
        allowed = re.search(
            r"^Cpus_allowed_list:\s*(\S+)$", status, re.MULTILINE,
        )
        mems = re.search(
            r"^Mems_allowed_list:\s*(\S+)$", status, re.MULTILINE,
        )
        processes.append({
            "pid": int(entry.name),
            "command": command,
            "cpus_allowed_list": allowed.group(1) if allowed else "",
            "mems_allowed_list": mems.group(1) if mems else "",
            "cgroup": cgroup.strip(),
        })
    return {
        "container_id": container_id,
        "processes": sorted(processes, key=lambda row: int(row["pid"])),
        "unreadable_cgroup_pids": sorted(unreadable_cgroups),
        "scan_complete": not unreadable_cgroups,
    }


def _database_workload_processes(scan: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        copy.deepcopy(row) for row in scan.get("processes") or []
        if DATABASE_WORKLOAD_COMMAND.search(str(row.get("command") or ""))
    ]


def _inactive_restart_proof(container_id: str) -> dict[str, Any]:
    started = time.monotonic()
    deadline = started + 45.0
    attempt_count = 0
    while True:
        attempt_count += 1
        first = _container_process_scan(container_id)
        time.sleep(0.25)
        second = _container_process_scan(container_id)
        first_database_processes = _database_workload_processes(first)
        second_database_processes = _database_workload_processes(second)
        passed = bool(
            first["scan_complete"]
            and second["scan_complete"]
            and not first_database_processes
            and not second_database_processes
        )
        if passed or time.monotonic() >= deadline:
            break
        time.sleep(0.25)
    return {
        "method": "two_direct_proc_cgroup_scans",
        "interval_seconds": 0.25,
        "maximum_wait_seconds": 45.0,
        "wait_seconds": round(time.monotonic() - started, 6),
        "attempt_count": attempt_count,
        "first": first,
        "second": second,
        "first_database_workload_processes": first_database_processes,
        "second_database_workload_processes": second_database_processes,
        "passed": passed,
    }


def _docker_inventory(
    target_cpus: set[int], intended_names: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    intended_names = {name.removeprefix("/") for name in intended_names}
    inventory: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    with predecessor._snapshot_lock() as lock_evidence:
        summaries = None
        list_errors: list[str] = []
        for _attempt in range(1, 6):
            try:
                summaries = predecessor._docker_get(
                    "/containers/json?all=0", timeout=2.0,
                )
                break
            except (
                OSError,
                TimeoutError,
                http.client.HTTPException,
                json.JSONDecodeError,
                RuntimeError,
            ) as error:
                list_errors.append(f"{type(error).__name__}: {error}")
                time.sleep(0.2)
        if summaries is None:
            raise TimeoutError(
                "Docker API container list exhausted five short retries: "
                + list_errors[-1]
            )
        if not isinstance(summaries, list):
            raise RuntimeError("Docker API container list is not an array")
        for summary in summaries:
            if not isinstance(summary, dict):
                raise RuntimeError("Docker API container summary is not an object")
            name = predecessor._summary_name(summary)
            container_id = str(summary.get("Id") or "")
            database_like = predecessor._summary_database_like(summary)
            summary_state = str(summary.get("State") or "").lower()

            # Inspect is known to block for containers caught in this restart
            # loop.  Prove process inactivity first instead of spending the
            # old 15-second per-container management timeout.
            if (
                summary_state == "restarting"
                and database_like
                and name not in intended_names
                and container_id
            ):
                proof = _inactive_restart_proof(container_id)
                row = {
                    "name": name,
                    "container_id": container_id,
                    "pid": None,
                    "command": str(summary.get("Command") or ""),
                    "image": str(summary.get("Image") or ""),
                    "cpuset_cpus": None,
                    "cpuset_mems": None,
                    "overlap_cpus": "",
                    "disk2_bind_sources": [],
                    "docker_api_mount_summary": summary.get("Mounts") or [],
                    "database_like": True,
                    "intended_successor_container": False,
                    "inspect_status": "SKIPPED_RESTARTING_PROCESS_PROOF_USED",
                    "summary_state": summary.get("State"),
                    "summary_status": summary.get("Status"),
                    "inventory_source": "docker_unix_socket_api_and_proc_cgroup",
                    "snapshot_lock": dict(lock_evidence),
                    "inactive_restart_proof": proof,
                    "inspect_fallback_status": (
                        "PASS_RESTARTING_NO_LIVE_CGROUP_PROCESS"
                        if proof["passed"] is True
                        else "FAIL_RESTARTING_PROCESS_PROOF"
                    ),
                    "runtime_process_watch_required": True,
                    "inspect_failure_ignored": False,
                    "inspect_failure_replaced_by_process_liveness_proof": True,
                }
                inventory.append(row)
                if proof["passed"] is not True:
                    blockers.append({
                        **row,
                        "reason": "restarting_database_has_live_or_unreadable_cgroup_process",
                    })
                continue

            try:
                if not container_id:
                    raise RuntimeError("Docker API summary has no container ID")
                document = predecessor._docker_get(
                    f"/containers/{quote(container_id, safe='')}/json",
                    timeout=5.0,
                )
                if not isinstance(document, dict):
                    raise RuntimeError("Docker API inspect result is not an object")
            except (
                OSError,
                TimeoutError,
                http.client.HTTPException,
                json.JSONDecodeError,
                RuntimeError,
            ) as error:
                summary_mounts = summary.get("Mounts") or []
                summary_disk2_sources = sorted(
                    str(mount.get("Source"))
                    for mount in summary_mounts
                    if isinstance(mount, dict)
                    and str(mount.get("Source") or "").startswith("/disk2/")
                )
                running_process_scan = (
                    _container_process_scan(container_id)
                    if container_id else None
                )
                row = {
                    "name": name,
                    "container_id": container_id or None,
                    "pid": None,
                    "command": str(summary.get("Command") or ""),
                    "image": str(summary.get("Image") or ""),
                    "database_like": database_like,
                    "intended_successor_container": name in intended_names,
                    "inspect_status": "TIMEOUT_OR_TRANSIENT_ERROR_SUMMARY_RECORDED",
                    "inspect_error_type": type(error).__name__,
                    "summary_state": summary.get("State"),
                    "summary_status": summary.get("Status"),
                    "inventory_source": "docker_unix_socket_api",
                    "snapshot_lock": dict(lock_evidence),
                    "disk2_bind_sources": summary_disk2_sources,
                    "process_scan": running_process_scan,
                }
                inventory.append(row)
                running_external_database_summary_fallback = bool(
                    name not in intended_names
                    and database_like
                    and summary_state == "running"
                    and not summary_disk2_sources
                    and isinstance(running_process_scan, dict)
                    and running_process_scan.get("scan_complete") is True
                )
                if running_external_database_summary_fallback:
                    row["inspect_fallback_status"] = (
                        "PASS_RUNNING_SUMMARY_NO_TARGET_DISK_PROCESS_INVENTORIED"
                    )
                    row["runtime_process_watch_required"] = True
                    row["inspect_failure_ignored"] = False
                    row["inspect_failure_replaced_by_summary_mount_and_process_proof"] = True
                elif name in intended_names or database_like:
                    blockers.append({
                        **row,
                        "reason": "database_or_intended_container_inspect_failed_closed",
                    })
                continue

            host = document.get("HostConfig") or {}
            mounts = document.get("Mounts") or []
            cpuset_text = str(host.get("CpusetCpus") or "0-511")
            overlap = target_cpus & _cpus(cpuset_text)
            disk2_sources = sorted(
                str(mount.get("Source"))
                for mount in mounts
                if str(mount.get("Source") or "").startswith("/disk2/")
            )
            command = " ".join([
                str(document.get("Path") or ""),
                *map(str, document.get("Args") or []),
            ]).strip()
            database_like = bool(re.search(
                r"(?:mysqld|postgres|gaussdb|mongod|redis-server)",
                command, re.I,
            )) or database_like
            row = {
                "name": name,
                "container_id": document.get("Id"),
                "pid": (document.get("State") or {}).get("Pid"),
                "command": command,
                "cpuset_cpus": cpuset_text,
                "cpuset_mems": str(host.get("CpusetMems") or ""),
                "overlap_cpus": _compress(overlap),
                "disk2_bind_sources": disk2_sources,
                "database_like": database_like,
                "intended_successor_container": name in intended_names,
                "inspect_status": "PASS",
                "inventory_source": "docker_unix_socket_api",
                "snapshot_lock": dict(lock_evidence),
            }
            inventory.append(row)
            if name not in intended_names and database_like and disk2_sources:
                blockers.append({
                    **row,
                    "reason": "co_resident_database_uses_target_disk2",
                })
    return inventory, blockers


__all__ = [
    "_compress", "_cpus", "_database_workload_processes",
    "_docker_inventory", "_process_inventory",
]
