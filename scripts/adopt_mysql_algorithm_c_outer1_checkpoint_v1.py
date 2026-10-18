#!/usr/bin/env python3
"""Seal the completed outer-1 checkpoint after stopping the v14 controller."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260811_v15"
    / "four_scene_effective_resource_successor.json"
)
PREDECESSOR = (
    ROOT / "history/mysql_four_scene_20260809"
    / "four_scene_algorithm_c_v14_v15_20260811T171400p0800"
)
SCENES = {"job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io"}
XP_IDS = {"xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2"}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,128}", args.run_id) is None:
        parser.error("unsafe run-id")
    output = ROOT / "history/mysql_four_scene_20260809" / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh checkpoint output exists: {output}")

    state_path = PREDECESSOR / "run_state.json"
    state = load(state_path)
    pid = int(state.get("pid") or 0)
    if (
        state.get("status") != "RUNNING"
        or state.get("stage") != "OUTER_002_PLAN_PHYSICS_RANKING"
        or state.get("outer_attempt") != 1
        or pid <= 1
        or Path(f"/proc/{pid}").exists()
        or state.get("accepted_rounds") != {
            "job_memory": 1,
            "tpch_sf4_memory": 0,
            "hybench_xp_cpu": 0,
            "hybench_xp_io": 1,
        }
        or any((state.get("converged") or {}).values())
    ):
        raise RuntimeError("stopped outer-1 checkpoint authority differs")

    batch_paths = [
        PREDECESSOR / f"outer_001/top5_batch_{index}/batch_result.json"
        for index in (1, 2, 3)
    ]
    if any(load(path).get("status") != "PASS" for path in batch_paths):
        raise RuntimeError("outer-1 completed batch authority differs")
    decision_paths = {
        scene: PREDECESSOR / f"outer_001/{scene}.decision.json" for scene in SCENES
    }
    decisions = {scene: load(path) for scene, path in decision_paths.items()}
    if (
        set(decisions) != SCENES
        or any(row.get("outer_attempt") != 1 for row in decisions.values())
        or {scene: bool(row.get("accepted")) for scene, row in decisions.items()}
        != {
            "job_memory": True,
            "tpch_sf4_memory": False,
            "hybench_xp_cpu": False,
            "hybench_xp_io": True,
        }
    ):
        raise RuntimeError("outer-1 decision authority differs")

    reset_path = PREDECESSOR / "xp_resets/outer_001_batch_3/reset_binding.json"
    reset = load(reset_path)
    workers = reset.get("workers") or []
    if (
        reset.get("status") != "PASS"
        or reset.get("freeze_sha256") != sha(FREEZE)
        or {row.get("worker_id") for row in workers} != XP_IDS
        or any(row.get("ready") is not True for row in workers)
    ):
        raise RuntimeError("outer-1 terminal XP reset authority differs")

    output.mkdir(parents=True, mode=0o700)
    document = {
        "schema_version": "mysql_algorithm_c_outer1_checkpoint_v1",
        "status": "PREPARED",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_path": str(FREEZE),
        "freeze_sha256": sha(FREEZE),
        "predecessor_run_path": str(PREDECESSOR),
        "predecessor_state_path": str(state_path),
        "predecessor_state_sha256": sha(state_path),
        "predecessor_pid": pid,
        "predecessor_pid_absent": True,
        "completed_outer_attempts": 1,
        "accepted_rounds": state["accepted_rounds"],
        "converged": state["converged"],
        "batch_result_paths": [str(path) for path in batch_paths],
        "batch_result_sha256": {str(path): sha(path) for path in batch_paths},
        "decision_paths": {scene: str(path) for scene, path in decision_paths.items()},
        "decision_sha256": {scene: sha(path) for scene, path in decision_paths.items()},
        "terminal_xp_reset_path": str(reset_path),
        "terminal_xp_reset_sha256": sha(reset_path),
        "workers": workers,
        "workload_started": False,
        "fresh_r0_complete": False,
        "formal_outer_started": False,
    }
    binding = output / "prepared_binding.json"
    binding.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    binding.with_name(binding.name + ".sha256").write_text(
        f"{sha(binding)}  {binding.name}\n", encoding="ascii"
    )
    print(json.dumps({"status": "PREPARED", "binding": str(binding)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
