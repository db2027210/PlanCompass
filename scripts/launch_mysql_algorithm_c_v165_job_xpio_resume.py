#!/usr/bin/env python3
"""Independently resume JOB and XP/I-O from their sealed outer-2 boundaries."""

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

from scripts import run_mysql_algorithm_c_four_scene_client_primary_v75 as controller


HISTORY = ROOT / "history/mysql_four_scene_20260809"
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v75.py"
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_full_resume_20260923_v165/"
    "algorithm_c_full_resume_contract.json"
)
FRESH_R0 = HISTORY / (
    "c_v157_full_resume_fresh_r0_v36_20260923/fresh_r0_binding.json"
)
SCENES = {
    "job_memory": {
        "suffix": "job",
        "affinity": "0-7,256-263",
        "checkpoint": HISTORY / (
            "c_v161_job_outer2_checkpoint_20260923/prepared_binding.json"
        ),
        "quarantine": HISTORY / (
            "c_v161_job_outer2_quarantine_20260923/replay_quarantine.json"
        ),
        "failed_state": HISTORY / (
            "c_v164_job_outer2_resume_20260923_job/run_state.json"
        ),
    },
    "hybench_xp_io": {
        "suffix": "xp_io",
        "affinity": "24-31,280-287",
        "checkpoint": HISTORY / (
            "c_v165_xp_io_outer2_checkpoint_20260923/prepared_binding.json"
        ),
        "quarantine": HISTORY / (
            "c_v165_xp_io_outer2_quarantine_20260923/replay_quarantine.json"
        ),
        "failed_state": HISTORY / (
            "c_v158_full_algorithm_resume_20260923_xp_io/run_state.json"
        ),
    },
}


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
    if (
        preflight_value.get("status") != "PASS"
        or preflight_value.get("formal_launch_authorized") is not True
        or (preflight_value.get("rolling_dynamic_noninterference_gate") or {}).get("status")
        != "PASS"
    ):
        raise RuntimeError("v165 runtime authority differs")
    launches: dict[str, dict] = {}
    campaign = HISTORY / args.run_prefix
    if campaign.exists():
        raise RuntimeError("fresh v165 campaign required")
    for scene, raw in SCENES.items():
        checkpoint = controller.runtime.scene_checkpoint.verify_or_standard(
            raw["checkpoint"]
        )
        quarantine = load(raw["quarantine"])
        failed = load(raw["failed_state"])
        run_id = f"{args.run_prefix}_{raw['suffix']}"
        output = HISTORY / run_id
        barrier = (
            Path("/tmp/mysql_async_outer_start") / args.run_prefix
            / raw["suffix"] / "algorithm_c"
        )
        if (
            output.exists()
            or barrier.exists()
            or int(checkpoint.get("completed_outer_attempts") or 0) != 2
            or (checkpoint.get("independent_scene_boundary") or {}).get("scene")
            != scene
            or checkpoint.get("freeze_sha256") != preflight_value.get("freeze_sha256")
            or quarantine.get("status") != "PASS"
            or quarantine.get("resume_checkpoint_outer_attempt") != 2
            or failed.get("status") != "FAILED_CLOSED"
            or int(failed.get("outer_attempt") or 0) != 2
        ):
            raise RuntimeError(f"v165 {scene} resume authority differs")
        launches[scene] = {
            "run_id": run_id,
            "output": output,
            "barrier": barrier,
            **raw,
        }
    campaign.mkdir(parents=True, mode=0o700)
    processes: dict[str, subprocess.Popen] = {}
    started = time.monotonic()
    for scene, launch in launches.items():
        launch["barrier"].mkdir(parents=True, mode=0o700)
        log_path = campaign / f"{launch['suffix']}.controller.log"
        log = log_path.open("w")
        environment = os.environ.copy()
        environment["MYSQL_TWO_WORKER_AUTHORITY_ROOT"] = str(authority)
        command = [
            sys.executable, str(CONTROLLER), "--run-id", launch["run_id"],
            "--checkpoint", str(launch["checkpoint"]),
            "--runtime-preflight", str(preflight),
            "--prepared-binding", str(prepared),
            "--benchmark-accounts", str(accounts),
            "--fresh-r0", str(FRESH_R0),
            "--replay-quarantine", str(launch["quarantine"]),
            "--scene", scene,
            "--scene-start-barrier", str(launch["barrier"]),
        ]
        process = subprocess.Popen(
            command, cwd=ROOT, env=environment, stdout=log,
            stderr=subprocess.STDOUT, start_new_session=True,
            preexec_fn=lambda value=launch["affinity"]: os.sched_setaffinity(
                0, cpus(value)
            ),
        )
        log.close()
        processes[scene] = process
        launch["log"] = log_path
    manifest = {
        "schema_version": "mysql_algorithm_c_v165_job_xpio_resume_launch_v1",
        "release_version": "v165-plan-capture-timeout-boundary-resume",
        "status": "RUNNING",
        "created_at": datetime.now().astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "resumed_scenes": list(launches),
        "completed_outer_attempts_preserved": 2,
        "discarded_incomplete_outer": 3,
        "discarded_incomplete_outer_credit": 0,
        "plan_capture_timeout_seconds": 900,
        "plan_capture_outside_formal_measurement_wall_time": True,
        "plan_capture_read_only": True,
        "contract": str(CONTRACT), "contract_sha256": sha(CONTRACT),
        "authority_root": str(authority),
        "preflight_sha256": sha(preflight),
        "started_offset_seconds": time.monotonic() - started,
        "sibling_scene_wait": False,
        "shared_nvme_mutual_exclusion": False,
        "postgresql_process_control_or_stop_performed": False,
        "launches": {
            scene: {
                "run_id": launch["run_id"],
                "pid": processes[scene].pid,
                "controller_cpuset": launch["affinity"],
                "log": str(launch["log"]),
                "checkpoint": str(launch["checkpoint"]),
                "checkpoint_sha256": sha(launch["checkpoint"]),
                "quarantine": str(launch["quarantine"]),
                "quarantine_sha256": sha(launch["quarantine"]),
                "failed_state": str(launch["failed_state"]),
                "failed_state_sha256": sha(launch["failed_state"]),
            }
            for scene, launch in launches.items()
        },
    }
    atomic(campaign / "launch_manifest.json", manifest)
    print(json.dumps({
        "status": "RUNNING", "campaign": str(campaign),
        "pids": {scene: process.pid for scene, process in processes.items()},
        "run_ids": {scene: launch["run_id"] for scene, launch in launches.items()},
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
