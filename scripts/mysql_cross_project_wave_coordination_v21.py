#!/usr/bin/env python3
"""Bounded process inventory that never reads cmdline from D-state tasks."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
from typing import Any

from scripts import mysql_cross_project_wave_coordination_v1 as gate_v1
from scripts import mysql_cross_project_wave_coordination_v17 as gate_v17
from scripts import mysql_cross_project_wave_coordination_v20 as predecessor


_D_STATE_WORKLOAD_NAMES = {"postgres", "psql", "pgbench", "java", "docker"}


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        return ""


def _status(path: Path) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in _text(path).splitlines():
        key, separator, value = line.partition(":")
        if separator:
            fields[key] = value.strip()
    return fields


def _safe_external_process_inventory(
    run_id: str, target_mems: set[int], *, ignored_pids: set[int] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {"controllers": [], "workloads": []}
    ignored = set(ignored_pids or ())
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) in ignored:
            continue
        fields = _status(entry / "status")
        if not fields:
            continue
        state = str(fields.get("State") or "")[:1]
        name = str(fields.get("Name") or "")
        mems_text = str(fields.get("Mems_allowed_list") or "")
        overlap = target_mems & gate_v1.cpus(mems_text)
        if not overlap:
            continue
        if state == "D":
            if name not in _D_STATE_WORKLOAD_NAMES:
                continue
            command = f"{name} <cmdline_not_read_because_d_state>"
            kind = "workload"
        else:
            try:
                raw = (entry / "cmdline").read_bytes()
            except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
                continue
            command = raw.replace(b"\0", b" ").decode("utf-8", errors="replace").strip()
            kind = gate_v1.command_kind(command)
            if not command or run_id in command or kind is None:
                continue
        row = {
            "pid": int(entry.name),
            "ppid": int(str(fields.get("PPid") or "0").split()[0]),
            "command": command,
            "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
            "cpus_allowed_list": fields.get("Cpus_allowed_list") or "",
            "mems_allowed_list": mems_text,
            "overlap_mems": sorted(overlap),
            "kind": kind,
            "process_state": state,
            "d_state_cmdline_read_prohibited": state == "D",
        }
        result[f"{kind}s"].append(row)
    for rows in result.values():
        rows.sort(key=lambda row: int(row["pid"]))
    return result


def _safe_host_container_process_inventory() -> tuple[list[dict[str, Any]], set[str]]:
    rows: list[dict[str, Any]] = []
    ids: set[str] = set()
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        cgroup = _text(proc / "cgroup")
        matched = sorted(set(gate_v17._CONTAINER_ID.findall(cgroup)))
        if not matched:
            continue
        fields = _status(proc / "status")
        if not fields:
            continue
        state = str(fields.get("State") or "")[:1]
        raw = b""
        if state != "D":
            try:
                raw = (proc / "cmdline").read_bytes()
            except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
                pass
        command = (
            raw.replace(b"\0", b" ").decode("utf-8", errors="replace").strip()
            if raw else f"{fields.get('Name') or ''} <cmdline_not_read_because_d_state>"
        )
        ids.update(matched)
        rows.append({
            "pid": int(proc.name),
            "ppid": int(str(fields.get("PPid") or "0").split()[0]),
            "name": fields.get("Name"),
            "container_ids": matched,
            "cpus_allowed_list": fields.get("Cpus_allowed_list"),
            "mems_allowed_list": fields.get("Mems_allowed_list"),
            "command": command[:4096],
            "command_sha256": hashlib.sha256(raw or command.encode()).hexdigest(),
            "cgroup": cgroup.splitlines(),
            "process_state": state,
            "d_state_cmdline_read_prohibited": state == "D",
        })
    return sorted(rows, key=lambda row: int(row["pid"])), ids


def rolling_quiet_gate(**kwargs: Any) -> dict[str, Any]:
    original_external = gate_v1.external_process_inventory
    original_container = gate_v17._host_container_process_inventory
    gate_v1.external_process_inventory = _safe_external_process_inventory
    gate_v17._host_container_process_inventory = _safe_host_container_process_inventory
    try:
        value = predecessor.rolling_quiet_gate(**kwargs)
    finally:
        gate_v1.external_process_inventory = original_external
        gate_v17._host_container_process_inventory = original_container
    value["schema_version"] = "mysql_cross_project_wave_gate_v21"
    value["release_version"] = "v77"
    value["bounded_proc_inventory_successor"] = {
        "status_read_before_cmdline": True,
        "d_state_cmdline_read_prohibited": True,
        "d_state_database_or_benchmark_processes_recorded_fail_closed": True,
        "unrelated_d_state_kernel_or_system_processes_not_classified_as_workloads": True,
        "external_process_control_or_stop_performed": False,
    }
    return value


for _name in dir(predecessor):
    if not _name.startswith("__") and _name != "rolling_quiet_gate":
        globals().setdefault(_name, getattr(predecessor, _name))
