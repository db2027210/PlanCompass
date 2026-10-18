#!/usr/bin/env python3
"""Perpetual MySQL SGD orchestration: gate-checked launches + continuous reporter + live viewer."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_sgd_env_agents_20260723"
LOCK = HISTORY / "perpetual_orchestrator.lock"
READINESS = ROOT / "shell/verify_mysql_sgd_v4_readiness.py"
LAUNCH1 = ROOT / "shell/launch_mysql_sgd_claude_agents.py"
LAUNCH2 = ROOT / "shell/launch_mysql_sgd_four_lane_controllers.py"
REPORTER = ROOT / "shell/report_mysql_sgd_progress_every_5min.py"
SHOW = ROOT / "shell/show_mysql_sgd_progress.py"

def lock_or_die() -> None:
    lock_descriptor = os.open(LOCK, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(lock_descriptor)
        print("Orchestrator already running. Exiting.", file=sys.stderr)
        sys.exit(2)
    signal.signal(signal.SIGINT, lambda *a: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))

def run_subprocess(cmd: str, cwd: Path = ROOT) -> None:
    subprocess.run([sys.executable, str(cwd / cmd)], cwd=cwd, check=True)

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    lock_or_die()

    while True:
        # Gate check
        result = subprocess.run(
            [sys.executable, str(READINESS), "--json"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
        try:
            status = json.loads(result.stdout)
        except json.JSONDecodeError:
            status = {"ready": False, "status": "ERROR"}
        if status.get("ready", False):
            print("Gates passed — launching experiments", file=sys.stderr)
            run_subprocess("shell/launch_mysql_sgd_claude_agents.py")
            run_subprocess("shell/launch_mysql_sgd_four_lane_controllers.py")
            # Start reporter in background (already perpetual)
            # Start show in background
            subprocess.run([sys.executable, str(SHOW), "--follow"], cwd=ROOT, check=False)
        else:
            print(f"Gate not ready: {status.get('status')}", file=sys.stderr)

        if args.once:
            break
        time.sleep(60)  # poll gate every minute

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
