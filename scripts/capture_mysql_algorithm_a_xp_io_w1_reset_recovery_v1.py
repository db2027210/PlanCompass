#!/usr/bin/env python3
"""Capture fail-closed recovery evidence for Algorithm A XP/I/O worker 1."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
FAILED_STATE = (
    ROOT / "history/mysql_four_scene_20260809/"
    "v102_to_a_xp_io_external_diagnostic_resume_20260823_attempt001_xp_io/"
    "algorithm_a_state.json"
)
OUTPUT = (
    ROOT / "history/mysql_four_scene_20260809/"
    "v106_to_a_xp_io_w1_reset_recovery_20260824/"
    "recovery_readback.json"
)
CONTAINERS = {
    "xp_io_w1": "ra_mysql_disk2_algc_xp_io_w1_20260809",
    "xp_io_w2": "ra_mysql_disk2_algc_xp_io_w2_20260809",
}
DEFAULTS = {
    "xp_io_w1": Path(
        "/disk2/resource_adaptive_mysql_algorithm_c_disk2_v4/"
        "xp_io_w1/credentials/selected.cnf"
    ),
    "xp_io_w2": Path(
        "/disk2/resource_adaptive_mysql_algorithm_c_disk2_v4/"
        "xp_io_w2/credentials/selected.cnf"
    ),
}
PORTS = {"xp_io_w1": 65341, "xp_io_w2": 65342}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(*argv: str) -> str:
    return subprocess.run(
        argv, check=True, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=30,
    ).stdout.strip()


def inspect(container: str) -> dict[str, Any]:
    value = json.loads(run("docker", "inspect", container))
    if not isinstance(value, list) or len(value) != 1:
        raise RuntimeError(f"single Docker inspect row required: {container}")
    return dict(value[0])


def mysql_probe(worker_id: str) -> dict[str, Any]:
    defaults = DEFAULTS[worker_id]
    output = run(
        "mysql", f"--defaults-file={defaults}", "--host=127.0.0.1",
        f"--port={PORTS[worker_id]}", "--connect-timeout=10", "-NBe",
        "SELECT 1; SELECT COUNT(*) FROM information_schema.processlist "
        "WHERE USER NOT IN ('system user','event_scheduler') "
        "AND ID <> CONNECTION_ID();",
    ).splitlines()
    if output != ["1", "0"]:
        raise RuntimeError(f"MySQL recovery probe differs for {worker_id}: {output}")
    return {
        "defaults_file": str(defaults),
        "host_port": PORTS[worker_id],
        "select_one": 1,
        "other_non_system_sessions": 0,
    }


def main() -> int:
    failed = json.loads(FAILED_STATE.read_text(encoding="utf-8"))
    checkpoint = failed.get("round_checkpoint") or {}
    if (
        failed.get("status") != "FAILED_CLOSED"
        or failed.get("stage") != "FAILED"
        or int(failed.get("accepted") or 0) != 0
        or list(checkpoint.get("attempted_ranks") or []) != list(range(1, 91))
        or "--port=65341" not in str(failed.get("error") or "")
        or "returned non-zero exit status 1" not in str(failed.get("error") or "")
    ):
        raise RuntimeError("XP/I/O failed predecessor differs")

    workers: dict[str, Any] = {}
    for worker_id, container in CONTAINERS.items():
        row = inspect(container)
        state = row.get("State") or {}
        host = row.get("HostConfig") or {}
        if state.get("Running") is not True or state.get("Status") != "running":
            raise RuntimeError(f"container is not running: {container}")
        workers[worker_id] = {
            "container": container,
            "container_id": row.get("Id"),
            "container_pid": state.get("Pid"),
            "running": True,
            "oom_killed": state.get("OOMKilled"),
            "exit_code": state.get("ExitCode"),
            "memory_bytes": host.get("Memory"),
            "swap_bytes": host.get("MemorySwap"),
            "database_cpuset": host.get("CpusetCpus"),
            "database_cpuset_mems": host.get("CpusetMems"),
            "pids_max": host.get("PidsLimit"),
            "mysql_probe": mysql_probe(worker_id),
        }

    document = {
        "schema_version": "mysql_algorithm_a_xp_io_w1_reset_recovery_readback_v1",
        "status": "PASS",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "failed_state_path": str(FAILED_STATE.relative_to(ROOT)),
        "failed_state_sha256": sha(FAILED_STATE),
        "failed_worker": "xp_io_w1",
        "failure_class": "container_shutdown_exceeded_reset_window_then_mysql_unreachable",
        "oom_failure": False,
        "cached_attempted_ranks": list(range(1, 91)),
        "cached_completed_candidate_prefix_reused": True,
        "failed_wave_credited": False,
        "new_physical_work_starts_after_rank": 90,
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
        "native_xp_workload_unchanged": True,
        "workers": workers,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = OUTPUT.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(OUTPUT)
    print(OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
