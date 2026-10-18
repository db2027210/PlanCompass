#!/usr/bin/env python3
"""Launch the B/CPU, B/I/O, and C/memory boundary recoveries together."""

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
CAMPAIGN_ID = "v1198_mysql_bc_gate_recovery_20260904"
CAMPAIGN = HISTORY / CAMPAIGN_ID
MANIFEST = CAMPAIGN / "launch_manifest.json"
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_bc_gate_recovery_20260904_v96/"
    "recovery_contract.json"
)
AUTHORITY = HISTORY / (
    "v1192_to_c_client_primary_live_authority_20260903/authority"
)
FRESH_R0 = HISTORY / (
    "v1193_to_c_client_primary_fresh_r0_20260903/fresh_r0_binding.json"
)
C_CHECKPOINT = HISTORY / (
    "v1195_to_c_memory_pair_gate_checkpoint_v81_20260904/"
    "prepared_binding.json"
)
C_QUARANTINE = HISTORY / (
    "v1195_to_c_memory_pair_gate_checkpoint_v81_20260904/"
    "replay_quarantine.json"
)
B_CONTROLLER = ROOT / (
    "scripts/run_mysql_algorithm_b_client_primary_gate_v81_resume_v1.py"
)
C_CONTROLLER = ROOT / (
    "scripts/run_mysql_algorithm_c_four_scene_client_primary_v7.py"
)

