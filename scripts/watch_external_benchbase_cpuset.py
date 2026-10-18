#!/usr/bin/env python3
"""Keep one known co-resident BenchBase campaign off formal MySQL CPUs."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import select
import subprocess


CONTAINERS = (
    "bench-run-mixstrg-0809",
    "bench-pg-mixstrg-0809",
    "bench-run-mixstrg-0708",
    "bench-pg-mixstrg-0708",
    "bench-run-row",
    "bench-pg-row",
)
CPUSET = "32-95,288-351"
MEMS = "0"


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def append_event(path: Path, event: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()


def inspect(name: str) -> dict | None:
    result = subprocess.run(
        ["docker", "inspect", name], capture_output=True, text=True,
        check=False, timeout=15,
    )
    if result.returncode != 0:
        return None
    rows = json.loads(result.stdout)
    return rows[0] if isinstance(rows, list) and len(rows) == 1 else None


def enforce(name: str, log: Path) -> None:
    row = inspect(name)
    if row is None:
        return
    host = row.get("HostConfig") or {}
    if host.get("CpusetCpus") == CPUSET and host.get("CpusetMems") == MEMS:
        return
    result = subprocess.run(
        [
            "docker", "update", "--cpuset-cpus", CPUSET,
            "--cpuset-mems", MEMS, name,
        ], capture_output=True, text=True, check=False, timeout=30,
    )
    append_event(log, {
        "captured_at": now(),
        "container": name,
        "container_id": row.get("Id"),
        "previous_cpuset_cpus": host.get("CpusetCpus"),
        "previous_cpuset_mems": host.get("CpusetMems"),
        "requested_cpuset_cpus": CPUSET,
        "requested_cpuset_mems": MEMS,
        "returncode": result.returncode,
        "succeeded": result.returncode == 0,
        "stderr_tail": result.stderr[-500:],
    })
    if result.returncode != 0:
        raise RuntimeError(f"failed to isolate {name}: {result.stderr[-500:]}")


def enforce_existing(log: Path) -> None:
    result = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{.Names}}"],
        capture_output=True, text=True, check=True, timeout=30,
    )
    existing = set(result.stdout.splitlines())
    for name in CONTAINERS:
        if name in existing:
            enforce(name, log)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--interval-seconds", type=float, default=0.5)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.parent_pid <= 1 or args.interval_seconds <= 0:
        parser.error("parent pid and interval must be positive")

    append_event(args.log, {
        "captured_at": now(), "event": "watcher_started",
        "watcher_pid": os.getpid(), "parent_pid": args.parent_pid,
        "containers": list(CONTAINERS), "cpuset_cpus": CPUSET,
        "cpuset_mems": MEMS,
    })
    if args.once:
        enforce_existing(args.log)
    else:
        events = subprocess.Popen(
            [
                "docker", "events", "--format", "{{json .}}",
                "--filter", "type=container", "--filter", "event=create",
                "--filter", "event=start",
            ],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )
        try:
            if events.stdout is None:
                raise RuntimeError("docker events stdout is unavailable")
            enforce_existing(args.log)
            while Path(f"/proc/{args.parent_pid}").exists():
                readable, _, _ = select.select(
                    [events.stdout], [], [], args.interval_seconds
                )
                if not readable:
                    if events.poll() is not None:
                        stderr = events.stderr.read() if events.stderr else ""
                        raise RuntimeError(
                            f"docker events exited: {events.returncode}: {stderr[-500:]}"
                        )
                    continue
                line = events.stdout.readline()
                if not line:
                    continue
                event = json.loads(line)
                name = ((event.get("Actor") or {}).get("Attributes") or {}).get(
                    "name"
                )
                if name in CONTAINERS:
                    enforce(str(name), args.log)
        finally:
            if events.poll() is None:
                events.terminate()
                try:
                    events.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    events.kill()
                    events.wait(timeout=10)
    append_event(args.log, {
        "captured_at": now(), "event": "watcher_stopped",
        "watcher_pid": os.getpid(), "parent_pid": args.parent_pid,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
