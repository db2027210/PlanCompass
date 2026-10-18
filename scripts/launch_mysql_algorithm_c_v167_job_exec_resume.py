#!/usr/bin/env python3
"""Validate the quiet window and exec in place as the JOB controller."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_mysql_algorithm_c_v166_job_quiet_resume as quiet
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v77 as controller


HISTORY = ROOT / "history/mysql_four_scene_20260809"
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v77.py"
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_full_resume_20260923_v167/"
    "algorithm_c_full_resume_contract.json"
)
QUIET_STATUS = HISTORY / (
    "c_v166_job_quiet_resume_20260923/quiet_window_status.json"
)
CHECKPOINT = quiet.CHECKPOINT
QUARANTINE = quiet.QUARANTINE
FRESH_R0 = quiet.FRESH_R0
CONTROLLER_AFFINITY = quiet.CONTROLLER_AFFINITY


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-prefix", required=True)
    parser.add_argument("--authority-root", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,120}", args.run_prefix) is None:
        parser.error("unsafe run prefix")
    controller.validate_contract()
    authority = args.authority_root.expanduser().resolve()
    preflight = authority / "transition_preflight.json"
    prepared = authority / "prepared_binding.json"
    accounts = authority / "benchmark_accounts_binding.json"
    quiet_status = load(QUIET_STATUS)
    captured = datetime.fromisoformat(str(quiet_status.get("captured_at") or ""))
    age = (datetime.now().astimezone() - captured).total_seconds()
    blockers = quiet.active_overlapping_postgres()
    checkpoint = controller.runtime.scene_checkpoint.verify_or_standard(CHECKPOINT)
    if (
        quiet_status.get("status") != "PASS"
        or float(quiet_status.get("quiet_elapsed_seconds") or 0) < 120.0
        or quiet_status.get("active_overlapping_postgres") != []
        or quiet_status.get("formal_runtime_gate_still_required") is not True
        or quiet_status.get("postgresql_process_control_or_stop_performed") is not False
        or age < 0 or age > 600
        or blockers
        or load(preflight).get("status") != "PASS"
        or int(checkpoint.get("completed_outer_attempts") or 0) != 2
    ):
        raise RuntimeError("v167 JOB exec-resume authority differs")
    campaign = HISTORY / args.run_prefix
    run_id = f"{args.run_prefix}_job"
    output = HISTORY / run_id
    barrier = Path("/tmp/mysql_async_outer_start") / args.run_prefix / "algorithm_c"
    if campaign.exists() or output.exists() or barrier.exists():
        raise RuntimeError("fresh v167 JOB output required")
    campaign.mkdir(parents=True, mode=0o700)
    barrier.mkdir(parents=True, mode=0o700)
    log_path = campaign / "job.controller.log"
    command = [
        sys.executable, str(CONTROLLER), "--run-id", run_id,
        "--checkpoint", str(CHECKPOINT),
        "--runtime-preflight", str(preflight),
        "--prepared-binding", str(prepared),
        "--benchmark-accounts", str(accounts),
        "--fresh-r0", str(FRESH_R0),
        "--replay-quarantine", str(QUARANTINE),
        "--scene", "job_memory", "--scene-start-barrier", str(barrier),
    ]
    atomic(campaign / "launch_manifest.json", {
        "schema_version": "mysql_algorithm_c_v167_job_exec_resume_launch_v1",
        "release_version": "v167-job-persistent-exec-resume",
        "status": "RUNNING",
        "created_at": datetime.now().astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene": "job_memory",
        "run_id": run_id,
        "pid": os.getpid(),
        "controller_cpuset": CONTROLLER_AFFINITY,
        "log": str(log_path),
        "checkpoint": str(CHECKPOINT), "checkpoint_sha256": sha(CHECKPOINT),
        "quarantine": str(QUARANTINE), "quarantine_sha256": sha(QUARANTINE),
        "contract": str(CONTRACT), "contract_sha256": sha(CONTRACT),
        "quiet_window_status": str(QUIET_STATUS),
        "quiet_window_status_sha256": sha(QUIET_STATUS),
        "quiet_window_age_seconds_at_exec": age,
        "fresh_overlap_prefilter": [],
        "formal_runtime_gate_still_required": True,
        "systemd_launcher_execs_controller_in_place": True,
        "plan_capture_timeout_seconds": 900,
        "completed_outer_attempts_preserved": 2,
        "discarded_incomplete_outer_credit": 0,
        "postgresql_process_control_or_stop_performed": False,
        "command": command,
    })
    environment = os.environ.copy()
    environment["MYSQL_TWO_WORKER_AUTHORITY_ROOT"] = str(authority)
    os.sched_setaffinity(0, quiet.cpus(CONTROLLER_AFFINITY))
    log = log_path.open("w")
    os.dup2(log.fileno(), 1)
    os.dup2(log.fileno(), 2)
    os.chdir(ROOT)
    os.execve(sys.executable, command, environment)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
