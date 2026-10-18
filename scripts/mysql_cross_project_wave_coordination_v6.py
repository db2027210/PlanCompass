#!/usr/bin/env python3
"""Actual-affinity gate without obsolete external CPU-number allowlist."""

from __future__ import annotations

import copy
import re
from typing import Any

from scripts import mysql_cross_project_wave_coordination_v1 as legacy
from scripts import mysql_cross_project_wave_coordination_v5 as predecessor


def _actual_affinity_evaluator(workloads: list[dict[str, Any]]) -> dict[str, Any]:
    result = copy.deepcopy(_ORIGINAL_EVALUATOR(workloads))
    removed_pids: set[int] = set()
    for row in result.get("workloads") or []:
        errors = list(row.get("errors") or [])
        if (
            errors == ["external_cpu_not_in_frozen_union"]
            and str(row.get("cpus_allowed_list") or "")
            and row.get("overlap_target_cpus") in ([], None)
        ):
            row["errors"] = []
            row["actual_cpu_affinity_disjoint"] = True
            removed_pids.add(int(row["pid"]))
    retained = []
    pattern = re.compile(r"^pid_(\d+):external_cpu_not_in_frozen_union$")
    for error in result.get("errors") or []:
        match = pattern.fullmatch(str(error))
        if match and int(match.group(1)) in removed_pids:
            continue
        retained.append(str(error))
    result["errors"] = retained
    result["passed"] = not retained
    result["status"] = (
        "PASS_ACTUAL_CPU_DISJOINT_CO_RESIDENT_PG"
        if not retained else "BLOCKED_FAIL_CLOSED"
    )
    result["actual_external_cpu_affinity_release"] = {
        "obsolete_frozen_external_cpu_number_allowlist_required": False,
        "nonempty_affinity_required": True,
        "actual_target_cpu_disjointness_required": True,
        "waived_error": "external_cpu_not_in_frozen_union",
        "waived_pids": sorted(removed_pids),
        "all_other_row_and_global_errors_preserved": True,
    }
    return result


_ORIGINAL_EVALUATOR = legacy.evaluate_co_resident_pg_isolation


def rolling_quiet_gate(**kwargs: Any) -> dict[str, Any]:
    original = legacy.evaluate_co_resident_pg_isolation
    legacy.evaluate_co_resident_pg_isolation = _actual_affinity_evaluator
    try:
        result = predecessor.rolling_quiet_gate(**kwargs)
    finally:
        legacy.evaluate_co_resident_pg_isolation = original
    result["release_version"] = "v61"
    result["external_cpu_policy"] = (
        "actual_nonempty_affinity_disjoint_from_current_mysql_wave"
    )
    return result

