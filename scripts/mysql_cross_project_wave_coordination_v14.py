#!/usr/bin/env python3
"""Accept external container reincarnation only when placement is unchanged."""

from __future__ import annotations

from typing import Any, Mapping

from scripts import mysql_cross_project_wave_coordination_v13 as predecessor


def _same_rows(
    before: Mapping[str, Any], after: Mapping[str, Any], *, probe: bool,
) -> bool:
    fields = (
        ("container_name", "running", "image", "cpuset_cpus", "overlap_target_cpus",
         "memory_bytes", "memory_swap_bytes", "pids_limit", "bind_mounts",
         "disk2_source", "database_identity")
        if probe else
        ("name", "running", "cpuset_cpus", "cpuset_mems", "memory_bytes",
         "memory_swap_bytes", "pids_limit", "mount_sources")
    )
    return all(before.get(field) == after.get(field) for field in fields)


def _effective_placement_stable(value: Mapping[str, Any]) -> bool:
    before_rows = {
        str(row.get("name")): row
        for row in (value.get("external_container_boundary_readback_before") or {}).get("containers") or []
    }
    after_rows = {
        str(row.get("name")): row
        for row in (value.get("external_container_boundary_readback_after") or {}).get("containers") or []
    }
    if not before_rows or set(before_rows) != set(after_rows):
        return False
    if not all(_same_rows(before_rows[name], after_rows[name], probe=False) for name in before_rows):
        return False
    probe_before = value.get("external_statistics_container_readback_before") or {}
    probe_after = value.get("external_statistics_container_readback_after") or {}
    if not probe_before or set(probe_before) != set(probe_after):
        return False
    return all(
        before.get("passed") is True and probe_after[name].get("passed") is True
        and _same_rows(before, probe_after[name], probe=True)
        for name, before in probe_before.items()
    )


def rolling_quiet_gate(**kwargs: Any) -> dict[str, Any]:
    value = predecessor.rolling_quiet_gate(**kwargs)
    errors = set(value.get("errors") or [])
    allowed = {"external_container_boundary_placement_changed_or_unproven"}
    reincarnation_only = bool(errors) and errors.issubset(allowed)
    stable = _effective_placement_stable(value)
    if reincarnation_only and stable:
        value["status"] = "PASS"
        value["passed"] = True
        value["errors"] = []
        value["external_container_placement_stable_across_window"] = True
        value["external_statistics_container_placement_stable"] = True
    value["schema_version"] = "mysql_cross_project_wave_gate_v14"
    value["release_version"] = "v70"
    value["external_container_reincarnation_successor"] = {
        "effective_placement_equal_excluding_container_id": stable,
        "container_id_equality_required": False,
        "container_id_change_accepted": reincarnation_only and stable,
        "cpu_numa_memory_storage_and_database_identity_equality_required": True,
        "external_process_control_or_stop_performed": False,
    }
    return value


for _name in dir(predecessor):
    if not _name.startswith("__") and _name != "rolling_quiet_gate":
        globals().setdefault(_name, getattr(predecessor, _name))
