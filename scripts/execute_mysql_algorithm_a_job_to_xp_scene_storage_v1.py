#!/usr/bin/env python3
"""Move a fresh XP A scene to four workers under shared-NVMe serialization."""

from __future__ import annotations

import fcntl
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import execute_mysql_algorithm_a_job_to_xp_scene_v1 as legacy
from scripts import mysql_algorithm_a_shared_nvme_wave_lease_v1 as lease
from scripts import run_mysql_algorithm_c_async_scene_v49 as c49

HISTORY = ROOT / "history/mysql_four_scene_20260809"
LOG_ROOT = HISTORY / "formal_launch_logs"
CAMPAIGN = "v54_to_a_storage_compatible_20260823"

legacy.CONTROLLER = (
    ROOT / "scripts/run_mysql_algorithm_a_storage_serialized_job_to_xp_resume_v1.py"
)
legacy.TRANSITION_ROOT = HISTORY / f"{CAMPAIGN}_job_to_xp_slots_v4"
legacy.CONTRACT_PATH = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_job_to_xp_slots_20260823_v4"
    / "transition_contract.json"
)

_rolling_quiet_gate = legacy.coordination.rolling_quiet_gate


def rolling_quiet_gate(*args: Any, **kwargs: Any):
    scene = str(kwargs.get("run_id") or "").rsplit("_", 1)[-1]
    kwargs["run_id"] = f"{CAMPAIGN}_job_to_xp_{scene}_v4"
    return _rolling_quiet_gate(*args, **kwargs)


legacy.coordination.rolling_quiet_gate = rolling_quiet_gate

_original_provision = legacy.provision.provision_worker
_condition = threading.Condition()
_started = 0
_finished = 0
_lock_file = None
_lease_document: dict[str, Any] | None = None
_lease_path: Path | None = None
_acquired_monotonic = 0.0


def serialized_provision(*args: Any, **kwargs: Any):
    """Let a scene's two copies run together while excluding the other XP scene."""
    global _started, _finished, _lock_file, _lease_document, _lease_path
    global _acquired_monotonic
    worker = args[1] if len(args) > 1 else kwargs["worker"]
    scene = str(worker["scene"])
    with _condition:
        if _started == 0:
            handle = lease.LOCK_PATH.open("w", encoding="ascii")
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            _lock_file = handle
            _acquired_monotonic = time.monotonic()
            _lease_path = (
                legacy.TRANSITION_ROOT / f"{scene}.slot_provision_shared_nvme_lease.json"
            )
            _lease_document = {
                "schema_version": "mysql_algorithm_a_shared_nvme_slot_provision_lease_v1",
                "status": "HELD",
                "acquired_at": legacy.now(),
                "formal_scope": "mysql_only",
                "cross_engine_comparison_prohibited": True,
                "scene": scene,
                "device": "/dev/nvme2n1",
                "lock_path": str(lease.LOCK_PATH),
                "lease_covers_both_new_worker_copies": True,
            }
            legacy.atomic(_lease_path, _lease_document)
        _started += 1
        if _started < 2:
            _condition.wait_for(lambda: _started == 2)
        else:
            _condition.notify_all()
    try:
        return _original_provision(*args, **kwargs)
    finally:
        with _condition:
            _finished += 1
            if _finished < 2:
                _condition.wait_for(lambda: _finished == 2)
            else:
                assert _lease_document is not None and _lease_path is not None
                _lease_document.update({
                    "status": "RELEASED",
                    "released_at": legacy.now(),
                    "held_seconds": time.monotonic() - _acquired_monotonic,
                })
                legacy.atomic(_lease_path, _lease_document)
                assert _lock_file is not None
                fcntl.flock(_lock_file.fileno(), fcntl.LOCK_UN)
                _lock_file.close()
                _condition.notify_all()


legacy.provision.provision_worker = serialized_provision


def launch_resume(*, scene: str, snapshot: Path, authority: Path) -> dict[str, Any]:
    suffix = "xp_cpu" if scene == "hybench_xp_cpu" else "xp_io"
    run_id = f"{CAMPAIGN}_job_to_xp_four_worker_attempt001_{suffix}"
    unit = f"mysql-alga-storage-job-to-xp-{suffix}.service"
    output = HISTORY / run_id
    stdout = LOG_ROOT / f"{run_id}.stdout.log"
    stderr = LOG_ROOT / f"{run_id}.stderr.log"
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh storage-compatible XP successor required: {output}")
    command = [
        "systemd-run", "--user", f"--unit={unit.removesuffix('.service')}",
        f"--description=MySQL Algorithm A storage-compatible four-worker {scene}",
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
                    "shared_storage_measurements_mutually_exclusive": True,
                }
        time.sleep(1)
    raise TimeoutError(f"{scene} storage-compatible successor startup timed out")


legacy.launch_resume = launch_resume


def main() -> int:
    c49.validate_v65_wave_coordination_contract()
    c49.install()
    legacy.coordination.predecessor.CONTRACT_PATH = c49.COORDINATION_CONTRACT
    return legacy.main()


if __name__ == "__main__":
    raise SystemExit(main())
