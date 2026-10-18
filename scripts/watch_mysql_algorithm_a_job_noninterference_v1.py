#!/usr/bin/env python3
"""Observe JOB Algorithm A cache preparation and scoring without a lock."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_co_resident_pg_isolation_v13 as isolation_v13
from scripts import mysql_cross_project_wave_coordination_v1 as coordination
from scripts import watch_mysql_algorithm_c_async_noninterference_v1 as async_watch
from scripts import watch_mysql_formal_external_postgres_interference_v5 as confirmed
from scripts import watch_mysql_formal_external_postgres_interference_v11 as retry


HISTORY = ROOT / "history/mysql_four_scene_20260809"
UNIT_PREFIX = "mysql-alga-"
RUN_PATTERN = re.compile(r"[A-Za-z0-9_.-]{20,180}")
CONFIRMATION_SECONDS = 15.0


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def append(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def atomic_state(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    digest = coordination.sha256(path)
    path.with_name(path.name + ".sha256").write_text(
        f"{digest}  {path.name}\n", encoding="ascii",
    )


def stop_job_fail_closed(
    *, output: Path, formal_unit: str, main_pid: int, state_path: Path,
    event: str, evidence: dict[str, Any],
) -> int:
    append(output, {
        "captured_at": now(),
        "event": event,
        "formal_pid": main_pid,
        "action": "SIGINT_then_bounded_user_systemd_stop",
        **copy.deepcopy(evidence),
    })
    try:
        os.kill(main_pid, signal.SIGINT)
    except ProcessLookupError:
        pass
    terminal = {}
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        terminal = retry.unit_fields(formal_unit)
        if terminal.get("ActiveState") in {"inactive", "failed"}:
            break
        time.sleep(1.0)
    else:
        subprocess.run(
            ["systemctl", "--user", "stop", formal_unit],
            cwd=ROOT, capture_output=True, text=True, check=False, timeout=30,
        )
        terminal = retry.unit_fields(formal_unit)

    state = load(state_path)
    prior_stage = str(evidence.get("runtime_stage") or state.get("stage") or "")
    state["status"] = "INTERRUPTED_FAIL_CLOSED"
    state["stage"] = "FAILED"
    state["stage_updated_at"] = now()
    state["error"] = f"RuntimeError: {event}"
    state["watchdog_interruption"] = {
        "event": event,
        "runtime_stage": prior_stage,
        "terminal_unit_fields": terminal,
        "evidence": copy.deepcopy(evidence),
        "formal_state_credit_increment": 0,
        "interrupted_wave_credit_prohibited": True,
    }
    atomic_state(state_path, state)
    append(output, {
        "captured_at": now(),
        "event": "job_formal_interruption_sealed",
        "unit_fields": terminal,
        "formal_state_credit_increment": 0,
    })
    return 3


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--formal-unit", required=True)
    parser.add_argument("--formal-run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--interval-seconds", type=float, default=1.0)
    args = parser.parse_args()
    if (
        not args.formal_unit.startswith(UNIT_PREFIX)
        or not args.formal_unit.endswith(".service")
        or RUN_PATTERN.fullmatch(args.formal_run_id) is None
    ):
        parser.error("JOB formal unit/run identity differs")
    output = args.output.resolve()
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh observer output required: {output}")
    state_path = HISTORY / args.formal_run_id / "algorithm_a_state.json"
    isolation_v13.install()
    append(output, {
        "captured_at": now(),
        "event": "job_noninterference_observer_started",
        "formal_unit": args.formal_unit,
        "formal_run_id": args.formal_run_id,
        "confirmation_seconds": CONFIRMATION_SECONDS,
        "cross_scene_lock": False,
        "resource_lease": False,
        "sibling_completion_dependency": False,
        "cache_precondition_is_not_scored_measurement": True,
        "cache_precondition_protected_from_persistent_interference": True,
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
            or b"run_mysql_algorithm_a_job" not in command
        ):
            raise RuntimeError("JOB formal MainPID identity differs")
        if not state_path.is_file():
            time.sleep(max(1.0, args.interval_seconds))
            continue
        state = load(state_path)
        stage = str(state.get("stage") or "")
        measuring = stage.endswith("_MEASURING")
        # A heavyweight overlapping workload can exhaust the JOB NUMA node
        # before scored measurement begins.  Protect the full-suite cache
        # primer as well: it earns no score, but losing mysqld here forces a
        # container restart and invalidates every byte of primed cache state.
        cache_preconditioning = stage.endswith(
            "_JOB_CACHE_PRECONDITION_FULL113"
        )
        protected_execution = measuring or cache_preconditioning
        inventory = coordination.external_process_inventory(
            args.formal_run_id, {0, 1},
        )
        workloads = coordination.sanitized_rows(inventory["workloads"])
        isolation = coordination.evaluate_co_resident_pg_isolation(
            inventory["workloads"]
        )
        heavyweight = async_watch.heavyweight_external_processes()
        fingerprint = json.dumps({
            "stage": stage,
            "measuring": measuring,
            "cache_preconditioning": cache_preconditioning,
            "postgres_workload_pids": [row["pid"] for row in workloads],
            "heavyweight_pids": [row["pid"] for row in heavyweight],
            "isolation_passed": isolation.get("passed"),
        }, sort_keys=True)
        if fingerprint != previous_fingerprint:
            append(output, {
                "captured_at": now(),
                "event": "co_resident_inventory_changed",
                "runtime_stage": stage,
                "scored_measurement": measuring,
                "cache_preconditioning": cache_preconditioning,
                "protected_execution": protected_execution,
                "postgres_workloads": workloads,
                "co_resident_pg_isolation": isolation,
                "heavyweight_external_processes": heavyweight,
            })
            previous_fingerprint = fingerprint

        if protected_execution and heavyweight:
            persistent = async_watch.confirmed_heavyweight_processes(heavyweight)
            current = load(state_path)
            if persistent and str(current.get("stage") or "") == stage:
                event = (
                    "runtime_noninterference_failure_during_job_measurement"
                    if measuring
                    else "runtime_noninterference_failure_during_job_cache_precondition"
                )
                return stop_job_fail_closed(
                    output=output,
                    formal_unit=args.formal_unit,
                    main_pid=main_pid,
                    state_path=state_path,
                    event=event,
                    evidence={
                        "runtime_stage": stage,
                        "blockers": persistent,
                        "confirmation_seconds": CONFIRMATION_SECONDS,
                        "cross_scene_lock_held": False,
                        "resource_lease_held": False,
                    },
                )
        if (
            protected_execution
            and workloads
            and isolation.get("passed") is not True
        ):
            persistent_pg = confirmed.confirmed_workload_isolation_failure(
                args.formal_run_id,
                isolation,
                target_mems={0, 1},
                confirmation_seconds=CONFIRMATION_SECONDS,
            )
            current = load(state_path)
            if persistent_pg is not None and str(current.get("stage") or "") == stage:
                event = (
                    "runtime_noninterference_failure_during_job_measurement"
                    if measuring
                    else "runtime_noninterference_failure_during_job_cache_precondition"
                )
                return stop_job_fail_closed(
                    output=output,
                    formal_unit=args.formal_unit,
                    main_pid=main_pid,
                    state_path=state_path,
                    event=event,
                    evidence={
                        "runtime_stage": stage,
                        "blockers": coordination.sanitized_rows(
                            persistent_pg["inventory"]["workloads"]
                        ),
                        "co_resident_pg_isolation": persistent_pg["isolation"],
                        "confirmation_seconds": CONFIRMATION_SECONDS,
                        "cross_scene_lock_held": False,
                        "resource_lease_held": False,
                    },
                )
        time.sleep(max(1.0, args.interval_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
