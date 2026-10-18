#!/usr/bin/env python3
"""Continuously monitor Algorithm A and trigger the audited XP slot handoff."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_EXECUTOR = (
    ROOT / "scripts/execute_mysql_algorithm_a_xp_slot_transition_v2.py"
)
A_CONTRACT = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_async_20260822_v3"
    / "algorithm_a_contract.json"
)
SCENES = (
    "job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io",
)
MEMORY_SCENES = ("job_memory", "tpch_sf4_memory")
XP_SCENES = ("hybench_xp_cpu", "hybench_xp_io")

from scripts import mysql_algorithm_a_dynamic_slots_v2 as slots


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


def state_paths(
    manifest: Mapping[str, Any], handoff: Mapping[str, Any] | None,
) -> dict[str, Path]:
    paths = {
        scene: Path(str((manifest.get("scenes") or {})[scene]["state"]))
        for scene in SCENES
    }
    if handoff and handoff.get("status") in {"LAUNCHED", "COMPLETE"}:
        successor = handoff.get("successor_state_paths") or {}
        for scene in XP_SCENES:
            if successor.get(scene):
                paths[scene] = Path(str(successor[scene]))
    return paths


def strict_complete(state: Mapping[str, Any], scene: str) -> bool:
    return slots.converged_terminal(state, scene)


def strict_terminal(state: Mapping[str, Any], scene: str) -> bool:
    return slots.algorithm_a_terminal(state, scene)


def summarize(path: Path, scene: str) -> dict[str, Any]:
    state = load(path)
    checkpoint = state.get("round_checkpoint") or {}
    complete = strict_complete(state, scene)
    terminal = strict_terminal(state, scene)
    return {
        "state_path": str(path.resolve()),
        "state_sha256": sha(path),
        "status": state.get("status"),
        "stage": state.get("stage"),
        "accepted": int(state.get("accepted") or 0),
        "accepted_target": int(state.get("accepted_target") or 10),
        "round_index": int(state.get("round_index") or 0),
        "attempted_candidates_in_current_round": len(
            checkpoint.get("attempted_ranks") or []
        ),
        "candidate_count_in_current_round": int(
            checkpoint.get("candidate_count") or 0
        ),
        "physical_replay_parallelism": int(
            state.get("physical_replay_parallelism") or 0
        ),
        "converged": bool(state.get("converged")),
        "complete": complete,
        "terminal": terminal,
        "terminal_outcome": state.get("status") if terminal else None,
        "updated_mtime": path.stat().st_mtime,
    }


def invoke_handoff(
    *, executor: Path, manifest: Path, output: Path, handoff: Path,
) -> dict[str, Any]:
    if executor.is_symlink() or not executor.is_file():
        return {
            "status": "WAITING_FOR_TRANSITION_EXECUTOR",
            "executor": str(executor),
        }
    completed = subprocess.run(
        [
            "/home/anonymous/miniconda3/envs/py313/bin/python", str(executor),
            "--launch-manifest", str(manifest),
            "--monitor-state", str(output),
            "--handoff-result", str(handoff),
        ],
        cwd=ROOT, capture_output=True, text=True, check=False, timeout=14400,
    )
    return {
        "status": "EXECUTOR_RETURNED",
        "executor": str(executor),
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-4000:],
    }


def run(args: argparse.Namespace) -> int:
    manifest_path = args.launch_manifest.resolve()
    output = args.output.resolve()
    handoff_path = args.handoff_result.resolve()
    executor = args.transition_executor.resolve()
    manifest = load(manifest_path)
    if (
        manifest.get("status") != "LAUNCHED"
        or manifest.get("algorithm") != "A"
        or not str(manifest.get("campaign_id") or "")
        or manifest.get("contract_path") != str(A_CONTRACT)
        or manifest.get("contract_sha256") != sha(A_CONTRACT)
        or set(manifest.get("scenes") or {}) != set(SCENES)
    ):
        raise RuntimeError("Algorithm A launch manifest differs")
    lock_path = output.with_name(output.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w", encoding="ascii") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Algorithm A dynamic slot monitor already active") from error
        handoff_attempted = False
        while True:
            handoff = load(handoff_path) if handoff_path.is_file() else None
            paths = state_paths(manifest, handoff)
            summaries = {
                scene: summarize(paths[scene], scene) for scene in SCENES
            }
            memory_finished = all(
                summaries[scene]["complete"] for scene in MEMORY_SCENES
            )
            xp_finished = all(
                summaries[scene]["terminal"] for scene in XP_SCENES
            )
            all_complete = all(row["complete"] for row in summaries.values())
            all_finished = memory_finished and xp_finished
            monitor_status = (
                "COMPLETE" if all_complete else
                "TERMINAL_WITH_NONCONVERGENCE" if all_finished else
                "MONITORING"
            )
            document: dict[str, Any] = {
                "schema_version": "mysql_algorithm_a_dynamic_slot_monitor_v2",
                "status": monitor_status,
                "updated_at": now(),
                "formal_scope": "mysql_only",
                "cross_engine_comparison_prohibited": True,
                "campaign_id": manifest["campaign_id"],
                "launch_manifest": str(manifest_path),
                "launch_manifest_sha256": sha(manifest_path),
                "policy": {
                    "initial_parallelism_per_scene": 2,
                    "trigger_requires_job_and_tpch_complete": True,
                    "memory_terminal_outcomes": [
                        "COMPLETE",
                    ],
                    "xp_cpu_parallelism_after_handoff": 4,
                    "xp_io_parallelism_after_handoff": 4,
                    "handoff_at_complete_round_boundary": True,
                    "mixed_worker_cohort_round_prohibited": True,
                },
                "memory_scenes_complete": memory_finished,
                "memory_scenes_finished": memory_finished,
                "memory_scenes_converged": all(
                    summaries[scene]["complete"] for scene in MEMORY_SCENES
                ),
                "xp_scenes_complete": all(
                    summaries[scene]["complete"] for scene in XP_SCENES
                ),
                "xp_scenes_finished": xp_finished,
                "complete_scene_count": sum(
                    int(row["complete"]) for row in summaries.values()
                ),
                "terminal_scene_count": sum(
                    int(row["terminal"]) for row in summaries.values()
                ),
                "scenes": summaries,
                "handoff_result": str(handoff_path),
                "handoff": handoff,
            }
            atomic(output, document)
            print(json.dumps({
                "time": document["updated_at"],
                "status": document["status"],
                "memory_finished": memory_finished,
                "xp_finished": xp_finished,
                "accepted": {
                    scene: summaries[scene]["accepted"] for scene in SCENES
                },
                "stages": {
                    scene: summaries[scene]["stage"] for scene in SCENES
                },
            }, ensure_ascii=False, sort_keys=True), flush=True)
            if all_finished:
                return 0
            if memory_finished and not xp_finished and handoff is None:
                result = invoke_handoff(
                    executor=executor, manifest=manifest_path,
                    output=output, handoff=handoff_path,
                )
                handoff_attempted = result.get("status") == "EXECUTOR_RETURNED"
                document["last_handoff_invocation"] = result
                atomic(output, document)
                if handoff_attempted and result.get("returncode") != 0:
                    # Keep monitoring.  The executor is idempotent and can be
                    # repaired/retried without touching a completed memory lane.
                    handoff_attempted = False
            time.sleep(args.poll_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--launch-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--handoff-result", type=Path, required=True)
    parser.add_argument("--transition-executor", type=Path, default=DEFAULT_EXECUTOR)
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    args = parser.parse_args()
    if not 5.0 <= args.poll_seconds <= 300.0:
        parser.error("poll seconds must be in [5, 300]")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
