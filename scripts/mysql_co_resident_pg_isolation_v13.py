#!/usr/bin/env python3
"""Allow sealed inactive PG containers that own no current workload PID."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_co_resident_pg_isolation_v11 as predecessor
from scripts import mysql_co_resident_pg_isolation_v4 as implementation
from scripts import mysql_cross_project_wave_coordination_v1 as coordination


CONTRACT_PATH = (
    ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260819_v65"
    / "coordination_contract.json"
)
BASE_EVALUATE = predecessor.predecessor.evaluate_co_resident_pg_isolation
PID_CONTAINER_ID = implementation._pid_container_id


def evaluate_co_resident_pg_isolation(
    workloads: list[dict[str, Any]],
) -> dict[str, Any]:
    """Ignore only a sealed stopped profile unused by the current workload."""

    result = copy.deepcopy(BASE_EVALUATE(workloads))
    if not workloads or not isinstance(result.get("container_readback"), dict):
        return result
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    policy = contract.get("co_resident_isolated_pg_allowance") or {}
    inactive = policy.get("inactive_sealed_base_container_policy") or {}
    if (
        inactive.get("enabled") is not True
        or inactive.get("current_workload_pid_membership_required_absent") is not True
        or inactive.get("all_non_liveness_profile_checks_must_pass") is not True
    ):
        return result

    active_container_ids: set[str] = set()
    for row in workloads:
        try:
            container_id = PID_CONTAINER_ID(int(row["pid"]))
        except (KeyError, TypeError, ValueError, OSError):
            container_id = None
        if container_id:
            active_container_ids.add(str(container_id))

    readback = result["container_readback"]
    readback_errors = [str(value) for value in (readback.get("errors") or [])]
    result_errors = [str(value) for value in (result.get("errors") or [])]
    ignored: list[dict[str, Any]] = []
    for row in readback.get("containers") or []:
        if not isinstance(row, dict) or row.get("running") is not False:
            continue
        name = str(row.get("name") or "")
        container_id = str(row.get("container_id") or "")
        prefix = f"container_isolation_readback_differs:{name}:"
        profile_errors = [value for value in readback_errors if value.startswith(prefix)]
        if (
            not name
            or not container_id
            or container_id in active_container_ids
            or profile_errors != [prefix + "container_not_running"]
        ):
            continue
        readback_errors.remove(prefix + "container_not_running")
        if prefix + "container_not_running" in result_errors:
            result_errors.remove(prefix + "container_not_running")
        ignored.append({
            "name": name,
            "container_id": container_id,
            "reason": "sealed_profile_inactive_and_owns_no_current_workload_pid",
        })

    readback["errors"] = readback_errors
    readback["passed"] = not readback_errors
    readback["inactive_sealed_base_containers_ignored"] = ignored
    readback["active_workload_container_ids"] = sorted(active_container_ids)
    result["errors"] = result_errors
    result["passed"] = not result_errors
    result["status"] = (
        "PASS_ISOLATED_CO_RESIDENT_PG"
        if not result_errors else "BLOCKED_FAIL_CLOSED"
    )
    result["inactive_sealed_base_container_policy"] = {
        "applied": bool(ignored),
        "ignored_count": len(ignored),
        "external_mutation": False,
        "active_container_liveness_unchanged": True,
        "identity_cpu_memory_mount_checks_unchanged": True,
    }
    return result


def install() -> None:
    predecessor.CONTRACT_PATH = CONTRACT_PATH
    predecessor.predecessor.CONTRACT_PATH = CONTRACT_PATH
    predecessor.install()
    coordination.CONTRACT_PATH = CONTRACT_PATH
    coordination.evaluate_co_resident_pg_isolation = (
        evaluate_co_resident_pg_isolation
    )


def validate_release() -> None:
    """Validate the inherited release and install the v65 evaluator facade."""

    if not CONTRACT_PATH.is_file() or CONTRACT_PATH.is_symlink():
        raise RuntimeError(f"v65 coordination contract absent: {CONTRACT_PATH}")
    predecessor.validate_release()
    install()
    if coordination.evaluate_co_resident_pg_isolation is not evaluate_co_resident_pg_isolation:
        raise RuntimeError("v65 co-resident isolation evaluator was not installed")
    if coordination.CONTRACT_PATH != CONTRACT_PATH:
        raise RuntimeError("v65 coordination contract pointer differs")


if __name__ == "__main__":
    validate_release()
