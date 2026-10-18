#!/usr/bin/env python3
"""Resume a fresh formal waiter after an interference pilot terminates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import time


ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
WAITER = ROOT / "scripts/wait_and_run_mysql_four_scene_r0_v18.py"
HISTORY = ROOT / "history/mysql_four_scene_20260809"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot-unit", required=True)
    parser.add_argument("--formal-run-id", required=True)
    parser.add_argument("--poll-seconds", type=int, default=20)
    args = parser.parse_args()
    if (
        re.fullmatch(r"mysql-four-scene-algc-[A-Za-z0-9_.-]+\.service", args.pilot_unit) is None
        or re.fullmatch(r"[A-Za-z0-9_.-]{8,120}", args.formal_run_id) is None
        or args.poll_seconds < 10
    ):
        parser.error("unsafe arguments")
    receipt = HISTORY / f"{args.formal_run_id}_post_pilot_resume.json"
    while True:
        checked = subprocess.run(
            ["systemctl", "--user", "show", args.pilot_unit, "-p", "ActiveState", "--value"],
            cwd=ROOT, capture_output=True, text=True, check=False, timeout=10,
        )
        if checked.returncode == 0 and checked.stdout.strip() not in {"active", "activating", "reloading"}:
            break
        time.sleep(args.poll_seconds)
    unit = f"mysql-{args.formal_run_id}.service"
    started = subprocess.run([
        "systemd-run", "--user", f"--unit={unit.removesuffix('.service')}",
        "--description=MySQL v18 formal waiter resumed after non-formal pilot",
        f"--property=WorkingDirectory={ROOT}", "--property=KillMode=control-group",
        "--property=RuntimeMaxSec=28800",
        str(PYTHON), str(WAITER), "--run-id", args.formal_run_id,
        "--poll-seconds", "30",
    ], cwd=ROOT, capture_output=True, text=True, check=False, timeout=30)
    document = {
        "status": "STARTED" if started.returncode == 0 else "FAILED",
        "pilot_unit": args.pilot_unit,
        "formal_run_id": args.formal_run_id,
        "formal_unit": unit,
        "returncode": started.returncode,
        "stdout": started.stdout[-1000:],
        "stderr": started.stderr[-1000:],
        "external_process_mutation": False,
    }
    receipt.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return started.returncode


if __name__ == "__main__":
    raise SystemExit(main())
