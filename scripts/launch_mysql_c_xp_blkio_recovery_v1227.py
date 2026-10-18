#!/usr/bin/env python3
"""Relaunch only the two C/XP lanes that failed before baseline measurement.

The original v1223 launch satisfied the four-scene concurrent-start contract.
JOB and TPC-H continue untouched.  These replacement lanes are fresh searches
because their v1223 predecessors failed at the pre-measurement resource gate.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
CAMPAIGN = HISTORY / "v1227_mysql_c_xp_blkio_recovery_20260905"
MANIFEST = CAMPAIGN / "launch_manifest.json"
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v13.py"
CHECKPOINT = HISTORY / (
    "v1185_to_c_client_primary_bootstrap_20260903/checkpoint/prepared_binding.json"
)
QUARANTINE = HISTORY / (
    "v1185_to_c_client_primary_bootstrap_20260903/"
    "replay_quarantine/replay_quarantine.json"
)
AUTHORITY = HISTORY / "v1221_to_c_current_identity_authority_20260905/authority"
R0 = HISTORY / (
    "v1222_to_c_current_identity_fresh_r0_20260905/fresh_r0_binding.json"
)
REPAIR = HISTORY / (
    "v1226_repair_xp_live_blkio_20260905/repair_binding.json"
)
BARRIER = Path("/tmp/mysql_async_outer_start/v1227_mysql_c_xp_recovery")
SPECS = {
    "hybench_xp_cpu": "v1227_to_c_pg_latest_v103_20260905_xp_cpu",
    "hybench_xp_io": "v1227_to_c_pg_latest_v103_20260905_xp_io",
}


def now() -> str:
    return datetime.now().astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def atomic(value: Mapping[str, Any]) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = MANIFEST.with_name(f".{MANIFEST.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, MANIFEST)


def snapshot(run_id: str) -> dict[str, Any]:
    path = HISTORY / run_id / "run_state.json"
    if not path.is_file():
        return {"status": "STARTING"}
    state = load(path)
    return {
        key: state.get(key) for key in (
            "status", "stage", "error", "outer_attempt", "accepted_rounds",
            "rejected_outer_attempts", "converged",
        )
    }


def main() -> int:
    repair = load(REPAIR)
    if (
        repair.get("status") != "PASS"
        or repair.get("container_restart_performed") is not False
        or repair.get("container_identity_changed") is not False
        or repair.get("search_state_changed") is not False
        or len(repair.get("workers") or []) != 4
        or any(row.get("passed") is not True for row in repair["workers"])
    ):
        raise RuntimeError("passed four-worker live blkio repair required")
    if CAMPAIGN.exists() or CAMPAIGN.is_symlink() or BARRIER.exists():
        raise RuntimeError("fresh XP recovery campaign and barrier required")
    for run_id in SPECS.values():
        if (HISTORY / run_id).exists():
            raise RuntimeError(f"fresh recovery run root required: {run_id}")
    for path in (
        CONTROLLER, CHECKPOINT, QUARANTINE, R0, REPAIR,
        AUTHORITY / "transition_preflight.json",
        AUTHORITY / "prepared_binding.json",
        AUTHORITY / "benchmark_accounts_binding.json",
    ):
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"regular recovery input required: {path}")

    CAMPAIGN.mkdir(parents=True, mode=0o700)
    BARRIER.mkdir(parents=True, mode=0o700)
    processes: dict[str, subprocess.Popen[str]] = {}
    logs: dict[str, Any] = {}
    manifest: dict[str, Any] = {
        "schema_version": "mysql_algorithm_c_xp_prebaseline_gate_recovery_v1",
        "status": "STARTING",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "algorithm": "C",
        "replaces_failed_prebaseline_lanes": {
            scene: f"v1223_to_c_pg_latest_v103_20260905_{'xp_cpu' if scene == 'hybench_xp_cpu' else 'xp_io'}"
            for scene in SPECS
        },
        "job_and_tpch_v1223_controllers_mutated": False,
        "fresh_search": True,
        "physical_workers_per_scene": 2,
        "per_scene_outer_state_machine": True,
        "sibling_scene_wait": False,
        "lease_mode": "none",
        "repair_path": str(REPAIR),
        "repair_sha256": sha(REPAIR),
        "controller_path": str(CONTROLLER),
        "controller_sha256": sha(CONTROLLER),
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
        "scenes": {},
    }
    try:
        for scene, run_id in SPECS.items():
            command = [
                sys.executable, str(CONTROLLER), "--run-id", run_id,
                "--checkpoint", str(CHECKPOINT),
                "--runtime-preflight", str(AUTHORITY / "transition_preflight.json"),
                "--prepared-binding", str(AUTHORITY / "prepared_binding.json"),
                "--benchmark-accounts", str(AUTHORITY / "benchmark_accounts_binding.json"),
                "--fresh-r0", str(R0),
                "--replay-quarantine", str(QUARANTINE),
                "--fresh-search", "--scene", scene,
                "--scene-start-barrier", str(BARRIER),
            ]
            environment = os.environ.copy()
            environment["MYSQL_TWO_WORKER_AUTHORITY_ROOT"] = str(AUTHORITY)
            log_path = CAMPAIGN / f"{scene}.controller.log"
            log = log_path.open("w", encoding="utf-8")
            process = subprocess.Popen(
                command, cwd=ROOT, env=environment, stdout=log,
                stderr=subprocess.STDOUT, text=True, start_new_session=True,
            )
            logs[scene] = log
            processes[scene] = process
            manifest["scenes"][scene] = {
                "run_id": run_id, "pid": process.pid, "status": "RUNNING",
                "log_path": str(log_path), "command": command,
            }
        manifest["status"] = "RUNNING"
        manifest["started_at"] = now()
        atomic(manifest)
        while any(process.poll() is None for process in processes.values()):
            for scene, process in processes.items():
                row = manifest["scenes"][scene]
                row["process_status"] = "RUNNING" if process.poll() is None else "EXITED"
                row["returncode"] = process.poll()
                row["state"] = snapshot(SPECS[scene])
            manifest["updated_at"] = now()
            atomic(manifest)
            time.sleep(10)
    finally:
        for log in logs.values():
            log.close()

    failed = False
    for scene, process in processes.items():
        code = int(process.returncode or 0)
        failed = failed or code != 0
        manifest["scenes"][scene].update({
            "process_status": "EXITED", "returncode": code,
            "status": "COMPLETE" if code == 0 else "FAILED_CLOSED",
            "state": snapshot(SPECS[scene]),
        })
    manifest["status"] = "FAILED_CLOSED" if failed else "COMPLETE"
    manifest["completed_at"] = now()
    atomic(manifest)
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
