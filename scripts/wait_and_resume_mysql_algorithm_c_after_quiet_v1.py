#!/usr/bin/env python3
"""Wait for a stable idle window, pass the formal gate, and resume Algorithm C."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any


if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import mysql_cross_project_wave_coordination_v1 as coordination


ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
PREFLIGHT = ROOT / "scripts/preflight_mysql_algorithm_c_boundary_v1.py"
REBIND = ROOT / "scripts/rebind_mysql_algorithm_c_boundary_v1.py"
ACCOUNTS = ROOT / "scripts/provision_mysql_algorithm_c_boundary_users_v1.py"
LAUNCH = ROOT / "scripts/launch_mysql_algorithm_c_boundary_resume_v2.py"
WATCHDOG = ROOT / "scripts/watch_mysql_formal_external_postgres_interference_v1.py"
HISTORY = ROOT / "history/mysql_four_scene_20260809"
PREFLIGHT_ROOT = ROOT / "preflight/mysql_four_scene_20260809"
EXTERNAL_CONTAINER = "bench-pg-mixstrg-0708"
RUN_PATTERN = re.compile(r"[A-Za-z0-9_.-]{8,128}")


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def regular(path: str) -> Path:
    resolved = Path(path).expanduser().resolve()
    if resolved.is_symlink() or not resolved.is_file():
        raise argparse.ArgumentTypeError(f"regular non-symlink file required: {resolved}")
    return resolved


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
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def run(command: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True,
        check=False, timeout=timeout,
    )


def sanitized_blockers(
    run_id: str, *, coordinated_lease_held: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from scripts.preflight_mysql_four_scene_live_v8 import host_pressure
    from scripts.watch_mysql_formal_external_postgres_interference_v1 import (
        external_blockers, external_workload_blockers,
    )

    pressure = host_pressure()
    raw = (
        external_workload_blockers(run_id, {0})
        if coordinated_lease_held else external_blockers(run_id, {0})
    )
    blockers = [
        {key: value for key, value in row.items() if key != "command"}
        for row in raw
    ]
    controllers = run(
        ["ps", "-eo", "pid=,args="],
        15,
    )
    if controllers.returncode != 0:
        blockers.append({"reason": "local_mysql_controller_inventory_failed"})
    else:
        own_pid = os.getpid()
        for line in controllers.stdout.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            pid_text, separator, command = stripped.partition(" ")
            if not separator or not pid_text.isdigit():
                continue
            pid = int(pid_text)
            if (
                pid == own_pid
                or run_id in command
                or "run_mysql_four_scene_algorithm_c_boundary_resume_v2.py"
                not in command
            ):
                continue
            blockers.append({
                "reason": "local_mysql_algorithm_c_controller_active",
                "pid": pid,
                "command_sha256": hashlib.sha256(command.encode("utf-8")).hexdigest(),
            })
    if pressure["blocking_d_state_pids"]:
        blockers.append({
            "reason": "host_uninterruptible_sleep_present",
            "d_state_pids": pressure["blocking_d_state_pids"],
        })
    if float(pressure["load1"]) > 16.0:
        blockers.append({
            "reason": "host_idle_load1_exceeds_16",
            "load1": pressure["load1"],
        })
    inspected = run(
        ["docker", "inspect", "-f", "{{.State.Running}}", EXTERNAL_CONTAINER],
        15,
    )
    if inspected.returncode == 0 and inspected.stdout.strip().lower() == "true":
        blockers.append({
            "reason": "known_external_container_running",
            "container": EXTERNAL_CONTAINER,
        })
    return blockers, pressure


def summarize_blockers(blockers: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(str(row.get("reason", "unknown")) for row in blockers)
    external = [
        {
            "pid": row.get("pid"),
            "command_sha256": row.get("command_sha256"),
            "overlap_mems": row.get("overlap_mems"),
        }
        for row in blockers
        if row.get("reason") == "runtime_external_database_workload_numa_overlap"
    ]
    return {
        "total": len(blockers),
        "counts_by_reason": dict(sorted(counts.items())),
        "external_samples": external[:4],
        "external_samples_truncated": len(external) > 4,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--rebind-run-id", required=True)
    parser.add_argument("--accounts-run-id", required=True)
    parser.add_argument("--checkpoint", type=regular, required=True)
    parser.add_argument("--replay-quarantine", type=regular, required=True)
    parser.add_argument("--passive-quiet-seconds", type=int, default=30)
    parser.add_argument("--formal-window-seconds", type=int, default=180)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--cross-project-wave-coordination", action="store_true")
    parser.add_argument("--coordination-contract", type=regular)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    for value in (args.run_id, args.rebind_run_id, args.accounts_run_id):
        if RUN_PATTERN.fullmatch(value) is None:
            parser.error(f"unsafe run identity: {value!r}")
    if args.passive_quiet_seconds < 15 or args.formal_window_seconds < 60:
        parser.error("quiet windows are too short")
    if args.cross_project_wave_coordination != (args.coordination_contract is not None):
        parser.error("wave coordination and its frozen contract must be enabled together")
    coordination_contract: dict[str, Any] | None = None
    if args.cross_project_wave_coordination:
        coordination_contract = load(args.coordination_contract)
        if (
            args.coordination_contract != coordination.CONTRACT_PATH.resolve()
            or coordination_contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
            or coordination_contract.get("formal_scope") != "mysql_only"
            or coordination_contract.get("cross_engine_comparison_prohibited") is not True
            or coordination_contract.get("lock_path") != str(coordination.LOCK_PATH)
        ):
            parser.error("cross-project wave coordination contract differs")
    log = args.log.expanduser().resolve()
    result = args.result.expanduser().resolve()
    for path in (log, result, result.with_name(result.name + ".sha256")):
        if path.exists():
            raise RuntimeError(f"fresh orchestrator artifact required: {path}")
    for path in (
        HISTORY / args.run_id,
        HISTORY / args.rebind_run_id,
        HISTORY / args.accounts_run_id,
    ):
        if path.exists():
            raise RuntimeError(f"fresh output required: {path}")

    lock_path = Path("/tmp/mysql_algorithm_c_auto_resume_v1.lock")
    lock_stream = lock_path.open("w", encoding="ascii")
    try:
        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        raise RuntimeError("another Algorithm C auto-resume orchestrator is active") from error
    lock_stream.write(f"{os.getpid()}\n")
    lock_stream.flush()

    sys.path.insert(0, str(ROOT))
    append(log, {
        "event": "orchestrator_started", "captured_at": now(),
        "run_id": args.run_id,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "passive_quiet_seconds": args.passive_quiet_seconds,
        "formal_window_seconds": args.formal_window_seconds,
        "cross_project_wave_coordination": args.cross_project_wave_coordination,
        "coordination_contract_path": (
            str(args.coordination_contract)
            if args.coordination_contract is not None else None
        ),
        "coordination_contract_sha256": (
            sha(args.coordination_contract)
            if args.coordination_contract is not None else None
        ),
    })
    quiet_since: float | None = None
    last_progress_log = 0.0
    gate_attempt = 0
    passed_preflight: Path | None = None
    workload_lease: coordination.ExclusiveLease | None = None
    lease_wait_started = time.monotonic()
    try:
      while passed_preflight is None:
        if args.cross_project_wave_coordination and workload_lease is None:
            append(log, {
                "event": "waiting_for_cross_project_workload_lease",
                "captured_at": now(),
                "lock_path": str(coordination.LOCK_PATH),
                "formal_state_mutation": False,
            })
            try:
                workload_lease = coordination.acquire_exclusive_blocking(
                    timeout_seconds=float(
                        coordination_contract["lease_acquisition_timeout_seconds"]
                    ),
                )
            except TimeoutError:
                append(log, {
                    "event": "cross_project_workload_lease_wait_timeout",
                    "captured_at": now(),
                    "wait_seconds": time.monotonic() - lease_wait_started,
                    "formal_state_mutation": False,
                })
                lease_wait_started = time.monotonic()
                continue
            quiet_since = None
            append(log, {
                "event": "cross_project_workload_lease_acquired",
                "captured_at": now(),
                "holder_pid": os.getpid(),
                "lock_path": str(coordination.LOCK_PATH),
                "lock_inode": workload_lease.inode,
                "wait_seconds": time.monotonic() - lease_wait_started,
                "formal_state_mutation": False,
            })
        blockers, pressure = sanitized_blockers(
            args.run_id,
            coordinated_lease_held=workload_lease is not None,
        )
        if args.cross_project_wave_coordination and workload_lease is None:
            blockers.append({
                "reason": "cross_project_physical_workload_lease_busy",
                "lock_path": str(coordination.LOCK_PATH),
            })
        blocker_summary = summarize_blockers(blockers)
        current = time.monotonic()
        if blockers:
            quiet_was_in_progress = quiet_since is not None
            quiet_since = None
            if quiet_was_in_progress or current - last_progress_log >= 30.0:
                append(log, {
                    "event": "waiting_for_quiet", "captured_at": now(),
                    "blocker_summary": blocker_summary,
                    "host_pressure": pressure,
                })
                last_progress_log = current
            if (
                workload_lease is not None
                and current - workload_lease.acquired_at_monotonic
                >= float(
                    coordination_contract["maximum_quiet_wait_while_held_seconds"]
                )
            ):
                held_seconds = coordination.release(workload_lease)
                workload_lease = None
                lease_wait_started = time.monotonic()
                append(log, {
                    "event": "cross_project_workload_lease_released_after_"
                             "bounded_pressure_wait",
                    "captured_at": now(),
                    "held_seconds": held_seconds,
                    "blocker_summary": blocker_summary,
                    "formal_state_mutation": False,
                })
                time.sleep(float(coordination_contract["fair_release_yield_seconds"]))
        else:
            if quiet_since is None:
                quiet_since = current
                append(log, {
                    "event": "passive_quiet_window_started",
                    "captured_at": now(), "host_pressure": pressure,
                })
            quiet_elapsed = current - quiet_since
            if current - last_progress_log >= 30.0:
                append(log, {
                    "event": "passive_quiet_progress", "captured_at": now(),
                    "quiet_elapsed_seconds": quiet_elapsed,
                    "quiet_required_seconds": args.passive_quiet_seconds,
                    "host_pressure": pressure,
                })
                last_progress_log = current
            if quiet_elapsed >= args.passive_quiet_seconds:
                gate_attempt += 1
                stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%Sp%z").replace("+", "")
                candidate = PREFLIGHT_ROOT / (
                    f"runtime_preflight_auto_v14_attempt{gate_attempt}_{stamp}.json"
                )
                append(log, {
                    "event": "formal_gate_started", "captured_at": now(),
                    "attempt": gate_attempt, "output": str(candidate),
                })
                checked = run([
                    str(PYTHON), str(PREFLIGHT), "--output", str(candidate),
                    "--window-seconds", str(args.formal_window_seconds),
                    "--interval-seconds", "1",
                    *(
                        [
                            "--coordinated-workload-lease-holder-pid", str(os.getpid()),
                            "--coordination-contract", str(args.coordination_contract),
                        ]
                        if workload_lease is not None else []
                    ),
                ], args.formal_window_seconds + 180)
                append(log, {
                    "event": "formal_gate_finished", "captured_at": now(),
                    "attempt": gate_attempt, "returncode": checked.returncode,
                    "stdout_tail": checked.stdout[-2000:],
                    "stderr_tail": checked.stderr[-2000:],
                    "output": str(candidate),
                })
                if checked.returncode == 0:
                    document = load(candidate)
                    if (
                        document.get("status") != "PASS"
                        or document.get("formal_launch_authorized") is not True
                        or float(document.get("observed_seconds", 0))
                        < args.formal_window_seconds
                    ):
                        raise RuntimeError("formal gate returned zero without PASS authority")
                    passed_preflight = candidate
                    break
                if checked.returncode != 3:
                    raise RuntimeError(
                        f"formal gate infrastructure failure rc={checked.returncode}: "
                        f"{checked.stderr[-1000:]}"
                    )
                if workload_lease is not None:
                    held_seconds = coordination.release(workload_lease)
                    workload_lease = None
                    lease_wait_started = time.monotonic()
                    append(log, {
                        "event": "cross_project_workload_lease_released_after_gate_retry",
                        "captured_at": now(),
                        "held_seconds": held_seconds,
                    })
                    time.sleep(0.5)
                quiet_since = None
        time.sleep(max(1.0, args.poll_seconds))
    finally:
        if workload_lease is not None:
            held_seconds = coordination.release(workload_lease)
            workload_lease = None
            append(log, {
                "event": "cross_project_workload_lease_released_after_preflight",
                "captured_at": now(),
                "held_seconds": held_seconds,
            })

    rebind = run([
        str(PYTHON), str(REBIND), "--run-id", args.rebind_run_id,
        "--runtime-preflight", str(passed_preflight),
    ], 180)
    append(log, {
        "event": "live_rebind_finished", "captured_at": now(),
        "returncode": rebind.returncode,
        "stdout_tail": rebind.stdout[-2000:], "stderr_tail": rebind.stderr[-2000:],
    })
    if rebind.returncode != 0:
        raise RuntimeError(f"live rebind failed: {rebind.stderr[-1000:]}")
    prepared = HISTORY / args.rebind_run_id / "prepared_binding.json"

    accounts = run([
        str(PYTHON), str(ACCOUNTS), "--run-id", args.accounts_run_id,
        "--prepared-binding", str(prepared), "--verify-existing",
    ], 300)
    append(log, {
        "event": "benchmark_accounts_finished", "captured_at": now(),
        "returncode": accounts.returncode,
        "stdout_tail": accounts.stdout[-4000:], "stderr_tail": accounts.stderr[-2000:],
    })
    if accounts.returncode != 0:
        raise RuntimeError(f"benchmark account verification failed: {accounts.stderr[-1000:]}")
    accounts_binding = HISTORY / args.accounts_run_id / "benchmark_accounts_binding.json"

    launched = run([
        str(PYTHON), str(LAUNCH), "--run-id", args.run_id,
        "--checkpoint", str(args.checkpoint),
        "--runtime-preflight", str(passed_preflight),
        "--prepared-binding", str(prepared),
        "--benchmark-accounts", str(accounts_binding),
        "--replay-quarantine", str(args.replay_quarantine),
    ], 120)
    append(log, {
        "event": "formal_launch_finished", "captured_at": now(),
        "returncode": launched.returncode,
        "stdout_tail": launched.stdout[-4000:], "stderr_tail": launched.stderr[-2000:],
    })
    if launched.returncode != 0:
        raise RuntimeError(f"formal launch failed: {launched.stderr[-1000:]}")

    formal_unit = f"mysql-four-scene-algc-{args.run_id}.service"
    watchdog_unit = f"mysql-four-scene-runtime-watch-{args.run_id}"
    watchdog_output = HISTORY / args.run_id / "runtime_external_interference_watchdog.jsonl"
    watchdog_log = HISTORY / "formal_launch_logs" / f"{args.run_id}.watchdog.log"
    watched = run([
        "systemd-run", "--user", "--collect", f"--unit={watchdog_unit}",
        "--description=MySQL formal runtime external interference watchdog",
        f"--property=WorkingDirectory={ROOT}",
        f"--property=StandardOutput=append:{watchdog_log}",
        f"--property=StandardError=append:{watchdog_log}",
        str(PYTHON), str(WATCHDOG),
        "--formal-unit", formal_unit,
        "--formal-run-id", args.run_id,
        "--output", str(watchdog_output),
        "--interval-seconds", "2",
    ], 30)
    append(log, {
        "event": "runtime_watchdog_launch_finished", "captured_at": now(),
        "returncode": watched.returncode,
        "stdout_tail": watched.stdout[-2000:], "stderr_tail": watched.stderr[-2000:],
        "watchdog_unit": f"{watchdog_unit}.service",
    })
    if watched.returncode != 0:
        subprocess.run(
            ["systemctl", "--user", "stop", formal_unit],
            cwd=ROOT, check=False, timeout=30,
        )
        raise RuntimeError("watchdog launch failed; formal unit stopped fail-closed")

    receipt = {
        "schema_version": "mysql_algorithm_c_quiet_auto_resume_v1",
        "status": "STARTED_WITH_RUNTIME_WATCHDOG",
        "completed_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "run_id": args.run_id,
        "formal_unit": formal_unit,
        "runtime_watchdog_unit": f"{watchdog_unit}.service",
        "runtime_watchdog_output": str(watchdog_output),
        "passed_preflight_path": str(passed_preflight),
        "passed_preflight_sha256": sha(passed_preflight),
        "prepared_binding_path": str(prepared),
        "prepared_binding_sha256": sha(prepared),
        "benchmark_accounts_path": str(accounts_binding),
        "benchmark_accounts_sha256": sha(accounts_binding),
        "gate_attempts": gate_attempt,
        "cross_project_wave_coordination": args.cross_project_wave_coordination,
        "coordination_contract_path": (
            str(args.coordination_contract)
            if args.coordination_contract is not None else None
        ),
        "coordination_contract_sha256": (
            sha(args.coordination_contract)
            if args.coordination_contract is not None else None
        ),
    }
    atomic(result, receipt)
    append(log, {"event": "orchestrator_completed", **receipt})
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
