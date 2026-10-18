#!/usr/bin/env python3
"""Move completed memory-scene slots to XP with external diagnostics only."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_external_workload_diagnostic_gate_v1 as diagnostic_gate
from scripts import execute_mysql_algorithm_a_job_to_xp_scene_v1 as implementation


HISTORY = ROOT / "history/mysql_four_scene_20260809"
LOG_ROOT = HISTORY / "formal_launch_logs"
CONFIG = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_a_external_diagnostic_to_xp4_20260823_v1"
)
TRANSITION_ROOT = (
    HISTORY / "v103_to_a_external_diagnostic_xp4_20260823_transition_v1"
)
CONTROLLER = (
    ROOT / "scripts/run_mysql_algorithm_a_job_to_xp_external_diagnostic_resume_v1.py"
)
MANAGEMENT_CPUSET = "240-241,496-497"
RUN_IDS = {
    "hybench_xp_cpu": "v104_to_a_xp_cpu4_external_diagnostic_resume_20260823_attempt001_xp_cpu",
    "hybench_xp_io": "v105_to_a_xp_io4_external_diagnostic_resume_20260823_attempt001_xp_io",
}
UNITS = {
    "hybench_xp_cpu": "mysql-alga-all8-v104-xp-cpu4-external-diagnostic.service",
    "hybench_xp_io": "mysql-alga-all8-v105-xp-io4-external-diagnostic.service",
}
_ORIGINAL_RUN = implementation.run
_TPCH_EVIDENCE: dict[str, Any] | None = None


def validate_tpch_trigger(args) -> dict[str, Any]:
    trigger = implementation.load(args.trigger.resolve())
    evidence = trigger.get("tpch_terminal_evidence") or {}
    state_path = Path(str(evidence.get("state_path") or ""))
    state = implementation.load(state_path) if state_path.is_file() else {}
    if (
        trigger.get("both_memory_scenes_complete_required") is not True
        or evidence.get("complete") is not True
        or int(evidence.get("accepted") or 0) != 10
        or evidence.get("state_sha256") != implementation.sha(state_path)
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
    authority_document = implementation.load(authority)
    authority_document["tpch_terminal_evidence"] = dict(_TPCH_EVIDENCE)
    authority_document["both_memory_scenes_complete_required"] = True
    authority_document["external_workloads_diagnostic_only"] = True
    authority_document["external_workload_wait_required"] = False
    authority_document["external_process_control_or_stop_prohibited"] = True
    authority_document["numa_exclusive_scheduler_lock_required"] = False
    authority_document["fixed_disjoint_cpu_affinity_required"] = True
    implementation.atomic(authority, authority_document)
    run_id = RUN_IDS[scene]
    unit = UNITS[scene]
    output = HISTORY / run_id
    stdout = LOG_ROOT / f"{run_id}.stdout.log"
    stderr = LOG_ROOT / f"{run_id}.stderr.log"
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh XP successor output required: {output}")
    command = [
        "systemd-run", "--user", f"--unit={unit.removesuffix('.service')}",
        f"--description=MySQL Algorithm A external-diagnostic XP4 {scene}",
        f"--property=WorkingDirectory={ROOT}",
        "--property=KillMode=control-group",
        f"--property=StandardOutput=append:{stdout}",
        f"--property=StandardError=append:{stderr}",
        "/usr/bin/taskset", "-c", MANAGEMENT_CPUSET,
        str(implementation.PYTHON), str(CONTROLLER), "--run-id", run_id,
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
        fields = implementation.systemctl(
            "show", unit, "-p", "ActiveState", "-p", "MainPID", check=False,
        ).stdout
        if "ActiveState=failed" in fields or "ActiveState=inactive" in fields:
            raise RuntimeError(f"{scene} successor exited during startup: {fields}")
        if state_path.is_file():
            state = implementation.load(state_path)
            if (
                state.get("status") == "RUNNING"
                and int(state.get("physical_replay_parallelism") or 0) == 4
            ):
                return {
                    "scene": scene, "run_id": run_id, "unit": unit,
                    "output": str(output), "state": str(state_path),
                    "stdout": str(stdout), "stderr": str(stderr),
                    "stage": state.get("stage"),
                    "management_controller_cpuset": MANAGEMENT_CPUSET,
                }
        time.sleep(1)
    raise TimeoutError(f"{scene} successor startup timed out")


def main() -> int:
    implementation.CONTRACT_PATH = CONFIG / "transition_contract.json"
    implementation.TRANSITION_ROOT = TRANSITION_ROOT
    implementation.CONTROLLER = CONTROLLER
    implementation.MANAGEMENT_CPUSET = MANAGEMENT_CPUSET
    implementation.coordination.rolling_quiet_gate = (
        diagnostic_gate.rolling_quiet_gate
    )
    implementation.run = run
    implementation.launch_resume = launch_resume
    return implementation.main()


if __name__ == "__main__":
    raise SystemExit(main())
