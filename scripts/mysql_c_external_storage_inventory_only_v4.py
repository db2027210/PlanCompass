#!/usr/bin/env python3
"""Rolling kernel/cgroup prestart inventory for Algorithm C.

The v3 inventory took two independent one-shot snapshots.  A short external
client or a brief host-pressure interval could therefore fail a controller at
startup even though the per-wave policy requires a continuous clean window.
This successor runs that same v27 rolling policy once and reuses its proven
terminal snapshot for both legacy inventory callbacks.  It never calls the
Docker API and never mutates an external process.
"""

from __future__ import annotations

import time
from typing import Any

from scripts import mysql_cross_project_wave_coordination_v27 as kernel_gate


WAIVED_CONTAINER = "gptuner_cost7_hybench"
WAIVER_ID = "kernel_rolling_prestart_no_static_external_container_waiver_v1"
REQUIRED_CLEAN_SECONDS = 15.0
MAXIMUM_WAIT_SECONDS = 300.0
_CACHE: dict[tuple[int, ...], tuple[float, dict[str, Any]]] = {}


def _rolling(target_cpus: set[int]) -> dict[str, Any]:
    key = tuple(sorted(target_cpus))
    cached = _CACHE.get(key)
    if cached is not None and time.monotonic() - cached[0] <= 10.0:
        return cached[1]
    result = kernel_gate.rolling_quiet_gate(
        run_id="algorithm_c_prestart",
        required_seconds=REQUIRED_CLEAN_SECONDS,
        maximum_wait_seconds=MAXIMUM_WAIT_SECONDS,
        target_cpus=set(target_cpus),
    )
    _CACHE[key] = (time.monotonic(), result)
    return result


def _terminal(result: dict[str, Any]) -> dict[str, Any]:
    samples = list(result.get("samples") or [])
    if not samples:
        raise RuntimeError("rolling prestart gate produced no samples")
    return dict(samples[-1])


def _process_inventory(target_cpus: set[int]):
    result = _rolling(target_cpus)
    sample = _terminal(result)
    inventory = list(sample.get("external_workloads") or []) + list(
        sample.get("d_state_inventory") or []
    )
    blockers: list[dict[str, Any]] = []
    if result.get("status") != "PASS":
        blockers.extend({
            **dict(row),
            "reason": "rolling_prestart_external_resource_overlap",
        } for row in sample.get("blocking_external_workloads") or [])
        blockers.extend({
            "pid": int(pid),
            "reason": "rolling_prestart_persistent_target_d_state",
        } for pid in sample.get("persistent_blocking_d_state_pids") or [])
        if not blockers:
            blockers.append({
                "reason": "rolling_prestart_gate_failed_closed",
                "errors": list(result.get("errors") or []),
            })
    return inventory, blockers


def _docker_inventory(
    target_cpus: set[int], intended_names: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    result = _rolling(target_cpus)
    sample = _terminal(result)
    target = dict(sample.get("target_local_resource_proof") or {})
    blockers: list[dict[str, Any]] = []
    if target.get("passed") is not True:
        blockers.append({
            "reason": "intended_target_cgroup_resource_readback_failed_closed",
            "errors": list(target.get("errors") or []),
        })
    if result.get("status") != "PASS":
        blockers.append({
            "reason": "rolling_prestart_gate_failed_closed",
            "errors": list(result.get("errors") or []),
        })
    inventory: list[dict[str, Any]] = [
        {
            "name": row["container"],
            "worker_id": row["worker_id"],
            "container_id": row["container_id"],
            "intended_successor_container": row["container"] in intended_names,
            "kernel_cgroup_readback": True,
            "passed": row["passed"],
            "checks": row["checks"],
            "pids": row["pids"],
            "cpuset_cpus": row["database_cpuset"],
            "cpuset_mems": row["database_cpuset_mems"],
            "database_like": True,
            "external_process_control_or_stop_performed": False,
        }
        for row in target.get("workers") or []
    ]
    inventory.extend({
        **dict(row),
        "name": None,
        "intended_successor_container": False,
        "kernel_proc_inventory": True,
        "external_process_control_or_stop_performed": False,
    } for row in sample.get("external_workloads") or [])
    inventory.append({
        "inventory_summary": True,
        "docker_api_used": False,
        "kernel_proc_and_cgroup_readback_authoritative": True,
        "rolling_prestart_gate_status": result.get("status"),
        "rolling_prestart_observed_seconds": result.get("observed_seconds"),
        "target_local_resource_proof_passed": target.get("passed") is True,
        "external_process_control_or_stop_performed": False,
        "cross_scene_completion_dependency": False,
    })
    return inventory, blockers


__all__ = ["_process_inventory", "_docker_inventory", "WAIVER_ID"]
