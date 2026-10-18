#!/usr/bin/env python3
"""Launch the normalized TPC-H C resume lane."""

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
RUN_ID = "v1234_to_c_pg_latest_recovery_v105_20260905_tpch"
OUTPUT = HISTORY / RUN_ID
CAMPAIGN = HISTORY / "v1234_mysql_c_tpch_normalized_resume_v105_20260905"
MANIFEST = CAMPAIGN / "launch_manifest.json"
BARRIER = Path("/tmp/mysql_async_outer_start/v1234_mysql_c_tpch_normalized_resume")
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_tpch_resume_client_primary_v15.py"
CHECKPOINT = HISTORY / "v1232_c_tpch_resume_checkpoint_v105_20260905/prepared_binding.json"
AUTHORITY = HISTORY / "v1230_c_tpch_scene_authority_v105_20260905/authority"
R0 = HISTORY / "v1230_c_tpch_fresh_r0_v105_20260905/fresh_r0_binding.json"
QUARANTINE = HISTORY / "v1233_c_tpch_resume_quarantine_v105_20260905/replay_quarantine.json"
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260905_v105/"
    "algorithm_c_pg_latest_outer_contract.json"
)
NORMALIZATION = HISTORY / (
    "v1223_to_c_pg_latest_v103_20260905_tpch/formal_incumbent_baseline/"
    "same_worker_qps_normalization.json"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def now() -> str:
    return datetime.now().astimezone().isoformat()


def atomic(value: dict) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = MANIFEST.with_name(f".{MANIFEST.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    os.replace(temporary, MANIFEST)


def main() -> int:
    if OUTPUT.exists() or CAMPAIGN.exists() or BARRIER.exists():
        raise RuntimeError("fresh normalized TPC-H recovery roots required")
    required = [
        CONTROLLER, CHECKPOINT, R0, QUARANTINE, CONTRACT, NORMALIZATION,
        AUTHORITY / "transition_preflight.json",
        AUTHORITY / "prepared_binding.json",
        AUTHORITY / "benchmark_accounts_binding.json",
    ]
    if any(not path.is_file() or path.is_symlink() for path in required):
        raise RuntimeError("normalized TPC-H recovery artifact differs")
    BARRIER.mkdir(parents=True, mode=0o700)
    log_path = CAMPAIGN / "tpch.controller.log"
    CAMPAIGN.mkdir(parents=True, mode=0o700)
    log = log_path.open("w", encoding="utf-8")
    env = os.environ.copy()
    env["MYSQL_TWO_WORKER_AUTHORITY_ROOT"] = str(AUTHORITY)
    command = [
        sys.executable, str(CONTROLLER), "--run-id", RUN_ID,
        "--checkpoint", str(CHECKPOINT),
        "--runtime-preflight", str(AUTHORITY / "transition_preflight.json"),
        "--prepared-binding", str(AUTHORITY / "prepared_binding.json"),
        "--benchmark-accounts", str(AUTHORITY / "benchmark_accounts_binding.json"),
        "--fresh-r0", str(R0), "--replay-quarantine", str(QUARANTINE),
        "--scene", "tpch_sf4_memory", "--scene-start-barrier", str(BARRIER),
    ]
    process = subprocess.Popen(
        command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
        text=True, start_new_session=True,
    )
    manifest = {
        "schema_version": "mysql_algorithm_c_tpch_normalized_resume_launch_v1",
        "status": "LAUNCHED", "created_at": now(), "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True, "run_id": RUN_ID,
        "pid": process.pid, "resume_after_outer": 1,
        "controller_path": str(CONTROLLER), "controller_sha256": sha(CONTROLLER),
        "contract_path": str(CONTRACT), "contract_sha256": sha(CONTRACT),
        "normalization_path": str(NORMALIZATION),
        "normalization_sha256": sha(NORMALIZATION),
        "checkpoint_path": str(CHECKPOINT), "checkpoint_sha256": sha(CHECKPOINT),
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
    }
    atomic(manifest)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        state_path = OUTPUT / "run_state.json"
        if state_path.is_file():
            state = json.loads(state_path.read_text())
            if state.get("status") == "FAILED_CLOSED" or state.get("error"):
                raise RuntimeError(f"normalized TPC-H resume failed: {state.get('error')}")
            manifest.update({"status": "RUNNING", "stage": state.get("stage"),
                             "outer_attempt": state.get("outer_attempt"),
                             "updated_at": now()})
            atomic(manifest)
            print(json.dumps(manifest, sort_keys=True))
            return 0
        if process.poll() is not None:
            raise RuntimeError("normalized TPC-H controller exited before state")
        time.sleep(2)
    raise RuntimeError("normalized TPC-H controller did not publish state")


if __name__ == "__main__":
    raise SystemExit(main())
