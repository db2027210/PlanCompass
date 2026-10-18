#!/usr/bin/env python3
"""Start low-frequency monitoring and final audit for a continued campaign."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
MONITOR = ROOT / "scripts/monitor_mysql_algorithm_c_v209_completion.py"
FINALIZER = ROOT / "scripts/finalize_mysql_algorithm_c_continued_after_monitor_v2.py"


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def atomic(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prefix")
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,120}", args.prefix) is None:
        parser.error("unsafe prefix")
    campaign = HISTORY / args.prefix
    manifest_path = campaign / "launch_manifest.json"
    manifest = load(manifest_path)
    if (
        manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or set(manifest.get("launches") or {}) != {
            "job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io"
        }
    ):
        raise RuntimeError("continued campaign manifest differs")
    monitor_log = campaign / "completion_monitor.log"
    finalizer_log = campaign / "completion_finalizer.log"
    if any(path.exists() for path in (monitor_log, finalizer_log, campaign / "completion_monitor_summary.json")):
        raise RuntimeError("fresh continued monitor outputs required")
    with monitor_log.open("w") as stream:
        monitor = subprocess.Popen(
            [sys.executable, "-u", str(MONITOR), args.prefix],
            cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    deadline = time.monotonic() + 30
    summary_path = campaign / "completion_monitor_summary.json"
    while time.monotonic() < deadline and not summary_path.is_file():
        if monitor.poll() is not None:
            raise RuntimeError(f"continued monitor exited: {monitor.returncode}")
        time.sleep(1)
    if not summary_path.is_file():
        raise RuntimeError("continued monitor did not write its first snapshot")
    with finalizer_log.open("w") as stream:
        finalizer = subprocess.Popen(
            [sys.executable, "-u", str(FINALIZER), args.prefix],
            cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    manifest["monitor"] = {
        "pid": monitor.pid, "interval_seconds": 300,
        "script": str(MONITOR), "log": str(monitor_log),
    }
    manifest["finalizer"] = {
        "pid": finalizer.pid, "script": str(FINALIZER),
        "writer": str(ROOT / "scripts/write_mysql_algorithm_c_continued_final_completion_audit_v2.py"),
        "log": str(finalizer_log),
    }
    atomic(manifest_path, manifest)
    print(json.dumps({
        "status": "RUNNING", "monitor_pid": monitor.pid,
        "finalizer_pid": finalizer.pid, "summary": str(summary_path),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

