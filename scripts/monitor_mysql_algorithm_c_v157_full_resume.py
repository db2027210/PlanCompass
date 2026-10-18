#!/usr/bin/env python3
"""Record v157 scene progress until every controller reaches a terminal state."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import time


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
TERMINAL = {"COMPLETE", "NON_CONVERGED", "FAILED_CLOSED"}


def load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def process_state(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/stat").read_text().split()[2]
    except (FileNotFoundError, IndexError, PermissionError):
        return "ABSENT"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--interval-seconds", type=float, default=60.0)
    args = parser.parse_args()
    campaign = args.campaign.expanduser().resolve()
    manifest_path = campaign / "launch_manifest.json"
    manifest = load(manifest_path)
    if (
        manifest.get("schema_version") != "mysql_algorithm_c_v157_full_resume_launch_v1"
        or manifest.get("status") != "RUNNING"
        or manifest.get("full_search_runs_until_runtime_terminal_condition") is not True
    ):
        raise RuntimeError("v157 running launch manifest required")
    launches = dict(manifest.get("launches") or {})
    if len(launches) != 4:
        raise RuntimeError("four v157 scene launches required")
    jsonl = campaign / "completion_monitor.jsonl"
    status_path = campaign / "completion_monitor_status.json"
    first_resumed_outer_at = None
    sample_index = 0
    while True:
        sample_index += 1
        scenes = {}
        for scene, launch in launches.items():
            run_root = HISTORY / str(launch["run_id"])
            state_path = run_root / "run_state.json"
            state = load(state_path)
            pid_state = process_state(int(launch["pid"]))
            scenes[scene] = {
                "pid": int(launch["pid"]),
                "process_state": pid_state,
                "controller_live": pid_state not in {"ABSENT", "Z"},
                "run_state_present": bool(state),
                "status": state.get("status"),
                "stage": state.get("stage"),
                "outer_attempt": state.get("outer_attempt"),
                "accepted_rounds": (state.get("accepted_rounds") or {}).get(scene),
                "rejected_outer_attempts": (
                    state.get("rejected_outer_attempts") or {}
                ).get(scene),
                "converged": (state.get("converged") or {}).get(scene),
                "error": state.get("error"),
                "run_state_path": str(state_path),
            }
        all_resumed_outer_complete = all(
            int(row.get("outer_attempt") or 0) >= 2 for row in scenes.values()
        )
        if all_resumed_outer_complete and first_resumed_outer_at is None:
            first_resumed_outer_at = datetime.now().astimezone().isoformat()
        all_terminal = all(row.get("status") in TERMINAL for row in scenes.values())
        unexpected_dead = [
            scene for scene, row in scenes.items()
            if not row["controller_live"] and row.get("status") not in TERMINAL
        ]
        sample = {
            "schema_version": "mysql_algorithm_c_v157_completion_monitor_sample_v1",
            "sample_index": sample_index,
            "captured_at": datetime.now().astimezone().isoformat(),
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "all_four_scenes_completed_first_resumed_outer": all_resumed_outer_complete,
            "first_resumed_outer_completed_at": first_resumed_outer_at,
            "all_scenes_terminal": all_terminal,
            "unexpected_dead_scenes": unexpected_dead,
            "scenes": scenes,
        }
        with jsonl.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(sample, sort_keys=True) + "\n")
        summary = {
            **sample,
            "schema_version": "mysql_algorithm_c_v157_completion_monitor_status_v1",
            "monitor_status": (
                "FAILED_CLOSED" if unexpected_dead else
                "COMPLETE" if all_terminal else "RUNNING"
            ),
            "monitor_pid": os.getpid(),
            "sample_log": str(jsonl),
        }
        atomic(status_path, summary)
        if unexpected_dead:
            return 4
        if all_terminal:
            return 0 if all(
                row.get("status") == "COMPLETE" for row in scenes.values()
            ) else 3
        time.sleep(max(10.0, args.interval_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
