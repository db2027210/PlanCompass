#!/usr/bin/env python3
"""Kernel/cgroup prestart inventory for client-primary Algorithm C.

This module preserves the existing process-pattern inventory but removes the
Docker list/inspect control-plane dependency.  Intended workers are proven
from their frozen container IDs and cgroup v1 controls; co-resident database
processes are inventoried from /proc and evaluated by actual CPU/storage
activity.  No external process or container is mutated.
"""

from __future__ import annotations

from typing import Any

from scripts import mysql_cross_project_wave_coordination_v27 as kernel_gate


WAIVED_CONTAINER = "gptuner_cost7_hybench"
WAIVER_ID = "kernel_dynamic_inventory_no_static_external_container_waiver_v1"


def _process_inventory(target_cpus: set[int]):
    selected, _ = kernel_gate._selected_workers(target_cpus)
    target_workers = {str(row["worker_id"]) for row in selected}
    external, d_state, _ = kernel_gate._inventory(target_cpus, target_workers)
    blockers: list[dict[str, Any]] = [
        {**row, "reason": "active_benchmark_or_controller_resource_overlap"}
        for row in external
        if not row.get("allowed_disjoint_frozen_mysql_sibling")
        and not row.get("dynamically_idle")
        and (not row.get("cpu_disjoint") or not row.get("storage_disjoint"))
    ]
    blockers.extend(
        {**row, "reason": "target_relevant_uninterruptible_process_present"}
        for row in d_state
        if row.get("sampled_processor_on_target") or row.get("target_device_open")
    )
    return external + d_state, blockers


def _docker_inventory(
    target_cpus: set[int], intended_names: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    target = kernel_gate.target_local_resource_proof(target_cpus)
    target_workers = set(target.get("selected_workers") or [])
    external, d_state, _ = kernel_gate._inventory(target_cpus, target_workers)
    blockers: list[dict[str, Any]] = []
    if target.get("passed") is not True:
        blockers.append({
            "reason": "intended_target_cgroup_resource_readback_failed_closed",
            "errors": list(target.get("errors") or []),
        })
    for row in external:
        if (
            not row.get("allowed_disjoint_frozen_mysql_sibling")
            and not row.get("dynamically_idle")
            and (not row.get("cpu_disjoint") or not row.get("storage_disjoint"))
        ):
            blockers.append({
                **row,
                "reason": "active_external_database_or_benchmark_not_isolated",
            })
    for row in d_state:
        if row.get("sampled_processor_on_target") or row.get("target_device_open"):
            blockers.append({
                **row,
                "reason": "target_relevant_uninterruptible_process_present",
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
        **row,
        "name": None,
        "intended_successor_container": False,
        "kernel_proc_inventory": True,
        "external_process_control_or_stop_performed": False,
    } for row in external)
    inventory.append({
        "inventory_summary": True,
        "docker_api_used": False,
        "kernel_proc_and_cgroup_readback_authoritative": True,
        "target_local_resource_proof_passed": target.get("passed") is True,
        "target_worker_ids": sorted(target_workers),
        "external_process_count": len(external),
        "d_state_process_count": len(d_state),
        "external_process_control_or_stop_performed": False,
        "cross_scene_completion_dependency": False,
    })
    return inventory, blockers


__all__ = ["_process_inventory", "_docker_inventory", "WAIVER_ID"]
