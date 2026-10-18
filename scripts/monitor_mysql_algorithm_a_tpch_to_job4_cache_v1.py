#!/usr/bin/env python3
"""Trigger cache-fixed JOB 2-to-4 handoff after TPC-H A converges."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
HISTORY = ROOT / "history/mysql_four_scene_20260809"
TPCH_STATE = (
    HISTORY / "v41_to_a_tpch_cache_fixed_20260823_attempt003_tpch"
    / "algorithm_a_state.json"
)
JOB_STATE = (
    HISTORY / "v41_to_a_job_cache_fixed_20260823_attempt001_job"
    / "algorithm_a_state.json"
)
EXECUTOR = ROOT / "scripts/execute_mysql_algorithm_a_job_cache_slot_transition_v1.py"


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular state required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"state is not an object: {path}")
    return dict(value)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def summary(state: Mapping[str, Any]) -> dict[str, Any]:
    checkpoint = state.get("round_checkpoint") or {}
    return {
        "status": state.get("status"), "stage": state.get("stage"),
        "accepted": int(state.get("accepted") or 0),
        "round_index": int(state.get("round_index") or 0),
        "converged": bool(state.get("converged")),
        "current_round_attempted": len(checkpoint.get("attempted_ranks") or []),
        "physical_replay_parallelism": int(
            state.get("physical_replay_parallelism") or 0
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--handoff-result", type=Path, required=True)
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    parser.add_argument("--timeout-seconds", type=float, default=172800.0)
    args = parser.parse_args()
    if args.poll_seconds < 5 or args.timeout_seconds < 3600:
        parser.error("invalid monitor bounds")
    started = time.monotonic()
    while time.monotonic() - started < args.timeout_seconds:
        tpch = load(TPCH_STATE)
        job = load(JOB_STATE)
        document = {
            "schema_version": "mysql_algorithm_a_tpch_to_job4_cache_monitor_v1",
            "status": "MONITORING", "updated_at": now(),
            "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
            "policy": {
                "trigger": "tpch_accepted_10_converged",
                "job_transition_at_complete_round_boundary": True,
                "job_parallelism_before": 2, "job_parallelism_after": 4,
                "cache_precondition_retained": True,
                "historical_accepted_rounds_reused": False,
            },
            "tpch_state": str(TPCH_STATE), "tpch": summary(tpch),
            "job_state": str(JOB_STATE), "job": summary(job),
            "executor": str(EXECUTOR), "executor_sha256": sha(EXECUTOR),
            "handoff_result": str(args.handoff_result.resolve()),
        }
        if tpch.get("status") == "FAILED_CLOSED":
            document["status"] = "FAILED_CLOSED_TPCH"
            atomic(args.output.resolve(), document)
            return 1
        tpch_complete = (
            tpch.get("algorithm") == "A"
            and tpch.get("async_scene") == "tpch_sf4_memory"
            and tpch.get("status") == "COMPLETE"
            and tpch.get("stage") == "COMPLETE"
            and tpch.get("converged") is True
            and int(tpch.get("accepted") or 0) == 10
        )
        job_complete = (
            job.get("status") == "COMPLETE" and job.get("converged") is True
            and int(job.get("accepted") or 0) == 10
        )
        if tpch_complete and job_complete:
            document["status"] = "JOB_ALREADY_COMPLETE_NO_FOUR_WORKER_TRANSITION"
            atomic(args.output.resolve(), document)
            return 0
        if tpch_complete:
            document["status"] = "INVOKING_JOB_FOUR_WORKER_TRANSITION"
            atomic(args.output.resolve(), document)
            completed = subprocess.run(
                [str(PYTHON), str(EXECUTOR), "--result",
                 str(args.handoff_result.resolve()), "--timeout-seconds", "86400"],
                cwd=ROOT, text=True, capture_output=True, check=False,
                timeout=90000,
            )
            document.update({
                "status": (
                    "JOB_FOUR_WORKER_TRANSITION_LAUNCHED"
                    if completed.returncode == 0 else "FAILED_CLOSED_TRANSITION"
                ),
                "updated_at": now(), "executor_returncode": completed.returncode,
                "executor_stdout_tail": completed.stdout[-4000:],
                "executor_stderr_tail": completed.stderr[-4000:],
            })
            atomic(args.output.resolve(), document)
            return completed.returncode
        atomic(args.output.resolve(), document)
        time.sleep(args.poll_seconds)
    atomic(args.output.resolve(), {
        "schema_version": "mysql_algorithm_a_tpch_to_job4_cache_monitor_v1",
        "status": "FAILED_CLOSED_TIMEOUT", "updated_at": now(),
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "timeout_seconds": args.timeout_seconds,
    })
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
