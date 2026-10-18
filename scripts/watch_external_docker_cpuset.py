#!/usr/bin/env python3
"""Keep exact co-resident containers off the formal MySQL CPU union."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def inspect(name: str, log: Path) -> dict | None:
    try:
        result = subprocess.run(
            ["docker", "inspect", name], capture_output=True, text=True,
            check=False, timeout=30,
        )
    except subprocess.TimeoutExpired:
        append(log, {
            "captured_at": now(), "event": "docker_inspect_timeout",
            "container": name, "retrying": True,
        })
        return None
    if result.returncode != 0:
        return None
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError:
        append(log, {
            "captured_at": now(), "event": "docker_inspect_json_invalid",
            "container": name, "retrying": True,
        })
        return None
    return value[0] if isinstance(value, list) and len(value) == 1 else None


def enforce(name: str, cpuset: str, log: Path) -> None:
    before = inspect(name, log)
    if before is None or (before.get("State") or {}).get("Running") is not True:
        return
    container_id = str(before.get("Id") or "")
    prior = str((before.get("HostConfig") or {}).get("CpusetCpus") or "")
    if prior != cpuset:
        try:
            result = subprocess.run(
                ["docker", "update", "--cpuset-cpus", cpuset, name],
                capture_output=True, text=True, check=False, timeout=30,
            )
        except subprocess.TimeoutExpired:
            append(log, {
                "captured_at": now(), "event": "cpuset_update_timeout",
                "container": name, "container_id": container_id,
                "previous_cpuset_cpus": prior,
                "requested_cpuset_cpus": cpuset, "retrying": True,
            })
            return
        if result.returncode != 0:
            append(log, {
                "captured_at": now(), "event": "cpuset_update_failed",
                "container": name, "container_id": container_id,
                "previous_cpuset_cpus": prior, "requested_cpuset_cpus": cpuset,
                "stderr_sha256": hashlib.sha256(result.stderr.encode()).hexdigest(),
            })
            return
    after = inspect(name, log)
    readback = str(((after or {}).get("HostConfig") or {}).get("CpusetCpus") or "")
    if after is None or str(after.get("Id") or "") != container_id or readback != cpuset:
        append(log, {
            "captured_at": now(), "event": "cpuset_readback_differed",
            "container": name, "container_id_before": container_id,
            "container_id_after": str((after or {}).get("Id") or ""),
            "requested_cpuset_cpus": cpuset,
            "readback_cpuset_cpus": readback, "retrying": True,
        })
        return
    if prior != cpuset:
        append(log, {
            "captured_at": now(), "event": "cpuset_updated",
            "container": name, "container_id": container_id,
            "previous_cpuset_cpus": prior, "requested_cpuset_cpus": cpuset,
            "readback_cpuset_cpus": readback, "container_restarted": False,
            "cpu_quota_unchanged": True, "workload_unchanged": True,
        })


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", action="append", required=True)
    parser.add_argument("--cpuset-cpus", required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--interval-seconds", type=float, default=0.25)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if (
        args.interval_seconds <= 0
        or re.fullmatch(r"[0-9,-]+", args.cpuset_cpus) is None
        or any(re.fullmatch(r"[A-Za-z0-9_.-]+", name) is None for name in args.container)
    ):
        parser.error("unsafe container, cpuset, or interval")
    log = args.log.expanduser().resolve()
    append(log, {
        "captured_at": now(), "event": "watcher_started",
        "watcher_pid": os.getpid(), "containers": sorted(set(args.container)),
        "cpuset_cpus": args.cpuset_cpus,
    })
    while True:
        for name in sorted(set(args.container)):
            enforce(name, args.cpuset_cpus, log)
        if args.once:
            break
        time.sleep(args.interval_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
