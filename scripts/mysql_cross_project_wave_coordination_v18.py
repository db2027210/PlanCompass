#!/usr/bin/env python3
"""Rolling gate with batched Docker boundary readback.

The continuous process/cgroup inventory remains the v17 host-/proc path.  Only
the before/after immutable container placement readback is changed from eight
serialized ``docker inspect`` calls to one batched call, avoiding daemon queue
amplification while retaining fail-closed identity and isolation checks.
"""

from __future__ import annotations

import json
import subprocess
import time
from typing import Any, Mapping

from scripts import mysql_cross_project_wave_coordination_v13 as gate_v13
from scripts import mysql_cross_project_wave_coordination_v17 as predecessor


def batch_inspect(names: list[str], *, maximum_wait_seconds: float = 180.0) -> dict[str, dict[str, Any]]:
    if not names:
        return {}
    started = time.monotonic()
    last_error = "not_attempted"
    while time.monotonic() - started < maximum_wait_seconds:
        remaining = maximum_wait_seconds - (time.monotonic() - started)
        try:
            completed = subprocess.run(
                ["docker", "inspect", *names], capture_output=True, text=True,
                check=True, timeout=max(1.0, min(60.0, remaining)),
            )
            values = json.loads(completed.stdout)
            by_name = {
                str(value.get("Name") or "").lstrip("/"): dict(value)
                for value in values
            }
            if set(by_name) != set(names):
                raise RuntimeError("batched Docker inspect name set differs")
            return by_name
        except (OSError, subprocess.SubprocessError, ValueError, RuntimeError, json.JSONDecodeError) as error:
            last_error = f"{type(error).__name__}: {error}"
            time.sleep(1.0)
    raise RuntimeError(f"batched Docker inspect timed out: {last_error}")


def _batched_readback(
    policy: Mapping[str, Any], target_cpus: set[int],
    *, maximum_wait_seconds: float = 180.0,
) -> dict[str, Any]:
    expected = [dict(row) for row in policy.get("containers") or []]
    names = [str(row["name"]) for row in expected]
    started = time.monotonic()
    attempts: list[dict[str, Any]] = []
    try:
        values = batch_inspect(names, maximum_wait_seconds=maximum_wait_seconds)
        rows = []
        for frozen in expected:
            value = values[str(frozen["name"])]
            host = value.get("HostConfig") or {}
            rows.append({
                "name": str(frozen["name"]),
                "container_id": value.get("Id"),
                "running": (value.get("State") or {}).get("Running") is True,
                "cpuset_cpus": str(host.get("CpusetCpus") or ""),
                "cpuset_mems": str(host.get("CpusetMems") or ""),
                "memory_bytes": int(host.get("Memory") or 0),
                "memory_swap_bytes": int(host.get("MemorySwap") or 0),
                "pids_limit": host.get("PidsLimit"),
                "mount_sources": sorted(
                    str(row.get("Source") or "")
                    for row in value.get("Mounts") or [] if row.get("Source")
                ),
            })
        value = {"containers": rows, "errors": []}
        passed = gate_v13._actual_isolation_pass(value, policy, target_cpus)
        attempts.append({
            "elapsed_seconds": time.monotonic() - started,
            "batched_container_count": len(rows),
            "actual_mysql_only_isolation_passed": passed,
        })
        return {
            **value, "passed": passed,
            "errors": [] if passed else ["batched_external_container_isolation_differs"],
            "attempts": attempts,
            "legacy_exact_resource_values_required": False,
            "actual_identity_cpu_storage_memory_isolation_passed": passed,
            "docker_inspect_mode": "single_batched_call",
        }
    except BaseException as error:
        attempts.append({
            "elapsed_seconds": time.monotonic() - started,
            "error": f"{type(error).__name__}: {error}",
        })
        return {
            "passed": False,
            "errors": ["external_container_batched_readback_timeout"],
            "containers": [], "attempts": attempts,
            "docker_inspect_mode": "single_batched_call",
        }


def rolling_quiet_gate(**kwargs: Any) -> dict[str, Any]:
    original_readback = gate_v13._readback
    gate_v13._readback = _batched_readback
    try:
        value = predecessor.rolling_quiet_gate(**kwargs)
    finally:
        gate_v13._readback = original_readback
    value["schema_version"] = "mysql_cross_project_wave_gate_v18"
    value["release_version"] = "v74"
    value["external_boundary_readback_successor"] = {
        "mode": "single_batched_docker_inspect",
        "serialized_per_container_inspect_prohibited": True,
        "continuous_host_proc_cgroup_inventory_retained": True,
        "external_process_control_or_stop_performed": False,
    }
    return value


for _name in dir(predecessor):
    if not _name.startswith("__") and _name != "rolling_quiet_gate":
        globals().setdefault(_name, getattr(predecessor, _name))
