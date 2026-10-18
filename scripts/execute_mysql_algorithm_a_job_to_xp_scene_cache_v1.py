#!/usr/bin/env python3
"""Move one unfinished XP scene to four workers after cache-fixed JOB."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts import execute_mysql_algorithm_a_job_to_xp_scene_v1 as legacy
from scripts import run_mysql_algorithm_c_async_scene_v49 as c49


HISTORY = ROOT / "history/mysql_four_scene_20260809"
LOG_ROOT = HISTORY / "formal_launch_logs"
CAMPAIGN = "v41_to_a_cache_fixed_20260823"

legacy.CONTROLLER = ROOT / "scripts/run_mysql_algorithm_a_job_to_xp_resume_v2.py"
legacy.TRANSITION_ROOT = HISTORY / f"{CAMPAIGN}_job_to_xp_slots_v3"
legacy.CONTRACT_PATH = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_job_to_xp_slots_20260823_v3"
    / "transition_contract.json"
)

_rolling_quiet_gate = legacy.coordination.rolling_quiet_gate


def rolling_quiet_gate(*args: Any, **kwargs: Any):
    scene = str(kwargs.get("run_id") or "").rsplit("_", 1)[-1]
    kwargs["run_id"] = f"{CAMPAIGN}_job_to_xp_{scene}_v3"
    return _rolling_quiet_gate(*args, **kwargs)


legacy.coordination.rolling_quiet_gate = rolling_quiet_gate


def launch_resume(
    *, scene: str, snapshot: Path, authority: Path,
) -> dict[str, Any]:
    suffix = "xp_cpu" if scene == "hybench_xp_cpu" else "xp_io"
    run_id = f"{CAMPAIGN}_job_to_xp_four_worker_attempt001_{suffix}"
    unit = f"mysql-alga-v41-job-to-xp-{suffix}.service"
    output = HISTORY / run_id
    stdout = LOG_ROOT / f"{run_id}.stdout.log"
    stderr = LOG_ROOT / f"{run_id}.stderr.log"
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh XP successor output required: {output}")
    command = [
        "systemd-run", "--user", f"--unit={unit.removesuffix('.service')}",
        f"--description=MySQL Algorithm A v41 four-worker resume {scene}",
        f"--property=WorkingDirectory={ROOT}", "--property=KillMode=control-group",
        f"--property=StandardOutput=append:{stdout}",
        f"--property=StandardError=append:{stderr}",
        "/usr/bin/taskset", "-c", legacy.MANAGEMENT_CPUSET,
        str(legacy.PYTHON), str(legacy.CONTROLLER), "--run-id", run_id,
        "--scene", scene, "--predecessor-state", str(snapshot),
        "--transition-authority", str(authority), "--output-dir", str(output),
    ]
    completed = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"{scene} successor launch failed: {completed.stderr}")
    state_path = output / "algorithm_a_state.json"
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        fields = legacy.systemctl(
            "show", unit, "-p", "ActiveState", "-p", "MainPID", check=False,
        ).stdout
        if "ActiveState=failed" in fields or "ActiveState=inactive" in fields:
            raise RuntimeError(f"{scene} successor exited during startup: {fields}")
        if state_path.is_file():
            state = legacy.load(state_path)
            if (
                state.get("status") == "RUNNING"
                and int(state.get("physical_replay_parallelism") or 0) == 4
            ):
                return {
                    "scene": scene, "run_id": run_id, "unit": unit,
                    "output": str(output), "state": str(state_path),
                    "stdout": str(stdout), "stderr": str(stderr),
                    "stage": state.get("stage"),
                }
        time.sleep(1)
    raise TimeoutError(f"{scene} successor startup timed out")


legacy.launch_resume = launch_resume


def main() -> int:
    c49.validate_v65_wave_coordination_contract()
    c49.install()
    legacy.coordination.predecessor.CONTRACT_PATH = c49.COORDINATION_CONTRACT
    return legacy.main()


if __name__ == "__main__":
    raise SystemExit(main())
