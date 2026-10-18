#!/usr/bin/env python3
"""Launch the three failed C scenes from their last completed outer boundary."""

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
CAMPAIGN = HISTORY / "v1233_mysql_c_scene_resume_v105_20260905"
MANIFEST = CAMPAIGN / "launch_manifest.json"
BARRIER = Path("/tmp/mysql_async_outer_start/v1233_mysql_c_scene_resume_v105")
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v14.py"
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260905_v105/"
    "algorithm_c_pg_latest_outer_contract.json"
)
SPECS = {
    "job_memory": {
        "suffix": "job",
        "run_id": "v1233_to_c_pg_latest_recovery_v105_20260905_job",
        "checkpoint": "v1232_c_job_resume_checkpoint_v105_20260905",
        "authority": "v1230_c_job_scene_authority_v105_20260905",
        "fresh_r0": "v1230_c_job_fresh_r0_v105_20260905",
        "quarantine": "v1233_c_job_resume_quarantine_v105_20260905",
        "resume_after_outer": 12,
    },
    "tpch_sf4_memory": {
        "suffix": "tpch",
        "run_id": "v1233_to_c_pg_latest_recovery_v105_20260905_tpch",
        "checkpoint": "v1232_c_tpch_resume_checkpoint_v105_20260905",
        "authority": "v1230_c_tpch_scene_authority_v105_20260905",
        "fresh_r0": "v1230_c_tpch_fresh_r0_v105_20260905",
        "quarantine": "v1233_c_tpch_resume_quarantine_v105_20260905",
        "resume_after_outer": 1,
    },
    "hybench_xp_cpu": {
        "suffix": "xp_cpu",
        "run_id": "v1233_to_c_pg_latest_recovery_v105_20260905_xp_cpu",
        "checkpoint": "v1232_c_xp_cpu_resume_checkpoint_v105_20260905",
        "authority": "v1230_c_xp_cpu_scene_authority_v105_20260905",
        "fresh_r0": "v1230_c_xp_cpu_fresh_r0_v105_20260905",
        "quarantine": "v1233_c_xp_cpu_resume_quarantine_v105_20260905",
        "resume_after_outer": 3,
    },
}


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


def now() -> str:
    return datetime.now().astimezone().isoformat()


def main() -> int:
    if CAMPAIGN.exists() or CAMPAIGN.is_symlink():
        raise RuntimeError(f"fresh campaign required: {CAMPAIGN}")
    if BARRIER.exists() or BARRIER.is_symlink():
        raise RuntimeError(f"fresh barrier required: {BARRIER}")
    contract = load(CONTRACT)
    if (
        contract.get("release_version") != "v105-pg-latest-outer-recovery"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
    ):
        raise RuntimeError("v105 recovery contract differs")
    required = [CONTROLLER, CONTRACT]
    for spec in SPECS.values():
        authority = HISTORY / spec["authority"] / "authority"
        required.extend([
            HISTORY / spec["checkpoint"] / "prepared_binding.json",
            HISTORY / spec["fresh_r0"] / "fresh_r0_binding.json",
            HISTORY / spec["quarantine"] / "replay_quarantine.json",
            authority / "transition_preflight.json",
            authority / "prepared_binding.json",
            authority / "benchmark_accounts_binding.json",
        ])
        if (HISTORY / spec["run_id"]).exists():
            raise RuntimeError(f"fresh output required: {spec['run_id']}")
    for path in required:
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"regular launch artifact required: {path}")

    CAMPAIGN.mkdir(parents=True, mode=0o700)
    BARRIER.mkdir(parents=True, mode=0o700)
    manifest: dict[str, Any] = {
        "schema_version": "mysql_algorithm_c_scene_resume_launch_v1",
        "status": "STARTING",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "contract_path": str(CONTRACT),
        "contract_sha256": sha(CONTRACT),
        "controller_path": str(CONTROLLER),
        "controller_sha256": sha(CONTROLLER),
        "independent_scene_controllers": True,
        "four_scene_wave_barrier": False,
        "sibling_scene_wait": False,
        "lease_mode": "none",
        "completed_outer_history_preserved": True,
        "incomplete_outer_credit": 0,
        "xp_io_existing_controller_untouched": True,
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
        "launches": {},
    }
    processes: dict[str, subprocess.Popen[str]] = {}
    logs: dict[str, Any] = {}
    try:
        for scene, spec in SPECS.items():
            authority = HISTORY / spec["authority"] / "authority"
            log_path = CAMPAIGN / f"{spec['suffix']}.controller.log"
            log = log_path.open("w", encoding="utf-8")
            env = os.environ.copy()
            env["MYSQL_TWO_WORKER_AUTHORITY_ROOT"] = str(authority)
            command = [
                sys.executable, str(CONTROLLER),
                "--run-id", str(spec["run_id"]),
                "--checkpoint", str(HISTORY / spec["checkpoint"] / "prepared_binding.json"),
                "--runtime-preflight", str(authority / "transition_preflight.json"),
                "--prepared-binding", str(authority / "prepared_binding.json"),
                "--benchmark-accounts", str(authority / "benchmark_accounts_binding.json"),
                "--fresh-r0", str(HISTORY / spec["fresh_r0"] / "fresh_r0_binding.json"),
                "--replay-quarantine", str(
                    HISTORY / spec["quarantine"] / "replay_quarantine.json"
                ),
                "--scene", scene,
                "--scene-start-barrier", str(BARRIER),
            ]
            process = subprocess.Popen(
                command, cwd=ROOT, env=env, stdout=log,
                stderr=subprocess.STDOUT, text=True, start_new_session=True,
            )
            processes[scene] = process
            logs[scene] = log
            manifest["launches"][scene] = {
                "run_id": spec["run_id"], "pid": process.pid,
                "resume_after_outer": spec["resume_after_outer"],
                "checkpoint": command[command.index("--checkpoint") + 1],
                "authority_root": str(authority), "log": str(log_path),
                "started_at": now(),
            }
        manifest["status"] = "LAUNCHED"
        manifest["launched_at"] = now()
        atomic(MANIFEST, manifest)

        deadline = time.monotonic() + 180.0
        while time.monotonic() < deadline:
            states: dict[str, Any] = {}
            failed: list[str] = []
            for scene, spec in SPECS.items():
                state_path = HISTORY / spec["run_id"] / "run_state.json"
                if state_path.is_file():
                    state = load(state_path)
                    states[scene] = {
                        "status": state.get("status"),
                        "stage": state.get("stage"),
                        "outer_attempt": state.get("outer_attempt"),
                        "accepted_rounds": state.get("accepted_rounds"),
                        "rejected_outer_attempts": state.get("rejected_outer_attempts"),
                        "error": state.get("error"),
                    }
                    if state.get("status") == "FAILED_CLOSED" or state.get("error"):
                        failed.append(scene)
                elif processes[scene].poll() is not None:
                    failed.append(scene)
            if failed:
                manifest.update({"status": "FAILED_CLOSED", "failed_scenes": failed,
                                 "states": states, "updated_at": now()})
                atomic(MANIFEST, manifest)
                raise RuntimeError(f"recovery launch failed: {failed}")
            if len(states) == len(SPECS):
                manifest.update({"status": "RUNNING", "states": states,
                                 "updated_at": now()})
                atomic(MANIFEST, manifest)
                print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
                return 0
            time.sleep(2.0)
        raise RuntimeError("recovery controllers did not publish state in 180 seconds")
    finally:
        for log in logs.values():
            log.close()


if __name__ == "__main__":
    raise SystemExit(main())
