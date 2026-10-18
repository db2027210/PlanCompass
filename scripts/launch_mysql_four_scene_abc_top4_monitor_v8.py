#!/usr/bin/env python3
"""Start the current persistent Top4 ABC pipeline monitor."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import time


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
STATE = HISTORY / "v260_to_abc_top4_pipeline_20260826/pipeline_state.json"
SCRIPT = ROOT / "scripts/monitor_mysql_four_scene_abc_pipeline_v8.py"
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
UNIT = "mysql-abc-top4-v260-monitor.service"


def main() -> int:
    command = [
        "systemd-run", "--user", f"--unit={UNIT.removesuffix('.service')}",
        "--description=MySQL current Top4 ABC independent pipeline monitor",
        f"--property=WorkingDirectory={ROOT}", "--property=KillMode=control-group",
        f"--property=StandardOutput=append:{HISTORY / 'formal_launch_logs/v260_abc_top4_monitor.stdout.log'}",
        f"--property=StandardError=append:{HISTORY / 'formal_launch_logs/v260_abc_top4_monitor.stderr.log'}",
        "/usr/bin/taskset", "-c", "240-241,496-497",
        str(PYTHON), str(SCRIPT),
    ]
    launched = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, check=False, timeout=30,
    )
    if launched.returncode != 0:
        raise RuntimeError("ABC monitor launch failed: " + launched.stderr[-2000:])
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        if STATE.is_file():
            state = json.loads(STATE.read_text(encoding="utf-8"))
            if state.get("status") in {"MONITORING", "MONITORING_WITH_FAILURE", "COMPLETE"}:
                print(json.dumps({
                    "status": "LAUNCHED", "unit": UNIT, "state": str(STATE),
                    "pipeline_status": state["status"],
                }, sort_keys=True))
                return 0
        time.sleep(1.0)
    raise TimeoutError("ABC monitor startup evidence timed out")


if __name__ == "__main__":
    raise SystemExit(main())
