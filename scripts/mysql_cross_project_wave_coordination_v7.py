#!/usr/bin/env python3
"""Dynamic PG-container resource readback for MySQL-only isolation."""

from __future__ import annotations

import copy
from typing import Any

from scripts import mysql_cross_project_wave_coordination_v1 as legacy
from scripts import mysql_cross_project_wave_coordination_v5 as scoped_gate
from scripts import mysql_cross_project_wave_coordination_v6 as affinity_gate


def _dynamic_container_evaluator(workloads: list[dict[str, Any]]) -> dict[str, Any]:
    result = copy.deepcopy(affinity_gate._actual_affinity_evaluator(workloads))
    readback = result.get("container_readback") or {}
    rows = readback.get("containers") or []
    target = set(int(value) for value in result.get("target_cpu_union") or [])
    policy = legacy.json.loads(legacy.CONTRACT_PATH.read_text(encoding="utf-8"))[
        "co_resident_isolated_pg_allowance"
    ]
    expected_ids = {
        str(row["name"]): str(row["container_id"])
        for row in policy.get("containers") or []
    }
    dynamic_errors: list[str] = []
    for row in rows:
        name = str(row.get("name") or "")
        row_cpus = legacy.cpus(str(row.get("cpuset_cpus") or ""))
        mounts = [str(value) for value in row.get("mount_sources") or []]
        if name not in expected_ids or str(row.get("container_id")) != expected_ids[name]:
            dynamic_errors.append(f"container_identity_differs:{name}")
        if row.get("running") is not True:
            dynamic_errors.append(f"container_not_running:{name}")
        if not row_cpus or row_cpus & target:
            dynamic_errors.append(f"container_actual_cpu_overlaps_mysql:{name}")
        if not mounts or any(value.startswith("/disk2/") for value in mounts):
            dynamic_errors.append(f"container_storage_not_disk2_disjoint:{name}")
        if int(row.get("memory_bytes") or 0) <= 0:
            dynamic_errors.append(f"container_memory_limit_missing:{name}")
    stale = {
        str(error) for error in result.get("errors") or []
        if str(error).startswith("container_isolation_readback_differs:")
    }
    retained = [
        str(error) for error in result.get("errors") or [] if str(error) not in stale
    ]
    retained.extend(dynamic_errors)
    readback["legacy_exact_resource_errors"] = sorted(stale)
    readback["errors"] = dynamic_errors
    readback["passed"] = not dynamic_errors
    readback["dynamic_actual_resource_policy"] = {
        "exact_legacy_pg_cpu_and_memory_values_required": False,
        "container_identity_required": True,
        "running_required": True,
        "actual_cpu_disjointness_required": True,
        "disk2_mount_prohibited": True,
        "positive_memory_limit_required": True,
        "host_memory_swap_psi_and_rolling_decline_checked_by_outer_gate": True,
    }
    result["container_readback"] = readback
    result["errors"] = retained
    result["passed"] = not retained
    result["status"] = (
        "PASS_DYNAMIC_ACTUAL_CO_RESIDENT_PG"
        if not retained else "BLOCKED_FAIL_CLOSED"
    )
    return result


def rolling_quiet_gate(**kwargs: Any) -> dict[str, Any]:
    original = legacy.evaluate_co_resident_pg_isolation
    legacy.evaluate_co_resident_pg_isolation = _dynamic_container_evaluator
    try:
        result = scoped_gate.rolling_quiet_gate(**kwargs)
    finally:
        legacy.evaluate_co_resident_pg_isolation = original
    result["release_version"] = "v62"
    result["external_container_policy"] = "dynamic_actual_mysql_only_isolation"
    return result

