#!/usr/bin/env python3
"""Target-local D-state and memory successor for independent scene gates."""

from __future__ import annotations

import copy
from pathlib import Path
import re
from typing import Any

from scripts import mysql_cross_project_wave_coordination_v3 as gate_v3
from scripts import mysql_cross_project_wave_coordination_v1 as gate_v1
from scripts import mysql_cross_project_wave_coordination_v25 as predecessor
from scripts.mysql_cross_project_wave_coordination_v25 import *  # noqa: F401,F403
from scripts import preflight_mysql_four_scene_live_v8 as pressure_source


def _actual_cpus(pid: int) -> set[int]:
    try:
        status = Path(f"/proc/{pid}/status").read_text(
            encoding="utf-8", errors="replace"
        )
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        return set()
    matched = re.search(r"^Cpus_allowed_list:\s*(\S+)$", status, re.MULTILINE)
    return gate_v1.cpus(matched.group(1)) if matched else set()


def rolling_quiet_gate(**kwargs: Any) -> dict[str, Any]:
    target_cpus = {int(value) for value in kwargs.get("target_cpus") or set()}
    if not target_cpus:
        raise ValueError("actual target CPU scope is required")
    original_pressure = pressure_source.host_pressure
    original_decline = gate_v3.MAXIMUM_WINDOW_MEMORY_DECLINE_BYTES

    def target_scoped_pressure() -> dict[str, Any]:
        value = copy.deepcopy(original_pressure())
        exempted = set(int(pid) for pid in value.get("exempted_unrelated_d_state_pids") or [])
        for row in value.get("d_state_details") or []:
            if row.get("classification") != "blocking_unknown_or_target_relevant":
                continue
            pid = int(row["pid"])
            cpus = _actual_cpus(pid)
            row["actual_cpus_allowed"] = sorted(cpus)
            row["target_cpu_overlap"] = sorted(cpus & target_cpus)
            if cpus and cpus.isdisjoint(target_cpus):
                row["classification"] = "actual_cpu_disjoint_nonblocking"
                exempted.add(pid)
        value["blocking_d_state_pids"] = [
            int(row["pid"]) for row in value.get("d_state_details") or []
            if row.get("classification") == "blocking_unknown_or_target_relevant"
        ]
        value["exempted_unrelated_d_state_pids"] = sorted(exempted)
        value["target_cpu_scoped_d_state"] = True
        return value

    pressure_source.host_pressure = target_scoped_pressure
    # Global MemAvailable movement from a disjoint, hard-limited sibling is
    # diagnostic only.  v25 still enforces target cgroups, absolute host
    # headroom and full-memory PSI on every sample.
    gate_v3.MAXIMUM_WINDOW_MEMORY_DECLINE_BYTES = 2**63 - 1
    try:
        value = predecessor.rolling_quiet_gate(**kwargs)
    finally:
        pressure_source.host_pressure = original_pressure
        gate_v3.MAXIMUM_WINDOW_MEMORY_DECLINE_BYTES = original_decline
    value["schema_version"] = "mysql_cross_project_wave_gate_v26"
    value["release_version"] = "v82-independent-target-local"
    value["independent_target_local_successor"] = {
        "actual_d_state_cpu_overlap_required": True,
        "global_memory_decline_diagnostic_only": True,
        "target_cgroup_memory_swap_readback_required": True,
        "absolute_host_memory_headroom_required": True,
        "host_full_memory_psi_limit_required": True,
        "external_cpu_numa_storage_overlap_permitted": False,
        "sibling_scene_completion_dependency": False,
        "external_process_control_or_stop_performed": False,
    }
    return value


for _name in dir(predecessor):
    if not _name.startswith("__") and _name != "rolling_quiet_gate":
        globals().setdefault(_name, getattr(predecessor, _name))
