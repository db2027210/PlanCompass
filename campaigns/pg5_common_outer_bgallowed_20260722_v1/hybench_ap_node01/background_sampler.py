#!/usr/bin/env python3
"""Record host background load without enforcing a host-idle gate."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time

import psutil


def process_exists(pid: int) -> bool:
    try:
        process = psutil.Process(pid)
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--watched-pid", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--interval", type=float, default=15.0)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    psutil.cpu_percent(interval=None)
    with args.output.open("a", encoding="utf-8") as handle:
        while process_exists(args.watched_pid):
            memory = psutil.virtual_memory()
            swap = psutil.swap_memory()
            disk = psutil.disk_io_counters(perdisk=False)
            row = {
                "timestamp_epoch": time.time(),
                "background_load_allowed": True,
                "loadavg": os.getloadavg(),
                "cpu_percent": psutil.cpu_percent(interval=1.0),
                "memory": {
                    "total": memory.total,
                    "available": memory.available,
                    "used": memory.used,
                    "percent": memory.percent,
                },
                "swap": {"used": swap.used, "percent": swap.percent},
                "disk_io": None if disk is None else disk._asdict(),
            }
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            handle.flush()
            time.sleep(max(1.0, args.interval - 1.0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
