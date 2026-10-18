#!/usr/bin/env python3
"""Actual-target-CPU scoped rolling non-interference gate successor.

The v4 rolling gate receives the CPU union of the workers participating in the
current physical wave, but its legacy PostgreSQL isolation evaluator still
compares external workloads with a historical all-MySQL-worker CPU union.
That can reject a CPU-disjoint co-resident workload merely because it overlaps
an inactive historical slot.  Scope that one readback to the actual target
CPUs supplied by the caller; retain every process, NUMA, memory/swap, storage,
PSI and D-state check from v4.
"""

from __future__ import annotations

from typing import Any

from scripts import mysql_cross_project_wave_coordination_v4 as predecessor


def rolling_quiet_gate(
    *, run_id: str, required_seconds: float, maximum_wait_seconds: float,
    maximum_load1: float = 16.0, interval_seconds: float = 1.0,
    target_cpus: set[int] | None = None,
) -> dict[str, Any]:
    scoped = set(target_cpus or ())
    if not scoped:
        raise ValueError("actual target CPU scope is required")

    legacy_module = predecessor.predecessor.predecessor.predecessor
    original_target_cpu_union = legacy_module._target_cpu_union
    legacy_module._target_cpu_union = lambda: set(scoped)
    try:
        result = predecessor.rolling_quiet_gate(
            run_id=run_id,
            required_seconds=required_seconds,
            maximum_wait_seconds=maximum_wait_seconds,
            maximum_load1=maximum_load1,
            interval_seconds=interval_seconds,
            target_cpus=scoped,
        )
    finally:
        legacy_module._target_cpu_union = original_target_cpu_union

    result["release_version"] = "v60"
    result["actual_target_cpu_scope_release"] = {
        "target_cpus": sorted(scoped),
        "historical_inactive_mysql_cpu_union_used": False,
        "external_process_inventory_preserved": True,
        "cpu_affinity_disjointness_required": True,
        "numa_memory_swap_storage_psi_and_d_state_checks_preserved": True,
        "external_process_control_or_stop_performed": False,
        "algorithm_candidate_or_acceptance_flow_changed": False,
    }
    return result

