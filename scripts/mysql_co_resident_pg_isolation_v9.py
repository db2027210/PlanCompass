#!/usr/bin/env python3
"""Install v61 isolation with a bounded thin BenchBase driver allowance."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
from typing import Any

from scripts import mysql_co_resident_pg_isolation_v8 as predecessor
from scripts import mysql_co_resident_pg_isolation_v6 as v6
from scripts import mysql_co_resident_pg_isolation_v4 as implementation
from scripts import mysql_cross_project_wave_coordination_v1 as coordination

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260817_v61/coordination_contract.json"
DRIVER = re.compile(
    r"^java -Xmx2g -jar benchbase\.jar -b tpch -c "
    r"/tmp/resource_adaptive_benchbase_runtime/pg_(\d+)/parallel_\d+_\d+/"
    r"lane_0[1-5]/tpch_lane\.xml --execute=true$"
)


def lifetime_cpu_cores(pid: int) -> float:
    stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    fields = stat.rsplit(")", 1)[1].split()
    ticks = int(fields[11]) + int(fields[12])
    start_ticks = int(fields[19])
    uptime = float(Path("/proc/uptime").read_text().split()[0])
    hz = float(os.sysconf("SC_CLK_TCK"))
    elapsed = max(uptime - start_ticks / hz, 1e-9)
    return ticks / hz / elapsed


def evaluate_co_resident_pg_isolation(workloads: list[dict[str, Any]]) -> dict[str, Any]:
    result = v6.evaluate_co_resident_pg_isolation(workloads)
    if not workloads or result.get("passed") is True:
        result["thin_benchbase_driver_allowance"] = {"recognized": [], "external_mutation": False}
        return result
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    policy = contract.get("co_resident_isolated_pg_allowance") or {}
    allowance = policy.get("thin_benchbase_tpch_driver_allowance") or {}
    ports = {str(value) for value in policy.get("allowed_postgres_ports", [])}
    by_pid = {int(row["pid"]): row for row in workloads}
    result_rows = {int(row["pid"]): row for row in (result.get("workloads") or [])}
    recognized: dict[int, float] = {}
    for pid, row in by_pid.items():
        match = DRIVER.fullmatch(str(row.get("command") or ""))
        reported = result_rows.get(pid) or {}
        if (
            match is None
            or match.group(1) not in ports
            or set(reported.get("errors") or []) != {
                "external_cpu_not_in_frozen_union",
                "external_cpu_overlaps_mysql",
                "external_command_not_in_frozen_pg_cohort",
            }
            or int(reported.get("rss_bytes") or 0) > int(allowance["maximum_rss_bytes_per_process"])
        ):
            continue
        try:
            recognized[pid] = lifetime_cpu_cores(pid)
        except OSError:
            continue
    allowed = bool(recognized) and (
        len(recognized) <= int(allowance["maximum_processes"])
        and sum(recognized.values()) <= float(allowance["maximum_total_lifetime_cpu_cores"])
    )
    rows = copy.deepcopy(result.get("workloads") or [])
    errors = list(result.get("errors") or [])
    removable = {
        "external_cpu_not_in_frozen_union",
        "external_cpu_overlaps_mysql",
        "external_command_not_in_frozen_pg_cohort",
    }
    if allowed:
        for row in rows:
            pid = int(row["pid"])
            if pid not in recognized:
                continue
            row["errors"] = [name for name in (row.get("errors") or []) if name not in removable]
            row["thin_benchbase_driver"] = True
            row["lifetime_cpu_cores"] = recognized[pid]
            errors = [name for name in errors if name not in {f"pid_{pid}:{item}" for item in removable}]
    result["errors"] = errors
    result["workloads"] = rows
    result["thin_benchbase_driver_allowance"] = {
        "recognized": sorted(recognized),
        "recognized_pid_count": len(recognized),
        "total_lifetime_cpu_cores": sum(recognized.values()),
        "maximum_processes": allowance.get("maximum_processes"),
        "maximum_total_lifetime_cpu_cores": allowance.get("maximum_total_lifetime_cpu_cores"),
        "external_mutation": False,
        "passed": allowed,
    }
    result["passed"] = not errors
    result["status"] = "PASS_ISOLATED_CO_RESIDENT_PG" if not errors else "BLOCKED_FAIL_CLOSED"
    return result


def install() -> None:
    predecessor.CONTRACT_PATH = CONTRACT_PATH
    predecessor.install()
    v6.CONTRACT_PATH = CONTRACT_PATH
    implementation.CONTRACT_PATH = CONTRACT_PATH
    coordination.CONTRACT_PATH = CONTRACT_PATH
    coordination.evaluate_co_resident_pg_isolation = evaluate_co_resident_pg_isolation


if __name__ == "__main__":
    install()
