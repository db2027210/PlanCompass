#!/usr/bin/env python3
"""Retry v203 JOB after a premeasurement dynamic-gate timeout."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v103.py"
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_complete_launch_20260929_v203/"
    "four_scene_launch_contract.json"
)
AUTHORITY = HISTORY / "c_v156_candidate_authority_v36_20260923/authority"
CHECKPOINT = HISTORY / (
    "c_v150_paper_resource_v36_bootstrap_20260923/checkpoint/prepared_binding.json"
)
FRESH_R0 = HISTORY / (
    "c_v156_candidate_fresh_r0_v36_20260923/fresh_r0_binding.json"
)
QUARANTINE = HISTORY / (
    "c_v150_paper_resource_v36_bootstrap_20260923/replay_quarantine/replay_quarantine.json"
)
BASELINE = HISTORY / (
    "c_v150_paper_baseline_calibration_20260923_job/"
    "formal_incumbent_baseline/batch_result.json"
)
R0_SET = HISTORY / "c_v152_formal_r0_set_v36_20260923/formal_r0_set.json"
SOURCE_RUN = HISTORY / "c_v203_pg_complete_recovery_four_scene_20260929_job"
RUN_ID = "c_v203_pg_complete_recovery_four_scene_20260929_gate_retry3_job"
META_ROOT = HISTORY / (
    "c_v203_pg_complete_recovery_four_scene_20260929_gate_retry3_job_launch"
)
CPUSET = set(range(0, 8)) | set(range(256, 264))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    output = HISTORY / RUN_ID
    if output.exists() or META_ROOT.exists():
        raise RuntimeError("fresh JOB retry output and metadata roots required")
    source_state_path = SOURCE_RUN / "run_state.json"
    source_state = json.loads(source_state_path.read_text())
    if (
        source_state.get("status") != "FAILED_CLOSED"
        or source_state.get("outer_attempt") != 0
        or source_state.get("accepted_rounds") != {"job_memory": 0}
        or source_state.get("error")
        != "RuntimeError: dynamic non-interference gate failed closed"
    ):
        raise RuntimeError("JOB retry source is not the required zero-credit gate failure")
    gate = SOURCE_RUN / "outer_001/top5_batch_1_dynamic_noninterference_gate.json"
    gate_value = json.loads(gate.read_text())
    if (
        gate_value.get("status") != "BLOCKED_FAIL_CLOSED"
        or gate_value.get("errors") != ["dynamic_noninterference_window_timeout"]
    ):
        raise RuntimeError("JOB retry source gate differs")
    for path in (
        CONTROLLER, CONTRACT, CHECKPOINT, FRESH_R0, QUARANTINE, BASELINE, R0_SET,
        AUTHORITY / "transition_preflight.json",
        AUTHORITY / "prepared_binding.json",
        AUTHORITY / "benchmark_accounts_binding.json",
    ):
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"regular retry input required: {path}")
    META_ROOT.mkdir(parents=True, mode=0o700)
    log_path = META_ROOT / "controller.log"
    command = [
        sys.executable, str(CONTROLLER),
        "--run-id", RUN_ID,
        "--checkpoint", str(CHECKPOINT),
        "--runtime-preflight", str(AUTHORITY / "transition_preflight.json"),
        "--prepared-binding", str(AUTHORITY / "prepared_binding.json"),
        "--benchmark-accounts", str(AUTHORITY / "benchmark_accounts_binding.json"),
        "--fresh-r0", str(FRESH_R0),
        "--replay-quarantine", str(QUARANTINE),
        "--fresh-search", "--scene", "job_memory",
        "--scene-start-barrier", str(
            Path("/tmp/mysql_async_outer_start")
            / "c_v203_pg_complete_recovery_four_scene_20260929"
            / "algorithm_c"
        ),
        "--formal-baseline", str(BASELINE),
        "--formal-r0-set", str(R0_SET),
    ]
    environment = os.environ.copy()
    environment["MYSQL_TWO_WORKER_AUTHORITY_ROOT"] = str(AUTHORITY)
    log = log_path.open("w")
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env=environment,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        preexec_fn=lambda: os.sched_setaffinity(0, CPUSET),
    )
    manifest = {
        "schema_version": "mysql_algorithm_c_v203_job_dynamic_gate_retry_v1",
        "status": "RUNNING",
        "created_at": datetime.now().astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene": "job_memory",
        "run_id": RUN_ID,
        "pid": process.pid,
        "controller_cpuset": "0-7,256-263",
        "controller": str(CONTROLLER),
        "controller_sha256": sha(CONTROLLER),
        "launch_contract": str(CONTRACT),
        "launch_contract_sha256": sha(CONTRACT),
        "retry_launcher": str(Path(__file__).resolve()),
        "retry_launcher_sha256": sha(Path(__file__).resolve()),
        "source_failed_run": str(SOURCE_RUN),
        "source_failed_state_sha256": sha(source_state_path),
        "source_failed_gate_sha256": sha(gate),
        "source_accepted_credit": 0,
        "historical_interval_state_reused": False,
        "fresh_search": True,
        "sibling_scene_wait": False,
        "other_running_scenes_controlled_or_stopped": False,
        "postgresql_process_control_or_stop_performed": False,
        "log": str(log_path),
    }
    manifest_path = META_ROOT / "retry_launch_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    manifest_path.with_suffix(".json.sha256").write_text(
        f"{sha(manifest_path)}  {manifest_path.name}\n"
    )
    print(json.dumps({"pid": process.pid, "run_id": RUN_ID}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
