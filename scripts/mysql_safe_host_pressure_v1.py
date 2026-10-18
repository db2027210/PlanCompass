#!/usr/bin/env python3
"""Bounded host-pressure sampling without /proc/PID/status reads."""

from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Any

from scripts import preflight_mysql_four_scene_live_v8 as legacy


_TARGET_CPUS: set[int] = set()


def configure_target_cpus(values: set[int]) -> None:
    global _TARGET_CPUS
    if not values:
        raise RuntimeError("nonempty target CPU set required")
    _TARGET_CPUS = {int(value) for value in values}


def _cgroup_container_id(entry: Path) -> str | None:
    try:
        value = (entry / "cgroup").read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        return None
    matched = re.search(r"/docker(?:-limited)?/([0-9a-f]{64})(?:$|\n)", value)
    return matched.group(1) if matched else None


def _stat_state_and_name(entry: Path) -> tuple[str, str] | None:
    """Read process state/name from stat, which never enters access_remote_vm."""
    try:
        value = (entry / "stat").read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        return None
    left = value.find("(")
    right = value.rfind(")")
    if left < 0 or right <= left or right + 2 >= len(value):
        return None
    suffix = value[right + 2 :].split(maxsplit=1)
    if not suffix:
        return None
    return suffix[0], value[left + 1 : right]


def host_pressure() -> dict[str, Any]:
    load1 = float(Path("/proc/loadavg").read_text(encoding="ascii").split()[0])
    authority = legacy.unrelated_megasas_poll_authority()
    details: list[dict[str, Any]] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        state_name = _stat_state_and_name(entry)
        if state_name is None or state_name[0] != "D":
            continue
        name = state_name[1]
        classification = legacy.classify_d_state_name(name, authority)
        container_id = _cgroup_container_id(entry)
        try:
            affinity = {int(value) for value in os.sched_getaffinity(int(entry.name))}
        except (ProcessLookupError, PermissionError, OSError):
            affinity = set()
        overlap = sorted(affinity & _TARGET_CPUS)
        if _TARGET_CPUS and affinity and not overlap:
            classification = "observed_cpu_disjoint_d_state_nonblocking"
        elif (
            authority.get("proven") is True
            and re.fullmatch(r"kworker/u\d+:\d+\+flush-8:0", name)
        ):
            classification = "exempt_unrelated_disk3_flush_thread"
        details.append({
            "pid": int(entry.name),
            "name": name,
            "classification": classification,
            "container_id": container_id,
            "cpus_allowed_list": sorted(affinity),
            "overlap_target_cpus": overlap,
        })
    details.sort(key=lambda row: int(row["pid"]))
    return {
        "load1": load1,
        "d_state_pids": [row["pid"] for row in details],
        "blocking_d_state_pids": [
            row["pid"] for row in details
            if row["classification"] == "blocking_unknown_or_target_relevant"
        ],
        "exempted_unrelated_d_state_pids": [
            row["pid"] for row in details
            if row["classification"] in {
                "exempt_unrelated_disk3_megasas_status_poll",
                "exempt_unrelated_disk3_flush_thread",
                "observed_cpu_disjoint_d_state_nonblocking",
            }
        ],
        "observed_target_journal_d_state_pids": [
            row["pid"] for row in details
            if row["classification"]
            == "observed_target_journal_thread_nonblocking"
        ],
        "d_state_details": details,
        "unrelated_megasas_poll_authority": authority,
        "process_state_source": "/proc/PID/stat",
        "proc_status_reads_performed": 0,
        "bounded_access_remote_vm_risk": True,
        "target_cpus": sorted(_TARGET_CPUS),
        "cpu_disjoint_d_state_is_recorded_nonblocking": True,
        "cpu_overlap_d_state_remains_fail_closed": True,
    }


__all__ = ["configure_target_cpus", "host_pressure"]
