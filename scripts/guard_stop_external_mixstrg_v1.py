#!/usr/bin/env python3
"""Temporarily stop only the authorized external mixstrg script and containers."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Mapping


SCRIPT = "/home/anonymous/working/benchbase/run_mixstrg_0809.sh"
CONTAINERS = ("bench-run-mixstrg-opt", "bench-pg-mixstrg-0817")


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def append(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def matching_script_processes() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    own_pid = os.getpid()
    own_pgid = os.getpgrp()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == own_pid:
            continue
        try:
            arguments = [
                token.decode("utf-8", "replace")
                for token in (entry / "cmdline").read_bytes().split(b"\0")
                if token
            ]
            if SCRIPT not in arguments:
                continue
            pid = int(entry.name)
            pgid = os.getpgid(pid)
            rows.append({
                "pid": pid,
                "pgid": pgid,
                "own_process_group": pgid == pid and pgid != own_pgid,
                "command_sha256": hashlib.sha256(
                    b"\0".join(token.encode() for token in arguments)
                ).hexdigest(),
            })
        except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
            continue
    return rows


def container_running(name: str) -> bool:
    completed = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.Running}}", name],
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )
    return completed.returncode == 0 and completed.stdout.strip() == "true"


def stop_once(log: Path) -> None:
    for row in matching_script_processes():
        event = {"captured_at": now(), "event": "script_observed", **row}
        if row["own_process_group"]:
            completed = subprocess.run(
                ["sudo", "-n", "kill", "-TERM", "--", f"-{row['pgid']}"],
                text=True,
                capture_output=True,
                check=False,
                timeout=15,
            )
            event.update({
                "action": "terminate_exact_script_process_group",
                "returncode": completed.returncode,
                "parent_terminal_mutation": False,
            })
        else:
            event.update({
                "action": "skip_shared_process_group",
                "returncode": None,
                "parent_terminal_mutation": False,
            })
        append(log, event)
    for container in CONTAINERS:
        try:
            running = container_running(container)
        except subprocess.SubprocessError as error:
            append(log, {
                "captured_at": now(),
                "event": "container_probe_failed",
                "container": container,
                "error": f"{type(error).__name__}: {error}",
            })
            continue
        if not running:
            continue
        completed = subprocess.run(
            ["docker", "stop", "--time", "30", container],
            text=True,
            capture_output=True,
            check=False,
            timeout=45,
        )
        append(log, {
            "captured_at": now(),
            "event": "container_stop",
            "container": container,
            "returncode": completed.returncode,
            "delete_requested": False,
            "unrelated_container_mutation": False,
        })


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--maximum-seconds", type=float, default=8 * 3600)
    parser.add_argument("--interval-seconds", type=float, default=1.0)
    args = parser.parse_args()
    if args.maximum_seconds <= 0 or args.maximum_seconds > 8 * 3600:
        parser.error("maximum must be in (0, 28800]")
    if args.interval_seconds < 0.25 or args.interval_seconds > 30:
        parser.error("interval must be in [0.25, 30]")
    log = args.log.expanduser().resolve()
    append(log, {
        "captured_at": now(),
        "event": "guard_started",
        "authorized_script": SCRIPT,
        "authorized_containers": list(CONTAINERS),
        "maximum_seconds": args.maximum_seconds,
        "script_delete_prohibited": True,
        "container_delete_command_prohibited": True,
        "parent_terminal_mutation_prohibited": True,
    })
    started = time.monotonic()
    while time.monotonic() - started < args.maximum_seconds:
        stop_once(log)
        time.sleep(args.interval_seconds)
    append(log, {"captured_at": now(), "event": "guard_time_bound_reached"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
