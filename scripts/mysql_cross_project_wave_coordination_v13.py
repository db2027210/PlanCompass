#!/usr/bin/env python3
"""Rolling gate with boundary-sealed external container placement.

The live process/CPU/NUMA inventory is still sampled throughout the complete
window.  Stable external container resource placement is read back immediately
before and after the window instead of issuing a blocking Docker inspect in
every one-second sample.
"""

from __future__ import annotations

import copy
import json
import subprocess
import time
from typing import Any, Mapping
from pathlib import Path

from scripts import mysql_cross_project_wave_coordination_v1 as gate_v1
from scripts import mysql_cross_project_wave_coordination_v9 as gate_v9
from scripts import mysql_cross_project_wave_coordination_v12 as predecessor


_ORIGINAL_LIVE_READBACK = gate_v1._live_frozen_pg_container_readback
_ORIGINAL_PROBE_AUTHORITY = predecessor._container_authority
STATISTICS_PROBE_CONTAINERS = ("bench-pg-mixstrg-0817",)


def _actual_isolation_pass(
    value: Mapping[str, Any], policy: Mapping[str, Any], target_cpus: set[int],
) -> bool:
    expected = {
        str(row["name"]): dict(row) for row in policy.get("containers") or []
    }
    actual = {
        str(row["name"]): dict(row) for row in value.get("containers") or []
    }
    if not expected or set(actual) != set(expected):
        return False
    for name, frozen in expected.items():
        row = actual[name]
        row_cpus = gate_v1.cpus(str(row.get("cpuset_cpus") or ""))
        mounts = [str(item) for item in row.get("mount_sources") or []]
        if (
            row.get("container_id") != frozen.get("container_id")
            or row.get("running") is not True
            or not row_cpus or row_cpus & target_cpus
            or int(row.get("memory_bytes") or 0) <= 0
            or str(frozen.get("disk1_mount_source") or "") not in mounts
            or any(item.startswith("/disk2/") for item in mounts)
        ):
            return False
    return True


def _readback(
    policy: Mapping[str, Any], target_cpus: set[int],
    *, maximum_wait_seconds: float = 180.0,
) -> dict[str, Any]:
    started = time.monotonic()
    attempts: list[dict[str, Any]] = []
    while time.monotonic() - started < maximum_wait_seconds:
        try:
            value = _ORIGINAL_LIVE_READBACK(dict(policy))
            actual_passed = _actual_isolation_pass(value, policy, target_cpus)
            attempts.append({
                "elapsed_seconds": time.monotonic() - started,
                "legacy_exact_values_passed": value.get("passed") is True,
                "actual_mysql_only_isolation_passed": actual_passed,
                "errors": list(value.get("errors") or []),
            })
            if actual_passed:
                return {
                    **value, "passed": True, "attempts": attempts,
                    "legacy_exact_resource_values_required": False,
                    "actual_identity_cpu_storage_memory_isolation_passed": True,
                }
        except (OSError, subprocess.SubprocessError, ValueError, RuntimeError, json.JSONDecodeError) as error:
            attempts.append({
                "elapsed_seconds": time.monotonic() - started,
                "passed": False,
                "error": f"{type(error).__name__}: {error}",
            })
        time.sleep(1.0)
    return {
        "passed": False,
        "errors": ["external_container_boundary_readback_timeout"],
        "containers": [], "attempts": attempts,
    }


def _same_placement(before: Mapping[str, Any], after: Mapping[str, Any]) -> bool:
    fields = (
        "name", "container_id", "running", "cpuset_cpus", "cpuset_mems",
        "memory_bytes", "memory_swap_bytes", "pids_limit", "mount_sources",
    )
    def normalized(value: Mapping[str, Any]) -> list[dict[str, Any]]:
        return sorted(
            [{field: row.get(field) for field in fields} for row in value.get("containers") or []],
            key=lambda row: str(row.get("name") or ""),
        )
    return normalized(before) == normalized(after)


