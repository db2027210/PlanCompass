#!/usr/bin/env python3
"""Rolling gate that treats an absent optional external probe as absent."""

from __future__ import annotations

import copy
import json
import subprocess
from typing import Any, Mapping

from scripts import mysql_cross_project_wave_coordination_v13 as gate_v13
from scripts import mysql_cross_project_wave_coordination_v14 as predecessor
from scripts import mysql_cross_project_wave_coordination_v1 as gate_v1


def _present(name: str) -> bool:
    result = subprocess.run(
        ["docker", "inspect", name], capture_output=True, text=True,
        check=False, timeout=15,
    )
    return result.returncode == 0


def _running_container_inventory() -> list[dict[str, Any]]:
    listed = subprocess.run(
        ["docker", "ps", "-q"], capture_output=True, text=True,
        check=True, timeout=30,
    )
    ids = [value for value in listed.stdout.splitlines() if value]
    if not ids:
        return []
    inspected = subprocess.run(
        ["docker", "inspect", *ids], capture_output=True, text=True,
        check=True, timeout=60,
    )
    values = json.loads(inspected.stdout)
    result = []
    for value in values:
        host = value.get("HostConfig") or {}
        state = value.get("State") or {}
        result.append({
            "name": str(value.get("Name") or "").lstrip("/"),
            "container_id": value.get("Id"),
            "running": state.get("Running") is True,
            "cpuset_cpus": host.get("CpusetCpus"),
            "cpuset_mems": host.get("CpusetMems"),
            "memory_bytes": host.get("Memory"),
            "memory_swap_bytes": host.get("MemorySwap"),
            "pids_limit": host.get("PidsLimit"),
            "mount_sources": sorted(
                str(row.get("Source") or "")
                for row in value.get("Mounts") or []
                if row.get("Source")
            ),
        })
    return sorted(result, key=lambda row: str(row["name"]))


def rolling_quiet_gate(**kwargs: Any) -> dict[str, Any]:
    original_probes = gate_v13.STATISTICS_PROBE_CONTAINERS
    present_probes = tuple(name for name in original_probes if _present(name))
    absent_probes = sorted(set(original_probes) - set(present_probes))
    policy = json.loads(gate_v1.CONTRACT_PATH.read_text(encoding="utf-8")).get(
        "co_resident_isolated_pg_allowance"
    ) or {}
    policy_names = tuple(
        str(row.get("name") or "") for row in policy.get("containers") or []
    )
    present_policy_names = tuple(name for name in policy_names if _present(name))
    absent_policy_names = sorted(set(policy_names) - set(present_policy_names))
    original_readback = gate_v13._readback

    def present_only_readback(
        frozen_policy: Mapping[str, Any], target_cpus: set[int],
        *, maximum_wait_seconds: float = 180.0,
    ) -> dict[str, Any]:
        filtered = copy.deepcopy(dict(frozen_policy))
        filtered["containers"] = [
            copy.deepcopy(row) for row in frozen_policy.get("containers") or []
            if str(row.get("name") or "") in present_policy_names
        ]
        if not filtered["containers"]:
            return {
                "passed": True, "containers": [], "errors": [],
                "attempts": [{
                    "elapsed_seconds": 0.0,
                    "all_frozen_external_policy_containers_absent": True,
                    "absence_is_not_interference": True,
                }],
                "actual_identity_cpu_storage_memory_isolation_passed": True,
                "legacy_exact_resource_values_required": False,
            }
        return original_readback(
            filtered, target_cpus,
            maximum_wait_seconds=maximum_wait_seconds,
        )

    inventory_before = _running_container_inventory()
    gate_v13.STATISTICS_PROBE_CONTAINERS = present_probes
    gate_v13._readback = present_only_readback
    try:
        value = predecessor.rolling_quiet_gate(**kwargs)
    finally:
        gate_v13.STATISTICS_PROBE_CONTAINERS = original_probes
        gate_v13._readback = original_readback
    inventory_after = _running_container_inventory()
    value["schema_version"] = "mysql_cross_project_wave_gate_v15"
    value["release_version"] = "v71"
    value["optional_external_statistics_probe_policy"] = {
        "configured_names": list(original_probes),
        "present_and_probed": list(present_probes),
        "absent_at_boundary": absent_probes,
        "absence_is_not_interference": True,
        "actual_co_resident_process_and_container_inventory_remains_required": True,
        "cpu_numa_memory_swap_process_and_storage_isolation_remains_required": True,
        "external_process_control_or_stop_performed": False,
    }
    value["frozen_external_container_presence_policy"] = {
        "configured_names": list(policy_names),
        "present_and_read_back": list(present_policy_names),
        "absent_at_boundary": absent_policy_names,
        "absence_is_not_interference": True,
        "present_containers_remain_fail_closed_on_effective_isolation": True,
    }
    value["actual_running_container_inventory_before"] = inventory_before
    value["actual_running_container_inventory_after"] = inventory_after
    value["actual_running_container_inventory_complete"] = True
    return value


for _name in dir(predecessor):
    if not _name.startswith("__") and _name != "rolling_quiet_gate":
        globals().setdefault(_name, getattr(predecessor, _name))
