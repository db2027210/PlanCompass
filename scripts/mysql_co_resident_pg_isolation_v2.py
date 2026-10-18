#!/usr/bin/env python3
"""Install the current co-resident PostgreSQL isolation policy.

The predecessor policy required one exact Docker CPU/memory profile for every
known PostgreSQL container.  The external campaign has two sealed, mutually
exclusive profiles for its ``hybench_none`` pair.  Both profiles remain CPU
disjoint from MySQL, mount disk1 only, and retain finite memory limits.  This
module accepts only those explicitly sealed profiles; arbitrary drift still
fails closed.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any

from scripts import mysql_cross_project_wave_coordination_v1 as coordination


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    ROOT
    / "config/formal/frozen_mysql_cross_project_wave_coordination_20260816_v54"
    / "coordination_contract.json"
)


def live_sealed_pg_container_readback(policy: dict[str, Any]) -> dict[str, Any]:
    """Read back one of the explicitly sealed, CPU-disjoint profiles."""

    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    target_cpus = coordination._target_cpu_union()
    allowed_union = coordination.cpus(
        str(policy.get("allowed_external_cpu_union") or "")
    )
    for expected in policy["containers"]:
        completed = subprocess.run(
            ["docker", "inspect", expected["name"]],
            capture_output=True, text=True, check=False, timeout=15,
        )
        if completed.returncode != 0:
            errors.append(f"container_inspect_failed:{expected['name']}")
            continue
        try:
            values = json.loads(completed.stdout)
        except json.JSONDecodeError:
            values = None
        if not isinstance(values, list) or len(values) != 1:
            errors.append(f"container_inspect_cardinality:{expected['name']}")
            continue
        value = values[0]
        host = value.get("HostConfig") or {}
        mounts = value.get("Mounts") or []
        sources = sorted(str(row.get("Source") or "") for row in mounts)
        actual = {
            "name": expected["name"],
            "container_id": value.get("Id"),
            "running": (value.get("State") or {}).get("Running"),
            "cpuset_cpus": str(host.get("CpusetCpus") or ""),
            "cpuset_mems": str(host.get("CpusetMems") or ""),
            "memory_bytes": int(host.get("Memory") or 0),
            "memory_swap_bytes": int(host.get("MemorySwap") or 0),
            "pids_limit": host.get("PidsLimit"),
            "mount_sources": sources,
        }
        rows.append(actual)
        allowed_profiles = {
            str(item)
            for item in (
                expected.get("allowed_cpuset_cpus")
                or [expected["cpuset_cpus"]]
            )
        }
        allowed_memory = {
            int(item)
            for item in (
                expected.get("allowed_memory_bytes")
                or [expected["memory_bytes"]]
            )
        }
        actual_cpus = coordination.cpus(actual["cpuset_cpus"])
        expected_mount = str(expected["disk1_mount_source"])
        profile_errors: list[str] = []
        if actual["container_id"] != expected["container_id"]:
            profile_errors.append("container_identity_changed")
        if actual["running"] is not True:
            profile_errors.append("container_not_running")
        if actual["cpuset_cpus"] not in allowed_profiles:
            profile_errors.append("container_cpuset_not_in_sealed_profiles")
        if not actual_cpus or not actual_cpus <= allowed_union:
            profile_errors.append("container_cpu_not_in_external_union")
        if actual_cpus & target_cpus:
            profile_errors.append("container_cpu_overlaps_mysql")
        if actual["memory_bytes"] not in allowed_memory:
            profile_errors.append("container_memory_not_in_sealed_profiles")
        if expected_mount not in sources:
            profile_errors.append("container_disk1_mount_changed")
        if any(source.startswith("/disk2/") for source in sources):
            profile_errors.append("container_mounts_mysql_disk2")
        errors.extend(
            f"container_isolation_readback_differs:{expected['name']}:{error}"
            for error in profile_errors
        )
    return {"passed": not errors, "errors": errors, "containers": rows}


def install() -> None:
    """Activate the sealed policy in the predecessor coordination module."""

    if not CONTRACT_PATH.is_file() or CONTRACT_PATH.is_symlink():
        raise RuntimeError(f"coordination contract absent: {CONTRACT_PATH}")
    coordination.CONTRACT_PATH = CONTRACT_PATH
    coordination._live_frozen_pg_container_readback = (
        live_sealed_pg_container_readback
    )