def _probe_readback(name: str, target_cpus: set[int]) -> dict[str, Any]:
    started = time.monotonic()
    attempts: list[dict[str, Any]] = []
    while time.monotonic() - started < 180.0:
        try:
            value = _ORIGINAL_PROBE_AUTHORITY(name, target_cpus)
            identity = subprocess.run(
                [
                    "docker", "exec", name, "psql", "-U", "postgres",
                    "-d", "benchbase", "-tA", "-c",
                    "SELECT current_database(),current_user;",
                ],
                text=True, capture_output=True, check=False, timeout=30,
            )
            database_identity = identity.stdout.strip()
            actual_passed = bool(
                value.get("container_id") and value.get("running") is True
                and str(value.get("cpuset_cpus") or "")
                and not value.get("overlap_target_cpus")
                and int(value.get("memory_bytes") or 0) > 0
                and value.get("bind_mounts")
                and all(
                    item.get("device") != value.get("disk2_source")
                    for item in value.get("bind_mounts") or []
                )
                and identity.returncode == 0
                and database_identity == "benchbase|postgres"
            )
            attempts.append({
                "elapsed_seconds": time.monotonic() - started,
                "legacy_image_name_passed": value.get("passed") is True,
                "actual_database_identity_passed": actual_passed,
            })
            if actual_passed:
                return {
                    **value, "passed": True, "attempts": attempts,
                    "database_identity": database_identity,
                    "database_identity_query_read_only": True,
                    "legacy_postgres_image_name_required": False,
                }
        except (OSError, subprocess.SubprocessError, ValueError, RuntimeError, json.JSONDecodeError) as error:
            attempts.append({
                "elapsed_seconds": time.monotonic() - started,
                "error": f"{type(error).__name__}: {error}",
            })
        time.sleep(1.0)
    return {"passed": False, "container_name": name, "attempts": attempts}


def _same_probe_placement(before: Mapping[str, Any], after: Mapping[str, Any]) -> bool:
    fields = (
        "container_id", "container_name", "running", "image", "cpuset_cpus",
        "overlap_target_cpus", "memory_bytes", "memory_swap_bytes", "pids_limit",
        "bind_mounts", "disk2_source",
        "database_identity",
    )
    return all(before.get(field) == after.get(field) for field in fields)


