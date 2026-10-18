#!/usr/bin/env python3
"""Restart the v16 scheduler after its current active wave becomes idle.

The running scheduler cannot accept new ``--retry-task`` requests.  This
helper waits until the durable queue contains no preparing/running task, stops
only the now-idle control-plane service, and starts one continuation service
that retries every reviewable task.  It never interrupts a worker, runner, or
VM that is represented as active in the queue.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEDULER_ROOT = Path(
    "/disk1/resource_adaptive_experiment_matrix_20260718/"
    "rootless_scheduled_18_v16_unified_grid_20260720"
)
DEFAULT_CONFIG = (
    ROOT
    / "config/double_loop_rootless_vm_scheduler_20260720_v12_recovery_serial.json"
)
PYTHON = Path("/home/anonymous/miniconda3/envs/resource_adaptive/bin/python")
ACTIVE_STATES = {"preparing", "running"}


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def task_rows(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    tasks = state.get("tasks") or {}
    if not isinstance(tasks, dict):
        raise ValueError("scheduler state has no task mapping")
    rows = [row for row in tasks.values() if isinstance(row, dict)]
    return sorted(rows, key=lambda row: int(row.get("order") or 0))


def continuation_command(
    state: Dict[str, Any],
    *,
    config: Path,
    max_active: int,
    exclude_retry_task_ids: Optional[Set[str]] = None,
) -> List[str]:
    rows = task_rows(state)
    excluded = exclude_retry_task_ids or set()
    command = [
        str(PYTHON),
        "-u",
        str(ROOT / "shell/double_loop_vm_task_scheduler.py"),
        "--config",
        str(config.resolve()),
        "--execute",
        "--watch",
        "--max-active",
        str(max_active),
    ]
    for row in rows:
        if (
            row.get("status") == "needs_review"
            and str(row.get("task_id")) not in excluded
        ):
            command.extend(["--retry-task", str(row["task_id"])])
    # B6 remains dependency-gated by A18. Releasing the administrative hold
    # here cannot dispatch either repeat before A18 has completed successfully.
    for identity in ("b6_r2", "b6_r3"):
        row = next((item for item in rows if item.get("task_id") == identity), None)
        if row is not None and row.get("status") == "held":
            command.extend(["--release-hold", identity])
    return command


def externally_owned_task_ids(
    roots: List[Path],
    *,
    include_completed: bool = True,
) -> Set[str]:
    """Protect live satellite tasks and, optionally, completed satellite tasks."""
    protected: Set[str] = set()
    for root in roots:
        try:
            state = load_json(root.resolve() / "scheduler_state.json")
        except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
            continue
        for row in task_rows(state):
            status = str(row.get("status") or "")
            if status in ACTIVE_STATES:
                try:
                    worker_pid = int(row.get("worker_pid") or 0)
                    if worker_pid <= 0:
                        continue
                    os.kill(worker_pid, 0)
                except (TypeError, ValueError, ProcessLookupError, PermissionError):
                    continue
            elif status != "completed" or not include_completed:
                continue
            task_id = re.sub(r"_retry\d+$", "", str(row.get("task_id")))
            protected.add(task_id)
    return protected


def wait_for_lock_release(path: Path, timeout_s: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_s
    while True:
        handle = path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            if time.monotonic() >= deadline:
                raise TimeoutError(f"scheduler lock stayed owned: {path}")
            time.sleep(0.5)
            continue
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
        return


def run_checked(command: List[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scheduler-root", type=Path, default=DEFAULT_SCHEDULER_ROOT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--current-service", default="ra-double-loop-v16.service")
    parser.add_argument("--next-service", default="ra-double-loop-v16-continuation.service")
    parser.add_argument("--max-active", type=int, default=4)
    parser.add_argument("--poll-s", type=float, default=30.0)
    parser.add_argument(
        "--external-scheduler-root",
        type=Path,
        action="append",
        default=[],
        help="Do not retry tasks already active or complete in this satellite root.",
    )
    parser.add_argument(
        "--external-active-only-scheduler-root",
        type=Path,
        action="append",
        default=[],
        help=(
            "Do not retry tasks while they are active in this satellite root, "
            "but do not trust completed state as reusable completion evidence."
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.max_active < 1 or args.max_active > 4:
        raise ValueError("formal continuation max_active must be between 1 and 4")

    state_path = args.scheduler_root.resolve() / "scheduler_state.json"
    while True:
        state = load_json(state_path)
        active = [
            str(row.get("task_id"))
            for row in task_rows(state)
            if row.get("status") in ACTIVE_STATES
        ]
        if active:
            print(
                f"waiting active={','.join(active)} updated_at={state.get('updated_at')}",
                flush=True,
            )
            if args.dry_run:
                return 0
            time.sleep(max(5.0, args.poll_s))
            continue

        externally_owned = externally_owned_task_ids(args.external_scheduler_root)
        externally_owned.update(
            externally_owned_task_ids(
                args.external_active_only_scheduler_root,
                include_completed=False,
            )
        )
        scheduler_command = continuation_command(
            state,
            config=args.config,
            max_active=args.max_active,
            exclude_retry_task_ids=externally_owned,
        )
        retry_count = scheduler_command.count("--retry-task")
        if args.dry_run:
            print(shlex.join(scheduler_command), flush=True)
            print(f"retry_count={retry_count}", flush=True)
            return 0

        print(
            f"idle handoff retry_count={retry_count} state_updated_at={state.get('updated_at')}",
            flush=True,
        )
        run_checked(["systemctl", "--user", "stop", args.current_service])
        wait_for_lock_release(args.scheduler_root.resolve() / "scheduler.lock")
        # Re-read after the idle watcher has stopped so the continuation sees
        # the final durable status of every task in the completed wave.
        state = load_json(state_path)
        if any(row.get("status") in ACTIVE_STATES for row in task_rows(state)):
            raise RuntimeError("active task appeared during idle handoff; refusing restart")
        externally_owned = externally_owned_task_ids(args.external_scheduler_root)
        externally_owned.update(
            externally_owned_task_ids(
                args.external_active_only_scheduler_root,
                include_completed=False,
            )
        )
        scheduler_command = continuation_command(
            state,
            config=args.config,
            max_active=args.max_active,
            exclude_retry_task_ids=externally_owned,
        )
        next_unit = str(args.next_service)
        if next_unit.endswith(".service"):
            next_unit = next_unit[: -len(".service")]
        service_command = [
            "systemd-run",
            "--user",
            f"--unit={next_unit}",
            "--collect",
            "--property=Restart=no",
            "/usr/bin/sg",
            "kvm",
            "-c",
            "exec " + shlex.join(scheduler_command),
        ]
        completed = run_checked(service_command)
        print(completed.stdout.strip(), flush=True)
        time.sleep(2.0)
        active_result = subprocess.run(
            ["systemctl", "--user", "is-active", args.next_service],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if active_result.stdout.strip() != "active":
            raise RuntimeError(
                f"continuation service did not become active: {active_result.stdout.strip()}"
            )
        print(f"continuation_active={args.next_service}", flush=True)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
