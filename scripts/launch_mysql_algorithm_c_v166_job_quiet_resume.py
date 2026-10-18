#!/usr/bin/env python3
"""Wait for a 120s JOB CPU quiet window, then launch the outer-2 resume."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_four_scene_client_primary_v76 as controller


HISTORY = ROOT / "history/mysql_four_scene_20260809"
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v76.py"
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_full_resume_20260923_v166/"
    "algorithm_c_full_resume_contract.json"
)
CHECKPOINT = HISTORY / (
    "c_v161_job_outer2_checkpoint_20260923/prepared_binding.json"
)
QUARANTINE = HISTORY / (
    "c_v161_job_outer2_quarantine_20260923/replay_quarantine.json"
)
FRESH_R0 = HISTORY / (
    "c_v157_full_resume_fresh_r0_v36_20260923/fresh_r0_binding.json"
)
FAILED_STATE = HISTORY / (
    "c_v165_job_xpio_outer2_resume_20260923_job/run_state.json"
)
JOB_TARGET_CPUS = set(range(104, 112)) | set(range(360, 368))
CONTROLLER_AFFINITY = "0-7,256-263"
QUIET_WINDOW_SECONDS = 120.0
POLL_SECONDS = 15.0
CPU_SAMPLE_SECONDS = 0.30


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


def cpus(value: str) -> set[int]:
    result: set[int] = set()
    for item in value.split(","):
        left, separator, right = item.partition("-")
        result.update(range(int(left), int(right) + 1) if separator else [int(left)])
    return result


def parse_cpu_list(value: str) -> set[int]:
    return cpus(value.strip()) if value.strip() else set()


def postgres_ticks() -> dict[int, dict[str, Any]]:
    rows: dict[int, dict[str, Any]] = {}
    for root in Path("/proc").iterdir():
        if not root.name.isdigit():
            continue
        try:
            if (root / "comm").read_text().strip() != "postgres":
                continue
            fields = {}
            for line in (root / "status").read_text().splitlines():
                key, separator, value = line.partition(":")
                if separator and key in {"Cpus_allowed_list", "State", "PPid"}:
                    fields[key] = value.strip()
            overlap = sorted(
                parse_cpu_list(fields.get("Cpus_allowed_list", ""))
                & JOB_TARGET_CPUS
            )
            if not overlap:
                continue
            stat = (root / "stat").read_text().split()
            rows[int(root.name)] = {
                "pid": int(root.name),
                "ppid": int(fields.get("PPid", "0")),
                "state": fields.get("State", ""),
                "cpus_allowed_list": fields.get("Cpus_allowed_list", ""),
                "target_cpu_overlap": overlap,
                "ticks": int(stat[13]) + int(stat[14]),
            }
        except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError):
            continue
    return rows


def active_overlapping_postgres() -> list[dict[str, Any]]:
    before = postgres_ticks()
    time.sleep(CPU_SAMPLE_SECONDS)
    after = postgres_ticks()
    active = []
    for pid, row in after.items():
        delta = int(row["ticks"]) - int(before.get(pid, {}).get("ticks", row["ticks"]))
        if delta > 0:
            active.append({**row, "cpu_ticks_sample": delta})
    return active


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
    checkpoint = controller.runtime.scene_checkpoint.verify_or_standard(CHECKPOINT)
    quarantine = load(QUARANTINE)
    failed = load(FAILED_STATE)
    if (
        load(preflight).get("status") != "PASS"
        or int(checkpoint.get("completed_outer_attempts") or 0) != 2
        or (checkpoint.get("independent_scene_boundary") or {}).get("scene")
        != "job_memory"
        or quarantine.get("status") != "PASS"
        or quarantine.get("resume_checkpoint_outer_attempt") != 2
        or failed.get("status") != "FAILED_CLOSED"
        or "non-interference gate blocked" not in str(failed.get("error") or "")
        or int(failed.get("outer_attempt") or 0) != 2
    ):
        raise RuntimeError("v166 JOB quiet-window resume authority differs")
    campaign = HISTORY / args.run_prefix
    run_id = f"{args.run_prefix}_job"
    output = HISTORY / run_id
    barrier = Path("/tmp/mysql_async_outer_start") / args.run_prefix / "algorithm_c"
    if campaign.exists() or output.exists() or barrier.exists():
        raise RuntimeError("fresh v166 JOB output required")
    campaign.mkdir(parents=True, mode=0o700)
    status_path = campaign / "quiet_window_status.json"
    quiet_started: float | None = None
    wait_started = time.monotonic()
    sample_index = 0
    while True:
        sample_index += 1
        blockers = active_overlapping_postgres()
        now = time.monotonic()
        if blockers:
            quiet_started = None
        elif quiet_started is None:
            quiet_started = now
        quiet_elapsed = 0.0 if quiet_started is None else now - quiet_started
        atomic(status_path, {
            "schema_version": "mysql_algorithm_c_v166_job_quiet_window_v1",
            "status": "WAITING" if blockers or quiet_elapsed < QUIET_WINDOW_SECONDS else "PASS",
            "captured_at": datetime.now().astimezone().isoformat(),
            "sample_index": sample_index,
            "wait_elapsed_seconds": now - wait_started,
            "quiet_elapsed_seconds": quiet_elapsed,
            "required_quiet_seconds": QUIET_WINDOW_SECONDS,
            "active_overlapping_postgres": blockers,
            "postgresql_process_control_or_stop_performed": False,
            "formal_runtime_gate_still_required": True,
        })
        if not blockers and quiet_elapsed >= QUIET_WINDOW_SECONDS:
            break
        time.sleep(POLL_SECONDS)
    barrier.mkdir(parents=True, mode=0o700)
    log_path = campaign / "job.controller.log"
    log = log_path.open("w")
    environment = os.environ.copy()
    environment["MYSQL_TWO_WORKER_AUTHORITY_ROOT"] = str(authority)
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
    process = subprocess.Popen(
        command, cwd=ROOT, env=environment, stdout=log,
        stderr=subprocess.STDOUT, start_new_session=True,
        preexec_fn=lambda: os.sched_setaffinity(0, cpus(CONTROLLER_AFFINITY)),
    )
    log.close()
    atomic(campaign / "launch_manifest.json", {
        "schema_version": "mysql_algorithm_c_v166_job_quiet_resume_launch_v1",
        "release_version": "v166-job-quiet-window-resume",
        "status": "RUNNING",
        "created_at": datetime.now().astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene": "job_memory",
        "run_id": run_id,
        "pid": process.pid,
        "controller_cpuset": CONTROLLER_AFFINITY,
        "log": str(log_path),
        "checkpoint": str(CHECKPOINT), "checkpoint_sha256": sha(CHECKPOINT),
        "quarantine": str(QUARANTINE), "quarantine_sha256": sha(QUARANTINE),
        "contract": str(CONTRACT), "contract_sha256": sha(CONTRACT),
        "quiet_window_status": str(status_path),
        "quiet_window_status_sha256": sha(status_path),
        "prelaunch_quiet_window_seconds": QUIET_WINDOW_SECONDS,
        "formal_runtime_gate_still_required": True,
        "plan_capture_timeout_seconds": 900,
        "completed_outer_attempts_preserved": 2,
        "discarded_incomplete_outer_credit": 0,
        "postgresql_process_control_or_stop_performed": False,
    })
    print(json.dumps({
        "status": "RUNNING", "run_id": run_id, "pid": process.pid,
        "wait_elapsed_seconds": time.monotonic() - wait_started,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
