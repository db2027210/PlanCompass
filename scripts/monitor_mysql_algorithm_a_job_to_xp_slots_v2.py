#!/usr/bin/env python3
"""Bind the audited JOB-to-XP monitor to the live v40 campaign."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts import monitor_mysql_algorithm_a_job_to_xp_slots_v1 as legacy


HISTORY = ROOT / "history/mysql_four_scene_20260809"
LOG_ROOT = HISTORY / "formal_launch_logs"
CAMPAIGN = "v40_to_a_last_accepted_20260822_attempt002"
JOB_RUN = f"{CAMPAIGN}_job_four_worker_attempt001"

legacy.CONTRACT_PATH = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_job_to_xp_slots_20260823_v2"
    / "transition_contract.json"
)
legacy.SOURCE_FREEZE = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_job_dynamic_slots_20260823_v2"
    / "job_four_worker_resource_successor.json"
)
legacy.SUCCESSOR_FREEZE = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_job_to_xp_slots_20260823_v2"
    / "xp_four_worker_resource_successor.json"
)
legacy.TRIGGER_PATH = LOG_ROOT / f"{CAMPAIGN}.job_to_xp_v2.trigger.json"
legacy.EXECUTOR = ROOT / "scripts/execute_mysql_algorithm_a_job_to_xp_scene_v2.py"
legacy.JOB = {
    "scene": "job_memory",
    "state": str(HISTORY / JOB_RUN / "algorithm_a_state.json"),
    "unit": f"mysql-alga-dynamic-{JOB_RUN}.service",
    "output": str(HISTORY / JOB_RUN),
}
legacy.XP = {
    "hybench_xp_cpu": {
        "scene": "hybench_xp_cpu",
        "state": str(HISTORY / f"{CAMPAIGN}_xp_cpu" / "algorithm_a_state.json"),
        "unit": "mysql-alga-async-v40_to_a_partial_round_quiet_gate_resume_20260822_attempt005_xp_cpu.service",
        "output": str(HISTORY / f"{CAMPAIGN}_xp_cpu"),
        "result": str(LOG_ROOT / f"{CAMPAIGN}.job_to_xp_v2.cpu_handoff.json"),
    },
    "hybench_xp_io": {
        "scene": "hybench_xp_io",
        "state": str(HISTORY / f"{CAMPAIGN}_xp_io" / "algorithm_a_state.json"),
        "unit": "mysql-alga-async-v40_to_a_partial_round_quiet_gate_resume_20260822_attempt009_xp_io.service",
        "output": str(HISTORY / f"{CAMPAIGN}_xp_io"),
        "result": str(LOG_ROOT / f"{CAMPAIGN}.job_to_xp_v2.io_handoff.json"),
    },
}


def launch_executor(scene: str) -> dict[str, Any]:
    suffix = "cpu" if scene == "hybench_xp_cpu" else "io"
    unit = f"mysql-alga-v40-job-to-xp-transition-{suffix}.service"
    result = Path(str(legacy.XP[scene]["result"]))
    stdout = LOG_ROOT / f"{CAMPAIGN}.job_to_xp_v2.{suffix}.stdout.log"
    stderr = LOG_ROOT / f"{CAMPAIGN}.job_to_xp_v2.{suffix}.stderr.log"
    command = [
        "systemd-run", "--user", f"--unit={unit.removesuffix('.service')}",
        f"--description=MySQL Algorithm A v40 independent {scene} slot transition",
        f"--property=WorkingDirectory={ROOT}", "--property=KillMode=control-group",
        f"--property=StandardOutput=append:{stdout}",
        f"--property=StandardError=append:{stderr}",
        "/usr/bin/taskset", "-c", legacy.MANAGEMENT_CPUSET,
        str(legacy.PYTHON), str(legacy.EXECUTOR), "--scene", scene,
        "--trigger", str(legacy.TRIGGER_PATH), "--result", str(result),
        "--timeout-seconds", "172800",
    ]
    completed = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"{scene} transition launch failed: {completed.stderr}")
    return {
        "scene": scene, "unit": unit, "result": str(result),
        "stdout": str(stdout), "stderr": str(stderr),
    }


legacy.launch_executor = launch_executor


if __name__ == "__main__":
    raise SystemExit(legacy.main())
