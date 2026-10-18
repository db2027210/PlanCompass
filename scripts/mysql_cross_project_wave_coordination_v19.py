#!/usr/bin/env python3
"""Inventory current container-scoped PostgreSQL clients without host-shim false positives.

The host-side ``docker exec`` process has the caller's broad CPU affinity, but
the psql process and PostgreSQL execution are constrained by the destination
container.  This successor recognizes only the current named PostgreSQL data
plane, and only after before/after live read-back proves that its CPU and
storage placement is disjoint from both MySQL NVMe devices.  Arbitrary host
processes and arbitrary Docker commands remain fail-closed.
"""

from __future__ import annotations

import re
from typing import Any

from scripts import mysql_cross_project_wave_coordination_v12 as gate_v12
from scripts import mysql_cross_project_wave_coordination_v13 as gate_v13
from scripts import mysql_cross_project_wave_coordination_v18 as predecessor


_CONTAINER = "bench-pg-mixstrg-0828"
_EXEC_PSQL = re.compile(
    r"^docker exec (?P<container>bench-pg-mixstrg-0828) "
    r"psql -U postgres -d benchbase(?: [^\r\n]*)?$"
)


def _authority_with_both_mysql_disks_excluded(
    name: str, target_cpus: set[int],
) -> dict[str, Any]:
    value = dict(_ORIGINAL_PROBE_AUTHORITY(name, target_cpus))
    disk1_source = gate_v12._mount_source("/disk1")
    bind_devices = {
        str(row.get("device") or "") for row in value.get("bind_mounts") or []
    }
    value["disk1_source"] = disk1_source
    value["both_mysql_storage_devices_excluded"] = bool(
        bind_devices
        and disk1_source not in bind_devices
        and str(value.get("disk2_source") or "") not in bind_devices
    )
    value["passed"] = bool(
        value.get("passed") is True
        and value["both_mysql_storage_devices_excluded"]
    )
    return value


_ORIGINAL_PROBE_AUTHORITY = gate_v13._ORIGINAL_PROBE_AUTHORITY


def rolling_quiet_gate(**kwargs: Any) -> dict[str, Any]:
    original_probe = gate_v12._PROBE
    original_names = gate_v13.STATISTICS_PROBE_CONTAINERS
    original_authority = gate_v13._ORIGINAL_PROBE_AUTHORITY
    gate_v12._PROBE = _EXEC_PSQL
    gate_v13.STATISTICS_PROBE_CONTAINERS = tuple(
        dict.fromkeys((*original_names, _CONTAINER))
    )
    gate_v13._ORIGINAL_PROBE_AUTHORITY = _authority_with_both_mysql_disks_excluded
    try:
        value = predecessor.rolling_quiet_gate(**kwargs)
    finally:
        gate_v12._PROBE = original_probe
        gate_v13.STATISTICS_PROBE_CONTAINERS = original_names
        gate_v13._ORIGINAL_PROBE_AUTHORITY = original_authority
    value["schema_version"] = "mysql_cross_project_wave_gate_v19"
    value["release_version"] = "v75"
    value["current_container_scoped_pg_client_successor"] = {
        "container_name": _CONTAINER,
        "exact_docker_exec_psql_shape_required": True,
        "live_database_identity_readback_required": True,
        "before_after_container_placement_stability_required": True,
        "target_cpu_overlap_prohibited": True,
        "disk1_and_disk2_backing_devices_prohibited": True,
        "arbitrary_docker_command_waiver_prohibited": True,
        "external_process_control_or_stop_performed": False,
    }
    return value


for _name in dir(predecessor):
    if not _name.startswith("__") and _name != "rolling_quiet_gate":
        globals().setdefault(_name, getattr(predecessor, _name))
