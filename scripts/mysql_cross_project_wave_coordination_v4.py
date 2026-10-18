#!/usr/bin/env python3
"""Zero-safe rolling non-interference gate successor.

The v3 implementation used truth-value fallback for numeric telemetry.  That
turns the valid readings ``swap_total=0`` and ``memory_full_psi_avg10=0`` into
the missing-value sentinels ``-1`` and ``inf``.  Keep the v3 gate and all of
its isolation decisions unchanged, but parse those two fields by presence.
"""

from __future__ import annotations

import copy
from typing import Any

from scripts import mysql_cross_project_wave_coordination_v3 as predecessor


def effective_isolation(result: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(result)
    if result.get("passed") is True and int(result.get("workload_count") or 0) == 0:
        value["dynamic_noninterference_successor"] = {
            "applied": False,
            "reason": "no_external_database_workload",
            "minimum_host_mem_available_bytes": (
                predecessor.MINIMUM_HOST_MEM_AVAILABLE_BYTES
            ),
            "maximum_window_memory_decline_bytes": (
                predecessor.MAXIMUM_WINDOW_MEMORY_DECLINE_BYTES
            ),
            "dynamic_errors": [],
            "zero_value_parsing": "explicit_none_check",
        }
        return value

    errors = {str(item) for item in (value.get("errors") or [])}
    readback = value.get("container_readback") or {}
    workloads = value.get("workloads") or []
    available_raw = value.get("host_mem_available_bytes")
    psi_raw = value.get("host_memory_full_psi_avg10")
    swap_raw = value.get("host_swap_total_bytes")
    available = 0 if available_raw is None else int(available_raw)
    psi = float("inf") if psi_raw is None else float(psi_raw)
    swap_total = -1 if swap_raw is None else int(swap_raw)
    row_errors = [
        str(error)
        for row in workloads if isinstance(row, dict)
        for error in (row.get("errors") or [])
    ]
    dynamic_errors: list[str] = []
    if errors - predecessor.TOLERATED_LEGACY_ERRORS:
        dynamic_errors.append("legacy_isolation_failure_not_headroom_only")
    if readback and readback.get("passed") is not True:
        dynamic_errors.append("external_storage_or_container_isolation_unproven")
    if row_errors:
        dynamic_errors.append("external_process_cpu_or_identity_isolation_unproven")
    if available < predecessor.MINIMUM_HOST_MEM_AVAILABLE_BYTES:
        dynamic_errors.append("dynamic_host_memory_headroom_below_minimum")
    if swap_total != 0:
        dynamic_errors.append("host_swap_must_remain_disabled")
    if psi > predecessor.MAXIMUM_MEMORY_FULL_PSI_AVG10:
        dynamic_errors.append("host_memory_full_psi_exceeds_dynamic_maximum")

    passed = not dynamic_errors
    value["legacy_passed"] = result.get("passed") is True
    value["legacy_errors"] = sorted(errors)
    value["dynamic_noninterference_successor"] = {
        "applied": errors == predecessor.TOLERATED_LEGACY_ERRORS,
        "minimum_host_mem_available_bytes": (
            predecessor.MINIMUM_HOST_MEM_AVAILABLE_BYTES
        ),
        "maximum_window_memory_decline_bytes": (
            predecessor.MAXIMUM_WINDOW_MEMORY_DECLINE_BYTES
        ),
        "maximum_memory_full_psi_avg10": (
            predecessor.MAXIMUM_MEMORY_FULL_PSI_AVG10
        ),
        "tolerated_legacy_errors": sorted(predecessor.TOLERATED_LEGACY_ERRORS),
        "cpu_affinity_overlap_permitted": False,
        "disk2_external_mount_permitted": False,
        "swap_permitted": False,
        "dynamic_errors": dynamic_errors,
        "zero_value_parsing": "explicit_none_check",
    }
    value["passed"] = passed
    value["status"] = (
        "PASS_DYNAMIC_ISOLATED_CO_RESIDENT_PG"
        if passed else "BLOCKED_FAIL_CLOSED"
    )
    return value


def rolling_quiet_gate(**kwargs: Any) -> dict[str, Any]:
    predecessor.effective_isolation = effective_isolation
    result = predecessor.rolling_quiet_gate(**kwargs)
    result["release_version"] = "v59"
    result["zero_value_parsing_release"] = {
        "swap_zero_is_valid": True,
        "memory_full_psi_zero_is_valid": True,
        "missing_values_remain_fail_closed": True,
        "algorithm_or_candidate_flow_changed": False,
    }
    return result
