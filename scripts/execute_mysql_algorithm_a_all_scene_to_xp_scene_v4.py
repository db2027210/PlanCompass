#!/usr/bin/env python3
"""Execute XP4 handoff with v15 isolation and inherited CPU pinning."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_co_resident_pg_isolation_v15 as pg_wrapper
from scripts import execute_mysql_algorithm_a_all_scene_to_xp_scene_v1 as implementation


CONFIG = ROOT / "config/formal/frozen_mysql_algorithm_a_all_scene_to_xp4_20260823_v4"


def launch_resume(*, scene: str, snapshot: Path, authority: Path) -> dict[str, Any]:
    if implementation._TPCH_EVIDENCE is None:
        raise RuntimeError("TPC-H terminal evidence was not validated")
    authority_document = implementation.predecessor.load(authority)
    authority_document["tpch_terminal_evidence"] = dict(
        implementation._TPCH_EVIDENCE
    )
    authority_document["both_memory_scenes_complete_required"] = True
    authority_document["numa_exclusive_scheduler_lock_required"] = False
    authority_document["fixed_disjoint_cpu_affinity_required"] = True
    authority_document["management_controller_cpuset"] = (
        implementation.MANAGEMENT_CPUSET
    )
    implementation.predecessor.atomic(authority, authority_document)
    run_id = implementation.RUN_IDS[scene]
    unit = implementation.UNITS[scene]
    output = implementation.HISTORY / run_id
    stdout = implementation.LOG_ROOT / f"{run_id}.stdout.log"
    stderr = implementation.LOG_ROOT / f"{run_id}.stderr.log"
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh XP successor output required: {output}")
    command = [
        "systemd-run", "--user", f"--unit={unit.removesuffix('.service')}",
        f"--description=MySQL Algorithm A all-scene four-worker resume {scene}",
        f"--property=WorkingDirectory={ROOT}",
        "--property=KillMode=control-group",
        f"--property=StandardOutput=append:{stdout}",
        f"--property=StandardError=append:{stderr}",
        "/usr/bin/taskset", "-c", implementation.MANAGEMENT_CPUSET,
        str(implementation.predecessor.PYTHON), str(implementation.CONTROLLER),
        "--run-id", run_id, "--scene", scene,
        "--predecessor-state", str(snapshot),
        "--transition-authority", str(authority),
        "--output-dir", str(output),
    ]
    completed = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"{scene} successor launch failed: {completed.stderr}")
    state_path = output / "algorithm_a_state.json"
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        fields = implementation.predecessor.systemctl(
            "show", unit, "-p", "ActiveState", "-p", "MainPID", check=False,
        ).stdout
        if "ActiveState=failed" in fields or "ActiveState=inactive" in fields:
            raise RuntimeError(f"{scene} successor exited during startup: {fields}")
        if state_path.is_file():
            state = implementation.predecessor.load(state_path)
            if (
                state.get("status") == "RUNNING"
                and int(state.get("physical_replay_parallelism") or 0) == 4
            ):
                return {
                    "scene": scene, "run_id": run_id, "unit": unit,
                    "output": str(output), "state": str(state_path),
                    "stdout": str(stdout), "stderr": str(stderr),
                    "stage": state.get("stage"),
                    "management_controller_cpuset": implementation.MANAGEMENT_CPUSET,
                }
        time.sleep(1)
    raise TimeoutError(f"{scene} successor startup timed out")


def main() -> int:
    implementation.CONTRACT_PATH = CONFIG / "transition_contract.json"
    implementation.CONTROLLER = ROOT / "scripts/run_mysql_algorithm_a_job_to_xp_resume_v3.py"
    implementation.pg_wrapper = pg_wrapper
    implementation.launch_resume = launch_resume
    return implementation.main()


if __name__ == "__main__":
    raise SystemExit(main())
