#!/usr/bin/env python3
"""Keep co-resident PostgreSQL Algorithm-C controllers off formal MySQL CPUs."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time


CPUSET = frozenset((*range(32, 96), *range(288, 352)))
CPUSET_TEXT = "32-95,288-351"
CONTROLLER_MARKER = "run_pg_algorithm_c_calibrate_then_scene.py"


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def proc_rows() -> tuple[dict[int, int], dict[int, str]]:
    parents: dict[int, int] = {}
    commands: dict[int, str] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            fields = (entry / "stat").read_text(encoding="utf-8").split()
            command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(
                "utf-8", errors="replace",
            ).strip()
            parents[pid] = int(fields[3])
            commands[pid] = command
        except (FileNotFoundError, ProcessLookupError, PermissionError, ValueError):
            continue
    return parents, commands


def targets() -> tuple[set[int], dict[int, str]]:
    parents, commands = proc_rows()
    roots = {pid for pid, command in commands.items() if CONTROLLER_MARKER in command}
    selected = set(roots)
    changed = True
    while changed:
        changed = False
        for pid, ppid in parents.items():
            if ppid in selected and pid not in selected:
                selected.add(pid)
                changed = True
    return selected, commands


def enforce(log: Path) -> int:
    selected, commands = targets()
    for pid in sorted(selected):
        try:
            previous = os.sched_getaffinity(pid)
            if previous == CPUSET:
                continue
            os.sched_setaffinity(pid, CPUSET)
            readback = os.sched_getaffinity(pid)
            append(log, {
                "captured_at": now(), "event": "affinity_updated", "pid": pid,
                "command_sha256": hashlib.sha256(
                    commands.get(pid, "").encode("utf-8"),
                ).hexdigest(),
                "previous_cpu_count": len(previous),
                "requested_cpuset_cpus": CPUSET_TEXT,
                "readback_cpuset_cpus": CPUSET_TEXT if readback == CPUSET else sorted(readback),
                "succeeded": readback == CPUSET,
            })
            if readback != CPUSET:
                raise RuntimeError(f"pid {pid} affinity readback differs")
        except (FileNotFoundError, ProcessLookupError):
            continue
    return len(selected)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--interval-seconds", type=float, default=0.25)
    parser.add_argument("--parent-pid", type=int)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.interval_seconds <= 0 or (args.parent_pid is not None and args.parent_pid <= 1):
        parser.error("interval and optional parent pid must be positive")
    append(args.log, {
        "captured_at": now(), "event": "watcher_started", "watcher_pid": os.getpid(),
        "lifetime_parent_pid": args.parent_pid, "controller_marker": CONTROLLER_MARKER,
        "cpuset_cpus": CPUSET_TEXT,
    })
    while True:
        count = enforce(args.log)
        if args.once:
            break
        if args.parent_pid is not None and not Path(f"/proc/{args.parent_pid}").exists():
            break
        time.sleep(args.interval_seconds)
    append(args.log, {
        "captured_at": now(), "event": "watcher_stopped", "watcher_pid": os.getpid(),
        "last_target_count": count,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
