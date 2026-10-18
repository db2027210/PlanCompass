#!/usr/bin/env python3
"""Monitor memory-scene completion and launch/track fresh XP at 4+4."""

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
HISTORY = ROOT / "history/mysql_four_scene_20260809"
LOG_ROOT = HISTORY / "formal_launch_logs"
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
EXECUTOR = ROOT / "scripts/execute_mysql_algorithm_a_job_to_xp_four_plus_four_fresh_v1.py"
CONTRACT = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_xp_four_plus_four_20260823_v1"
    / "transition_contract.json"
)
RESULT = LOG_ROOT / "v57_to_a_xp_four_plus_four_20260823.transition.json"
EXECUTOR_UNIT = "mysql-alga-xp-four-plus-four-transition-v1.service"
MANAGEMENT_CPUSET = "240-241,496-497"
TPCH_STATE = (
    HISTORY / "v54_to_a_storage_compatible_20260823_attempt001_tpch"
    / "algorithm_a_state.json"
)
JOB_STATES = (
    HISTORY / "v55_to_a_storage_compatible_job4_20260823_attempt001_job"
    / "algorithm_a_state.json",
    HISTORY / "v54_to_a_storage_compatible_20260823_attempt001_job"
    / "algorithm_a_state.json",
)
RUNS = {
    "hybench_xp_cpu": "v57_to_a_xp_four_plus_four_fresh_20260823_attempt001_xp_cpu",
    "hybench_xp_io": "v57_to_a_xp_four_plus_four_fresh_20260823_attempt001_xp_io",
}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"regular JSON required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
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
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def strict_complete(state: Mapping[str, Any], scene: str) -> bool:
    return bool(
        state.get("algorithm") == "A"
        and state.get("async_scene") == scene
        and state.get("formal_scope") == "mysql_only"
        and state.get("status") == "COMPLETE"
        and state.get("stage") == "COMPLETE"
        and state.get("converged") is True
        and int(state.get("accepted") or 0) == 10
        and state.get("round_checkpoint") is None
    )


def current_job() -> tuple[Path, dict[str, Any]]:
    for path in JOB_STATES:
        if path.is_file() and not path.is_symlink():
            state = load(path)
            if state.get("status") in {"RUNNING", "COMPLETE"}:
                return path, state
    raise RuntimeError("current JOB Algorithm A state is absent")


def validate_contract() -> None:
    contract = load(CONTRACT)
    if (
        contract.get("schema_version")
        != "mysql_algorithm_a_xp_four_plus_four_contract_v1"
        or contract.get("status") != "FROZEN"
        or contract.get("xp_parallelism")
        != {"hybench_xp_cpu": 4, "hybench_xp_io": 4, "total": 8}
        or contract.get("xp_scenes_measure_concurrently") is not True
    ):
        raise RuntimeError("XP 4+4 monitor contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(binding.get("path") or "")
        if path.is_symlink() or not path.is_file() or sha(path) != binding.get("sha256"):
            raise RuntimeError(f"XP 4+4 monitor source binding differs: {path}")


def launch_executor() -> dict[str, Any]:
    stdout = LOG_ROOT / "v57_to_a_xp_four_plus_four_20260823.transition.stdout.log"
    stderr = LOG_ROOT / "v57_to_a_xp_four_plus_four_20260823.transition.stderr.log"
    command = [
        "systemd-run", "--user", f"--unit={EXECUTOR_UNIT.removesuffix('.service')}",
        "--description=MySQL Algorithm A memory-to-XP fresh 4+4 transition",
        f"--property=WorkingDirectory={ROOT}", "--property=KillMode=control-group",
        f"--property=StandardOutput=append:{stdout}",
        f"--property=StandardError=append:{stderr}",
        "/usr/bin/taskset", "-c", MANAGEMENT_CPUSET,
        str(PYTHON), str(EXECUTOR), "--result", str(RESULT),
    ]
    started = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30)
    if started.returncode != 0:
        raise RuntimeError(f"XP 4+4 executor launch failed: {started.stderr[-2000:]}")
    return {
        "unit": EXECUTOR_UNIT, "stdout": str(stdout), "stderr": str(stderr),
        "launched_at": now(),
    }


