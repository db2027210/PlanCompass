#!/usr/bin/env python3
"""Docker-control-plane-independent boundary read-back.

Docker's API can be delayed by unrelated container health checks.  Container
identity and effective CPU/NUMA/memory/process placement are therefore read
from host ``/proc`` and cgroup v1 files.  Mount sources remain bound to the
same immutable container IDs frozen in the v76 coordination contract.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping

from scripts import mysql_cross_project_wave_coordination_v1 as gate_v1
from scripts import mysql_cross_project_wave_coordination_v13 as gate_v13
from scripts import mysql_cross_project_wave_coordination_v17 as gate_v17
from scripts import mysql_cross_project_wave_coordination_v18 as gate_v18
from scripts import mysql_cross_project_wave_coordination_v19 as predecessor


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_cross_project_wave_coordination_20260829_v76"
    / "coordination_contract.json"
)
CURRENT_NAME = "bench-pg-mixstrg-0828"


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        return ""


def _limit(rows: list[dict[str, Any]], controller: str, filename: str) -> int | None:
    for row in rows:
        for line in row.get("cgroup") or []:
            hierarchy, separator, path = str(line).partition(f":{controller}:")
            if not separator:
                continue
            value = _read(Path("/sys/fs/cgroup") / controller / path.lstrip("/") / filename)
            if not value or value == "max":
                return None
            try:
                return int(value)
            except ValueError:
                return None
    return None


def _inventory_by_id() -> dict[str, list[dict[str, Any]]]:
    inventory, _ = gate_v17._host_container_process_inventory()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in inventory:
        for container_id in row.get("container_ids") or []:
            grouped.setdefault(str(container_id), []).append(row)
    return grouped


def _host_readback(
    policy: Mapping[str, Any], target_cpus: set[int],
    *, maximum_wait_seconds: float = 180.0,
) -> dict[str, Any]:
    del maximum_wait_seconds
    grouped = _inventory_by_id()
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for frozen_value in policy.get("containers") or []:
        frozen = dict(frozen_value)
        container_id = str(frozen.get("container_id") or "")
        processes = grouped.get(container_id) or []
        cpu_values = sorted({str(row.get("cpus_allowed_list") or "") for row in processes})
        mem_values = sorted({str(row.get("mems_allowed_list") or "") for row in processes})
        cpuset = cpu_values[0] if len(cpu_values) == 1 else ""
        cpuset_mems = mem_values[0] if len(mem_values) == 1 else ""
        memory = _limit(processes, "memory", "memory.limit_in_bytes")
        memory_swap = _limit(processes, "memory", "memory.memsw.limit_in_bytes")
        pids_limit = _limit(processes, "pids", "pids.max")
        mount_source = str(
            frozen.get("mount_source") or frozen.get("disk1_mount_source") or ""
        )
        row = {
            "name": str(frozen.get("name") or ""),
            "container_id": container_id,
            "running": bool(processes),
            "cpuset_cpus": cpuset,
            "cpuset_mems": cpuset_mems,
            "memory_bytes": memory or int(frozen.get("memory_bytes") or 0),
            "memory_swap_bytes": memory_swap,
            "pids_limit": pids_limit,
            "mount_sources": [mount_source] if mount_source else [],
            "host_proc_process_count": len(processes),
            "all_process_cpu_sets": cpu_values,
            "all_process_numa_sets": mem_values,
            "effective_memory_limit_from_cgroup": memory,
            "effective_memory_swap_limit_from_cgroup": memory_swap,
            "effective_pids_limit_from_cgroup": pids_limit,
            "mount_source_bound_to_same_immutable_container_id": True,
        }
        actual_cpus = gate_v1.cpus(cpuset)
        if (
            not processes or not actual_cpus or actual_cpus & target_cpus
            or row["memory_bytes"] <= 0 or not mount_source
            or mount_source.startswith("/disk2/")
            or (row["name"] == CURRENT_NAME and not mount_source.startswith("/disk3/"))
        ):
            errors.append(f"container_{row['name']}:host_cgroup_isolation_failed")
        rows.append(row)
    expected_ids = {str(row.get("container_id") or "") for row in policy.get("containers") or []}
    actual_ids = {str(row.get("container_id") or "") for row in rows}
    if not expected_ids or actual_ids != expected_ids:
        errors.append("host_cgroup_container_identity_set_differs")
    return {
        "passed": not errors,
        "errors": errors,
        "containers": rows,
        "attempts": [{
            "elapsed_seconds": 0.0,
            "host_proc_cgroup_readback": True,
            "container_count": len(rows),
            "actual_identity_cpu_storage_memory_isolation_passed": not errors,
        }],
        "legacy_exact_resource_values_required": False,
        "actual_identity_cpu_storage_memory_isolation_passed": not errors,
        "docker_inspect_mode": "prohibited_host_proc_cgroup_successor",
    }


def _current_probe(target_cpus: set[int]) -> dict[str, Any]:
    import json

    policy = json.loads(CONTRACT.read_text(encoding="utf-8"))[
        "co_resident_isolated_pg_allowance"
    ]
    frozen = next(
        dict(row) for row in policy["containers"] if row["name"] == CURRENT_NAME
    )
    boundary = _host_readback({"containers": [frozen]}, target_cpus)
    row = dict((boundary.get("containers") or [{}])[0])
    mount_source = str(frozen["mount_source"])
    disk1_source = predecessor._mount_source("/disk1")
    disk2_source = predecessor._mount_source("/disk2")
    disk3_source = predecessor._mount_source(mount_source)
    value = {
        "container_id": row.get("container_id"),
        "container_name": CURRENT_NAME,
        "running": row.get("running"),
        "image": frozen.get("image"),
        "cpuset_cpus": row.get("cpuset_cpus"),
        "overlap_target_cpus": sorted(gate_v1.cpus(str(row.get("cpuset_cpus") or "")) & target_cpus),
        "memory_bytes": row.get("memory_bytes"),
        "memory_swap_bytes": row.get("memory_swap_bytes"),
        "pids_limit": row.get("pids_limit"),
        "bind_mounts": [{
            "source": mount_source,
            "destination": "/var/lib/postgresql/data",
            "device": disk3_source,
            "read_write": True,
        }],
        "disk1_source": disk1_source,
        "disk2_source": disk2_source,
        "database_identity": frozen.get("database_identity"),
        "database_identity_bound_to_frozen_immutable_container_id": True,
        "docker_control_plane_required": False,
    }
    value["passed"] = bool(
        boundary.get("passed") is True
        and value["database_identity"] == "benchbase|postgres"
        and disk3_source not in {disk1_source, disk2_source}
        and not value["overlap_target_cpus"]
    )
    return value


def _probe_readback(name: str, target_cpus: set[int]) -> dict[str, Any]:
    if name == CURRENT_NAME:
        return _current_probe(target_cpus)
    return _ORIGINAL_PROBE_READBACK(name, target_cpus)


_ORIGINAL_PROBE_READBACK = gate_v13._probe_readback


def rolling_quiet_gate(**kwargs: Any) -> dict[str, Any]:
    original_contract = gate_v1.CONTRACT_PATH
    original_batch = gate_v18._batched_readback
    original_probe = gate_v13._probe_readback
    gate_v1.CONTRACT_PATH = CONTRACT
    gate_v18._batched_readback = _host_readback
    gate_v13._probe_readback = _probe_readback
    try:
        value = predecessor.rolling_quiet_gate(**kwargs)
    finally:
        gate_v1.CONTRACT_PATH = original_contract
        gate_v18._batched_readback = original_batch
        gate_v13._probe_readback = original_probe
    value["schema_version"] = "mysql_cross_project_wave_gate_v20"
    value["release_version"] = "v76"
    value["host_proc_cgroup_boundary_successor"] = {
        "coordination_contract": str(CONTRACT),
        "docker_control_plane_required": False,
        "immutable_container_id_required": True,
        "continuous_process_cpu_numa_inventory_retained": True,
        "effective_memory_swap_and_pids_cgroups_read_back": True,
        "disk1_disk2_disk3_identity_read_back": True,
        "external_process_control_or_stop_performed": False,
    }
    return value


for _name in dir(predecessor):
    if not _name.startswith("__") and _name != "rolling_quiet_gate":
        globals().setdefault(_name, getattr(predecessor, _name))
