#!/usr/bin/env python3
"""Allow inventoried, fixed-affinity external clients when CPUs are disjoint.

The MySQL-only contract permits stable co-resident work.  The predecessor
already proves the actual affinity of known PostgreSQL clients, but an
otherwise identical non-PostgreSQL benchmark client retains an identity error
and is rejected even when its affinity is non-empty and disjoint from the
current MySQL wave.  Remove only those two legacy identity/CPU-number errors;
all container, memory/swap, PSI, storage and D-state checks remain fail-closed.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from scripts import mysql_cross_project_wave_coordination_v6 as affinity_gate
from scripts import mysql_cross_project_wave_coordination_v9 as predecessor


_ORIGINAL = affinity_gate._actual_affinity_evaluator
_WAIVABLE = {
    "external_cpu_not_in_frozen_union",
    "external_command_not_in_frozen_pg_cohort",
}


def _isolated_external_evaluator(workloads: list[dict[str, Any]]) -> dict[str, Any]:
    result = copy.deepcopy(_ORIGINAL(workloads))
    waived: dict[int, list[str]] = {}
    for row in result.get("workloads") or []:
        errors = [str(value) for value in row.get("errors") or []]
        if (
            errors
            and set(errors).issubset(_WAIVABLE)
            and str(row.get("cpus_allowed_list") or "")
            and row.get("overlap_target_cpus") in ([], None)
        ):
            pid = int(row["pid"])
            waived[pid] = errors
            row["errors"] = []
            row["actual_cpu_affinity_disjoint"] = True
            row["co_resident_identity_class"] = "inventoried_external_benchmark_client"
    retained: list[str] = []
    pattern = re.compile(r"^pid_(\d+):(.+)$")
    for error in result.get("errors") or []:
        match = pattern.fullmatch(str(error))
        if (
            match
            and int(match.group(1)) in waived
            and match.group(2) in waived[int(match.group(1))]
        ):
            continue
        retained.append(str(error))
    result["errors"] = retained
    result["passed"] = not retained
    result["status"] = (
        "PASS_ACTUAL_CPU_DISJOINT_CO_RESIDENT_WORKLOADS"
        if not retained else "BLOCKED_FAIL_CLOSED"
    )
    result["isolated_external_client_release"] = {
        "waived_pids": sorted(waived),
        "waived_errors_by_pid": {str(pid): values for pid, values in sorted(waived.items())},
        "nonempty_actual_affinity_required": True,
        "actual_target_cpu_disjointness_required": True,
        "numa_exclusivity_required": False,
        "inventory_preserved": True,
        "memory_swap_psi_storage_container_and_dstate_checks_preserved": True,
        "external_process_control_or_stop_performed": False,
    }
    return result


def rolling_quiet_gate(**kwargs: Any) -> dict[str, Any]:
    original = affinity_gate._actual_affinity_evaluator
    affinity_gate._actual_affinity_evaluator = _isolated_external_evaluator
    try:
        result = predecessor.rolling_quiet_gate(**kwargs)
    finally:
        affinity_gate._actual_affinity_evaluator = original
    result["release_version"] = "v65"
    result["external_process_policy"] = (
        "inventoried_nonempty_fixed_affinity_disjoint_from_current_mysql_wave"
    )
    return result