def run(output: Path, poll_seconds: float) -> int:
    validate_contract()
    executor_launch: dict[str, Any] | None = None
    while True:
        job_path, job = current_job()
        tpch = load(TPCH_STATE)
        memory_done = strict_complete(job, "job_memory") and strict_complete(
            tpch, "tpch_sf4_memory",
        )
        if memory_done and not RESULT.is_file() and executor_launch is None:
            executor_launch = launch_executor()
        transition = load(RESULT) if RESULT.is_file() else {}
        scenes: dict[str, Any] = {}
        all_complete = transition.get("status") == "LAUNCHED"
        for scene, run_id in RUNS.items():
            state_path = HISTORY / run_id / "algorithm_a_state.json"
            state = load(state_path) if state_path.is_file() else {}
            terminal = strict_complete(state, scene)
            all_complete = all_complete and terminal
            checkpoint = state.get("round_checkpoint") or {}
            scenes[scene] = {
                "run_id": run_id, "state_path": str(state_path),
                "status": state.get("status"), "stage": state.get("stage"),
                "accepted": int(state.get("accepted") or 0),
                "round_index": int(state.get("round_index") or 0),
                "current_round_attempted": len(checkpoint.get("attempted_ranks") or []),
                "current_round_valid": len(checkpoint.get("valid_ranks") or []),
                "current_round_technical": len(checkpoint.get("technical_ranks") or []),
                "physical_replay_parallelism": int(state.get("physical_replay_parallelism") or 0),
                "terminal": terminal,
            }
        if transition.get("status") == "FAILED_CLOSED":
            status = "FAILED_CLOSED"
        elif all_complete:
            status = "COMPLETE"
        elif transition.get("status") == "LAUNCHED":
            status = "XP_RUNNING_4_PLUS_4"
        elif memory_done:
            status = "TRANSITIONING_TO_XP_4_PLUS_4"
        else:
            status = "MONITORING_MEMORY_SCENES"
        document = {
            "schema_version": "mysql_algorithm_a_job_to_xp_four_plus_four_monitor_v1",
            "status": status, "updated_at": now(), "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "contract_path": str(CONTRACT), "contract_sha256": sha(CONTRACT),
            "memory_scenes": {
                "tpch_sf4_memory": {
                    "state_path": str(TPCH_STATE), "status": tpch.get("status"),
                    "stage": tpch.get("stage"), "accepted": int(tpch.get("accepted") or 0),
                    "complete": strict_complete(tpch, "tpch_sf4_memory"),
                },
                "job_memory": {
                    "state_path": str(job_path), "status": job.get("status"),
                    "stage": job.get("stage"), "accepted": int(job.get("accepted") or 0),
                    "complete": strict_complete(job, "job_memory"),
                },
            },
            "transition_result": str(RESULT),
            "transition_status": transition.get("status"),
            "transition_stage": transition.get("stage"),
            "transition_error": transition.get("error"),
            "executor_launch": executor_launch,
            "xp_scenes": scenes,
            "policy": {
                "fresh_xp_runs_required": True,
                "historical_accepted_rounds_reused": False,
                "xp_parallelism": {"hybench_xp_cpu": 4, "hybench_xp_io": 4, "total": 8},
                "xp_scenes_measure_concurrently": True,
                "post_convergence_stability_required": False,
            },
        }
        atomic(output, document)
        if status in {"COMPLETE", "FAILED_CLOSED"}:
            return 0 if status == "COMPLETE" else 2
        time.sleep(poll_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    args = parser.parse_args()
    if not 5 <= args.poll_seconds <= 300:
        parser.error("poll seconds must be in [5, 300]")
    return run(args.output.expanduser().resolve(), args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
