#!/usr/bin/env python3
"""Rolling non-interference gate for the full-parallel MySQL successor.

The v2 gate treated the historical 512 GiB host-headroom calibration as an
absolute no-co-resident-workload requirement.  With all eight frozen MySQL
workers running, that rejects an otherwise CPU- and disk-disjoint PostgreSQL
cohort even when the host has hundreds of GiB free and zero memory PSI.  This
successor retains fail-closed process, CPU, storage, swap, PSI and D-state
checks, while judging memory interference over the required rolling window.
"""

from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path
import time
from typing import Any

from scripts import mysql_cross_project_wave_coordination_v2 as predecessor


ROOT = Path(__file__).resolve().parents[1]
RELEASE_CONTRACT_PATH = (
    ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_c_20260825_v5"
    / "algorithm_c_async_contract.json"
)

MINIMUM_HOST_MEM_AVAILABLE_BYTES = 256 * 1024**3
MAXIMUM_WINDOW_MEMORY_DECLINE_BYTES = 8 * 1024**3
MAXIMUM_MEMORY_FULL_PSI_AVG10 = 0.10
PERSISTENT_D_STATE_SAMPLES = 3
TOLERATED_LEGACY_ERRORS = {"host_memory_headroom_below_frozen_minimum"}


def effective_isolation(result: dict[str, Any]) -> dict[str, Any]:
    """Re-evaluate only the obsolete fixed-headroom failure.

    Every identity, CPU-affinity, disk mount, process-count, RSS, swap and PSI
    check from the frozen predecessor must already pass.  The sole permitted
    predecessor error is its historical 512 GiB floor; this successor replaces
    that one field with a 256 GiB absolute floor plus a rolling decline bound.
    """

    value = copy.deepcopy(result)
    if result.get("passed") is True and int(result.get("workload_count") or 0) == 0:
        value["dynamic_noninterference_successor"] = {
            "applied": False,
            "reason": "no_external_database_workload",
            "minimum_host_mem_available_bytes": MINIMUM_HOST_MEM_AVAILABLE_BYTES,
            "maximum_window_memory_decline_bytes": (
                MAXIMUM_WINDOW_MEMORY_DECLINE_BYTES
            ),
            "dynamic_errors": [],
        }
        return value
    errors = {str(item) for item in (value.get("errors") or [])}
    readback = value.get("container_readback") or {}
    workloads = value.get("workloads") or []
    available = int(value.get("host_mem_available_bytes") or 0)
    psi = float(value.get("host_memory_full_psi_avg10") or float("inf"))
    swap_total = int(value.get("host_swap_total_bytes") or -1)
    row_errors = [
        str(error)
        for row in workloads if isinstance(row, dict)
        for error in (row.get("errors") or [])
    ]
    dynamic_errors: list[str] = []
    if errors - TOLERATED_LEGACY_ERRORS:
        dynamic_errors.append("legacy_isolation_failure_not_headroom_only")
    if readback and readback.get("passed") is not True:
        dynamic_errors.append("external_storage_or_container_isolation_unproven")
    if row_errors:
        dynamic_errors.append("external_process_cpu_or_identity_isolation_unproven")
    if available < MINIMUM_HOST_MEM_AVAILABLE_BYTES:
        dynamic_errors.append("dynamic_host_memory_headroom_below_minimum")
    if swap_total != 0:
        dynamic_errors.append("host_swap_must_remain_disabled")
    if psi > MAXIMUM_MEMORY_FULL_PSI_AVG10:
        dynamic_errors.append("host_memory_full_psi_exceeds_dynamic_maximum")
    passed = not dynamic_errors
    value["legacy_passed"] = result.get("passed") is True
    value["legacy_errors"] = sorted(errors)
    value["dynamic_noninterference_successor"] = {
        "applied": errors == TOLERATED_LEGACY_ERRORS,
        "minimum_host_mem_available_bytes": MINIMUM_HOST_MEM_AVAILABLE_BYTES,
        "maximum_window_memory_decline_bytes": (
            MAXIMUM_WINDOW_MEMORY_DECLINE_BYTES
        ),
        "maximum_memory_full_psi_avg10": MAXIMUM_MEMORY_FULL_PSI_AVG10,
        "tolerated_legacy_errors": sorted(TOLERATED_LEGACY_ERRORS),
        "cpu_affinity_overlap_permitted": False,
        "disk2_external_mount_permitted": False,
        "swap_permitted": False,
        "dynamic_errors": dynamic_errors,
    }
    value["passed"] = passed
    value["status"] = (
        "PASS_DYNAMIC_ISOLATED_CO_RESIDENT_PG"
        if passed else "BLOCKED_FAIL_CLOSED"
    )
    return value