JOBS = {
    "algorithm_b_hybench_xp_cpu": {
        "run_id": "v1198_to_b_cpu_gate_v81_resume_20260904_xp_cpu",
        "scenes": ["hybench_xp_cpu"],
        "command": [
            sys.executable, str(B_CONTROLLER),
            "--scene", "hybench_xp_cpu",
            "--run-id", "v1198_to_b_cpu_gate_v81_resume_20260904_xp_cpu",
            "--resume-from", str(HISTORY / (
                "v1184_to_b_client_primary_xp_cleanup_recovery_20260903_xp_cpu"
            )),
        ],
    },
    "algorithm_b_hybench_xp_io": {
        "run_id": "v1198_to_b_io_gate_v81_resume_20260904_xp_io",
        "scenes": ["hybench_xp_io"],
        "command": [
            sys.executable, str(B_CONTROLLER),
            "--scene", "hybench_xp_io",
            "--run-id", "v1198_to_b_io_gate_v81_resume_20260904_xp_io",
            "--resume-from", str(HISTORY / (
                "v1184_to_b_client_primary_xp_cleanup_recovery_20260903_xp_io"
            )),
        ],
    },
    "algorithm_c_memory_pair": {
        "run_id": "v1198_to_c_memory_pair_gate_v81_resume_20260904_memory_pair_job",
        "scenes": ["job_memory", "tpch_sf4_memory"],
        "command": [
            sys.executable, str(C_CONTROLLER),
            "--run-id", "v1198_to_c_memory_pair_gate_v81_resume_20260904_memory_pair_job",
            "--checkpoint", str(C_CHECKPOINT),
            "--runtime-preflight", str(AUTHORITY / "transition_preflight.json"),
            "--prepared-binding", str(AUTHORITY / "prepared_binding.json"),
            "--benchmark-accounts", str(
                AUTHORITY / "benchmark_accounts_binding.json"
            ),
            "--fresh-r0", str(FRESH_R0),
            "--replay-quarantine", str(C_QUARANTINE),
        ],
    },
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
    if not CONTRACT.is_file() or CONTRACT.is_symlink():
        raise RuntimeError(f"regular recovery contract required: {CONTRACT}")
    contract = load(CONTRACT)
    launch = dict(contract.get("launch_shape") or {})
    if (
        contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or launch.get("physical_workers_per_scene") != 2
        or launch.get("maximum_concurrent_mysql_workers") != 8
        or launch.get("four_scene_wave_barrier") is not False
        or launch.get("sibling_scene_wait") is not False
        or launch.get("lease_mode") != "none"
    ):
        raise RuntimeError("recovery launch contract differs")
    for name, raw in dict(contract.get("source_bindings") or {}).items():
        binding = dict(raw or {})
        path = (ROOT / str(binding.get("path") or "")).resolve()
        if (
            not path.is_file()
            or path.is_symlink()
            or sha(path) != str(binding.get("sha256") or "")
        ):
            raise RuntimeError(f"recovery source binding differs: {name}: {path}")
    return contract


def state_snapshot(run_id: str) -> dict[str, Any]:
    path = HISTORY / run_id / "run_state.json"
    if not path.is_file():
        return {"status": "STARTING", "state_path": str(path)}
    try:
        state = load(path)
    except (OSError, ValueError, RuntimeError) as error:
        return {
            "status": "STATE_READ_RETRY",
            "state_path": str(path),
            "error": f"{type(error).__name__}: {error}",
        }
    return {
        "status": state.get("status"),
        "stage": state.get("stage"),
        "outer_attempt": state.get("outer_attempt"),
        "accepted_rounds": state.get("accepted_rounds"),
        "rejected_outer_attempts": state.get("rejected_outer_attempts"),
        "converged": state.get("converged"),
        "error": state.get("error"),
        "state_path": str(path),
        "state_sha256": sha(path),
    }


def main() -> int:
    contract = validate_contract()
    required = [
        B_CONTROLLER, C_CONTROLLER, C_CHECKPOINT, C_QUARANTINE, FRESH_R0,
        AUTHORITY / "transition_preflight.json",
        AUTHORITY / "prepared_binding.json",
        AUTHORITY / "benchmark_accounts_binding.json",
    ]
    for path in required:
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"regular launch artifact required: {path}")
    if CAMPAIGN.exists() or CAMPAIGN.is_symlink():
        raise RuntimeError(f"fresh recovery campaign required: {CAMPAIGN}")
    for row in JOBS.values():
        output = HISTORY / str(row["run_id"])
        if output.exists() or output.is_symlink():
            raise RuntimeError(f"fresh recovery output required: {output}")

    CAMPAIGN.mkdir(parents=True, mode=0o700)
    environment = os.environ.copy()
    environment["MYSQL_TWO_WORKER_AUTHORITY_ROOT"] = str(AUTHORITY)
    processes: dict[str, subprocess.Popen[str]] = {}
    logs: dict[str, Any] = {}
    manifest: dict[str, Any] = {
        "schema_version": "mysql_bc_gate_recovery_launch_v96",
        "status": "STARTING",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "declared_scenes": list(contract["declared_scenes"]),
        "contract_path": str(CONTRACT),
        "contract_sha256": sha(CONTRACT),
        "controller_source_path": str(Path(__file__).resolve()),
        "controller_source_sha256": sha(Path(__file__).resolve()),
        "physical_workers_per_scene": 2,
        "maximum_concurrent_mysql_workers": 8,
        "four_scene_wave_barrier": False,
        "sibling_scene_wait": False,
        "lease_mode": "none",
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
        "jobs": {},
    }
    try:
        for name, row in JOBS.items():
            log_path = CAMPAIGN / f"{name}.controller.log"
            log = log_path.open("w", encoding="utf-8")
            logs[name] = log
            child_environment = environment.copy()
            if name == "algorithm_c_memory_pair":
                child_environment["MYSQL_C_SCENE_GROUP"] = "memory_pair"
            process = subprocess.Popen(
                list(row["command"]), cwd=ROOT, env=child_environment,
                stdout=log, stderr=subprocess.STDOUT, text=True,
                start_new_session=True,
            )
            processes[name] = process
            manifest["jobs"][name] = {
                "run_id": row["run_id"],
                "scenes": row["scenes"],
                "pid": process.pid,
                "log_path": str(log_path),
                "started_at": now(),
                "process_status": "RUNNING",
                "command": row["command"],
            }
        manifest["status"] = "RUNNING"
        manifest["updated_at"] = now()
        atomic(MANIFEST, manifest)

        while True:
            running = 0
            failed = False
            complete = True
            for name, process in processes.items():
                code = process.poll()
                job = manifest["jobs"][name]
                job["process_status"] = "RUNNING" if code is None else "EXITED"
                job["returncode"] = code
                job["state"] = state_snapshot(str(job["run_id"]))
                if code is None:
                    running += 1
                    complete = False
                elif code != 0:
                    failed = True
                if job["state"].get("status") not in ("COMPLETE", "CONVERGED"):
                    complete = False
            if failed:
                manifest["status"] = (
                    "PARTIAL_FAILURE_RUNNING" if running else "FAILED_CLOSED"
                )
            elif complete:
                manifest["status"] = "COMPLETE"
            else:
                manifest["status"] = "RUNNING"
            manifest["running_controller_count"] = running
            manifest["updated_at"] = now()
            atomic(MANIFEST, manifest)
            if running == 0:
                return 0 if manifest["status"] == "COMPLETE" else 1
            time.sleep(10)
    finally:
        for log in logs.values():
            log.close()


if __name__ == "__main__":
    raise SystemExit(main())
