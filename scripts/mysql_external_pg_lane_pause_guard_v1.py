#!/usr/bin/env python3
"""Reversibly pause external PostgreSQL lane controllers during a MySQL run.

Only the controller processes are stopped.  Already-running child workloads are
allowed to finish, so no database query is killed.  Controllers are resumed
when the anchored MySQL maintenance guard exits, on a signal, or at the bounded
deadline.
"""

from __future__ import annotations

import argparse
import atexit
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = Path("/tmp/mysql_external_pg_lane_pause_guard_v1.lock")
ACKNOWLEDGEMENT = "pause-external-pg-lane-controllers-max-8h"
CONTROLLER_FRAGMENTS = (
    "/home/anonymous/program/resource_adaptive/shell/pg_algorithm_c_lane_entry.py",
    "/home/anonymous/program/resource_adaptive/shell/run_pg_algorithm_c_scene.py",
    "/home/anonymous/program/resource_adaptive/shell/run_pg_algorithm_b_scene.py",
    "/home/anonymous/program/resource_adaptive/shell/run_pg_algorithm_c_cohort_support_supervisor.py",
)
ANCHOR_FRAGMENT = "scripts/mysql_external_compose_stop_maintenance_guard_v1.py"
FORMAL_UNIT_PREFIX = "mysql-four-scene-algc-"


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def append(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_name(path.name + ".sha256").write_text(
        f"{digest}  {path.name}\n", encoding="ascii",
    )


def proc_identity(pid: int) -> dict[str, Any] | None:
    proc = Path("/proc") / str(pid)
    try:
        stat = (proc / "stat").read_text(encoding="ascii")
        close = stat.rfind(")")
        if close < 0:
            raise ValueError("invalid proc stat")
        fields = stat[close + 2 :].split()
        cmdline = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode(
            "utf-8", errors="replace"
        ).strip()
        return {
            "pid": pid,
            "state": fields[0],
            "ppid": int(fields[1]),
            "pgrp": int(fields[2]),
            "session": int(fields[3]),
            "start_ticks": int(fields[19]),
            "cmdline": cmdline,
            "cmdline_sha256": hashlib.sha256(cmdline.encode()).hexdigest(),
        }
    except (FileNotFoundError, ProcessLookupError):
        return None


def same_process(
    expected: dict[str, Any], fragment: str | None = None,
) -> dict[str, Any] | None:
    required_fragment = fragment or str(expected.get("identity_fragment") or "")
    current = proc_identity(int(expected["pid"]))
    if (
        current is None
        or current["start_ticks"] != expected["start_ticks"]
        or current["cmdline_sha256"] != expected["cmdline_sha256"]
        or not required_fragment
        or required_fragment not in current["cmdline"]
    ):
        return None
    return current


def wait_state(expected: dict[str, Any], states: set[str], timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = same_process(expected)
        if current is None or current["state"] in states:
            return True
        time.sleep(0.05)
    return False


def unit_fields(unit: str) -> dict[str, str]:
    shown = subprocess.run(
        ["systemctl", "--user", "show", unit, "-p", "ActiveState",
         "-p", "SubState", "-p", "MainPID", "-p", "Result"],
        capture_output=True, text=True, check=True, timeout=10,
    ).stdout
    return dict(line.split("=", 1) for line in shown.splitlines() if "=" in line)


def terminate_formal_unit(unit: str, expected: dict[str, Any], log: Path) -> None:
    current = same_process(expected, "run_mysql_four_scene_algorithm_c")
    if current is not None:
        append(log, {
            "event": "formal_unit_stopping_before_pg_resume", "at": now(),
            "unit": unit, "pid": expected["pid"],
        })
        os.kill(int(expected["pid"]), signal.SIGINT)
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            if unit_fields(unit).get("ActiveState") in {"inactive", "failed"}:
                return
            time.sleep(1.0)
    subprocess.run(
        ["systemctl", "--user", "stop", unit], check=False, timeout=30,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, action="append", required=True)
    parser.add_argument("--anchor-pid", type=int)
    parser.add_argument("--anchor-unit")
    parser.add_argument("--anchor-run-id")
    parser.add_argument("--anchor-start-timeout-seconds", type=int, default=900)
    parser.add_argument("--maximum-seconds", type=int, default=28800)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--acknowledge-external-pause")
    args = parser.parse_args()
    if (
        not args.execute
        or args.acknowledge_external_pause != ACKNOWLEDGEMENT
        or not 600 <= args.maximum_seconds <= 28800
        or not 0.1 <= args.poll_seconds <= 10.0
        or not 1 <= len(args.pid) <= 5
        or len(args.pid) != len(set(args.pid))
        or (args.anchor_pid is not None and args.anchor_pid in args.pid)
        or (args.anchor_pid is None) == (args.anchor_unit is None)
        or not 30 <= args.anchor_start_timeout_seconds <= 1800
    ):
        parser.error(
            "exact acknowledgement, one to five unique PIDs, one anchor mode, "
            "bounded polling, and "
            "600..28800 seconds required"
        )
    if args.anchor_unit is not None and (
        not args.anchor_unit.startswith(FORMAL_UNIT_PREFIX)
        or not args.anchor_unit.endswith(".service")
        or not args.anchor_run_id
        or args.anchor_run_id not in args.anchor_unit
    ):
        parser.error("formal anchor unit/run identity differs")
    if args.anchor_pid is not None and args.anchor_run_id is not None:
        parser.error("anchor run id is valid only for unit mode")

    result = args.result.expanduser().resolve()
    log = args.log.expanduser().resolve()
    for path in (result, result.with_name(result.name + ".sha256"), log):
        if path.exists():
            raise RuntimeError(f"fresh artifact required: {path}")

    lanes: list[dict[str, Any]] = []
    for pid in args.pid:
        identity = proc_identity(pid)
        fragment = next(
            (
                candidate for candidate in CONTROLLER_FRAGMENTS
                if identity is not None and candidate in identity["cmdline"]
            ),
            None,
        )
        if identity is None or fragment is None:
            raise RuntimeError(f"PID {pid} is not the expected PostgreSQL lane controller")
        if identity["pgrp"] != pid or identity["session"] != pid:
            raise RuntimeError(f"PID {pid} is not an isolated lane session leader")
        identity["identity_fragment"] = fragment
        lanes.append(identity)
    anchor = None
    if args.anchor_pid is not None:
        anchor = proc_identity(args.anchor_pid)
        if anchor is None or ANCHOR_FRAGMENT not in anchor["cmdline"]:
            raise RuntimeError("anchor is not the active MySQL maintenance guard")
    anchor_descriptor: dict[str, Any] = (
        anchor if anchor is not None else {
            "unit": args.anchor_unit,
            "run_id": args.anchor_run_id,
            "start_timeout_seconds": args.anchor_start_timeout_seconds,
            "status": "WAITING_FOR_FORMAL_UNIT",
        }
    )

    lock = LOCK_PATH.open("w", encoding="ascii")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        raise RuntimeError("another PostgreSQL lane pause guard is active") from error
    lock.write(f"{os.getpid()}\n")
    lock.flush()

    paused: list[dict[str, Any]] = []
    cleanup_done = False
    cleanup_errors: list[str] = []
    reason = "unknown"
    formal_anchor: dict[str, Any] | None = None
    started = time.monotonic()
    started_at = now()

    def resume_all() -> None:
        nonlocal cleanup_done
        if cleanup_done:
            return
        cleanup_done = True
        for expected in reversed(paused):
            current = same_process(expected)
            if current is None:
                append(log, {
                    "event": "lane_absent_or_identity_changed_at_resume",
                    "at": now(), "pid": expected["pid"],
                })
                continue
            try:
                os.kill(int(expected["pid"]), signal.SIGCONT)
                if not wait_state(expected, {"R", "S", "D", "I", "Z"}, 2.0):
                    raise RuntimeError("controller remained stopped")
                append(log, {"event": "lane_controller_resumed", "at": now(), "pid": expected["pid"]})
            except Exception as error:  # pragma: no cover - operational recovery
                cleanup_errors.append(f"PID {expected['pid']}: {error}")

    def on_signal(signum: int, _frame: Any) -> None:
        nonlocal reason
        reason = f"signal_{signum}"
        if args.anchor_unit is not None and formal_anchor is not None:
            terminate_formal_unit(args.anchor_unit, formal_anchor, log)
        resume_all()
        raise SystemExit(128 + signum)

    atexit.register(resume_all)
    for handled in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(handled, on_signal)

    try:
        append(log, {
            "event": "pause_guard_started", "at": now(),
            "anchor": anchor_descriptor,
            "lanes": lanes, "maximum_seconds": args.maximum_seconds,
            "semantics": "controllers_only_existing_child_workloads_finish_naturally",
        })
        for expected in lanes:
            if same_process(expected) is None:
                raise RuntimeError(f"PID {expected['pid']} changed before pause")
            os.kill(int(expected["pid"]), signal.SIGSTOP)
            if not wait_state(expected, {"T", "t"}, 2.0):
                raise RuntimeError(f"PID {expected['pid']} did not enter stopped state")
            paused.append(expected)
            append(log, {"event": "lane_controller_paused", "at": now(), "pid": expected["pid"]})

        deadline = started + args.maximum_seconds
        anchor_start_deadline = started + args.anchor_start_timeout_seconds
        while True:
            if anchor is not None:
                if same_process(anchor, ANCHOR_FRAGMENT) is None:
                    reason = "mysql_maintenance_guard_exited"
                    break
            else:
                assert args.anchor_unit is not None
                fields = unit_fields(args.anchor_unit)
                main_pid = int(fields.get("MainPID") or 0)
                if formal_anchor is None and fields.get("ActiveState") == "active" and main_pid > 0:
                    candidate = proc_identity(main_pid)
                    if (
                        candidate is None
                        or str(args.anchor_run_id) not in candidate["cmdline"]
                        or "run_mysql_four_scene_algorithm_c" not in candidate["cmdline"]
                    ):
                        raise RuntimeError("formal anchor MainPID identity differs")
                    candidate["identity_fragment"] = "run_mysql_four_scene_algorithm_c"
                    formal_anchor = candidate
                    anchor_descriptor = {
                        "unit": args.anchor_unit,
                        "run_id": args.anchor_run_id,
                        "status": "FORMAL_UNIT_BOUND",
                        "process": candidate,
                    }
                    append(log, {
                        "event": "formal_anchor_unit_bound", "at": now(),
                        "anchor": anchor_descriptor,
                    })
                elif formal_anchor is None and time.monotonic() >= anchor_start_deadline:
                    reason = "formal_anchor_start_timeout"
                    break
                elif formal_anchor is not None and (
                    fields.get("ActiveState") in {"inactive", "failed"}
                    or same_process(
                        formal_anchor, "run_mysql_four_scene_algorithm_c"
                    ) is None
                ):
                    reason = "mysql_formal_unit_exited"
                    break
            if time.monotonic() >= deadline:
                reason = "maximum_duration_reached"
                if args.anchor_unit is not None and formal_anchor is not None:
                    terminate_formal_unit(args.anchor_unit, formal_anchor, log)
                break
            time.sleep(args.poll_seconds)
    finally:
        resume_all()
        document = {
            "schema_version": "mysql_external_pg_lane_pause_result_v1",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "started_at": started_at,
            "completed_at": now(),
            "termination_reason": reason,
            "maximum_seconds": args.maximum_seconds,
            "anchor": anchor_descriptor,
            "lanes": lanes,
            "paused_pid_count": len(paused),
            "all_controllers_resumed": not cleanup_errors,
            "cleanup_errors": cleanup_errors,
        }
        atomic(result, document)
    return 0 if not cleanup_errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
