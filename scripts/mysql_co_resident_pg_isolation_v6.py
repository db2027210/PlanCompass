#!/usr/bin/env python3
"""Install v58 isolation with read-only transient launcher recognition."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

from scripts import mysql_co_resident_pg_isolation_v5 as predecessor
from scripts import mysql_co_resident_pg_isolation_v4 as implementation
from scripts import mysql_cross_project_wave_coordination_v1 as coordination


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260817_v58"
    / "coordination_contract.json"
)
BASE_EVALUATE = implementation.evaluate_co_resident_pg_isolation
MAX_TRANSIENT_LAUNCHER_RSS_BYTES = 8 * 1024 * 1024


def _transient_launcher_pids(
    workloads: list[dict[str, Any]], result: dict[str, Any],
) -> set[int]:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    policy = contract.get("co_resident_isolated_pg_allowance") or {}
    allowed = coordination.cpus(str(policy.get("allowed_external_cpu_union") or ""))
    target = coordination._target_cpu_union()
    full_host = set(range(os.cpu_count() or 1))
    by_pid = {int(row["pid"]): row for row in workloads}
    children: dict[int, list[dict[str, Any]]] = {}
    for row in workloads:
        children.setdefault(int(row.get("ppid") or 0), []).append(row)
    result_rows = {
        int(row["pid"]): row for row in (result.get("workloads") or [])
    }
    recognized: set[int] = set()
    for pid, row in by_pid.items():
        reported = result_rows.get(pid) or {}
        row_errors = set(reported.get("errors") or [])
        row_cpus = coordination.cpus(str(row.get("cpus_allowed_list") or ""))
        command = str(row.get("command") or "")
        if (
            row_errors != {
                "external_cpu_not_in_frozen_union",
                "external_cpu_overlaps_mysql",
            }
            or row_cpus != full_host
            or int(reported.get("rss_bytes") or 0) > MAX_TRANSIENT_LAUNCHER_RSS_BYTES
            or "/disk1/resource_adaptive/" not in command
            or "/disk2/" in command
        ):
            continue
        valid_child = False
        for child in children.get(pid, []):
            child_cpus = coordination.cpus(
                str(child.get("cpus_allowed_list") or "")
            )
            child_command = str(child.get("command") or "")
            if (
                child_cpus
                and child_cpus <= allowed
                and not child_cpus & target
                and "/disk1/resource_adaptive/" in child_command
                and "/disk2/" not in child_command
            ):
                valid_child = True
                break
        if valid_child:
            recognized.add(pid)
    return recognized


def evaluate_co_resident_pg_isolation(
    workloads: list[dict[str, Any]],
) -> dict[str, Any]:
    result = BASE_EVALUATE(workloads)
    if not workloads or result.get("passed") is True:
        result["transient_launcher_recognition"] = {
            "recognized": [],
            "recognized_pid_count": 0,
            "external_mutation": False,
        }
        return result
    recognized = _transient_launcher_pids(workloads, result)
    rows = copy.deepcopy(result.get("workloads") or [])
    errors = list(result.get("errors") or [])
    removable = {
        "external_cpu_not_in_frozen_union",
        "external_cpu_overlaps_mysql",
    }
    for row in rows:
        pid = int(row["pid"])
        if pid not in recognized:
            continue
        row["errors"] = [
            value for value in (row.get("errors") or []) if value not in removable
        ]
        row["transient_launcher_wrapper"] = True
        row["external_mutation"] = False
        errors = [
            value for value in errors
            if value not in {f"pid_{pid}:{name}" for name in removable}
        ]
    result["errors"] = errors
    result["workloads"] = rows
    result["transient_launcher_recognition"] = {
        "recognized": sorted(recognized),
        "recognized_pid_count": len(recognized),
        "maximum_rss_bytes": MAX_TRANSIENT_LAUNCHER_RSS_BYTES,
        "required_direct_child_cpu_disjointness": True,
        "required_disk1_command_lineage": True,
        "external_mutation": False,
    }
    result["passed"] = not errors
    result["status"] = (
        "PASS_ISOLATED_CO_RESIDENT_PG"
        if not errors else "BLOCKED_FAIL_CLOSED"
    )
    return result


def install() -> None:
    predecessor.install()
    implementation.CONTRACT_PATH = CONTRACT_PATH
    coordination.CONTRACT_PATH = CONTRACT_PATH
    coordination.evaluate_co_resident_pg_isolation = evaluate_co_resident_pg_isolation


if __name__ == "__main__":
    install()
