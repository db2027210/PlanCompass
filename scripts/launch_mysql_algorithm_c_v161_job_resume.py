#!/usr/bin/env python3
"""Launch JOB from the sealed outer-2 boundary after interference clears."""

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


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_four_scene_client_primary_v71 as controller


HISTORY = ROOT / "history/mysql_four_scene_20260809"
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v71.py"
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_full_resume_20260923_v161/"
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
FAILED_SOURCE = HISTORY / (
    "c_v158_full_algorithm_resume_20260923_job/run_state.json"
)
DECISION = HISTORY / (
    "c_v158_full_algorithm_resume_20260923_job/outer_002/"
    "job_memory.decision.json"
)
BLOCKED_GATE = HISTORY / (
    "c_v158_full_algorithm_resume_20260923_job/outer_003/"
    "top5_batch_2_dynamic_noninterference_gate.json"
)
AFFINITY = "0-7,256-263"


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
    preflight_value = load(preflight)
    checkpoint = controller.runtime.scene_checkpoint.verify_or_standard(CHECKPOINT)
    quarantine = load(QUARANTINE)
    failed = load(FAILED_SOURCE)
    decision = load(DECISION)
    blocked = load(BLOCKED_GATE)
    freeze_sha = checkpoint.get("freeze_sha256")
    if (
        preflight_value.get("status") != "PASS"
        or preflight_value.get("formal_launch_authorized") is not True
        or (preflight_value.get("rolling_dynamic_noninterference_gate") or {}).get("status")
        != "PASS"
        or preflight_value.get("freeze_sha256") != freeze_sha
        or int(checkpoint.get("completed_outer_attempts") or 0) != 2
        or (checkpoint.get("independent_scene_boundary") or {}).get("scene")
        != "job_memory"
        or quarantine.get("status") != "PASS"
        or quarantine.get("resume_checkpoint_outer_attempt") != 2
        or failed.get("status") != "FAILED_CLOSED"
        or failed.get("error")
        != "RuntimeError: dynamic non-interference gate failed closed"
        or int(failed.get("outer_attempt") or 0) != 2
        or decision.get("accepted") is not True
        or int(decision.get("outer_attempt") or 0) != 2
        or blocked.get("status") != "BLOCKED_FAIL_CLOSED"
        or "external_database_or_benchmark_isolation_unproven"
        not in list(blocked.get("errors") or [])
    ):
        raise RuntimeError("v161 JOB resume authority differs")
    campaign = HISTORY / args.run_prefix
    run_id = f"{args.run_prefix}_job"
    output = HISTORY / run_id
    barrier = Path("/tmp/mysql_async_outer_start") / args.run_prefix / "algorithm_c"
    if campaign.exists() or output.exists() or barrier.exists():
        raise RuntimeError("fresh v161 JOB output required")
    campaign.mkdir(parents=True, mode=0o700)
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
        "--scene", "job_memory",
        "--scene-start-barrier", str(barrier),
    ]
    started = time.monotonic()
    process = subprocess.Popen(
        command, cwd=ROOT, env=environment, stdout=log,
        stderr=subprocess.STDOUT, start_new_session=True,
        preexec_fn=lambda: os.sched_setaffinity(0, cpus(AFFINITY)),
    )
    log.close()
    manifest = {
        "schema_version": "mysql_algorithm_c_v161_job_resume_launch_v1",
        "release_version": "v161-job-outer2-boundary-resume",
        "status": "RUNNING",
        "created_at": datetime.now().astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene": "job_memory",
        "run_id": run_id,
        "pid": process.pid,
        "controller_cpuset": AFFINITY,
        "started_offset_seconds": time.monotonic() - started,
        "log": str(log_path),
        "checkpoint": str(CHECKPOINT), "checkpoint_sha256": sha(CHECKPOINT),
        "quarantine": str(QUARANTINE), "quarantine_sha256": sha(QUARANTINE),
        "contract": str(CONTRACT), "contract_sha256": sha(CONTRACT),
        "authority_root": str(authority),
        "preflight_sha256": sha(preflight),
        "failed_v158_state": str(FAILED_SOURCE),
        "failed_v158_state_sha256": sha(FAILED_SOURCE),
        "completed_outer_attempts_preserved": 2,
        "discarded_incomplete_outer": 3,
        "discarded_incomplete_outer_credit": 0,
        "sibling_scene_wait": False,
        "postgresql_process_control_or_stop_performed": False,
    }
    atomic(campaign / "launch_manifest.json", manifest)
    print(json.dumps({
        "status": "RUNNING", "campaign": str(campaign),
        "run_id": run_id, "pid": process.pid,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
