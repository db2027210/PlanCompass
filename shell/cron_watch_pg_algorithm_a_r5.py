#!/usr/bin/env python3
"""Fast, process-independent watchdog for the three unfinished Algorithm A lanes."""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Dict, Iterable, Tuple


PROJECT_ROOT = Path("/home/anonymous/program/resource_adaptive")
CAMPAIGN_ROOT = Path(
    "/disk1/resource_adaptive/pg_algorithm_a_four_full_factorial_20260816/"
    "20260816_algorithm_a_full_factorial_globalrank_labels_clean_r5"
)
SUPERVISOR = PROJECT_ROOT / "shell/supervise_pg_algorithm_a_r5_lane.sh"
LOCK_PATH = Path("/tmp/resource_adaptive_pg_algorithm_a_r5_cron_watch_v3.lock")
WATCHDOG_LOG = CAMPAIGN_ROOT / "cron_supervisor.log"
LANES: Tuple[Tuple[str, str], ...] = (
    ("job", "job_memory"),
    ("tpch", "tpch_memory"),
    ("xp_cpu", "xp_cpu"),
)
TERMINAL_PHASES = {
    "complete",
    "algorithm_a_nonconvergence_clean",
    "algorithm_a_technical_failure_stopped_clean",
    "all_champions_semantic_stability_failed_clean",
    "clean_restore_technical_failure",
}


def process_command_lines() -> Iterable[str]:
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
            continue
        if raw:
            yield raw.replace(b"\0", b" ").decode("utf-8", errors="replace")


def lane_running(command_lines: Iterable[str], lane: str, state_dir: str) -> bool:
    supervisor_pattern = f"{SUPERVISOR} {lane}"
    config_pattern = (
        "run_pg_algorithm_a_scene.py --config "
        f"{CAMPAIGN_ROOT}/{state_dir}/optimizer_config.json"
    )
    return any(
        supervisor_pattern in command or config_pattern in command
        for command in command_lines
    )


def append_watchdog_log(payload: Dict[str, object]) -> None:
    WATCHDOG_LOG.parent.mkdir(parents=True, exist_ok=True)
    with WATCHDOG_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def main() -> int:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+", encoding="utf-8") as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        commands = tuple(process_command_lines())
        for lane, state_dir in LANES:
            pause_path = CAMPAIGN_ROOT / state_dir / ".algorithm_a_watchdog_pause"
            if pause_path.is_file():
                continue
            state_path = CAMPAIGN_ROOT / state_dir / "algorithm_a_state.json"
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
                append_watchdog_log(
                    {
                        "event": "cron_state_read_failed",
                        "lane": lane,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "time": time.time(),
                    }
                )
                continue
            phase = str(state.get("phase") or "missing")
            accepted = int(state.get("accepted") or 0)
            target = int(state.get("accepted_target") or 7)
            if phase in TERMINAL_PHASES or (
                phase == "complete" and accepted >= target
            ):
                continue
            if lane_running(commands, lane, state_dir):
                continue
            bootstrap_path = CAMPAIGN_ROOT / state_dir / "cron_bootstrap.log"
            bootstrap_path.parent.mkdir(parents=True, exist_ok=True)
            with bootstrap_path.open("a", encoding="utf-8") as output:
                process = subprocess.Popen(
                    [str(SUPERVISOR), lane],
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    close_fds=True,
                    start_new_session=True,
                )
            append_watchdog_log(
                {
                    "event": "cron_supervisor_start",
                    "watchdog_contract": "pg_algorithm_a_r5_python_watchdog_v1",
                    "lane": lane,
                    "state_dir": state_dir,
                    "phase": phase,
                    "accepted": accepted,
                    "accepted_target": target,
                    "pid": process.pid,
                    "time": time.time(),
                }
            )
            commands = (*commands, f"{SUPERVISOR} {lane}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
