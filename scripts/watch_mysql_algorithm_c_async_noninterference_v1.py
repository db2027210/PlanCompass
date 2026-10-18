#!/usr/bin/env python3
"""Observe one asynchronous Algorithm C scene without taking a resource lock.

The observer has no cross-scene barrier or lease.  During a scored physical
measurement it fails only the affected scene when persistent co-resident
PostgreSQL isolation failure or the known heavyweight heterogeneous graph
workload is confirmed for 15 seconds.  Reset and plan-capture stages are not
scored; every following measurement still has its own premeasurement quiet
gate and remains protected here for its entire duration.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v1 as coordination
from scripts import mysql_co_resident_pg_isolation_v13 as isolation_v13
from scripts import watch_mysql_formal_external_postgres_interference_v1 as base
from scripts import watch_mysql_formal_external_postgres_interference_v5 as confirmed
from scripts import watch_mysql_formal_external_postgres_interference_v11 as retry


HISTORY = ROOT / "history/mysql_four_scene_20260809"
UNIT_PREFIX = "mysql-algc-async-"
RUN_PATTERN = re.compile(r"[A-Za-z0-9_.-]{20,180}")
CONFIRMATION_SECONDS = 15.0
EXTERNAL_DATABASE_MAX_CPU_CORES = 0.10
EXTERNAL_DATABASE_MAX_IO_BYTES_PER_SECOND = 1024 * 1024
EXTERNAL_DATABASE_MAX_RSS_GROWTH_BYTES = 256 * 1024 * 1024
HETEROGENEOUS_MARKER = b"run_heterogeneous\0--application=bfs"
RESTARTING_EXTERNAL_DATABASE_CONTAINERS = {
    "zzk-opengauss": "d945b08e1a2ad736a2c8ada4704f7123165ea8dc4c41ed3fcfeaf1d3b4d718bb",
    "mongo": "4e0f56d16ce0397222c58236b8182c35ea41189bd9a8f776122fe0da135fc70d",
}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def append(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def heavyweight_external_processes() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            command = (proc / "cmdline").read_bytes()
            if HETEROGENEOUS_MARKER not in command:
                continue
            status = {}
            for line in (proc / "status").read_text(
                encoding="utf-8", errors="replace",
            ).splitlines():
                key, separator, value = line.partition(":")
                if separator:
                    status[key] = value.strip()
            rows.append({
                "pid": int(proc.name),
                "rss_kib": int((status.get("VmRSS") or "0 kB").split()[0]),
                "cpus_allowed_list": status.get("Cpus_allowed_list"),
                "mems_allowed_list": status.get("Mems_allowed_list"),
                "identity": "run_heterogeneous_bfs",
            })
        except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError):
            continue
    return sorted(rows, key=lambda row: int(row["pid"]))


def confirmed_heavyweight_processes(
    first: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    first_pids = {int(row["pid"]) for row in first}
    time.sleep(CONFIRMATION_SECONDS)
    return [
        row for row in heavyweight_external_processes()
        if int(row["pid"]) in first_pids
    ]


def restarting_external_database_processes() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            cgroup = (proc / "cgroup").read_text(
                encoding="utf-8", errors="replace",
            )
            owner = next(
                (
                    name for name, container_id
                    in RESTARTING_EXTERNAL_DATABASE_CONTAINERS.items()
                    if container_id in cgroup
                ),
                None,
            )
            if owner is None:
                continue
            command = (
                (proc / "cmdline").read_bytes().replace(b"\0", b" ")
                .decode("utf-8", errors="replace").strip()
            )
            if re.search(
                r"(?:^|/)(?:gaussdb|mongod|mysqld|postgres|redis-server)(?:\s|$)",
                command,
                re.I,
            ) is None:
                continue
            status = {}
            for line in (proc / "status").read_text(
                encoding="utf-8", errors="replace",
            ).splitlines():
                key, separator, value = line.partition(":")
                if separator:
                    status[key] = value.strip()
            stat_fields = (proc / "stat").read_text(
                encoding="ascii", errors="replace",
            ).rsplit(")", 1)[1].split()
            io_fields = {}
            for line in (proc / "io").read_text(
                encoding="ascii", errors="replace",
            ).splitlines():
                key, separator, value = line.partition(":")
                if separator:
                    io_fields[key] = int(value.strip())
            rows.append({
                "pid": int(proc.name),
                "container_name": owner,
                "command": command,
                "cpus_allowed_list": status.get("Cpus_allowed_list"),
                "mems_allowed_list": status.get("Mems_allowed_list"),
                "rss_bytes": int(
                    (status.get("VmRSS") or "0 kB").split()[0]
                ) * 1024,
                "cpu_seconds": (
                    int(stat_fields[11]) + int(stat_fields[12])
                ) / float(os.sysconf("SC_CLK_TCK")),
                "io_read_bytes": int(io_fields.get("read_bytes") or 0),
                "io_write_bytes": int(io_fields.get("write_bytes") or 0),
                "identity": "restarting_external_database_cgroup_process",
            })
        except (
            FileNotFoundError, PermissionError, ProcessLookupError,
            OSError, ValueError, IndexError,
        ):
            continue
    return sorted(rows, key=lambda row: int(row["pid"]))


def confirmed_restarting_database_processes(
    first: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    first_by_pid = {int(row["pid"]): row for row in first}
    time.sleep(CONFIRMATION_SECONDS)
    blockers: list[dict[str, Any]] = []
    for row in restarting_external_database_processes():
        previous = first_by_pid.get(int(row["pid"]))
        if previous is None:
            continue
        cpu_cores = max(
            0.0,
            (float(row["cpu_seconds"]) - float(previous["cpu_seconds"]))
            / CONFIRMATION_SECONDS,
        )
        io_bytes_per_second = max(
            0.0,
            (
                int(row["io_read_bytes"]) + int(row["io_write_bytes"])
                - int(previous["io_read_bytes"])
                - int(previous["io_write_bytes"])
            ) / CONFIRMATION_SECONDS,
        )
        rss_growth = max(
            0, int(row["rss_bytes"]) - int(previous["rss_bytes"]),
        )
        if (
            cpu_cores > EXTERNAL_DATABASE_MAX_CPU_CORES
            or io_bytes_per_second > EXTERNAL_DATABASE_MAX_IO_BYTES_PER_SECOND
            or rss_growth > EXTERNAL_DATABASE_MAX_RSS_GROWTH_BYTES
        ):
            blockers.append({
                **row,
                "rolling_window_seconds": CONFIRMATION_SECONDS,
                "rolling_cpu_cores": cpu_cores,
                "rolling_io_bytes_per_second": io_bytes_per_second,
                "rolling_rss_growth_bytes": rss_growth,
                "maximum_cpu_cores": EXTERNAL_DATABASE_MAX_CPU_CORES,
                "maximum_io_bytes_per_second": (
                    EXTERNAL_DATABASE_MAX_IO_BYTES_PER_SECOND
                ),
                "maximum_rss_growth_bytes": (
                    EXTERNAL_DATABASE_MAX_RSS_GROWTH_BYTES
                ),
            })
    return blockers


def measuring(stage: str) -> bool:
    return stage.endswith("_MEASURING")


def stop_async_fail_closed(
    *, output: Path, formal_unit: str, main_pid: int, state_path: Path,
    event: str, evidence: dict[str, Any],
) -> int:
    """Stop one scene and make the zero-credit reason win the SIGINT race."""

    result = base.stop_formal_fail_closed(
        output=output,
        formal_unit=formal_unit,
        main_pid=main_pid,
        run_state_path=state_path,
        event=event,
        evidence=evidence,
    )
    if not state_path.is_file() or state_path.is_symlink():
        return result
    value = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        return result
    if value.get("status") == "FAILED_CLOSED" and str(
        value.get("error") or ""
    ).startswith("KeyboardInterrupt"):
        updated = copy.deepcopy(value)
        updated["stage"] = "FAILED"
        updated["failed_at"] = now()
        updated["error"] = f"RuntimeError: runtime watchdog stopped formal run after {event}"
        updated["watchdog_interruption"] = {
            "event": event,
            "terminal_event": "formal_failed_closed_after_sigint",
            "runtime_stage": evidence.get("runtime_stage"),
            "evidence": copy.deepcopy(evidence),
            "formal_state_credit_increment": 0,
            "interrupted_candidate_replay_prohibited": True,
        }
        temporary = state_path.with_name(
            f".{state_path.name}.{os.getpid()}.tmp"
        )
        temporary.write_text(
            json.dumps(updated, ensure_ascii=False, sort_keys=True, indent=2)
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, state_path)
        digest = coordination.sha256(state_path)
        state_path.with_name(state_path.name + ".sha256").write_text(
            f"{digest}  {state_path.name}\n", encoding="ascii",
        )
        append(output, {
            "captured_at": now(),
            "event": "async_terminal_interruption_reconciled",
            "formal_state_credit_increment": 0,
        })
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--formal-unit", required=True)
    parser.add_argument("--formal-run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--interval-seconds", type=float, default=1.0)
    args = parser.parse_args()
    # Match the asynchronous v49-v54 controller's effective co-resident PG
    # evaluator.  The base v1 evaluator still enforces an obsolete fixed CPU
    # allow-list and rejects disjoint, read-only PG clients that the sealed
    # asynchronous quiet gate correctly accepts.
    isolation_v13.install()
    if (
        not args.formal_unit.startswith(UNIT_PREFIX)
        or not args.formal_unit.endswith(".service")
        or RUN_PATTERN.fullmatch(args.formal_run_id) is None
        or args.formal_run_id not in args.formal_unit
    ):
        parser.error("asynchronous formal unit/run identity differs")
    output = args.output.resolve()
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh observer output required: {output}")
    state_path = HISTORY / args.formal_run_id / "run_state.json"
    append(output, {
        "captured_at": now(),
        "event": "async_noninterference_observer_started",
        "formal_unit": args.formal_unit,
        "formal_run_id": args.formal_run_id,
        "confirmation_seconds": CONFIRMATION_SECONDS,
        "cross_scene_lock": False,
        "resource_lease": False,
        "sibling_completion_dependency": False,
    })
    previous_fingerprint = ""
    while True:
        fields = retry.unit_fields(args.formal_unit)
        main_pid = int(fields.get("MainPID") or 0)
        if fields.get("ActiveState") in {"inactive", "failed"} or main_pid <= 0:
            append(output, {
                "captured_at": now(),
                "event": "formal_unit_terminal",
                "unit_fields": fields,
            })
            return 0
        command = Path(f"/proc/{main_pid}/cmdline").read_bytes().replace(b"\0", b" ")
        if (
            args.formal_run_id.encode() not in command
            or b"run_mysql_algorithm_c_async_scene" not in command
        ):
            raise RuntimeError("asynchronous formal MainPID identity differs")
        state = base.load_run_state(args.formal_run_id)
        stage = str(state.get("stage") or "")
        inventory = coordination.external_process_inventory(
            args.formal_run_id, {0, 1},
        )
        workloads = coordination.sanitized_rows(inventory["workloads"])
        isolation = coordination.evaluate_co_resident_pg_isolation(
            inventory["workloads"]
        )
        heavyweight = heavyweight_external_processes()
        restarting_databases = restarting_external_database_processes()
        fingerprint = json.dumps({
            "stage": stage,
            "postgres_workload_pids": [row["pid"] for row in workloads],
            "heavyweight_pids": [row["pid"] for row in heavyweight],
            "restarting_database_pids": [
                row["pid"] for row in restarting_databases
            ],
        }, sort_keys=True)
        if fingerprint != previous_fingerprint:
            append(output, {
                "captured_at": now(),
                "event": "co_resident_inventory_changed",
                "runtime_stage": stage,
                "postgres_workloads": workloads,
                "co_resident_pg_isolation": isolation,
                "heavyweight_external_processes": heavyweight,
                "restarting_external_database_processes": (
                    restarting_databases
                ),
            })
            previous_fingerprint = fingerprint

        if measuring(stage) and heavyweight:
            persistent = confirmed_heavyweight_processes(heavyweight)
            if persistent:
                evidence = {
                    "runtime_stage": stage,
                    "blockers": persistent,
                    "confirmation_seconds": CONFIRMATION_SECONDS,
                    "cross_scene_lock_held": False,
                    "resource_lease_held": False,
                }
                return stop_async_fail_closed(
                    output=output,
                    formal_unit=args.formal_unit,
                    main_pid=main_pid,
                    state_path=state_path,
                    event=(
                        "runtime_interference_detected_under_exclusive_wave_lease"
                    ),
                    evidence=evidence,
                )
        if measuring(stage) and restarting_databases:
            persistent_databases = confirmed_restarting_database_processes(
                restarting_databases,
            )
            if persistent_databases:
                evidence = {
                    "runtime_stage": stage,
                    "blockers": persistent_databases,
                    "confirmation_seconds": CONFIRMATION_SECONDS,
                    "cross_scene_lock_held": False,
                    "resource_lease_held": False,
                }
                return stop_async_fail_closed(
                    output=output,
                    formal_unit=args.formal_unit,
                    main_pid=main_pid,
                    state_path=state_path,
                    event="runtime_restarting_external_database_interference",
                    evidence=evidence,
                )
        if measuring(stage) and workloads and isolation.get("passed") is not True:
            persistent_pg = confirmed.confirmed_workload_isolation_failure(
                args.formal_run_id,
                isolation,
                target_mems={0, 1},
                confirmation_seconds=CONFIRMATION_SECONDS,
            )
            if persistent_pg is not None:
                evidence = {
                    "runtime_stage": stage,
                    "blockers": coordination.sanitized_rows(
                        persistent_pg["inventory"]["workloads"]
                    ),
                    "co_resident_pg_isolation": persistent_pg["isolation"],
                    "confirmation_seconds": CONFIRMATION_SECONDS,
                    "cross_scene_lock_held": False,
                    "resource_lease_held": False,
                }
                return stop_async_fail_closed(
                    output=output,
                    formal_unit=args.formal_unit,
                    main_pid=main_pid,
                    state_path=state_path,
                    event=(
                        "runtime_interference_detected_under_exclusive_wave_lease"
                    ),
                    evidence=evidence,
                )
        time.sleep(max(1.0, args.interval_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
