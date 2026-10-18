#!/usr/bin/env python3
"""Transition one current all-scene XP lane from two to four workers."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import execute_mysql_algorithm_a_job_to_xp_scene_v1 as predecessor
from scripts import mysql_co_resident_pg_isolation_v14 as pg_wrapper


HISTORY = ROOT / "history/mysql_four_scene_20260809"
LOG_ROOT = HISTORY / "formal_launch_logs"
CONTRACT_PATH = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_a_all_scene_to_xp4_20260823_v1"
    / "transition_contract.json"
)
TRANSITION_ROOT = (
    HISTORY / "v67_to_a_all_scene_parallel_xp4_20260823_transition_v1"
)
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_a_job_to_xp_resume_v2.py"
MANAGEMENT_CPUSET = "240-241,496-497"
RUN_IDS = {
    "hybench_xp_cpu": "v67_to_a_all_scene_parallel_xp4_20260823_attempt001_xp_cpu",
    "hybench_xp_io": "v67_to_a_all_scene_parallel_xp4_20260823_attempt001_xp_io",
}
UNITS = {
    "hybench_xp_cpu": "mysql-alga-all8-v67-xp-cpu4.service",
    "hybench_xp_io": "mysql-alga-all8-v67-xp-io4.service",
}
_ORIGINAL_RUN = predecessor.run
_TPCH_EVIDENCE: dict[str, Any] | None = None


def validate_tpch_trigger(args) -> dict[str, Any]:
    trigger = predecessor.load(args.trigger.resolve())
    evidence = trigger.get("tpch_terminal_evidence") or {}
    state_path = Path(str(evidence.get("state_path") or ""))
    state = predecessor.load(state_path) if state_path.is_file() else {}
    if (
        trigger.get("both_memory_scenes_complete_required") is not True
        or evidence.get("complete") is not True
        or int(evidence.get("accepted") or 0) != 10
        or evidence.get("state_sha256") != predecessor.sha(state_path)
        or state.get("algorithm") != "A"
        or state.get("async_scene") != "tpch_sf4_memory"
        or state.get("status") != "COMPLETE"
        or state.get("converged") is not True
        or int(state.get("accepted") or 0) != 10
        or state.get("round_checkpoint") is not None
    ):
        raise RuntimeError("TPC-H terminal trigger authority differs")
    return dict(evidence)


def run(args) -> int:
    global _TPCH_EVIDENCE
    _TPCH_EVIDENCE = validate_tpch_trigger(args)
    return _ORIGINAL_RUN(args)


def launch_resume(*, scene: str, snapshot: Path, authority: Path) -> dict[str, Any]:
    if _TPCH_EVIDENCE is None:
        raise RuntimeError("TPC-H terminal evidence was not validated")
    authority_document = predecessor.load(authority)
    authority_document["tpch_terminal_evidence"] = dict(_TPCH_EVIDENCE)
    authority_document["both_memory_scenes_complete_required"] = True
    authority_document["numa_exclusive_scheduler_lock_required"] = False
    authority_document["fixed_disjoint_cpu_affinity_required"] = True
    predecessor.atomic(authority, authority_document)
    run_id = RUN_IDS[scene]
    unit = UNITS[scene]
    output = HISTORY / run_id
    stdout = LOG_ROOT / f"{run_id}.stdout.log"
    stderr = LOG_ROOT / f"{run_id}.stderr.log"
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh XP successor output required: {output}")
    command = [
        "systemd-run", "--user", f"--unit={unit.removesuffix('.service')}",
        f"--description=MySQL Algorithm A all-scene four-worker resume {scene}",
        f"--property=WorkingDirectory={ROOT}",
        "--property=KillMode=control-group",
        f"--property=AllowedCPUs={MANAGEMENT_CPUSET}",
        f"--property=StandardOutput=append:{stdout}",
        f"--property=StandardError=append:{stderr}",
        str(predecessor.PYTHON), str(CONTROLLER), "--run-id", run_id,
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
        fields = predecessor.systemctl(
            "show", unit, "-p", "ActiveState", "-p", "MainPID", check=False,
        ).stdout
        if "ActiveState=failed" in fields or "ActiveState=inactive" in fields:
            raise RuntimeError(f"{scene} successor exited during startup: {fields}")
        if state_path.is_file():
            state = predecessor.load(state_path)
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


def main() -> int:
    pg_wrapper.load_contract()
    pg_wrapper.install()
    predecessor.CONTRACT_PATH = CONTRACT_PATH
    predecessor.TRANSITION_ROOT = TRANSITION_ROOT
    predecessor.CONTROLLER = CONTROLLER
    predecessor.MANAGEMENT_CPUSET = MANAGEMENT_CPUSET
    predecessor.run = run
    predecessor.launch_resume = launch_resume
    return predecessor.main()


if __name__ == "__main__":
    raise SystemExit(main())
