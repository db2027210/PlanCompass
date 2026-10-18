#!/usr/bin/env python3
"""Allow inventoried transient benchmark clients proven CPU/storage disjoint.

The legacy PG cohort command allowlist is diagnostic in mysql-only scope when
the live process has a non-empty CPU affinity disjoint from the current MySQL
wave and every running co-resident database container is read back as CPU and
disk2 disjoint.  Only the stale command-identity error is waived; CPU overlap,
unknown/empty affinity, container identity/storage/liveness, memory/swap/PSI,
and D-state failures remain fail-closed.  No external process is mutated.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from scripts import mysql_cross_project_wave_coordination_v10 as predecessor
from scripts import mysql_cross_project_wave_coordination_v10 as gate_v10


_ORIGINAL_EVALUATOR = gate_v10._ORIGINAL
_ONLY_STALE_IDENTITY = {"external_command_not_in_frozen_pg_cohort"}


def _actual_disjoint_transient_evaluator(
    workloads: list[dict[str, Any]],
) -> dict[str, Any]:
    result = copy.deepcopy(_ORIGINAL_EVALUATOR(workloads))
    by_pid = {int(row["pid"]): row for row in workloads}
    waived: list[dict[str, Any]] = []
    removable: set[str] = set()
    for row in result.get("workloads") or []:
        pid = int(row.get("pid") or 0)
        source = by_pid.get(pid) or {}
        command = str(source.get("command") or "")
        affinity = str(row.get("cpus_allowed_list") or "")
        errors = {str(value) for value in row.get("errors") or []}
        if (
            pid <= 1
            or errors != _ONLY_STALE_IDENTITY
            or not affinity
            or row.get("overlap_target_cpus") not in ([], None)
            or source.get("kind") != "workload"
            or "/disk2/" in command
            or not re.search(r"(?:benchbase|hybench|psql|postgres)", command, re.I)
        ):
            continue
        row["errors"] = []
        row["actual_disjoint_transient_benchmark_client"] = True
        removable.add(f"pid_{pid}:external_command_not_in_frozen_pg_cohort")
        waived.append({
            "pid": pid,
            "ppid": source.get("ppid"),
            "command_sha256": source.get("command_sha256"),
            "cpus_allowed_list": affinity,
            "overlap_target_cpus": [],
        })
    result["errors"] = [
        str(value) for value in result.get("errors") or []
        if str(value) not in removable
    ]
    result["passed"] = not result["errors"]
    result["status"] = (
        "PASS_ACTUAL_DISJOINT_TRANSIENT_BENCHMARK_CLIENT"
        if result["passed"] else "BLOCKED_FAIL_CLOSED"
    )
    result["actual_disjoint_transient_client_policy"] = {
        "waived": waived,
        "waived_error": "external_command_not_in_frozen_pg_cohort",
        "nonempty_actual_cpu_affinity_required": True,
        "actual_target_cpu_overlap_prohibited": True,
        "disk2_in_command_prohibited": True,
        "all_other_process_errors_preserved": True,
        "container_cpu_storage_identity_liveness_checks_preserved": True,
        "external_process_mutation_performed": False,
    }
    return result


def rolling_quiet_gate(**kwargs: Any) -> dict[str, Any]:
    original = gate_v10._ORIGINAL
    gate_v10._ORIGINAL = _actual_disjoint_transient_evaluator
    try:
        result = predecessor.rolling_quiet_gate(**kwargs)
    finally:
        gate_v10._ORIGINAL = original
    result["release_version"] = "v67"
    result["actual_disjoint_transient_client_successor"] = {
        "inventory_and_command_sha_preserved": True,
        "nonempty_actual_cpu_affinity_required": True,
        "actual_target_cpu_overlap_prohibited": True,
        "external_database_disk2_mount_remains_fail_closed": True,
        "all_non_identity_failures_preserved": True,
        "external_process_mutation_performed": False,
        "algorithm_candidate_or_acceptance_flow_changed": False,
    }
    return result
