#!/usr/bin/env python3
"""Pause dispatch, use the next free owned VM slot to build TPCH, then resume."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


ROOT = Path(__file__).resolve().parents[1]


def write_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def owned_qemu_commands() -> List[str]:
    completed = subprocess.run(
        ["ps", "-eo", "args="],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=20,
        check=False,
    )
    return [
        row
        for row in completed.stdout.splitlines()
        if "qemu-system" in row and "-name ra-rootless-vm0" in row
    ]


def first_free_slot() -> Optional[int]:
    commands = owned_qemu_commands()
    for slot in range(1, 5):
        marker = f"-name ra-rootless-vm{slot:02d}-"
        if not any(marker in command for command in commands):
            return slot
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scheduler-pid", type=int, required=True)
    parser.add_argument("--base-seed", type=Path, required=True)
    parser.add_argument("--output-seed", type=Path, required=True)
    parser.add_argument("--seed-root", type=Path, required=True)
    parser.add_argument("--kernel", type=Path, required=True)
    parser.add_argument("--initrd", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--poll-s", type=float, default=10.0)
    args = parser.parse_args()

    audit: Dict[str, Any] = {
        "status": "waiting",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "scheduler_pid": args.scheduler_pid,
    }
    write_json(args.audit, audit)
    if not process_alive(args.scheduler_pid):
        raise RuntimeError(f"scheduler PID is not alive: {args.scheduler_pid}")

    scheduler_stopped = False
    try:
        os.kill(args.scheduler_pid, signal.SIGSTOP)
        scheduler_stopped = True
        audit.update(
            {
                "status": "dispatch_paused_waiting_for_slot",
                "dispatch_paused_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
        write_json(args.audit, audit)

        slot: Optional[int] = None
        while slot is None:
            if not process_alive(args.scheduler_pid):
                raise RuntimeError("scheduler exited while waiting for a free slot")
            slot = first_free_slot()
            if slot is None:
                time.sleep(max(1.0, args.poll_s))

        owned_before = len(owned_qemu_commands())
        if owned_before >= 4:
            raise RuntimeError(f"free-slot invariant failed: owned_qemu_count={owned_before}")
        ssh_port = 22100 + slot
        postgres_port = 25500 + slot
        audit.update(
            {
                "status": "building",
                "slot": f"vm{slot:02d}",
                "ssh_port": ssh_port,
                "postgres_port": postgres_port,
                "owned_qemu_count_before_build": owned_before,
                "build_started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
        write_json(args.audit, audit)

        command = [
            sys.executable,
            "-u",
            str(ROOT / "shell/build_rootless_benchmark_seed.py"),
            "--benchmark",
            "tpch",
            "--base-seed",
            str(args.base_seed.resolve()),
            "--output-seed",
            str(args.output_seed.resolve()),
            "--root",
            str(args.seed_root.resolve()),
            "--ssh-port",
            str(ssh_port),
            "--postgres-port",
            str(postgres_port),
            "--cpu-cores",
            "16",
            "--memory-mib",
            "12288",
            "--load-timeout-s",
            "18000",
            "--kernel",
            str(args.kernel.resolve()),
            "--initrd",
            str(args.initrd.resolve()),
        ]
        completed = subprocess.run(
            command,
            cwd=str(ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=19800,
            check=False,
        )
        audit.update(
            {
                "returncode": completed.returncode,
                "output_tail": completed.stdout[-12000:],
                "build_finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "status": "completed" if completed.returncode == 0 else "failed",
            }
        )
        write_json(args.audit, audit)
        return completed.returncode
    except Exception as exc:  # noqa: BLE001 - durable orchestration boundary
        audit.update(
            {
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "failed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
        write_json(args.audit, audit)
        raise
    finally:
        if scheduler_stopped and process_alive(args.scheduler_pid):
            os.kill(args.scheduler_pid, signal.SIGCONT)
            audit["dispatch_resumed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            write_json(args.audit, audit)


if __name__ == "__main__":
    raise SystemExit(main())
