#!/usr/bin/env python3
"""Atomically publish the TPC-C lane subagent heartbeat."""

from __future__ import annotations

from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import time


DEFAULT_PATH = Path(
    "/home/anonymous/program/resource_adaptive_mysql/history/"
    "mysql_sgd_env_agents_20260723/manual_controllers/tpcc_cpu/"
    "subagent_heartbeat.json"
)


def atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    data = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def payload(args: argparse.Namespace) -> dict:
    return {
        "utc": datetime.now(timezone.utc).isoformat(),
        "status": args.status,
        "current_task": args.current_task,
        "current_pid_or_command": args.current_pid_or_command,
        "mutex_state": args.mutex_state,
        "last_evidence": args.last_evidence,
        "next_action": args.next_action,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--status", default="IN_PROGRESS")
    parser.add_argument("--current-task", default="production_binding_capture_and_fresh_r0")
    parser.add_argument("--current-pid-or-command", default="no_formal_algorithm_c_accepted_round")
    parser.add_argument("--mutex-state", default="tpcc_lane_only;no_four_lane_launch")
    parser.add_argument("--last-evidence", default="template_cmp_pass;174_files;1452441280_bytes")
    parser.add_argument("--next-action", default="run_exact_production_benchbase_capture_then_fresh_node01_r0")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    while True:
        atomic_write(args.path, payload(args))
        if args.once:
            return 0
        time.sleep(300)


if __name__ == "__main__":
    raise SystemExit(main())
