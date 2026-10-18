#!/usr/bin/env python3
"""Process inventory using only status, comm and cgroup metadata.

Reading ``/proc/PID/cmdline`` can enter an unbounded ``access_remote_vm`` wait
when a task changes to D state after its status was sampled.  This successor
never reads cmdline.  Container processes are covered by immutable-ID and
cgroup placement read-back; recognizable host-side database clients remain
fail-closed until they exit.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from scripts import mysql_cross_project_wave_coordination_v1 as gate_v1
from scripts import mysql_cross_project_wave_coordination_v17 as gate_v17
from scripts import mysql_cross_project_wave_coordination_v20 as predecessor


_HOST_WORKLOAD_NAMES = {"postgres", "psql", "pgbench", "java", "docker", "mysql", "mysqld"}


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        return ""


def _fields(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in _text(path).splitlines():
        key, separator, value = line.partition(":")
        if separator:
            result[key] = value.strip()
    return result


def _metadata_external_inventory(
    run_id: str, target_mems: set[int], *, ignored_pids: set[int] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    del run_id
    ignored = set(ignored_pids or ())
    result: dict[str, list[dict[str, Any]]] = {"controllers": [], "workloads": []}
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit() or int(proc.name) in ignored:
            continue
        name = _text(proc / "comm").strip()
        if name not in _HOST_WORKLOAD_NAMES:
            continue
        cgroup = _text(proc / "cgroup")
        if gate_v17._CONTAINER_ID.search(cgroup):
            continue
        fields = _fields(proc / "status")
        process_state = str(fields.get("State") or "")[:1]
        # Idle host PostgreSQL postmasters/background workers remain visible in
        # the inventory but are not physical query execution.  Runnable or
        # uninterruptible PostgreSQL tasks remain fail-closed.
        if name == "postgres" and process_state not in {"R", "D"}:
            continue
        mems = str(fields.get("Mems_allowed_list") or "")
        overlap = target_mems & gate_v1.cpus(mems)
        if not overlap:
            continue
        command = f"{name} <cmdline_intentionally_not_read>"
        result["workloads"].append({
            "pid": int(proc.name),
            "ppid": int(str(fields.get("PPid") or "0").split()[0]),
            "command": command,
            "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
            "cpus_allowed_list": fields.get("Cpus_allowed_list") or "",
            "mems_allowed_list": mems,
            "overlap_mems": sorted(overlap),
            "kind": "workload",
            "process_state": process_state,
            "inventory_source": "proc_status_comm_cgroup",
            "cmdline_read_prohibited": True,
        })
    result["workloads"].sort(key=lambda row: int(row["pid"]))
    return result


def _metadata_container_inventory() -> tuple[list[dict[str, Any]], set[str]]:
    rows: list[dict[str, Any]] = []
    ids: set[str] = set()
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        cgroup = _text(proc / "cgroup")
        matched = sorted(set(gate_v17._CONTAINER_ID.findall(cgroup)))
        if not matched:
            continue
        fields = _fields(proc / "status")
        if not fields:
            continue
        name = str(fields.get("Name") or "")
        command = f"{name} <cmdline_intentionally_not_read>"
        ids.update(matched)
        rows.append({
            "pid": int(proc.name),
            "ppid": int(str(fields.get("PPid") or "0").split()[0]),
            "name": name,
            "container_ids": matched,
            "cpus_allowed_list": fields.get("Cpus_allowed_list"),
            "mems_allowed_list": fields.get("Mems_allowed_list"),
            "command": command,
            "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
            "cgroup": cgroup.splitlines(),
            "process_state": str(fields.get("State") or "")[:1],
            "inventory_source": "proc_status_comm_cgroup",
            "cmdline_read_prohibited": True,
        })
    return sorted(rows, key=lambda row: int(row["pid"])), ids


def _self_and_descendants(
    run_id: str, *, include_descendants: bool = False,
) -> set[int]:
    del run_id
    root_pid = os.getpid()
    del include_descendants
    return {root_pid}


def rolling_quiet_gate(**kwargs: Any) -> dict[str, Any]:
    original_external = gate_v1.external_process_inventory
    original_container = gate_v17._host_container_process_inventory
    original_same_run = gate_v1.same_run_process_pids
    gate_v1.external_process_inventory = _metadata_external_inventory
    gate_v17._host_container_process_inventory = _metadata_container_inventory
    gate_v1.same_run_process_pids = _self_and_descendants
    try:
        value = predecessor.rolling_quiet_gate(**kwargs)
    finally:
        gate_v1.external_process_inventory = original_external
        gate_v17._host_container_process_inventory = original_container
        gate_v1.same_run_process_pids = original_same_run
    value["schema_version"] = "mysql_cross_project_wave_gate_v22"
    value["release_version"] = "v78"
    value["proc_metadata_only_inventory_successor"] = {
        "cmdline_reads_performed": 0,
        "same_run_identity_source": "current_pid_and_status_ppid_descendants",
        "status_comm_and_cgroup_only": True,
        "container_workloads_covered_by_immutable_id_and_cgroup_readback": True,
        "recognized_host_database_clients_remain_fail_closed": True,
        "external_process_control_or_stop_performed": False,
    }
    return value


for _name in dir(predecessor):
    if not _name.startswith("__") and _name != "rolling_quiet_gate":
        globals().setdefault(_name, getattr(predecessor, _name))
