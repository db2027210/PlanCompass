#!/usr/bin/env python3
"""Validate target MySQL resources while treating external work as inventory."""

from __future__ import annotations

import copy
from datetime import datetime
from pathlib import Path
import time
from typing import Any

from scripts import mysql_cross_project_wave_coordination_v31 as strict_gate
from scripts import mysql_cross_project_wave_coordination_v30 as live_authority
from scripts import mysql_cross_project_wave_coordination_v27 as resource_readback
from scripts import preflight_mysql_four_scene_external_inventory_only_v1 as preflight


RELEASE = "v245-external-process-inventory-only"
target_local_resource_proof = live_authority.target_local_resource_proof


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def validate_point_authority(original: Any, r0mod: Any):
    """Run the inherited point authority with external rows made nonblocking."""
    saved = r0mod.PREFLIGHT_SOURCE
    r0mod.PREFLIGHT_SOURCE = Path(preflight.__file__).resolve()
    try:
        freeze, workers, point_gate = original()
    finally:
        r0mod.PREFLIGHT_SOURCE = saved

    target_cpus = {
        cpu
        for worker in workers.values()
        for cpu in r0mod._cpus(str(worker["database_cpuset"]))
        | r0mod._cpus(str(worker["client_cpuset"]))
    }
    target = target_local_resource_proof(target_cpus)
    if target.get("passed") is not True:
        raise RuntimeError(
            "target MySQL local resource readback failed: "
            f"{target.get('errors') or []}"
        )
    point_gate.update({
        "external_process_policy_release": RELEASE,
        "external_processes_inventory_only": True,
        "external_processes_can_block": False,
        "external_quiet_window_required": False,
        "strict_external_noninterference_evidence_eligible": False,
        "target_mysql_local_resource_proof": target,
        "target_mysql_local_resource_proof_required": True,
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
        "user_directed_policy_override": True,
    })
    return freeze, workers, point_gate


def rolling_quiet_gate(
    *, run_id: str, required_seconds: float, maximum_wait_seconds: float,
    maximum_load1: float = 512.0, interval_seconds: float = 1.0,
    target_cpus: set[int] | None = None,
) -> dict[str, Any]:
    """Take one inventory snapshot and fail only on target-MySQL readback."""
    del maximum_load1, interval_seconds
    scoped = {int(value) for value in target_cpus or set()}
    if not scoped:
        raise ValueError("actual target CPU scope is required")
    started = time.monotonic()
    target = target_local_resource_proof(scoped)
    target_workers = set(target.get("selected_workers") or [])
    workloads, d_rows, _ = strict_gate._inventory(scoped, target_workers)
    available, psi, memory = resource_readback._host_memory()
    errors = [] if target.get("passed") is True else [
        "target_mysql_local_resource_isolation_unproven"
    ]
    sample = {
        "captured_at": _now(),
        "errors": copy.deepcopy(errors),
        "target_local_resource_proof": target,
        "external_workloads": workloads,
        "blocking_external_workloads": [],
        "external_workloads_inventory_only": True,
        "strict_policy_would_evaluate_external_workloads": True,
        "d_state_inventory": d_rows,
        "persistent_blocking_d_state_pids": [],
        "external_d_state_inventory_only": True,
        "host_memory": memory,
        "host_memory_available_bytes": available,
        "host_memory_full_psi_avg10": psi,
        "global_memory_diagnostic_only": True,
        "clean_elapsed_seconds": 0.0,
    }
    passed = not errors
    return {
        "schema_version": "mysql_external_process_inventory_only_gate_v1",
        "release_version": RELEASE,
        "status": "PASS" if passed else "BLOCKED_FAIL_CLOSED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "run_id": run_id,
        "target_cpus": sorted(scoped),
        "requested_external_clean_seconds": float(required_seconds),
        "requested_maximum_wait_seconds": float(maximum_wait_seconds),
        "required_clean_seconds": 0.0,
        "maximum_wait_seconds": 0.0,
        "observed_seconds": time.monotonic() - started,
        "external_processes_inventory_only": True,
        "external_processes_can_block": False,
        "external_quiet_window_required": False,
        "target_mysql_local_resource_proof_required": True,
        "strict_external_noninterference_evidence_eligible": False,
        "user_directed_policy_override": True,
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
        "cross_scene_completion_dependency": False,
        "shared_nvme_mutual_exclusion": False,
        "numa_exclusive_scheduler_lock": False,
        "whole_round_resource_lease": False,
        "errors": errors,
        "samples": [sample],
        "completed_at": _now(),
    }


__all__ = [
    "RELEASE", "rolling_quiet_gate", "target_local_resource_proof",
    "validate_point_authority",
]