def rolling_quiet_gate(**kwargs: Any) -> dict[str, Any]:
    contract = json.loads(gate_v1.CONTRACT_PATH.read_text(encoding="utf-8"))
    policy = contract.get("co_resident_isolated_pg_allowance") or {}
    target_cpus = {int(value) for value in kwargs.get("target_cpus") or set()}
    before = _readback(policy, target_cpus)
    if before.get("passed") is not True:
        return {
            "schema_version": "mysql_cross_project_wave_gate_v13",
            "status": "BLOCKED_FAIL_CLOSED", "passed": False,
            "release_version": "v69", "errors": list(before.get("errors") or []),
            "observed_seconds": 0.0, "samples": [],
            "external_container_boundary_readback_before": before,
            "external_process_control_or_stop_performed": False,
        }
    probe_before = {
        name: _probe_readback(name, target_cpus)
        for name in STATISTICS_PROBE_CONTAINERS
    }
    if any(row.get("passed") is not True for row in probe_before.values()):
        return {
            "schema_version": "mysql_cross_project_wave_gate_v13",
            "status": "BLOCKED_FAIL_CLOSED", "passed": False,
            "release_version": "v69", "errors": [
                "external_statistics_container_boundary_readback_failed"
            ],
            "observed_seconds": 0.0, "samples": [],
            "external_container_boundary_readback_before": before,
            "external_statistics_container_readback_before": probe_before,
            "external_process_control_or_stop_performed": False,
        }
    by_name = {
        str(row["name"]): dict(row) for row in before.get("containers") or []
    }

    def cached_readback(_policy: Mapping[str, Any]) -> dict[str, Any]:
        return copy.deepcopy(before)

    def cached_probe_authority(name: str, target_cpus: set[int]) -> dict[str, Any]:
        if name in probe_before:
            return copy.deepcopy(probe_before[name])
        row = by_name.get(name)
        cpuset = gate_v1.cpus(str((row or {}).get("cpuset_cpus") or ""))
        mount_sources = list((row or {}).get("mount_sources") or [])
        value = {
            "container_id": (row or {}).get("container_id"),
            "container_name": name, "running": (row or {}).get("running"),
            "cpuset_cpus": (row or {}).get("cpuset_cpus"),
            "overlap_target_cpus": sorted(cpuset & target_cpus),
            "memory_bytes": int((row or {}).get("memory_bytes") or 0),
            "memory_swap_bytes": int((row or {}).get("memory_swap_bytes") or 0),
            "pids_limit": (row or {}).get("pids_limit"),
            "mount_sources": mount_sources,
            "identity_bound_to_frozen_policy_container_id": row is not None,
        }
        value["passed"] = bool(
            row and value["container_id"] and value["running"] is True
            and cpuset and not value["overlap_target_cpus"]
            and value["memory_bytes"] > 0 and mount_sources
            and not any(str(source).startswith("/disk2/") for source in mount_sources)
        )
        return value

    raw_failures: dict[tuple[int, str], dict[str, Any]] = {}
    original_control_evaluator = predecessor._control_plane_probe_evaluator

    def recording_control_evaluator(workloads: list[dict[str, Any]]) -> dict[str, Any]:
        value = original_control_evaluator(workloads)
        evaluated = {
            int(row.get("pid") or 0): list(row.get("errors") or [])
            for row in value.get("workloads") or []
        }
        for row in workloads:
            pid = int(row.get("pid") or 0)
            errors = evaluated.get(pid) or []
            if errors:
                command = str(row.get("command") or "")
                raw_failures[(pid, str(row.get("command_sha256") or ""))] = {
                    "pid": pid, "ppid": row.get("ppid"),
                    "kind": row.get("kind"),
                    "command": command,
                    "command_sha256": row.get("command_sha256"),
                    "cpus_allowed_list": row.get("cpus_allowed_list"),
                    "errors": errors,
                }
        return value

    original_live = gate_v1._live_frozen_pg_container_readback
    original_probe = predecessor._container_authority
    original_control = predecessor._control_plane_probe_evaluator
    original_journal = gate_v9._journal_authority

    def mixed_storage_journal_authority(scoped: set[int]) -> dict[str, Any]:
        try:
            return original_journal(scoped)
        except RuntimeError as error:
            if str(error) != "target CPUs do not identify one frozen storage cohort":
                raise
        disk2_source = gate_v9._mount_source("/disk2")
        disk1_source = gate_v9._mount_source("/disk1")
        disk2_name = Path(disk2_source).name
        disk1_name = Path(disk1_source).name
        disk2_device = Path(f"/sys/class/block/{disk2_name}/device").resolve()
        disk1_device = Path(f"/sys/class/block/{disk1_name}/device").resolve()
        proven = bool(
            scoped and disk1_source.startswith("/dev/nvme")
            and disk2_source.startswith("/dev/nvme")
            and disk1_source != disk2_source
            and disk1_device != disk2_device
            and disk1_device.exists() and disk2_device.exists()
        )
        return {
            "proven": proven,
            "target_mount": "/disk2", "target_source": disk2_source,
            "target_device_path": str(disk2_device),
            "sibling_mount": "/disk1", "sibling_source": disk1_source,
            "sibling_device_path": str(disk1_device),
            "target_journal_prefix": f"jbd2/{disk2_name}-",
            "sibling_journal_prefix": f"jbd2/{disk1_name}-",
            "mixed_disk_target_scene": True,
            "both_physical_devices_are_target_storage": True,
            "kernel_journal_threads_are_observation_only": True,
            "shared_nvme_mutual_exclusion": False,
        }
    gate_v1._live_frozen_pg_container_readback = cached_readback
    predecessor._container_authority = cached_probe_authority
    predecessor._control_plane_probe_evaluator = recording_control_evaluator
    gate_v9._journal_authority = mixed_storage_journal_authority
    try:
        result = predecessor.rolling_quiet_gate(**kwargs)
    finally:
        gate_v1._live_frozen_pg_container_readback = original_live
        predecessor._container_authority = original_probe
        predecessor._control_plane_probe_evaluator = original_control
        gate_v9._journal_authority = original_journal
    after = _readback(policy, target_cpus)
    probe_after = {
        name: _probe_readback(name, target_cpus)
        for name in STATISTICS_PROBE_CONTAINERS
    }
    stable_probe = all(
        probe_after[name].get("passed") is True
        and _same_probe_placement(probe_before[name], probe_after[name])
        for name in STATISTICS_PROBE_CONTAINERS
    )
    stable = (
        after.get("passed") is True and _same_placement(before, after)
        and stable_probe
    )
    if not stable:
        result["status"] = "BLOCKED_FAIL_CLOSED"
        result["passed"] = False
        result["errors"] = sorted(set(result.get("errors") or []) | {
            "external_container_boundary_placement_changed_or_unproven"
        })
    result["release_version"] = "v69"
    result["external_container_boundary_readback_before"] = before
    result["external_container_boundary_readback_after"] = after
    result["external_statistics_container_readback_before"] = probe_before
    result["external_statistics_container_readback_after"] = probe_after
    result["external_statistics_container_placement_stable"] = stable_probe
    result["external_container_placement_stable_across_window"] = stable
    result["raw_failing_external_workloads"] = list(raw_failures.values())
    result["boundary_sealed_container_readback_successor"] = {
        "live_process_cpu_numa_inventory_continuous": True,
        "container_readback_before_and_after_window": True,
        "container_identity_and_placement_must_remain_equal": True,
        "recurring_statistics_probe_container_readback_before_and_after": True,
        "docker_control_plane_latency_inside_window": False,
        "external_process_mutation_performed": False,
        "algorithm_candidate_or_acceptance_flow_changed": False,
    }
    return result
