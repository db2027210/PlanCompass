#!/usr/bin/env python3
"""Low-frequency, read-only progress monitor for the v203 C campaign."""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import time


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
OUTPUT = HISTORY / "c_v203_pg_complete_recovery_four_scene_20260929"
LOG = OUTPUT / "completion_monitor.jsonl"
SUMMARY = OUTPUT / "completion_monitor_summary.json"
INTERVAL_SECONDS = 300

SCENES = {
    "job_memory": {
        "pid": 105768,
        "state": HISTORY
        / "c_v203_pg_complete_recovery_four_scene_20260929_gate_retry3_job"
        / "run_state.json",
    },
    "tpch_sf4_memory": {
        "pid": 96475,
        "state": HISTORY
        / "c_v203_pg_complete_recovery_four_scene_20260929_tpch"
        / "run_state.json",
    },
    "hybench_xp_cpu": {
        "pid": 96476,
        "state": HISTORY
        / "c_v203_pg_complete_recovery_four_scene_20260929_xp_cpu"
        / "run_state.json",
    },
    "hybench_xp_io": {
        "pid": 96477,
        "state": HISTORY
        / "c_v203_pg_complete_recovery_four_scene_20260929_xp_io"
        / "run_state.json",
    },
}
TERMINAL = {"COMPLETE", "NON_CONVERGED", "FAILED_CLOSED", "PILOT_COMPLETE", "PILOT_FAILED_CLOSED"}


def now() -> str:
    return datetime.now().astimezone().isoformat()


def atomic(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def snapshot() -> dict:
    scenes = {}
    for scene, specification in SCENES.items():
        path = specification["state"]
        state = json.loads(path.read_text()) if path.is_file() else {}
        pid = int(specification["pid"])
        process_alive = Path(f"/proc/{pid}").exists()
        scenes[scene] = {
            "pid": pid,
            "process_alive": process_alive,
            "state_path": str(path),
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
        "at_least_one_complete_outer_round": any(
            row["outer_attempt"] >= 1 for row in scenes.values()
        ),
        "all_terminal": all(row["status"] in TERMINAL for row in scenes.values()),
        "all_complete": all(row["status"] == "COMPLETE" for row in scenes.values()),
    }


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
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
