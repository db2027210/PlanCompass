#!/usr/bin/env python3
"""Launch four independent fresh Algorithm-C scenes under v103."""

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
CAMPAIGN_ID = "v1223_mysql_c_four_scene_pg_latest_v103_20260905"
CAMPAIGN = HISTORY / CAMPAIGN_ID
MANIFEST = CAMPAIGN / "launch_manifest.json"
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260904_v103/"
    "four_scene_launch_contract.json"
)
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v13.py"
CHECKPOINT = HISTORY / (
    "v1185_to_c_client_primary_bootstrap_20260903/checkpoint/prepared_binding.json"
)
QUARANTINE = HISTORY / (
    "v1185_to_c_client_primary_bootstrap_20260903/"
    "replay_quarantine/replay_quarantine.json"
)
BARRIER = Path("/tmp/mysql_async_outer_start") / CAMPAIGN_ID / "algorithm_c"

MEMORY_AUTHORITY = HISTORY / (
    "v1221_to_c_current_identity_authority_20260905/authority"
)
CPU_AUTHORITY = MEMORY_AUTHORITY
IO_AUTHORITY = MEMORY_AUTHORITY
MEMORY_R0 = HISTORY / (
    "v1222_to_c_current_identity_fresh_r0_20260905/fresh_r0_binding.json"
)
CPU_R0 = MEMORY_R0
IO_R0 = MEMORY_R0

