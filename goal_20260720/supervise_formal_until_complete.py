#!/usr/bin/env python3
"""Supervise the Codex formal experiment path until it is no longer in flight.

Keeps the status monitor and B6 recovery watcher alive, refreshes durable status
JSON, and exits only when:
  - B5 remains accepted (already done), and
  - no unfinished B6 formal batch remains, and
  - no formal batch/grid runner is still executing, and
  - B6 is either acceptance-met OR recovery supersedes are exhausted / final
    non-timeout failure is recorded.

Does not rewrite the double-loop framework.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
GOAL = ROOT / "goal_20260720"
PYTHON = Path("/home/anonymous/miniconda3/envs/resource_adaptive/bin/python")
MONITOR = GOAL / "update_goal_monitor_status.py"
WATCHER = GOAL / "watch_b6_recovery.py"
STATUS_OUT = GOAL / "supervise_until_complete_status.json"
COMPLETE_OUT = GOAL / "experiment_complete.json"
PID_DIR = GOAL / "supervisor_pids"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def cmdline(pid: int) -> str:
    try:
        return (Path("/proc") / str(pid) / "cmdline").read_bytes().replace(b"\0", b" ").decode(
            errors="replace"
        )
    except OSError:
        return ""


def find_pids(needle: str) -> list[int]:
    """Return PIDs whose cmdline is a real python process executing *needle*."""
    found: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        cmd = cmdline(int(entry.name))
        if needle not in cmd:
            continue
        if "supervise_formal_until_complete.py" in cmd:
            continue
        # Require an actual interpreter argv head — never match shells that
        # only embed the path inside a longer script string.
        head = cmd.lstrip()
        if not (head.startswith("python") or "/python" in head.split(" ", 1)[0]):
            continue
        found.append(int(entry.name))
    return sorted(set(found))


def ensure_background(script: Path, args: list[str], log_name: str, pid_key: str) -> int:
    """Start script if no live python process already runs it; return pid."""
    existing = []
    for pid in find_pids(script.name):
        cmd = cmdline(pid)
        if str(script) in cmd or script.name in cmd:
            if "python" in cmd:
                existing.append(pid)
    if existing:
        return existing[0]
    PID_DIR.mkdir(parents=True, exist_ok=True)
    log = GOAL / log_name
    proc = subprocess.Popen(
        [str(PYTHON), str(script), *args],
        cwd=str(ROOT),
        stdout=log.open("a", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    (PID_DIR / f"{pid_key}.pid").write_text(f"{proc.pid}\n", encoding="utf-8")
    return proc.pid


def formal_runners_live() -> list[dict[str, Any]]:
    rows = []
    for needle in ("run_fixed_protocol_batch.py", "mysql_double_loop_grid_runner.py"):
        for pid in find_pids(needle):
            cmd = cmdline(pid)
            if needle in cmd and "python" in cmd:
                rows.append({"pid": pid, "cmd": cmd[:300]})
    return rows


def unfinished_b6_batches() -> list[Path]:
    root = GOAL / "formal_batches"
    out = []
    for path in sorted(root.glob("B6_*")):
        if path.is_dir() and not (path / "batch_summary.json").is_file():
            out.append(path)
    return out


def latest_b6_batch() -> Path | None:
    root = GOAL / "formal_batches"
    batches = sorted(
        [p for p in root.glob("B6_*") if p.is_dir()],
        key=lambda p: p.stat().st_mtime,
    )
    return batches[-1] if batches else None


def b6_acceptance(batch: Path | None) -> dict[str, Any]:
    if batch is None:
        return {"acceptance_met": False, "reason": "no_b6_batch"}
    audit = load_json(batch / "acceptance_audit.json")
    if isinstance(audit, dict) and "acceptance_met" in audit:
        return {
            "acceptance_met": bool(audit.get("acceptance_met")),
            "passed_attempts": audit.get("passed_attempts"),
            "batch": str(batch),
            "source": "acceptance_audit.json",
        }
    summary = load_json(batch / "batch_summary.json")
    if isinstance(summary, dict):
        return {
            "acceptance_met": bool(summary.get("majority_passed")),
            "passed_attempts": summary.get("passed_attempts"),
            "batch": str(batch),
            "source": "batch_summary.json",
        }
    return {"acceptance_met": False, "reason": "batch_incomplete", "batch": str(batch)}


def b5_accepted() -> bool:
    audit = load_json(
        GOAL / "formal_batches/B5_memory_calibrated_n3_20260720/acceptance_audit.json"
    )
    return bool(isinstance(audit, dict) and audit.get("acceptance_met") is True)


def refresh_monitor() -> dict[str, Any]:
    subprocess.run([str(PYTHON), str(MONITOR)], cwd=str(ROOT), check=False, capture_output=True)
    return load_json(GOAL / "goal_monitor_status.json") or {}


def completion_reached(state: dict[str, Any]) -> bool:
    if not state.get("b5_accepted"):
        return False
    if state.get("unfinished_b6"):
        return False
    if state.get("formal_runners"):
        return False
    # finished runners + finished batches: complete either accept or honest fail
    if state.get("b6_acceptance", {}).get("acceptance_met"):
        return True
    latest = state.get("latest_b6_batch")
    if latest and Path(latest).joinpath("batch_summary.json").is_file():
        # recovery watcher may still supersede; only complete if watcher not
        # about to launch and no unfinished dir. If watcher is up but batch
        # finished without acceptance, wait one grace of max_idle_finished_polls.
        return bool(state.get("allow_complete_on_b6_fail"))
    return False


def snapshot() -> dict[str, Any]:
    unfinished = unfinished_b6_batches()
    latest = latest_b6_batch()
    runners = formal_runners_live()
    mon = refresh_monitor()
    acc = b6_acceptance(latest)
    return {
        "updated_at": utc_now(),
        "b5_accepted": b5_accepted(),
        "latest_b6_batch": str(latest) if latest else None,
        "unfinished_b6": [str(p) for p in unfinished],
        "formal_runners": runners,
        "b6_acceptance": acc,
        "monitor_liveness": mon.get("liveness"),
        "monitor_active": mon.get("active"),
        "monitor_pids": {
            "status_loop": find_pids("update_goal_monitor_status.py"),
            "b6_watcher": find_pids("watch_b6_recovery.py"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--poll-s", type=float, default=180.0)
    parser.add_argument(
        "--fail-complete-after-polls",
        type=int,
        default=3,
        help="After B6 batch finished without acceptance and no new supersede starts, "
        "declare complete-fail after this many idle polls (watcher has max_supersedes).",
    )
    args = parser.parse_args()

    monitor_pid = ensure_background(
        MONITOR, ["--loop", "--interval-s", "60"], "goal_monitor_loop.log", "monitor_loop"
    )
    watcher_pid = ensure_background(
        WATCHER, ["--poll-s", "120"], "watch_b6_recovery.log", "watch_b6"
    )
    idle_finished = 0
    last_unfinished: list[str] = []

    while True:
        state = snapshot()
        state["supervisor"] = {
            "monitor_ensured_pid": monitor_pid,
            "watcher_ensured_pid": watcher_pid,
            "idle_finished_polls": idle_finished,
        }
        unfinished = state["unfinished_b6"]
        runners = state["formal_runners"]
        finished_no_accept = (
            not unfinished
            and not runners
            and state["latest_b6_batch"]
            and Path(state["latest_b6_batch"]).joinpath("batch_summary.json").is_file()
            and not state["b6_acceptance"].get("acceptance_met")
        )
        if finished_no_accept:
            # if a supersede just launched, unfinished will reappear next poll
            if unfinished == last_unfinished:
                idle_finished += 1
            else:
                idle_finished = 0
        else:
            idle_finished = 0
        last_unfinished = list(unfinished)
        state["allow_complete_on_b6_fail"] = idle_finished >= args.fail_complete_after_polls
        state["supervisor"]["idle_finished_polls"] = idle_finished
        write_json(STATUS_OUT, state)
        print(
            json.dumps(
                {
                    "ts": state["updated_at"],
                    "b5": state["b5_accepted"],
                    "b6_accept": state["b6_acceptance"].get("acceptance_met"),
                    "unfinished": state["unfinished_b6"],
                    "runners": len(runners),
                    "idle_finished": idle_finished,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

        if completion_reached(state):
            result = {
                "completed_at": utc_now(),
                "b5_accepted": state["b5_accepted"],
                "b6_accepted": bool(state["b6_acceptance"].get("acceptance_met")),
                "latest_b6_batch": state["latest_b6_batch"],
                "b6_acceptance": state["b6_acceptance"],
                "outcome": (
                    "success"
                    if state["b6_acceptance"].get("acceptance_met")
                    else "finished_without_b6_acceptance"
                ),
                "remaining_matrix_not_in_this_formal_path": [
                    "A13-A18", "E13-E18", "B4"
                ],
                "note": (
                    "Formal recovery path supervision ended. Full A/E matrix was not "
                    "part of the active fixed-protocol launch set."
                ),
            }
            write_json(COMPLETE_OUT, result)
            state["complete"] = result
            write_json(STATUS_OUT, state)
            print(json.dumps({"event": "experiment_path_complete", **result}, ensure_ascii=False), flush=True)
            return 0 if result["b6_accepted"] else 2

        time.sleep(max(30.0, float(args.poll_s)))


if __name__ == "__main__":
    raise SystemExit(main())
