#!/usr/bin/env python3
"""Wait read-only for 15 isolated seconds, then launch a partial Algorithm C run."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_co_resident_pg_isolation_v3 as isolation
from scripts import mysql_cross_project_wave_coordination_v1 as coordination
from scripts.preflight_mysql_four_scene_live_v8 import host_pressure


PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
LAUNCHER = ROOT / "scripts/launch_mysql_algorithm_c_boundary_resume_v4.py"
HISTORY = ROOT / "history/mysql_four_scene_20260809"


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def append(path: Path, event: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


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


def regular_file(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file() or path.is_symlink():
        raise argparse.ArgumentTypeError(f"regular file required: {path}")
    return path


def regular_directory(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_dir() or path.is_symlink():
        raise argparse.ArgumentTypeError(f"regular directory required: {path}")
    return path


def main() -> int:
    isolation.install()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--checkpoint", type=regular_file, required=True)
    parser.add_argument("--runtime-preflight", type=regular_file, required=True)
    parser.add_argument("--prepared-binding", type=regular_file, required=True)
    parser.add_argument("--benchmark-accounts", type=regular_file, required=True)
    parser.add_argument("--fresh-r0", type=regular_file, required=True)
    parser.add_argument("--replay-quarantine", type=regular_file, required=True)
    parser.add_argument("--formal-partial-root", type=regular_directory, required=True)
    parser.add_argument("--formal-start-rank", type=int, required=True)
    parser.add_argument("--required-clean-seconds", type=float, default=15.0)
    parser.add_argument("--maximum-wait-seconds", type=float, default=28800.0)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--launch-result", type=Path, required=True)
    args = parser.parse_args()
    if (
        re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.run_id) is None
        or args.formal_start_rank not in range(2, 6)
        or args.required_clean_seconds < 15.0
        or args.maximum_wait_seconds < args.required_clean_seconds
        or args.poll_seconds < 0.2
    ):
        parser.error("safe run id, rank 2..5, and valid wait bounds required")
    if (HISTORY / args.run_id).exists():
        parser.error("fresh run-id required")
    log = args.log.expanduser().resolve()
    result_path = args.launch_result.expanduser().resolve()
    if log.exists() or result_path.exists():
        parser.error("fresh wait log and launch result required")

    started = time.monotonic()
    clean_since: float | None = None
    last_summary = 0.0
    d_state_streaks: dict[int, int] = {}
    append(log, {
        "event": "formal_partial_wait_started",
        "captured_at": now(),
        "run_id": args.run_id,
        "formal_partial_root": str(args.formal_partial_root),
        "formal_start_rank": args.formal_start_rank,
        "required_clean_seconds": args.required_clean_seconds,
        "maximum_wait_seconds": args.maximum_wait_seconds,
        "external_process_mutation": False,
        "docker_mutation": False,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
    })
    while True:
        current = time.monotonic()
        try:
            inventory = coordination.external_process_inventory(args.run_id, {0, 1})
            workloads = coordination.sanitized_rows(inventory["workloads"])
            evaluated = coordination.evaluate_co_resident_pg_isolation(
                inventory["workloads"]
            )
            pressure = host_pressure()
            current_d_state = {
                int(pid) for pid in pressure["blocking_d_state_pids"]
            }
            d_state_streaks = {
                pid: d_state_streaks.get(pid, 0) + 1 for pid in current_d_state
            }
            persistent_d_state = sorted(
                pid for pid, streak in d_state_streaks.items() if streak >= 3
            )
            blockers: list[str] = []
            if workloads and evaluated.get("passed") is not True:
                blockers.append("external_database_physical_workload_not_isolated")
            if persistent_d_state:
                blockers.append("persistent_blocking_d_state_present")
        except Exception as error:
            workloads = []
            evaluated = {"passed": False, "status": "PROBE_FAILED"}
            persistent_d_state = []
            blockers = ["authority_probe_failed"]
            append(log, {
                "event": "formal_partial_wait_probe_failed",
                "captured_at": now(),
                "error": f"{type(error).__name__}: {error}",
                "action": "sample_rejected_and_retry",
            })
        if blockers:
            clean_since = None
        elif clean_since is None:
            clean_since = current
        clean_seconds = 0.0 if clean_since is None else current - clean_since
        if current - last_summary >= 30.0 or clean_seconds >= args.required_clean_seconds:
            append(log, {
                "event": "formal_partial_wait_sample",
                "captured_at": now(),
                "blockers": blockers,
                "external_workload_count": len(workloads),
                "co_resident_pg_status": evaluated.get("status"),
                "co_resident_pg_passed": evaluated.get("passed"),
                "persistent_d_state_pids": persistent_d_state,
                "clean_seconds": clean_seconds,
            })
            last_summary = current
        if clean_seconds >= args.required_clean_seconds:
            break
        if current - started >= args.maximum_wait_seconds:
            atomic(result_path, {
                "status": "TIMED_OUT",
                "captured_at": now(),
                "waited_seconds": current - started,
                "external_process_mutation": False,
                "docker_mutation": False,
            })
            return 3
        time.sleep(args.poll_seconds)

    command = [
        str(PYTHON), str(LAUNCHER),
        "--run-id", args.run_id,
        "--checkpoint", str(args.checkpoint),
        "--runtime-preflight", str(args.runtime_preflight),
        "--prepared-binding", str(args.prepared_binding),
        "--benchmark-accounts", str(args.benchmark_accounts),
        "--fresh-r0", str(args.fresh_r0),
        "--replay-quarantine", str(args.replay_quarantine),
        "--formal-partial-root", str(args.formal_partial_root),
        "--formal-start-rank", str(args.formal_start_rank),
    ]
    completed = subprocess.run(
        command, cwd=ROOT, text=True, capture_output=True,
        check=False, timeout=180,
    )
    append(log, {
        "event": "formal_partial_launch_attempt",
        "captured_at": now(),
        "returncode": completed.returncode,
        "stdout_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
        "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest(),
    })
    if completed.returncode != 0:
        atomic(result_path, {
            "status": "LAUNCH_FAILED",
            "captured_at": now(),
            "returncode": completed.returncode,
            "external_process_mutation": False,
            "docker_mutation": False,
        })
        return 4
    receipt = json.loads(completed.stdout)
    launch_receipt = HISTORY / args.run_id / "launch_receipt.json"
    result = {
        "attempt": 8,
        "captured_at": now(),
        "cross_engine_comparison_prohibited": True,
        "formal_scope": "mysql_only",
        "launch_receipt_path": str(launch_receipt),
        "run_id": args.run_id,
        "run_state_path": receipt["run_state_path"],
        "status": "STARTED",
        "systemd_unit": receipt["systemd_unit"],
        "waiter_source_path": str(Path(__file__).resolve()),
        "waiter_source_sha256": sha(Path(__file__).resolve()),
        "external_process_mutation": False,
        "docker_mutation": False,
    }
    atomic(result_path, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

