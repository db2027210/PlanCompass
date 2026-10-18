#!/usr/bin/env python3
"""Fail closed unless one Linux PID is absent or a childless zombie."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import List


def process_state(pid: int) -> str:
    status = Path(f"/proc/{pid}/status")
    if not status.exists():
        return "absent"
    for line in status.read_text(encoding="utf-8").splitlines():
        if line.startswith("State:"):
            return line.split()[1]
    raise RuntimeError(f"cannot read process state for PID {pid}")


def children(pid: int) -> List[int]:
    path = Path(f"/proc/{pid}/task/{pid}/children")
    if not path.exists():
        return []
    return [int(item) for item in path.read_text(encoding="utf-8").split()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", required=True, type=int)
    args = parser.parse_args()

    pid = args.pid
    state = process_state(pid)
    observed_children = children(pid)
    if state not in {"Z", "X", "absent"}:
        raise RuntimeError(
            f"PID {pid} can still execute: state={state}"
        )
    if observed_children:
        raise RuntimeError(
            f"inactive PID {pid} still owns child processes: {observed_children}"
        )
    cmdline_path = Path(f"/proc/{pid}/cmdline")
    cmdline_bytes = cmdline_path.read_bytes() if cmdline_path.exists() else b""
    if state != "absent" and cmdline_bytes:
        raise RuntimeError(
            f"zombie/dead PID {pid} unexpectedly has a non-empty command line"
        )
    print(
        json.dumps(
            {
                "pid": pid,
                "state": state,
                "children": observed_children,
                "can_execute": False,
                "safe_for_replacement_controller": True,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
