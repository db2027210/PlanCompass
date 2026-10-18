#!/usr/bin/env python3
"""Recover only XP/CPU worker 2 after the interrupted wave-45 reset."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts import mysql_async_xp_post_reset_settle_v2 as reset_adapter


FAILED = ROOT / "history/mysql_four_scene_20260809/v133_to_a_xp_cpu_w1_reset_external_diagnostic_resume_20260824_attempt001_xp_cpu/algorithm_a_state.json"
FREEZE = ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260813_v18/four_scene_effective_resource_successor.json"
PREPARED = ROOT / "history/mysql_four_scene_20260809/v29_prelaunch_xp_recovery_20260819_attempt001/prepared_binding.json"
CURRENT_W1 = ROOT / "history/mysql_four_scene_20260809/v133_to_a_xp_cpu_w1_reset_external_diagnostic_resume_20260824_attempt001_xp_cpu/round_003/wave_045/xp_resets/v133_to_a_xp_cpu_w1_reset_external_diagnostic_resume_20260824_attempt001_xp_cpu_round_003_wave_045_attempt_01/xp_cpu_w1/reset_worker.json"
OUTPUT = ROOT / "history/mysql_four_scene_20260809/v144_to_a_xp_cpu_w2_reset_recovery_20260824"
READBACK = OUTPUT / "recovery_readback.json"
TOKEN = "xp_cpu_w2_interrupted_wave45_reset_recovery_v1"
TARGET = "xp_cpu_w2"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"regular evidence required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def docker_row(container: str) -> dict[str, Any]:
    result = subprocess.run(
        ["docker", "inspect", container], check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
    )
    rows = json.loads(result.stdout)
    if not isinstance(rows, list) or len(rows) != 1:
        raise RuntimeError(f"single Docker row required: {container}")
    return dict(rows[0])


def mysql_probe(worker: Mapping[str, Any]) -> dict[str, Any]:
    defaults = str(worker.get("credential_defaults") or "")
    port = int(worker.get("host_port") or 0)
    result = subprocess.run(
        [
            "mysql", f"--defaults-file={defaults}", "--host=127.0.0.1",
            f"--port={port}", "--connect-timeout=10", "-NBe",
            "SELECT 1; SELECT COUNT(*) FROM information_schema.processlist "
            "WHERE USER NOT IN ('system user','event_scheduler') "
            "AND ID <> CONNECTION_ID();",
        ],
        check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=30,
    )
    values = result.stdout.splitlines()
    if values != ["1", "0"]:
        raise RuntimeError(f"MySQL recovery probe differs: {worker['worker_id']}")
    return {"select_one": 1, "other_non_system_sessions": 0}


def main() -> int:
    failed, freeze, prepared, current_w1 = (
        load(FAILED), load(FREEZE), load(PREPARED), load(CURRENT_W1)
    )
    checkpoint = failed.get("round_checkpoint") or {}
    if (
        failed.get("status") != "FAILED_CLOSED"
        or failed.get("stage") != "FAILED"
        or int(failed.get("accepted") or 0) != 2
        or int(failed.get("round_index") or 0) != 3
        or list(checkpoint.get("attempted_ranks") or []) != list(range(1, 89))
        or list(checkpoint.get("valid_ranks") or []) != list(range(1, 89))
        or list(checkpoint.get("technical_ranks") or [])
        or "--port=65332" not in str(failed.get("error") or "")
        or str((failed.get("authority") or {}).get("resource_freeze_sha256"))
        != sha(FREEZE)
        or prepared.get("freeze_sha256") != sha(FREEZE)
        or current_w1.get("worker_id") != "xp_cpu_w1"
        or current_w1.get("ready") is not True
    ):
        raise RuntimeError("XP/CPU w2 recovery authority differs")
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise FileExistsError(f"fresh recovery output required: {OUTPUT}")

    frozen = {str(row["worker_id"]): dict(row) for row in freeze["workers"]}
    prior = {str(row["worker_id"]): dict(row) for row in prepared["workers"]}
    workers = {
        "xp_cpu_w1": {**frozen["xp_cpu_w1"], **current_w1},
        "xp_cpu_w2": {**frozen["xp_cpu_w2"], **prior["xp_cpu_w2"]},
    }
    before_w1 = docker_row(str(workers["xp_cpu_w1"]["container"]))
    before_w2 = docker_row(str(workers["xp_cpu_w2"]["container"]))
    if (
        (before_w1.get("State") or {}).get("Running") is not True
        or (before_w2.get("State") or {}).get("Running") is not True
    ):
        raise RuntimeError("expected w1 and recovery-started w2 running before reset")

    reset_adapter.reset_xp(
        freeze, workers, OUTPUT, TOKEN, worker_ids={TARGET},
    )
    stage = OUTPUT / "xp_resets" / TOKEN
    binding = load(stage / "reset_binding.json")
    if (
        binding.get("status") != "PASS"
        or binding.get("reset_worker_ids") != [TARGET]
        or (load(stage / "post_reset_storage_settle.json")).get("status")
        != "PASS"
    ):
        raise RuntimeError("XP/CPU w2 reset evidence differs")

    evidence_workers: dict[str, Any] = {}
    for worker_id in ("xp_cpu_w1", "xp_cpu_w2"):
        worker = workers[worker_id]
        inspected = docker_row(str(worker["container"]))
        state = inspected.get("State") or {}
        if state.get("Running") is not True or state.get("Status") != "running":
            raise RuntimeError(f"worker is not running after recovery: {worker_id}")
        evidence_workers[worker_id] = {
            "worker_id": worker_id,
            "container": worker["container"],
            "container_id": inspected.get("Id"),
            "container_pid": state.get("Pid"),
            "running": True,
            "oom_killed": state.get("OOMKilled"),
            "exit_code": state.get("ExitCode"),
            "database_cpuset": (inspected.get("HostConfig") or {}).get("CpusetCpus"),
            "database_cpuset_mems": (inspected.get("HostConfig") or {}).get("CpusetMems"),
            "memory_bytes": (inspected.get("HostConfig") or {}).get("Memory"),
            "mysql_probe": mysql_probe(worker),
        }

    atomic(READBACK, {
        "schema_version": "mysql_algorithm_a_xp_cpu_w2_reset_recovery_readback_v1",
        "status": "PASS",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "failed_state_path": str(FAILED.relative_to(ROOT)),
        "failed_state_sha256": sha(FAILED),
        "failed_worker": TARGET,
        "failure_class": "interrupted_scene_reset_left_worker_stopped",
        "cached_attempted_ranks": list(range(1, 89)),
        "cached_completed_candidate_prefix_reused": True,
        "completed_candidate_measurements_replayed": False,
        "failed_wave_credited": False,
        "new_physical_work_starts_after_rank": 88,
        "reset_worker_ids": [TARGET],
        "companion_worker_data_plane_reset": False,
        "accepted_state_mutation": False,
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
        "native_xp_workload_unchanged": True,
        "reset_binding_path": str((stage / "reset_binding.json").relative_to(ROOT)),
        "reset_binding_sha256": sha(stage / "reset_binding.json"),
        "workers": evidence_workers,
    })
    print(json.dumps({"status": "PASS", "readback": str(READBACK), "sha256": sha(READBACK)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
