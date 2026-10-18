#!/usr/bin/env python3
"""Expand the cache-fixed Algorithm A JOB run from two to four workers."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts import adopt_mysql_algorithm_a_job_slot_workers_v1 as adoption
from scripts import execute_mysql_algorithm_a_job_slot_transition_v1 as legacy
from scripts import run_mysql_algorithm_c_async_scene_v49 as c49


HISTORY = ROOT / "history/mysql_four_scene_20260809"
LOG_ROOT = HISTORY / "formal_launch_logs"
CAMPAIGN = "v41_to_a_cache_fixed_20260823"
SUCCESSOR_RUN = "v41_to_a_job_cache_fixed_four_worker_20260823_attempt001_job"

legacy.CONTROLLER = ROOT / "scripts/run_mysql_algorithm_a_job_cache_dynamic_resume_v1.py"
legacy.TRANSITION_ROOT = HISTORY / f"{CAMPAIGN}_job_dynamic_slots_v1"
legacy.FREEZE_PATH = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_job_cache_dynamic_slots_20260823_v1"
    / "job_four_worker_resource_successor.json"
)
legacy.CONTRACT_PATH = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_job_cache_dynamic_slots_20260823_v1"
    / "transition_contract.json"
)
legacy.JOB_STATE = (
    HISTORY / "v41_to_a_job_cache_fixed_20260823_attempt001_job"
    / "algorithm_a_state.json"
)
legacy.JOB_UNIT = (
    "mysql-alga-cachefix-v41_to_a_job_cache_fixed_20260823_attempt001_job.service"
)
legacy.TPCH_STATE = (
    HISTORY / "v41_to_a_tpch_cache_fixed_20260823_attempt003_tpch"
    / "algorithm_a_state.json"
)
legacy.ORIGINAL_MONITOR_UNIT = (
    "mysql-alga-cachefix-v41-tpch-to-job4-monitor-v1.service"
)


_rolling_quiet_gate = legacy.coordination.rolling_quiet_gate


def rolling_quiet_gate(*args: Any, **kwargs: Any):
    kwargs["run_id"] = f"{CAMPAIGN}_job_dynamic_slots_v1"
    return _rolling_quiet_gate(*args, **kwargs)


legacy.coordination.rolling_quiet_gate = rolling_quiet_gate
legacy.provision.provision_worker = adoption.adopt_worker


def launch(snapshot: Path, authority: Path) -> dict[str, Any]:
    unit = f"mysql-alga-dynamic-{SUCCESSOR_RUN}.service"
    output = HISTORY / SUCCESSOR_RUN
    stdout = LOG_ROOT / f"{SUCCESSOR_RUN}.stdout.log"
    stderr = LOG_ROOT / f"{SUCCESSOR_RUN}.stderr.log"
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh cache-fixed JOB successor required: {output}")
    command = [
        "systemd-run", "--user", f"--unit={unit.removesuffix('.service')}",
        "--description=MySQL Algorithm A cache-fixed four-worker JOB resume",
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
        raise RuntimeError(f"cache-fixed four-worker JOB launch failed: {completed.stderr}")
    state_path = output / "algorithm_a_state.json"
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        fields = legacy.systemctl(
            "show", unit, "-p", "ActiveState", "-p", "MainPID", check=False,
        ).stdout
        if "ActiveState=failed" in fields or "ActiveState=inactive" in fields:
            raise RuntimeError(f"cache-fixed JOB successor exited: {fields}")
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
                    "cache_precondition_retained": True,
                }
        time.sleep(1)
    raise TimeoutError("cache-fixed four-worker JOB startup timed out")


legacy.launch = launch


def main() -> int:
    c49.validate_v65_wave_coordination_contract()
    c49.install()
    legacy.coordination.predecessor.CONTRACT_PATH = c49.COORDINATION_CONTRACT
    return legacy.main()


if __name__ == "__main__":
    raise SystemExit(main())
