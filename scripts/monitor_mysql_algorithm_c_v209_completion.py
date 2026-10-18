#!/usr/bin/env python3
"""Low-frequency, read-only progress monitor for a v209 campaign."""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
RUN_PREFIX = sys.argv[1] if len(sys.argv) > 1 else "c_v209_pg_startup_isolation_four_scene_20260929"
OUTPUT = HISTORY / RUN_PREFIX
MANIFEST = OUTPUT / "launch_manifest.json"
LOG = OUTPUT / "completion_monitor.jsonl"
SUMMARY = OUTPUT / "completion_monitor_summary.json"
INTERVAL_SECONDS = 300
TERMINAL = {"COMPLETE", "NON_CONVERGED", "FAILED_CLOSED", "PILOT_COMPLETE", "PILOT_FAILED_CLOSED"}


def now() -> str:
    return datetime.now().astimezone().isoformat()


def atomic(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def snapshot() -> dict:
    manifest = json.loads(MANIFEST.read_text())
    scenes = {}
    for scene, launch in manifest["launches"].items():
        state_path = HISTORY / launch["run_id"] / "run_state.json"
        state = json.loads(state_path.read_text()) if state_path.is_file() else {}
        pid = int(launch["pid"])
        scenes[scene] = {
            "pid": pid,
            "process_alive": Path(f"/proc/{pid}").exists(),
            "state_path": str(state_path),
            "state_present": bool(state),
            "status": state.get("status"),
            "stage": state.get("stage"),
            "outer_attempt": int(state.get("outer_attempt") or 0),
            "accepted_rounds": (state.get("accepted_rounds") or {}).get(scene),
            "rejected_outer_attempts": (state.get("rejected_outer_attempts") or {}).get(scene),
            "consecutive_failed_rounds": (state.get("consecutive_failed_rounds") or {}).get(scene),
            "converged": (state.get("converged") or {}).get(scene),
            "error": state.get("error"),
        }
    return {
        "captured_at": now(),
        "monitor_pid": os.getpid(),
        "interval_seconds": INTERVAL_SECONDS,
        "read_only": True,
        "process_control_performed": False,
        "postgresql_process_control_or_stop_performed": False,
        "scenes": scenes,
        "at_least_one_complete_outer_round": any(row["outer_attempt"] >= 1 for row in scenes.values()),
        "all_terminal": all(row["status"] in TERMINAL for row in scenes.values()),
        "all_complete": all(row["status"] == "COMPLETE" for row in scenes.values()),
    }


def main() -> int:
    first_round_at = None
    while True:
        row = snapshot()
        if row["at_least_one_complete_outer_round"] and first_round_at is None:
            first_round_at = row["captured_at"]
        row["first_complete_outer_round_observed_at"] = first_round_at
        with LOG.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        atomic(SUMMARY, row)
        if row["all_terminal"]:
            return 0 if row["all_complete"] else 2
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())