SPECS = {
    "job_memory": {
        "run_id": "v1223_to_c_pg_latest_v103_20260905_job",
        "authority": MEMORY_AUTHORITY,
        "fresh_r0": MEMORY_R0,
    },
    "tpch_sf4_memory": {
        "run_id": "v1223_to_c_pg_latest_v103_20260905_tpch",
        "authority": MEMORY_AUTHORITY,
        "fresh_r0": MEMORY_R0,
    },
    "hybench_xp_cpu": {
        "run_id": "v1223_to_c_pg_latest_v103_20260905_xp_cpu",
        "authority": CPU_AUTHORITY,
        "fresh_r0": CPU_R0,
    },
    "hybench_xp_io": {
        "run_id": "v1223_to_c_pg_latest_v103_20260905_xp_io",
        "authority": IO_AUTHORITY,
        "fresh_r0": IO_R0,
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
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def validate() -> dict[str, Any]:
    contract = load(CONTRACT)
    if (
        contract.get("release_version") != "v103-pg-latest-outer"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("algorithm") != "C"
        or contract.get("fresh_search") is not True
        or contract.get("fresh_formal_baseline_per_scene") is not True
        or contract.get("active_scenes") != list(SPECS)
        or contract.get("all_four_scenes_start_concurrently") is not True
        or contract.get("physical_workers_per_scene") != 2
        or contract.get("maximum_concurrent_mysql_workers") != 8
        or contract.get("per_scene_controller") is not True
        or contract.get("per_scene_outer_state_machine") is not True
        or contract.get("four_scene_wave_barrier") is not False
        or contract.get("sibling_scene_wait") is not False
        or contract.get("lease_mode") != "none"
        or contract.get("algorithm_a_or_previous_c_prior_used") is not False
        or contract.get("previous_c_accepted_or_rejected_credit_inherited") is not False
        or contract.get("previous_c_ranges_inherited") is not False
    ):
        raise RuntimeError("Algorithm C v103 four-scene launch contract differs")
    for name, raw in dict(contract.get("source_bindings") or {}).items():
        row = dict(raw or {})
        path = (ROOT / str(row.get("path") or "")).resolve()
        if not path.is_file() or path.is_symlink() or sha(path) != row.get("sha256"):
            raise RuntimeError(f"Algorithm C v103 launch source differs: {name}")
    required = [CONTROLLER, CHECKPOINT, QUARANTINE]
    for spec in SPECS.values():
        authority = Path(spec["authority"])
        required.extend([
            Path(spec["fresh_r0"]),
            authority / "transition_preflight.json",
            authority / "prepared_binding.json",
            authority / "benchmark_accounts_binding.json",
        ])
    for path in required:
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"regular launch artifact required: {path}")
    return contract


def snapshot(run_id: str) -> dict[str, Any]:
    path = HISTORY / run_id / "run_state.json"
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
    validate()
    if CAMPAIGN.exists() or CAMPAIGN.is_symlink() or BARRIER.exists() or BARRIER.is_symlink():
        raise RuntimeError("fresh v103 campaign and barrier roots required")
    for spec in SPECS.values():
        run_root = HISTORY / str(spec["run_id"])
        if run_root.exists() or run_root.is_symlink():
            raise RuntimeError(f"fresh run root required: {run_root}")
    CAMPAIGN.mkdir(parents=True, mode=0o700)
    BARRIER.mkdir(parents=True, mode=0o700)

    processes: dict[str, subprocess.Popen[str]] = {}
    logs: dict[str, Any] = {}
    commands: dict[str, list[str]] = {}
    manifest: dict[str, Any] = {
        "schema_version": "mysql_algorithm_c_four_scene_pg_latest_launch_v1",
        "status": "STARTING",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "contract_path": str(CONTRACT),
        "contract_sha256": sha(CONTRACT),
        "controller_path": str(CONTROLLER),
        "controller_sha256": sha(CONTROLLER),
        "all_four_scenes_started_concurrently": True,
        "physical_workers_per_scene": 2,
        "maximum_concurrent_mysql_workers": 8,
        "per_scene_outer_state_machine": True,
        "four_scene_wave_barrier": False,
        "sibling_scene_wait": False,
        "lease_mode": "none",
        "algorithm_a_or_previous_c_prior_used": False,
        "previous_c_state_inherited": False,
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
        "scenes": {},
    }
    try:
        for scene, spec in SPECS.items():
            authority = Path(spec["authority"])
            command = [
                sys.executable,
                str(CONTROLLER),
                "--run-id",
                str(spec["run_id"]),
                "--checkpoint",
                str(CHECKPOINT),
                "--runtime-preflight",
                str(authority / "transition_preflight.json"),
                "--prepared-binding",
                str(authority / "prepared_binding.json"),
                "--benchmark-accounts",
                str(authority / "benchmark_accounts_binding.json"),
                "--fresh-r0",
                str(spec["fresh_r0"]),
                "--replay-quarantine",
                str(QUARANTINE),
                "--fresh-search",
                "--scene",
                scene,
                "--scene-start-barrier",
                str(BARRIER),
            ]
            environment = os.environ.copy()
            environment["MYSQL_TWO_WORKER_AUTHORITY_ROOT"] = str(authority)
            log_path = CAMPAIGN / f"{scene}.controller.log"
            log = log_path.open("w", encoding="utf-8")
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
            logs[scene] = log
            processes[scene] = process
            commands[scene] = command
            manifest["scenes"][scene] = {
                "run_id": spec["run_id"],
                "pid": process.pid,
                "status": "RUNNING",
                "log_path": str(log_path),
                "command": command,
                "authority_root": str(authority),
                "fresh_formal_baseline": True,
            }
        manifest["status"] = "RUNNING"
        manifest["started_at"] = now()
        atomic(MANIFEST, manifest)
        while any(process.poll() is None for process in processes.values()):
            for scene, process in processes.items():
                row = manifest["scenes"][scene]
                row["process_status"] = (
                    "RUNNING" if process.poll() is None else "EXITED"
                )
                row["returncode"] = process.poll()
                row["state"] = snapshot(str(SPECS[scene]["run_id"]))
            manifest["updated_at"] = now()
            atomic(MANIFEST, manifest)
            time.sleep(10)
    finally:
        for log in logs.values():
            log.close()

    failed = False
    for scene, process in processes.items():
        code = int(process.returncode or 0)
        failed = failed or code != 0
        row = manifest["scenes"][scene]
        row["process_status"] = "EXITED"
        row["returncode"] = code
        row["state"] = snapshot(str(SPECS[scene]["run_id"]))
        row["status"] = "COMPLETE" if code == 0 else "FAILED_CLOSED"
    manifest["status"] = "FAILED_CLOSED" if failed else "COMPLETE"
    manifest["completed_at"] = now()
    atomic(MANIFEST, manifest)
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
