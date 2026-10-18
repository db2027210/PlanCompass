#!/usr/bin/env python3
"""Temporarily lift one container's blkio cap only during template DB restore.

The supervisor is fail-closed: any probe failure, signal, parent exit, or normal
termination restores the configured validation cap before returning.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple


STOP_REQUESTED = False


def request_stop(_signum: int, _frame: object) -> None:
    global STOP_REQUESTED
    STOP_REQUESTED = True


def command_output(args: list[str], *, env: Optional[Dict[str, str]] = None) -> str:
    completed = subprocess.run(
        args,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    return completed.stdout.strip()


def blkio_paths(container: str) -> Tuple[Path, Path]:
    pid = command_output(["docker", "inspect", "-f", "{{.State.Pid}}", container])
    relative = ""
    for line in Path(f"/proc/{int(pid)}/cgroup").read_text(encoding="utf-8").splitlines():
        fields = line.split(":", 2)
        if len(fields) == 3 and "blkio" in fields[1].split(","):
            relative = fields[2].lstrip("/")
            break
    if not relative:
        raise RuntimeError(f"cannot resolve blkio cgroup for {container}")
    root = Path("/sys/fs/cgroup/blkio") / relative
    return root / "blkio.throttle.read_bps_device", root / "blkio.throttle.write_bps_device"


def set_bps(paths: Tuple[Path, Path], device: str, bps: int) -> None:
    payload = f"{device} {int(bps)}\n"
    for path in paths:
        subprocess.run(
            ["sudo", "-n", "tee", str(path)],
            check=True,
            input=payload,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        current = path.read_text(encoding="utf-8")
        if f"{device} {int(bps)}" not in current.splitlines():
            raise RuntimeError(f"failed to verify {path}={payload.strip()!r}: {current!r}")


def restore_is_active(args: argparse.Namespace) -> bool:
    sql = (
        "SELECT CASE WHEN EXISTS ("
        "SELECT 1 FROM pg_stat_activity "
        "WHERE state='active' AND query LIKE 'CREATE DATABASE % WITH TEMPLATE %'"
        ") THEN 1 ELSE 0 END;"
    )
    env = dict(os.environ)
    env["PGPASSWORD"] = args.pg_password
    output = command_output(
        [
            "psql",
            "-h",
            args.pg_host,
            "-p",
            str(args.pg_port),
            "-U",
            args.pg_user,
            "-d",
            "postgres",
            "-X",
            "-q",
            "-A",
            "-t",
            "-c",
            sql,
        ],
        env=env,
    )
    return output == "1"


def append_event(log_path: Path, event: str, **fields: object) -> None:
    record = {
        "timestamp": datetime.now(timezone.utc).astimezone().isoformat(),
        "event": event,
        **fields,
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--validation-bps", required=True, type=int)
    parser.add_argument("--restore-bps", required=True, type=int)
    parser.add_argument("--parent-pid", required=True, type=int)
    parser.add_argument("--pg-host", default="127.0.0.1")
    parser.add_argument("--pg-port", required=True, type=int)
    parser.add_argument("--pg-user", default="postgres")
    parser.add_argument("--pg-password", default="postgres")
    parser.add_argument("--poll-seconds", default=0.25, type=float)
    parser.add_argument("--log-path", required=True, type=Path)
    args = parser.parse_args()
    if args.validation_bps <= 0 or args.restore_bps < args.validation_bps:
        parser.error("restore-bps must be >= positive validation-bps")
    if args.parent_pid <= 0 or args.pg_port <= 0 or args.poll_seconds <= 0.0:
        parser.error("parent-pid, pg-port, and poll-seconds must be positive")
    return args


def main() -> int:
    args = parse_args()
    paths = blkio_paths(args.container)
    current_bps: Optional[int] = None
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    append_event(
        args.log_path,
        "supervisor_started",
        container=args.container,
        device=args.device,
        validation_bps=args.validation_bps,
        restore_bps=args.restore_bps,
        parent_pid=args.parent_pid,
        paths=[str(path) for path in paths],
    )
    try:
        while not STOP_REQUESTED and Path(f"/proc/{args.parent_pid}").exists():
            try:
                active = restore_is_active(args)
                desired_bps = args.restore_bps if active else args.validation_bps
            except Exception as exc:
                active = False
                desired_bps = args.validation_bps
                append_event(args.log_path, "probe_failed_fail_closed", error=str(exc))
            if desired_bps != current_bps:
                set_bps(paths, args.device, desired_bps)
                current_bps = desired_bps
                append_event(
                    args.log_path,
                    "throttle_changed",
                    restore_active=active,
                    bps=desired_bps,
                )
            time.sleep(args.poll_seconds)
    finally:
        set_bps(paths, args.device, args.validation_bps)
        append_event(args.log_path, "validation_throttle_restored", bps=args.validation_bps)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
