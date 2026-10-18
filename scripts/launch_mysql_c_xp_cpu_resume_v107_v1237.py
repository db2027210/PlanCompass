#!/usr/bin/env python3
"""Restart only XP-CPU C after an external-overlap gate clears."""

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
CAMPAIGN = HISTORY / "v1237_mysql_c_xp_cpu_resume_v107_20260905"
RUN_ID = "v1237_to_c_pg_latest_recovery_v107_20260905_xp_cpu"
RUN_ROOT = HISTORY / RUN_ID
MANIFEST = CAMPAIGN / "launch_manifest.json"
BARRIER = Path("/tmp/mysql_async_outer_start/v1237_mysql_c_xp_cpu_resume_v107")
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v16.py"
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260905_v107/"
    "algorithm_c_pg_latest_outer_contract.json"
)
AUTHORITY = HISTORY / "v1236_c_xp_cpu_scene_authority_v107_20260905/authority"
CHECKPOINT = HISTORY / (
    "v1232_c_xp_cpu_resume_checkpoint_v105_20260905/prepared_binding.json"
)
FRESH_R0 = HISTORY / (
    "v1236_c_xp_cpu_scene_authority_v107_20260905_r0/fresh_r0_binding.json"
)
QUARANTINE = HISTORY / (
    "v1233_c_xp_cpu_resume_quarantine_v105_20260905/replay_quarantine.json"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def now() -> str:
    return datetime.now().astimezone().isoformat()


def main() -> int:
    for path in (
        CONTROLLER, CONTRACT, CHECKPOINT, FRESH_R0, QUARANTINE,
        AUTHORITY / "transition_preflight.json",
        AUTHORITY / "prepared_binding.json",
        AUTHORITY / "benchmark_accounts_binding.json",
    ):
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"regular launch artifact required: {path}")
    if CAMPAIGN.exists() or RUN_ROOT.exists() or BARRIER.exists():
        raise RuntimeError("fresh XP-CPU recovery paths required")
    contract = load(CONTRACT)
    if (
        contract.get("release_version") != "v107-pg-latest-outer-recovery"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
    ):
        raise RuntimeError("v107 recovery contract differs")

    CAMPAIGN.mkdir(parents=True, mode=0o700)
    BARRIER.mkdir(parents=True, mode=0o700)
    log_path = CAMPAIGN / "xp_cpu.controller.log"
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
    env = os.environ.copy()
    env["MYSQL_TWO_WORKER_AUTHORITY_ROOT"] = str(AUTHORITY)
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
            text=True, start_new_session=True,
        )
    manifest: dict[str, Any] = {
        "schema_version": "mysql_algorithm_c_single_scene_resume_launch_v1",
        "status": "STARTING", "created_at": now(), "pid": process.pid,
        "run_id": RUN_ID, "scene": "hybench_xp_cpu", "resume_after_outer": 3,
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
        "checkpoint": str(CHECKPOINT), "checkpoint_sha256": sha(CHECKPOINT),
        "controller": str(CONTROLLER), "controller_sha256": sha(CONTROLLER),
        "contract": str(CONTRACT), "contract_sha256": sha(CONTRACT),
        "log": str(log_path), "lease_mode": "none", "sibling_scene_wait": False,
    }
    atomic(MANIFEST, manifest)
    deadline = time.monotonic() + 240.0
    while time.monotonic() < deadline:
        state_path = RUN_ROOT / "run_state.json"
        if state_path.is_file():
            state = load(state_path)
            status = state.get("status")
            stage = str(state.get("stage") or "")
            if status == "FAILED_CLOSED" or state.get("error"):
                manifest.update(status="FAILED_CLOSED", state={
                    "status": status, "stage": stage, "error": state.get("error")
                }, updated_at=now())
                atomic(MANIFEST, manifest)
                raise RuntimeError(f"XP-CPU recovery failed: {state.get('error')}")
            if "MEASURING" in stage or "RESETTING_XP" in stage:
                manifest.update(status="RUNNING", state={
                    "status": status, "stage": stage,
                    "outer_attempt": state.get("outer_attempt"), "error": None,
                }, updated_at=now())
                atomic(MANIFEST, manifest)
                print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
                return 0
        if process.poll() is not None:
            raise RuntimeError(f"XP-CPU controller exited early: {process.returncode}")
        time.sleep(2.0)
    raise RuntimeError("XP-CPU controller did not enter measurement in 240 seconds")


if __name__ == "__main__":
    raise SystemExit(main())
