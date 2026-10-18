#!/usr/bin/env python3
"""Bind the audited JOB 2-to-4 transition to the live v40 campaign."""

from __future__ import annotations

import subprocess
import os
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts import execute_mysql_algorithm_a_job_slot_transition_v1 as legacy


HISTORY = ROOT / "history/mysql_four_scene_20260809"
LOG_ROOT = HISTORY / "formal_launch_logs"
CAMPAIGN = "v40_to_a_last_accepted_20260822_attempt002"
SUCCESSOR_RUN = f"{CAMPAIGN}_job_four_worker_attempt001"


legacy.CONTROLLER = ROOT / "scripts/run_mysql_algorithm_a_job_dynamic_resume_v3.py"
legacy.TRANSITION_ROOT = HISTORY / f"{CAMPAIGN}_job_dynamic_slots_v2"
legacy.FREEZE_PATH = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_job_dynamic_slots_20260823_v2"
    / "job_four_worker_resource_successor.json"
)
legacy.CONTRACT_PATH = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_job_dynamic_slots_20260823_v2"
    / "transition_contract.json"
)
legacy.JOB_STATE = HISTORY / f"{CAMPAIGN}_job" / "algorithm_a_state.json"
legacy.JOB_UNIT = (
    "mysql-alga-async-v40_to_a_partial_round_enospc2_20260822_attempt007_job.service"
)
legacy.TPCH_STATE = HISTORY / f"{CAMPAIGN}_tpch" / "algorithm_a_state.json"
legacy.ORIGINAL_MONITOR_UNIT = (
    "mysql-alga-v40-last-accepted-dynamic-monitor-resilient.service"
)


_rolling_quiet_gate = legacy.coordination.rolling_quiet_gate


def rolling_quiet_gate(*args: Any, **kwargs: Any):
    kwargs["run_id"] = f"{CAMPAIGN}_job_dynamic_slots_v2"
    return _rolling_quiet_gate(*args, **kwargs)


legacy.coordination.rolling_quiet_gate = rolling_quiet_gate


def launch(snapshot: Path, authority: Path) -> dict[str, Any]:
    unit = f"mysql-alga-dynamic-{SUCCESSOR_RUN}.service"
    output = HISTORY / SUCCESSOR_RUN
    stdout = LOG_ROOT / f"{SUCCESSOR_RUN}.stdout.log"
    stderr = LOG_ROOT / f"{SUCCESSOR_RUN}.stderr.log"
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh successor output required: {output}")
    command = [
        "systemd-run", "--user", f"--unit={unit.removesuffix('.service')}",
        "--description=MySQL Algorithm A four-worker JOB resume v40",
        f"--property=WorkingDirectory={ROOT}", "--property=KillMode=control-group",
        f"--property=StandardOutput=append:{stdout}",
        f"--property=StandardError=append:{stderr}",
        "/usr/bin/taskset", "-c", legacy.MANAGEMENT_CPUSET,
        str(legacy.PYTHON), str(legacy.CONTROLLER), "--run-id", SUCCESSOR_RUN,
        "--scene", legacy.slots.SCENE, "--predecessor-state", str(snapshot),
        "--transition-authority", str(authority), "--output-dir", str(output),
    ]
    completed = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"four-worker JOB launch failed: {completed.stderr}")
    state_path = output / "algorithm_a_state.json"
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        fields = legacy.systemctl(
            "show", unit, "-p", "ActiveState", "-p", "MainPID", check=False,
        ).stdout
        if "ActiveState=failed" in fields or "ActiveState=inactive" in fields:
            raise RuntimeError(f"JOB successor exited during startup: {fields}")
        if state_path.is_file():
            state = legacy.load(state_path)
            if (
                state.get("status") == "RUNNING"
                and int(state.get("physical_replay_parallelism") or 0) == 4
            ):
                return {
                    "run_id": SUCCESSOR_RUN, "unit": unit,
                    "output": str(output), "state": str(state_path),
                    "stdout": str(stdout), "stderr": str(stderr),
                    "stage": state.get("stage"),
                }
        time.sleep(1)
    raise TimeoutError("four-worker JOB successor startup timed out")


legacy.launch = launch


if __name__ == "__main__":
    raise SystemExit(legacy.main())
