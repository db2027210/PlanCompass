#!/usr/bin/env python3
"""Checks for the detached formal-run exit audit guard."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def wait_for(path: Path, predicate: object, timeout_s: float = 5.0) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if path.is_file():
            value = json.loads(path.read_text(encoding="utf-8"))
            if predicate(value):
                return value
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {path}")


def main() -> int:
    with tempfile.TemporaryDirectory() as raw:
        audit = Path(raw) / "audit.json"
        proc = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "shell/run_with_exit_audit.py"),
                "--audit-path",
                str(audit),
                "--",
                sys.executable,
                "-c",
                "import time; time.sleep(60)",
            ],
            start_new_session=True,
        )
        try:
            initial = wait_for(audit, lambda value: value.get("runner_pid"))
            runner_pid = int(initial["runner_pid"])
            os.kill(proc.pid, signal.SIGTERM)
            signalled = wait_for(
                audit,
                lambda value: bool(value.get("guard_signal_events")),
            )
            assert signalled["guard_signal_events"][-1]["forwarded_to_runner"] is False
            assert proc.poll() is None
            os.kill(runner_pid, 0)
            os.kill(proc.pid, signal.SIGINT)
            assert proc.wait(timeout=5) == 130
            finished = json.loads(audit.read_text(encoding="utf-8"))
            assert finished["status"] == "failed"
            assert finished["exit_signal_name"] == "SIGINT"
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
    print("run-with-exit-audit tests passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
