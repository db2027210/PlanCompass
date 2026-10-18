#!/usr/bin/env python3
"""Release Algorithm C for XP-CPU after its Algorithm B completion."""

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
CAMPAIGN_ID = "v1201_mysql_c_xp_cpu_release_v98_20260904"
RUN_ID = "v1201_to_c_xp_cpu_client_primary_v98_20260904_xp_cpu"
CAMPAIGN = HISTORY / CAMPAIGN_ID
RUN_ROOT = HISTORY / RUN_ID
MANIFEST = CAMPAIGN / "launch_manifest.json"
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_c_xp_cpu_release_20260904_v98/"
    "release_contract.json"
)
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v7.py"
AUTHORITY = HISTORY / (
    "v1200_to_c_xp_cpu_scene_authority_v97_20260904/authority"
)
FRESH_R0 = HISTORY / (
    "v1200_to_c_xp_cpu_scene_fresh_r0_v97_20260904/fresh_r0_binding.json"
)
CHECKPOINT = HISTORY / (
    "v1185_to_c_client_primary_bootstrap_20260903/checkpoint/prepared_binding.json"
)
QUARANTINE = HISTORY / (
    "v1185_to_c_client_primary_bootstrap_20260903/"
    "replay_quarantine/replay_quarantine.json"
)
B_CPU_STATE = HISTORY / (
    "v1198_to_b_cpu_gate_v81_resume_20260904_xp_cpu/run_state.json"
)
BARRIER = Path("/tmp/mysql_async_outer_start") / CAMPAIGN_ID / "algorithm_c"


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


def validate() -> dict[str, Any]:
    contract = load(CONTRACT)
    if (
        contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("scene") != "hybench_xp_cpu"
        or contract.get("algorithm_b_complete_required") is not True
        or contract.get("algorithm_b_results_used_as_search_prior") is not False
        or contract.get("fresh_search") is not True
        or contract.get("four_scene_wave_barrier") is not False
        or contract.get("sibling_scene_wait") is not False
        or contract.get("lease_mode") != "none"
    ):
        raise RuntimeError("XP-CPU C release contract differs")
    for name, raw in dict(contract.get("source_bindings") or {}).items():
        row = dict(raw or {})
        path = (ROOT / str(row.get("path") or "")).resolve()
        if not path.is_file() or path.is_symlink() or sha(path) != row.get("sha256"):
            raise RuntimeError(f"XP-CPU C source differs: {name}: {path}")
    b_state = load(B_CPU_STATE)
    if (
        b_state.get("status") != "COMPLETE"
        or b_state.get("error")
        or (b_state.get("converged") or {}).get("hybench_xp_cpu") is not True
    ):
        raise RuntimeError("XP-CPU Algorithm B completion differs")
    return contract


def snapshot() -> dict[str, Any]:
    path = RUN_ROOT / "run_state.json"
    if not path.is_file():
        return {"status": "STARTING", "state_path": str(path)}
    state = load(path)
    return {
        "status": state.get("status"), "stage": state.get("stage"),
        "error": state.get("error"), "outer_attempt": state.get("outer_attempt"),
        "accepted_rounds": state.get("accepted_rounds"),
        "rejected_outer_attempts": state.get("rejected_outer_attempts"),
        "converged": state.get("converged"), "state_path": str(path),
        "state_sha256": sha(path),
    }


def main() -> int:
    validate()
    required = [
        CONTROLLER, CHECKPOINT, QUARANTINE, FRESH_R0,
        AUTHORITY / "transition_preflight.json",
        AUTHORITY / "prepared_binding.json",
        AUTHORITY / "benchmark_accounts_binding.json",
    ]
    for path in required:
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"regular XP-CPU launch artifact required: {path}")
    if CAMPAIGN.exists() or RUN_ROOT.exists() or BARRIER.exists():
        raise RuntimeError("fresh XP-CPU C release roots required")
    CAMPAIGN.mkdir(parents=True, mode=0o700)
    BARRIER.mkdir(parents=True, mode=0o700)
    log_path = CAMPAIGN / "xp_cpu.controller.log"
    command = [
        sys.executable, str(CONTROLLER), "--run-id", RUN_ID,
        "--checkpoint", str(CHECKPOINT),
        "--runtime-preflight", str(AUTHORITY / "transition_preflight.json"),
        "--prepared-binding", str(AUTHORITY / "prepared_binding.json"),
        "--benchmark-accounts", str(AUTHORITY / "benchmark_accounts_binding.json"),
        "--fresh-r0", str(FRESH_R0), "--replay-quarantine", str(QUARANTINE),
        "--fresh-search", "--scene", "hybench_xp_cpu",
        "--scene-start-barrier", str(BARRIER),
    ]
    environment = os.environ.copy()
    environment["MYSQL_TWO_WORKER_AUTHORITY_ROOT"] = str(AUTHORITY)
    manifest: dict[str, Any] = {
        "schema_version": "mysql_c_xp_cpu_per_scene_release_launch_v98",
        "status": "STARTING", "created_at": now(),
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "declared_scenes": [
            "job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io"
        ],
        "active_scene": "hybench_xp_cpu",
        "release_reason": "same_scene_algorithm_b_complete",
        "algorithm_b_results_used_as_search_prior": False,
        "co_resident_b_xp_io_continues_unmodified": True,
        "co_resident_c_memory_pair_continues_unmodified": True,
        "four_scene_wave_barrier": False, "sibling_scene_wait": False,
        "lease_mode": "none", "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
        "contract_path": str(CONTRACT), "contract_sha256": sha(CONTRACT),
        "launcher_source": str(Path(__file__).resolve()),
        "launcher_source_sha256": sha(Path(__file__).resolve()),
        "run_id": RUN_ID, "log_path": str(log_path), "command": command,
    }
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command, cwd=ROOT, env=environment, stdout=log,
            stderr=subprocess.STDOUT, text=True, start_new_session=True,
        )
        manifest.update({
            "status": "RUNNING", "pid": process.pid,
            "process_status": "RUNNING", "started_at": now(),
            "state": snapshot(), "updated_at": now(),
        })
        atomic(MANIFEST, manifest)
        while process.poll() is None:
            manifest["state"] = snapshot()
            manifest["updated_at"] = now()
            atomic(MANIFEST, manifest)
            time.sleep(10)
        code = int(process.returncode or 0)
    manifest.update({
        "returncode": code, "process_status": "EXITED", "state": snapshot(),
        "status": "COMPLETE" if code == 0 else "FAILED_CLOSED",
        "updated_at": now(),
    })
    atomic(MANIFEST, manifest)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
