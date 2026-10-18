#!/usr/bin/env python3
"""Instant external-workload inventory that never blocks a MySQL wave."""

from __future__ import annotations

import os
import time
from typing import Any

from scripts import mysql_cross_project_wave_coordination_v1 as coordination


def rolling_quiet_gate(
    *, run_id: str, required_seconds: float, maximum_wait_seconds: float,
    maximum_load1: float = 16.0, interval_seconds: float = 1.0,
    target_cpus: set[int] | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    inventory_error = None
    try:
        inventory = coordination.external_process_inventory(run_id, {0})
        workloads = coordination.sanitized_rows(inventory.get("workloads") or [])
        controllers = coordination.sanitized_rows(
            inventory.get("controllers") or []
        )
    except BaseException as error:
        workloads = []
        controllers = []
        inventory_error = f"{type(error).__name__}: {error}"
    try:
        load1 = os.getloadavg()[0]
    except OSError:
        load1 = None
    return {
        "schema_version": "mysql_external_workload_diagnostic_gate_v1",
        "status": "PASS",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "external_workload_gate_enforced": False,
        "external_processes_diagnostic_only": True,
        "external_process_control_or_stop_performed": False,
        "mysql_fixed_cpu_affinity_retained": True,
        "numa_exclusive_scheduler_lock_required": False,
        "requested_clean_seconds_diagnostic": required_seconds,
        "requested_maximum_wait_seconds_diagnostic": maximum_wait_seconds,
        "actual_wait_seconds": 0.0,
        "errors": [],
        "diagnostic_inventory_error": inventory_error,
        "external_workloads": workloads,
        "external_controllers": controllers,
        "external_workload_count": len(workloads),
        "target_cpus": sorted(target_cpus or ()),
        "host_load1_diagnostic": load1,
        "maximum_load1_diagnostic": maximum_load1,
        "observed_seconds": time.monotonic() - started,
        "completed_at": coordination.now(),
    }
