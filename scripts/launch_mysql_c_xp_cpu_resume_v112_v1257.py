#!/usr/bin/env python3
"""Resume only XP-CPU C from completed outer 7 on the v112 repair."""

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
CAMPAIGN = HISTORY / "v1257_mysql_c_xp_cpu_resume_v112_20260906"
RUN_ID = "v1257_to_c_xp_cpu_stale_task_recovery_v112_20260906"
RUN_ROOT = HISTORY / RUN_ID
BARRIER = Path("/tmp/mysql_async_outer_start/v1257_mysql_c_xp_cpu_v112")
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v22.py"
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260906_v112/"
    "algorithm_c_pg_latest_outer_contract.json"
)
CHECKPOINT = HISTORY / (
    "v1256_c_xp_cpu_resume_checkpoint_v112_20260906/prepared_binding.json"
)
AUTHORITY = HISTORY / (
    "v1236_c_xp_cpu_scene_authority_v107_20260905/authority"
)
FRESH_R0 = HISTORY / (
    "v1236_c_xp_cpu_scene_authority_v107_20260905_r0/fresh_r0_binding.json"
)
QUARANTINE = HISTORY / (
    "v1233_c_xp_cpu_resume_quarantine_v105_20260905/replay_quarantine.json"
)
CPUSET = {*(range(16, 24)), *(range(272, 280))}


def now() -> str:
    return datetime.now().astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def main() -> int:
    required = [
        CONTROLLER, CONTRACT, CHECKPOINT, FRESH_R0, QUARANTINE,
        AUTHORITY / "transition_preflight.json",
        AUTHORITY / "prepared_binding.json",
        AUTHORITY / "benchmark_accounts_binding.json",
    ]
    if (
        CAMPAIGN.exists() or RUN_ROOT.exists() or BARRIER.exists()
        or any(not path.is_file() or path.is_symlink() for path in required)
    ):
        raise RuntimeError("fresh XP-CPU v112 launch boundary differs")
    contract = load(CONTRACT)
    checkpoint = load(CHECKPOINT)
    if (
        contract.get("release_version") != "v112-stale-containerd-task-recovery"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or checkpoint.get("scene") != "hybench_xp_cpu"
        or int(checkpoint.get("completed_outer_attempts") or 0) != 7
        or checkpoint.get("formal_scope") != "mysql_only"
    ):
        raise RuntimeError("XP-CPU v112 contract or checkpoint differs")
    CAMPAIGN.mkdir(parents=True, mode=0o700)
    BARRIER.mkdir(parents=True, mode=0o700)
    log_path = CAMPAIGN / "xp_cpu.controller.log"
    log = log_path.open("w", encoding="utf-8")
    environment = os.environ.copy()
    environment["MYSQL_TWO_WORKER_AUTHORITY_ROOT"] = str(AUTHORITY)
    command = [
        sys.executable, str(CONTROLLER), "--run-id", RUN_ID,
        "--checkpoint", str(CHECKPOINT),
        "--runtime-preflight", str(AUTHORITY / "transition_preflight.json"),
        "--prepared-binding", str(AUTHORITY / "prepared_binding.json"),
        "--benchmark-accounts", str(AUTHORITY / "benchmark_accounts_binding.json"),
        "--fresh-r0", str(FRESH_R0),
        "--replay-quarantine", str(QUARANTINE),
        "--scene", "hybench_xp_cpu",
        "--scene-start-barrier", str(BARRIER),
    ]
    process = subprocess.Popen(
        command, cwd=ROOT, env=environment, stdout=log,
        stderr=subprocess.STDOUT, text=True, start_new_session=True,
        preexec_fn=lambda: os.sched_setaffinity(0, CPUSET),
    )
    manifest = {
        "schema_version": "mysql_algorithm_c_xp_cpu_resume_launch_v1",
        "status": "STARTING", "created_at": now(),
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "scene": "hybench_xp_cpu", "run_id": RUN_ID, "pid": process.pid,
        "controller": str(CONTROLLER), "controller_sha256": sha(CONTROLLER),
        "contract": str(CONTRACT), "contract_sha256": sha(CONTRACT),
        "checkpoint": str(CHECKPOINT), "checkpoint_sha256": sha(CHECKPOINT),
        "completed_outer_attempts_preserved": 7,
        "incomplete_outer_credit": 0,
        "independent_scene_execution": True,
        "sibling_scene_wait": False, "lease_mode": "none",
        "controller_cpuset": "16-23,272-279",
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
        "log": str(log_path),
    }
    manifest_path = CAMPAIGN / "launch_manifest.json"
    atomic(manifest_path, manifest)
    deadline = time.monotonic() + 180
    try:
        while time.monotonic() < deadline:
            state_path = RUN_ROOT / "run_state.json"
            code = process.poll()
            if state_path.is_file():
                state = load(state_path)
                manifest.update({
                    "status": state.get("status"), "stage": state.get("stage"),
                    "outer_attempt": state.get("outer_attempt"),
                    "state_path": str(state_path), "updated_at": now(),
                })
                atomic(manifest_path, manifest)
                if state.get("status") == "FAILED_CLOSED" or state.get("error"):
                    return 4
                print(json.dumps(manifest, sort_keys=True))
                return 0
            if code is not None:
                manifest.update({"status": "FAILED_CLOSED", "exit_code": code,
                                 "updated_at": now()})
                atomic(manifest_path, manifest)
                return 4
            time.sleep(2)
        manifest.update({"status": "FAILED_CLOSED",
                         "error": "state publication timeout", "updated_at": now()})
        atomic(manifest_path, manifest)
        return 4
    finally:
        log.close()


if __name__ == "__main__":
    raise SystemExit(main())
