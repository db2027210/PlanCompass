#!/usr/bin/env python3
"""Keep one explicitly authorized external Docker container stopped."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def append(path: Path, event: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--interval-seconds", type=float, default=5.0)
    args = parser.parse_args()
    if args.container != "bench-pg-mixstrg-0708":
        parser.error("this guard is intentionally restricted to bench-pg-mixstrg-0708")
    log = args.log.expanduser().resolve()
    append(log, {"captured_at": now(), "event": "guard_started", "container": args.container})
    while True:
        try:
            inspected = subprocess.run(
                ["docker", "inspect", "--format", "{{.State.Running}}", args.container],
                capture_output=True, text=True, timeout=10,
            )
            if inspected.returncode == 0 and inspected.stdout.strip() == "true":
                stopped = subprocess.run(
                    ["docker", "stop", "--time", "60", args.container],
                    capture_output=True, text=True, timeout=90,
                )
                append(log, {
                    "captured_at": now(), "event": "container_stop_attempt",
                    "container": args.container, "returncode": stopped.returncode,
                    "stopped": stopped.returncode == 0,
                })
            elif inspected.returncode not in {0, 1}:
                append(log, {
                    "captured_at": now(), "event": "inspect_nonzero",
                    "container": args.container, "returncode": inspected.returncode,
                })
        except subprocess.TimeoutExpired as error:
            append(log, {
                "captured_at": now(), "event": "docker_control_timeout",
                "container": args.container, "command": error.cmd,
            })
        time.sleep(max(2.0, args.interval_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
