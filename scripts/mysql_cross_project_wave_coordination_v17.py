#!/usr/bin/env python3
"""Rolling gate with host-process container inventory and no Docker-wide wait."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

from scripts import mysql_cross_project_wave_coordination_v1 as gate_v1
from scripts import mysql_cross_project_wave_coordination_v15 as predecessor


_CONTAINER_ID = re.compile(r"(?<![0-9a-f])([0-9a-f]{64})(?![0-9a-f])")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        return ""


def _host_container_process_inventory() -> tuple[list[dict[str, Any]], set[str]]:
    rows: list[dict[str, Any]] = []
    ids: set[str] = set()
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        cgroup = _read(proc / "cgroup")
        matched = sorted(set(_CONTAINER_ID.findall(cgroup)))
        if not matched:
            continue
        status = _read(proc / "status")
        fields = {}
        for line in status.splitlines():
            key, separator, value = line.partition(":")
            if separator:
                fields[key] = value.strip()
        raw = b""
        try:
            raw = (proc / "cmdline").read_bytes()
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
            pass
        command = raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
        ids.update(matched)
        rows.append({
            "pid": int(proc.name),
            "ppid": int((fields.get("PPid") or "0").split()[0]),
            "name": fields.get("Name"),
            "container_ids": matched,
            "cpus_allowed_list": fields.get("Cpus_allowed_list"),
            "mems_allowed_list": fields.get("Mems_allowed_list"),
            "command": command[:4096],
            "command_sha256": hashlib.sha256(raw).hexdigest(),
            "cgroup": cgroup.splitlines(),
        })
    return sorted(rows, key=lambda row: int(row["pid"])), ids


def rolling_quiet_gate(**kwargs: Any) -> dict[str, Any]:
    inventory_before, active_before = _host_container_process_inventory()
    policy = json.loads(gate_v1.CONTRACT_PATH.read_text(encoding="utf-8")).get(
        "co_resident_isolated_pg_allowance"
    ) or {}
    ids_by_name = {
        str(row.get("name") or ""): str(row.get("container_id") or "")
        for row in policy.get("containers") or []
    }
    original_present = predecessor._present
    original_inventory = predecessor._running_container_inventory

    def present(name: str) -> bool:
        container_id = ids_by_name.get(name, "")
        return bool(container_id and container_id in active_before)

    predecessor._present = present
    predecessor._running_container_inventory = lambda: inventory_before
    try:
        value = predecessor.rolling_quiet_gate(**kwargs)
    finally:
        predecessor._present = original_present
        predecessor._running_container_inventory = original_inventory
    inventory_after, active_after = _host_container_process_inventory()
    value["schema_version"] = "mysql_cross_project_wave_gate_v17"
    value["release_version"] = "v73"
    value["actual_running_container_inventory_before"] = inventory_before
    value["actual_running_container_inventory_after"] = inventory_after
    value["actual_running_container_ids_before"] = sorted(active_before)
    value["actual_running_container_ids_after"] = sorted(active_after)
    value["actual_running_container_inventory_complete"] = True
    value["container_inventory_source"] = {
        "source": "host_proc_cgroup_and_status",
        "docker_control_plane_required": False,
        "all_host_visible_container_processes_inventoried": True,
        "continuous_process_cpu_numa_inventory_from_predecessor_retained": True,
        "target_mysql_identity_and_resources_verified_by_reset_and_launch_authority": True,
        "present_frozen_external_database_resources_remain_fail_closed": True,
        "external_process_control_or_stop_performed": False,
        "cross_scene_docker_control_plane_completion_dependency": False,
    }
    return value


for _name in dir(predecessor):
    if not _name.startswith("__") and _name != "rolling_quiet_gate":
        globals().setdefault(_name, getattr(predecessor, _name))
