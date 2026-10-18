#!/usr/bin/env python3
"""Launch one fresh matrix task after conflicting satellite work has exited."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path("/home/anonymous/miniconda3/envs/resource_adaptive/bin/python")
ACTIVE_STATES = {"preparing", "running"}


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def external_root_busy(root: Path) -> bool:
    """Return whether a scheduler root still owns a live worker or VM."""
    state_path = root.resolve() / "scheduler_state.json"
    try:
        state = load_json(state_path)
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        state = {}
    tasks = state.get("tasks") or {}
    if isinstance(tasks, dict):
        for row in tasks.values():
            if not isinstance(row, dict) or row.get("status") not in ACTIVE_STATES:
                continue
            try:
                worker_pid = int(row.get("worker_pid") or 0)
            except (TypeError, ValueError):
                worker_pid = 0
            if process_alive(worker_pid):
                return True
    for pid_path in root.glob("runs/*/attempt_*/rootless_vm/qemu.pid"):
        try:
            qemu_pid = int(pid_path.read_text(encoding="utf-8").strip())
        except (FileNotFoundError, OSError, TypeError, ValueError):
            continue
        if process_alive(qemu_pid):
            return True
    return False


def target_task_completed(root: Path, task_id: str) -> bool:
    try:
        state = load_json(root.resolve() / "scheduler_state.json")
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        return False
    tasks = state.get("tasks") or {}
    row = tasks.get(task_id) if isinstance(tasks, dict) else None
    return isinstance(row, dict) and row.get("status") == "completed"


def scheduler_command(args: argparse.Namespace) -> List[str]:
    return [
        str(PYTHON),
        "-u",
        str(ROOT / "shell/double_loop_vm_task_scheduler.py"),
        "--config",
        str(args.config.resolve()),
        "--scheduler-root",
        str(args.target_scheduler_root.resolve()),
        "--overlay-root",
        str(args.target_overlay_root.resolve()),
        "--runtime-overlay-root",
        str(args.target_runtime_overlay_root.resolve()),
        "--only-task",
        str(args.task_id),
        "--execute",
        "--watch",
        "--max-active",
        str(args.max_active),
    ]


def service_active(service: str) -> bool:
    result = subprocess.run(
        ["systemctl", "--user", "is-active", service],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return result.stdout.strip() == "active"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--external-scheduler-root", type=Path, action="append", required=True)
    parser.add_argument("--target-scheduler-root", type=Path, required=True)
    parser.add_argument("--target-overlay-root", type=Path, required=True)
    parser.add_argument("--target-runtime-overlay-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--target-service", required=True)
    parser.add_argument("--max-active", type=int, default=4)
    parser.add_argument("--poll-s", type=float, default=30.0)
    args = parser.parse_args()
    if args.max_active < 1 or args.max_active > 4:
        raise ValueError("formal scheduler max_active must be between 1 and 4")

    args.target_scheduler_root.mkdir(parents=True, exist_ok=True)
    lock_path = args.target_scheduler_root.resolve() / "launch_after_external.lock"
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        while True:
            if target_task_completed(args.target_scheduler_root, args.task_id):
                print(f"target_completed={args.task_id}", flush=True)
                return 0
            if service_active(args.target_service) or external_root_busy(args.target_scheduler_root):
                print(f"target_already_active={args.task_id}", flush=True)
                return 0
            busy_roots = [
                root.resolve()
                for root in args.external_scheduler_root
                if external_root_busy(root)
            ]
            if not busy_roots:
                break
            print(
                "waiting_external=" + ",".join(str(root) for root in busy_roots),
                flush=True,
            )
            time.sleep(max(5.0, args.poll_s))

        unit = args.target_service
        if unit.endswith(".service"):
            unit = unit[: -len(".service")]
        command = scheduler_command(args)
        launch = subprocess.run(
            [
                "systemd-run",
                "--user",
                f"--unit={unit}",
                "--collect",
                "--property=Restart=no",
                "/usr/bin/sg",
                "kvm",
                "-c",
                "exec " + shlex.join(command),
            ],
            cwd=str(ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
        )
        print(launch.stdout.strip(), flush=True)
        time.sleep(2.0)
        if not service_active(args.target_service):
            raise RuntimeError(f"target scheduler did not become active: {args.target_service}")
        print(f"target_scheduler_active={args.target_service}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
