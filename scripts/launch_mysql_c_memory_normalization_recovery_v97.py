#!/usr/bin/env python3
"""Launch the checkpoint-normalization recovery for C memory scenes."""

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
CAMPAIGN_ID = "v1199_mysql_c_memory_normalization_recovery_20260904"
RUN_ID = "v1199_to_c_memory_normalization_resume_20260904_memory_pair_job"
CAMPAIGN = HISTORY / CAMPAIGN_ID
RUN_ROOT = HISTORY / RUN_ID
MANIFEST = CAMPAIGN / "launch_manifest.json"
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_c_normalization_recovery_20260904_v97/"
    "recovery_contract.json"
)
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v8.py"
AUTHORITY = HISTORY / (
    "v1192_to_c_client_primary_live_authority_20260903/authority"
)
CHECKPOINT = HISTORY / (
    "v1195_to_c_memory_pair_gate_checkpoint_v81_20260904/prepared_binding.json"
)
QUARANTINE = HISTORY / (
    "v1195_to_c_memory_pair_gate_checkpoint_v81_20260904/replay_quarantine.json"
)
FRESH_R0 = HISTORY / (
    "v1193_to_c_client_primary_fresh_r0_20260903/fresh_r0_binding.json"
)
B_IO_STATE = HISTORY / (
    "v1198_to_b_io_gate_v81_resume_20260904_xp_io/run_state.json"
)


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
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def validate_contract() -> dict[str, Any]:
    contract = load(CONTRACT)
    if (
        contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("active_scenes") != ["job_memory", "tpch_sf4_memory"]
        or contract.get("four_scene_wave_barrier") is not False
        or contract.get("sibling_scene_wait") is not False
        or contract.get("lease_mode") != "none"
    ):
        raise RuntimeError("C normalization recovery contract differs")
    for name, raw in dict(contract.get("source_bindings") or {}).items():
        row = dict(raw or {})
        path = (ROOT / str(row.get("path") or "")).resolve()
        if not path.is_file() or path.is_symlink() or sha(path) != row.get("sha256"):
            raise RuntimeError(f"C normalization source differs: {name}: {path}")
    return contract


def state_snapshot() -> dict[str, Any]:
    path = RUN_ROOT / "run_state.json"
    if not path.is_file():
        return {"status": "STARTING", "state_path": str(path)}
    state = load(path)
    return {
        "status": state.get("status"),
        "stage": state.get("stage"),
        "error": state.get("error"),
        "outer_attempt": state.get("outer_attempt"),
        "accepted_rounds": state.get("accepted_rounds"),
        "rejected_outer_attempts": state.get("rejected_outer_attempts"),
        "converged": state.get("converged"),
        "state_path": str(path),
        "state_sha256": sha(path),
    }


def main() -> int:
    validate_contract()
    required = [
        CONTROLLER, CHECKPOINT, QUARANTINE, FRESH_R0, B_IO_STATE,
        AUTHORITY / "transition_preflight.json",
        AUTHORITY / "prepared_binding.json",
        AUTHORITY / "benchmark_accounts_binding.json",
    ]
    for path in required:
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"regular launch artifact required: {path}")
    if CAMPAIGN.exists() or CAMPAIGN.is_symlink() or RUN_ROOT.exists() or RUN_ROOT.is_symlink():
        raise RuntimeError("fresh C normalization recovery roots required")

    CAMPAIGN.mkdir(parents=True, mode=0o700)
    log_path = CAMPAIGN / "memory_pair.controller.log"
    command = [
        sys.executable, str(CONTROLLER), "--run-id", RUN_ID,
        "--checkpoint", str(CHECKPOINT),
        "--runtime-preflight", str(AUTHORITY / "transition_preflight.json"),
        "--prepared-binding", str(AUTHORITY / "prepared_binding.json"),
        "--benchmark-accounts", str(AUTHORITY / "benchmark_accounts_binding.json"),
        "--fresh-r0", str(FRESH_R0),
        "--replay-quarantine", str(QUARANTINE),
    ]
    environment = os.environ.copy()
    environment["MYSQL_TWO_WORKER_AUTHORITY_ROOT"] = str(AUTHORITY)
    environment["MYSQL_C_SCENE_GROUP"] = "memory_pair"
    b_state = load(B_IO_STATE)
    manifest: dict[str, Any] = {
        "schema_version": "mysql_c_memory_normalization_recovery_launch_v97",
        "status": "STARTING",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "declared_scenes": [
            "job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io"
        ],
        "active_c_scenes": ["job_memory", "tpch_sf4_memory"],
        "co_resident_b_io_continues_unmodified": True,
        "co_resident_b_io_state_at_launch": {
            "path": str(B_IO_STATE),
            "sha256": sha(B_IO_STATE),
            "status": b_state.get("status"),
            "stage": b_state.get("stage"),
            "outer_attempt": b_state.get("outer_attempt"),
        },
        "contract_path": str(CONTRACT),
        "contract_sha256": sha(CONTRACT),
        "launcher_source": str(Path(__file__).resolve()),
        "launcher_source_sha256": sha(Path(__file__).resolve()),
        "run_id": RUN_ID,
        "log_path": str(log_path),
        "four_scene_wave_barrier": False,
        "sibling_scene_wait": False,
        "lease_mode": "none",
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
    }
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command, cwd=ROOT, env=environment, stdout=log,
            stderr=subprocess.STDOUT, text=True, start_new_session=True,
        )
        manifest.update({
            "status": "RUNNING", "pid": process.pid,
            "process_status": "RUNNING", "command": command,
            "started_at": now(), "state": state_snapshot(), "updated_at": now(),
        })
        atomic(MANIFEST, manifest)
        while process.poll() is None:
            manifest["state"] = state_snapshot()
            manifest["updated_at"] = now()
            atomic(MANIFEST, manifest)
            time.sleep(10)
        code = int(process.returncode or 0)
    manifest.update({
        "returncode": code,
        "process_status": "EXITED",
        "state": state_snapshot(),
        "status": "COMPLETE" if code == 0 else "FAILED_CLOSED",
        "updated_at": now(),
    })
    atomic(MANIFEST, manifest)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
