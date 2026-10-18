#!/usr/bin/env python3
"""Fail closed a formal MySQL run if an external DB workload appears."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v1 as coordination


FORMAL_UNIT_PREFIX = "mysql-four-scene-algc-"
FORMAL_RUN_PATTERN = re.compile(r"[A-Za-z0-9_.-]{20,180}")
HISTORY = ROOT / "history/mysql_four_scene_20260809"


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def elapsed_since(value: str) -> float:
    captured = datetime.fromisoformat(value)
    if captured.tzinfo is None:
        raise ValueError("timezone-aware lease timestamp required")
    return (datetime.now(timezone.utc) - captured.astimezone(timezone.utc)).total_seconds()


def cpus(value: str) -> set[int]:
    result: set[int] = set()
    for field in value.split(","):
        start, separator, end = field.strip().partition("-")
        if not start:
            continue
        result.update(range(int(start), int(end) + 1) if separator else [int(start)])
    return result


def append(path: Path, event: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def unit_fields(unit: str) -> dict[str, str]:
    shown = subprocess.run(
        ["systemctl", "--user", "show", unit, "-p", "ActiveState", "-p",
         "SubState", "-p", "MainPID", "-p", "Result"],
        capture_output=True, text=True, check=True, timeout=10,
    ).stdout
    return dict(line.split("=", 1) for line in shown.splitlines() if "=" in line)


def external_blockers(run_id: str, target_mems: set[int]) -> list[dict]:
    """Compatibility API: both active workloads and idle controllers block."""

    inventory = coordination.external_process_inventory(run_id, target_mems)
    return [
        {**row, "reason": "runtime_external_database_workload_numa_overlap"}
        for row in [*inventory["workloads"], *inventory["controllers"]]
    ]


def external_workload_blockers(run_id: str, target_mems: set[int]) -> list[dict]:
    inventory = coordination.external_process_inventory(run_id, target_mems)
    return [
        {**row, "reason": "runtime_external_database_workload_numa_overlap"}
        for row in inventory["workloads"]
    ]


def load_run_state(run_id: str) -> dict:
    path = HISTORY / run_id / "run_state.json"
    if not path.is_file() or path.is_symlink():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def mark_run_state_failed_closed(
    path: Path, *, event: str, evidence: dict, terminal_event: str,
) -> bool:
    """Replace a watchdog-stopped stale RUNNING state with an auditable failure."""

    if not path.is_file() or path.is_symlink():
        return False
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("status") != "RUNNING":
        return False
    prior_stage = value.get("stage")
    value["status"] = "FAILED_CLOSED"
    value["stage"] = "FAILED"
    value["failed_at"] = now()
    value["error"] = f"RuntimeError: runtime watchdog stopped formal run after {event}"
    value["watchdog_interruption"] = {
        "event": event,
        "terminal_event": terminal_event,
        "runtime_stage": prior_stage,
        "evidence": evidence,
        "formal_state_credit_increment": 0,
        "interrupted_candidate_replay_prohibited": True,
    }
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    digest = coordination.sha256(path)
    path.with_name(path.name + ".sha256").write_text(
        f"{digest}  {path.name}\n", encoding="ascii"
    )
    return True


def coordinated_phase(state: dict) -> str:
    value = state.get("cross_project_wave_coordination") or {}
    return str(value.get("phase") or "not_initialized")


HELD_PHASES = {"lease_acquired", "quiet_gate", "measuring"}


def confirmed_lease_loss(run_id: str, main_pid: int) -> dict | None:
    """Confirm a missing lease against a fresh state snapshot.

    The controller publishes ``released`` before dropping the kernel flock.
    A watchdog sample may read the old ``measuring`` state and then inspect the
    new unlocked inode.  Re-read both authorities so that transition is not
    mistaken for a lost formal-wave lease.
    """

    rechecked = load_run_state(run_id)
    if coordinated_phase(rechecked) not in HELD_PHASES:
        return None
    if coordination.lock_held_exclusively_by(main_pid):
        return None
    return rechecked


def _isolation_error_identities(result: dict) -> set[tuple[int, str]]:
    """Return per-process hard failures suitable for a persistence check."""

    return {
        (int(row["pid"]), str(error))
        for row in (result.get("workloads") or [])
        for error in (row.get("errors") or [])
    }


def confirmed_workload_isolation_failure(
    run_id: str,
    first_result: dict,
    *,
    target_mems: set[int],
    confirmation_seconds: float = 3.0,
) -> dict | None:
    """Confirm that the same process still violates isolation after a short delay.

    External lane supervisors create a wrapper and then apply its frozen CPU
    affinity.  Sampling between those two operations exposes a harmless
    ``0-N`` startup mask.  Requiring the same PID/error identity to survive a
    fresh inventory preserves fail-closed handling for persistent overlap while
    avoiding that process-creation TOCTOU.
    """

    first_errors = _isolation_error_identities(first_result)
    if not first_errors:
        return None
    time.sleep(max(0.0, confirmation_seconds))
    inventory = coordination.external_process_inventory(run_id, target_mems)
    rechecked = coordination.evaluate_co_resident_pg_isolation(
        inventory["workloads"]
    )
    persistent_errors = sorted(first_errors & _isolation_error_identities(rechecked))
    if not persistent_errors:
        return None
    return {
        "inventory": inventory,
        "isolation": rechecked,
        "persistent_error_identities": [
            {"pid": pid, "error": error} for pid, error in persistent_errors
        ],
        "confirmation_seconds": confirmation_seconds,
    }


def stop_formal_fail_closed(
    *, output: Path, formal_unit: str, main_pid: int,
    run_state_path: Path, event: str, evidence: dict,
) -> int:
    append(output, {
        "captured_at": now(), "event": event,
        "formal_pid": main_pid, **evidence,
        "action": "SIGINT_then_bounded_systemd_stop",
    })
    coordination_contract = json.loads(
        coordination.CONTRACT_PATH.read_text(encoding="utf-8")
    )
    maximum_wave_lease_seconds = float(
        coordination_contract["maximum_wave_lease_seconds"]
    )
    os.kill(main_pid, signal.SIGINT)
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        terminal = unit_fields(formal_unit)
        if terminal.get("ActiveState") in {"inactive", "failed"}:
            state_updated = mark_run_state_failed_closed(
                run_state_path, event=event, evidence=evidence,
                terminal_event="formal_failed_closed_after_sigint",
            )
            append(output, {
                "captured_at": now(), "event": "formal_failed_closed_after_sigint",
                "unit_fields": terminal, "run_state_updated": state_updated,
            })
            return 3
        time.sleep(1.0)
    subprocess.run(
        ["systemctl", "--user", "stop", formal_unit],
        check=False, timeout=30,
    )
    state_updated = mark_run_state_failed_closed(
        run_state_path, event=event, evidence=evidence,
        terminal_event="formal_forced_stopped_after_sigint_timeout",
    )
    append(output, {
        "captured_at": now(),
        "event": "formal_forced_stopped_after_sigint_timeout",
        "unit_fields": unit_fields(formal_unit),
        "run_state_updated": state_updated,
    })
    return 4


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--formal-unit", required=True)
    parser.add_argument("--formal-run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--interval-seconds", type=float, default=2.0)
    args = parser.parse_args()
    if (
        not args.formal_unit.startswith(FORMAL_UNIT_PREFIX)
        or not args.formal_unit.endswith(".service")
        or FORMAL_RUN_PATTERN.fullmatch(args.formal_run_id) is None
        or args.formal_run_id not in args.formal_unit
    ):
        parser.error("formal unit/run identity differs")
    output = args.output.expanduser().resolve()
    if output.exists():
        raise RuntimeError(f"fresh watchdog output required: {output}")
    append(output, {
        "captured_at": now(), "event": "watchdog_started",
        "formal_unit": args.formal_unit, "formal_run_id": args.formal_run_id,
        "target_mems": [0, 1], "poll_seconds": max(1.0, args.interval_seconds),
        "coordination_contract_path": str(coordination.CONTRACT_PATH),
        "coordination_contract_sha256": coordination.sha256(
            coordination.CONTRACT_PATH
        ),
    })
    coordination_contract = json.loads(
        coordination.CONTRACT_PATH.read_text(encoding="utf-8")
    )
    maximum_wave_lease_seconds = float(
        coordination_contract["maximum_wave_lease_seconds"]
    )
    previous_inventory_fingerprint = ""
    run_state_path = HISTORY / args.formal_run_id / "run_state.json"
    while True:
        fields = unit_fields(args.formal_unit)
        main_pid = int(fields.get("MainPID") or 0)
        if fields.get("ActiveState") in {"inactive", "failed"} or main_pid <= 0:
            append(output, {
                "captured_at": now(), "event": "formal_unit_terminal",
                "unit_fields": fields, "interference_detected": False,
            })
            return 0
        command = Path(f"/proc/{main_pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(
            "utf-8", errors="replace"
        )
        if args.formal_run_id not in command or "run_mysql_four_scene_algorithm_c" not in command:
            raise RuntimeError("formal MainPID identity differs")
        state = load_run_state(args.formal_run_id)
        phase = coordinated_phase(state)
        inventory = coordination.external_process_inventory(
            args.formal_run_id, {0, 1},
        )
        controllers = coordination.sanitized_rows(inventory["controllers"])
        workloads = coordination.sanitized_rows(inventory["workloads"])
        workload_isolation = coordination.evaluate_co_resident_pg_isolation(
            inventory["workloads"]
        )
        fingerprint = json.dumps(
            {
                "controller_pids": [row["pid"] for row in controllers],
                "workload_pids": [row["pid"] for row in workloads],
                "phase": phase,
            },
            sort_keys=True,
        )
        if fingerprint != previous_inventory_fingerprint:
            append(output, {
                "captured_at": now(),
                "event": "co_resident_postgres_inventory_changed",
                "coordination_phase": phase,
                "controllers": controllers,
                "physical_workloads": workloads,
                "co_resident_pg_isolation": workload_isolation,
            })
            previous_inventory_fingerprint = fingerprint

        held_phases = HELD_PHASES
        measurement_stage = any(
            marker in str(state.get("stage") or "")
            for marker in ("_RESETTING_XP", "_MEASURING", "_PLAN_CAPTURE")
        )
        if phase in held_phases or measurement_stage:
            coordination_state = state.get("cross_project_wave_coordination") or {}
            acquired_at = str(coordination_state.get("lease_acquired_at") or "")
            try:
                lease_elapsed = elapsed_since(acquired_at)
            except (TypeError, ValueError):
                lease_elapsed = maximum_wave_lease_seconds + 1.0
            if lease_elapsed > maximum_wave_lease_seconds:
                return stop_formal_fail_closed(
                    output=output, formal_unit=args.formal_unit,
                    main_pid=main_pid,
                    run_state_path=run_state_path,
                    event="cross_project_workload_lease_deadline_exceeded",
                    evidence={
                        "coordination_phase": phase,
                        "runtime_stage": state.get("stage"),
                        "lease_elapsed_seconds": lease_elapsed,
                        "maximum_wave_lease_seconds": maximum_wave_lease_seconds,
                    },
                )
            if not coordination.lock_held_exclusively_by(main_pid):
                confirmed_state = confirmed_lease_loss(
                    args.formal_run_id, main_pid,
                )
                if confirmed_state is None:
                    time.sleep(max(1.0, args.interval_seconds))
                    continue
                confirmed_phase = coordinated_phase(confirmed_state)
                return stop_formal_fail_closed(
                    output=output, formal_unit=args.formal_unit,
                    main_pid=main_pid,
                    run_state_path=run_state_path,
                    event="cross_project_workload_lease_lost_during_formal_wave",
                    evidence={
                        "coordination_phase": confirmed_phase,
                        "runtime_stage": confirmed_state.get("stage"),
                        "lease_loss_confirmed_after_fresh_state_reread": True,
                    },
                )
            if workloads and workload_isolation["passed"] is not True:
                confirmed = confirmed_workload_isolation_failure(
                    args.formal_run_id,
                    workload_isolation,
                    target_mems={0, 1},
                )
                if confirmed is None:
                    time.sleep(max(1.0, args.interval_seconds))
                    continue
                confirmed_inventory = confirmed["inventory"]
                confirmed_workloads = coordination.sanitized_rows(
                    confirmed_inventory["workloads"]
                )
                return stop_formal_fail_closed(
                    output=output, formal_unit=args.formal_unit,
                    main_pid=main_pid,
                    run_state_path=run_state_path,
                    event="runtime_interference_detected_under_exclusive_wave_lease",
                    evidence={
                        "coordination_phase": phase,
                        "runtime_stage": state.get("stage"),
                        "blockers": confirmed_workloads,
                        "co_resident_pg_isolation": confirmed["isolation"],
                        "initial_co_resident_pg_isolation": workload_isolation,
                        "persistent_error_identities": confirmed[
                            "persistent_error_identities"
                        ],
                        "isolation_confirmation_seconds": confirmed[
                            "confirmation_seconds"
                        ],
                    },
                )
        time.sleep(max(1.0, args.interval_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
