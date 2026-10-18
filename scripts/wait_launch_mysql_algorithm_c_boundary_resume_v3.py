#!/usr/bin/env python3
"""Wait for a formal clean window, then launch a fresh full-boundary C run."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_co_resident_pg_isolation_v2 as isolation
from scripts import mysql_cross_project_wave_coordination_v1 as coordination
from scripts.preflight_mysql_four_scene_live_v8 import host_pressure


PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
LAUNCHER = ROOT / "scripts/launch_mysql_algorithm_c_boundary_resume_v3.py"
HISTORY = ROOT / "history/mysql_four_scene_20260809"


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def append(path: Path, event: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def regular(path: str | Path) -> Path:
    result = Path(path).expanduser().resolve()
    if not result.is_file() or result.is_symlink():
        raise argparse.ArgumentTypeError(f"regular non-symlink file required: {result}")
    return result


def main() -> int:
    isolation.install()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--checkpoint", type=regular, required=True)
    parser.add_argument("--runtime-preflight", type=regular, required=True)
    parser.add_argument("--prepared-binding", type=regular, required=True)
    parser.add_argument("--benchmark-accounts", type=regular, required=True)
    parser.add_argument("--fresh-r0", type=regular, required=True)
    parser.add_argument("--replay-quarantine", type=regular, required=True)
    parser.add_argument("--required-clean-seconds", type=float, default=15.0)
    parser.add_argument("--maximum-wait-seconds", type=float, default=28800.0)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()
    if args.required_clean_seconds < 15 or args.maximum_wait_seconds < 15:
        parser.error("formal wait bounds differ")
    if (HISTORY / args.run_id).exists():
        parser.error("fresh run-id required")
    log = args.log.expanduser().resolve()
    if log.exists():
        parser.error("fresh wait log required")

    started = time.monotonic()
    clean_since: float | None = None
    last_summary = 0.0
    append(log, {
        "event": "formal_full_boundary_wait_started",
        "captured_at": now(),
        "run_id": args.run_id,
        "required_clean_seconds": args.required_clean_seconds,
        "maximum_wait_seconds": args.maximum_wait_seconds,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
    })
    while True:
        current = time.monotonic()
        try:
            inventory = coordination.external_process_inventory(args.run_id, {0, 1})
            workloads = coordination.sanitized_rows(inventory["workloads"])
            workload_isolation = coordination.evaluate_co_resident_pg_isolation(
                inventory["workloads"]
            )
            pressure = host_pressure()
        except Exception as error:
            clean_since = None
            append(log, {
                "event": "formal_full_boundary_wait_probe_failed",
                "captured_at": now(),
                "error": f"{type(error).__name__}: {error}",
                "action": "sample_rejected_and_retry",
            })
            if current - started >= args.maximum_wait_seconds:
                append(log, {
                    "event": "formal_full_boundary_wait_timeout",
                    "captured_at": now(),
                    "elapsed_seconds": current - started,
                })
                return 3
            time.sleep(max(0.2, args.poll_seconds))
            continue
        normalized_load1 = float(pressure["load1"]) / max(1, os.cpu_count() or 1)
        blockers: list[str] = []
        if workloads and workload_isolation["passed"] is not True:
            blockers.append("external_database_physical_workload_not_isolated")
        if pressure["blocking_d_state_pids"]:
            blockers.append("blocking_user_or_unknown_d_state_present")
        if (not workloads and float(pressure["load1"]) > 16.0) or normalized_load1 > 0.10:
            blockers.append("host_load1_exceeds_limit")
        if blockers:
            clean_since = None
        elif clean_since is None:
            clean_since = current
        clean_seconds = 0.0 if clean_since is None else current - clean_since
        if current - last_summary >= 30.0 or clean_seconds >= args.required_clean_seconds:
            append(log, {
                "event": "formal_full_boundary_wait_sample",
                "captured_at": now(),
                "blockers": blockers,
                "external_workload_count": len(workloads),
                "co_resident_pg_isolation": workload_isolation,
                "blocking_d_state_pids": pressure["blocking_d_state_pids"],
                "load1": pressure["load1"],
                "normalized_load1": normalized_load1,
                "clean_seconds": clean_seconds,
            })
            last_summary = current
        if clean_seconds >= args.required_clean_seconds:
            break
        if current - started >= args.maximum_wait_seconds:
            append(log, {
                "event": "formal_full_boundary_wait_timeout",
                "captured_at": now(),
                "elapsed_seconds": current - started,
            })
            return 3
        time.sleep(max(0.2, args.poll_seconds))

    append(log, {
        "event": "formal_full_boundary_exec",
        "captured_at": now(),
        "clean_seconds": time.monotonic() - clean_since,
    })
    command = [
        str(PYTHON), str(LAUNCHER),
        "--run-id", args.run_id,
        "--checkpoint", str(args.checkpoint),
        "--runtime-preflight", str(args.runtime_preflight),
        "--prepared-binding", str(args.prepared_binding),
        "--benchmark-accounts", str(args.benchmark_accounts),
        "--fresh-r0", str(args.fresh_r0),
        "--replay-quarantine", str(args.replay_quarantine),
    ]
    os.execv(command[0], command)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