def rolling_quiet_gate(
    *, run_id: str, required_seconds: float, maximum_wait_seconds: float,
    maximum_load1: float = 16.0, interval_seconds: float = 1.0,
    target_cpus: set[int] | None = None,
) -> dict[str, Any]:
    """Require 15 seconds of proven, actual non-interference."""

    if required_seconds < 15 or maximum_wait_seconds < required_seconds:
        raise ValueError("invalid dynamic non-interference bounds")
    from scripts.preflight_mysql_four_scene_live_v8 import host_pressure

    started = time.monotonic()
    clean_since: float | None = None
    clean_start_available: int | None = None
    samples: list[dict[str, Any]] = []
    d_state_streaks: dict[int, int] = {}
    terminal_errors: list[str] = []
    while True:
        inventory = predecessor.predecessor.external_process_inventory(run_id, {0})
        raw_workloads = inventory["workloads"]
        scoped_target_cpus = set(target_cpus or ())
        allowed_sibling_mysql = [
            row for row in raw_workloads
            if scoped_target_cpus
            and str(predecessor.ROOT) in str(row.get("command") or "")
            and predecessor.predecessor.cpus(
                str(row.get("cpus_allowed_list") or "")
            ).isdisjoint(scoped_target_cpus)
        ]
        evaluated = [row for row in raw_workloads if row not in allowed_sibling_mysql]
        legacy_isolation = predecessor.predecessor.evaluate_co_resident_pg_isolation(
            evaluated
        )
        isolation = effective_isolation(legacy_isolation)
        pressure = host_pressure()
        same_run = predecessor.predecessor.same_run_process_pids(run_id)
        raw_d_state = {int(pid) for pid in pressure["blocking_d_state_pids"]}
        proved_external = predecessor._proved_isolated_workload_pids(isolation)
        sibling_pids = {int(row["pid"]) for row in allowed_sibling_mysql}
        blocking = raw_d_state - same_run - proved_external - sibling_pids
        d_state_streaks = {
            pid: d_state_streaks.get(pid, 0) + 1 for pid in blocking
        }
        persistent = sorted(
            pid for pid, streak in d_state_streaks.items()
            if streak >= PERSISTENT_D_STATE_SAMPLES
        )
        errors = list(
            (isolation.get("dynamic_noninterference_successor") or {}).get(
                "dynamic_errors"
            ) or []
        )
        if persistent:
            errors.append("host_uninterruptible_sleep_present")
        available = int(isolation.get("host_mem_available_bytes") or 0)
        current = time.monotonic()
        if errors:
            clean_since = None
            clean_start_available = None
        else:
            if clean_since is None:
                clean_since = current
                clean_start_available = available
            decline = max(0, int(clean_start_available or available) - available)
            if decline > MAXIMUM_WINDOW_MEMORY_DECLINE_BYTES:
                errors.append("rolling_window_memory_decline_exceeds_maximum")
                clean_since = None
                clean_start_available = None
        clean_elapsed = 0.0 if clean_since is None else current - clean_since
        pressure["raw_blocking_d_state_pids"] = sorted(raw_d_state)
        pressure["blocking_d_state_pids"] = sorted(blocking)
        samples.append({
            "captured_at": predecessor.predecessor.now(),
            "errors": errors,
            "host_pressure": pressure,
            "external_workloads": predecessor.predecessor.sanitized_rows(
                raw_workloads
            ),
            "allowed_cpu_disjoint_sibling_mysql_workloads": (
                predecessor.predecessor.sanitized_rows(allowed_sibling_mysql)
            ),
            "co_resident_pg_isolation": isolation,
            "persistent_d_state_pids": persistent,
            "clean_elapsed_seconds": clean_elapsed,
            "rolling_memory_decline_bytes": (
                0 if clean_start_available is None
                else max(0, clean_start_available - available)
            ),
            "normalized_load1": float(pressure["load1"]) / max(
                1, os.cpu_count() or 1
            ),
            "load1_diagnostic_only": True,
        })
        if not errors and clean_elapsed >= required_seconds:
            break
        if current - started >= maximum_wait_seconds:
            terminal_errors = ["dynamic_noninterference_window_timeout"]
            break
        time.sleep(max(0.2, interval_seconds))

    passed = not terminal_errors and samples[-1]["clean_elapsed_seconds"] >= required_seconds
    return {
        "schema_version": "mysql_dynamic_noninterference_gate_v1",
        "status": "PASS" if passed else "BLOCKED_FAIL_CLOSED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "full_parallel_successor": True,
        "release_version": "v56",
        "release_contract_path": str(RELEASE_CONTRACT_PATH),
        "release_contract_sha256": hashlib.sha256(
            RELEASE_CONTRACT_PATH.read_bytes()
        ).hexdigest(),
        "external_process_control_or_stop_performed": False,
        "cross_scene_completion_dependency": False,
        "required_clean_seconds": required_seconds,
        "maximum_wait_seconds": maximum_wait_seconds,
        "minimum_host_mem_available_bytes": MINIMUM_HOST_MEM_AVAILABLE_BYTES,
        "maximum_window_memory_decline_bytes": MAXIMUM_WINDOW_MEMORY_DECLINE_BYTES,
        "maximum_memory_full_psi_avg10": MAXIMUM_MEMORY_FULL_PSI_AVG10,
        "maximum_load1": maximum_load1,
        "global_load1_policy": "diagnostic_only_direct_isolation_is_authoritative",
        "persistent_d_state_required_consecutive_samples": PERSISTENT_D_STATE_SAMPLES,
        "observed_seconds": time.monotonic() - started,
        "errors": terminal_errors,
        "samples": samples,
        "completed_at": predecessor.predecessor.now(),
    }
