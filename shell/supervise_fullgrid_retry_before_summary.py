#!/usr/bin/env python3
"""Restart one full-grid tuner when a new retryable validation failure is logged.

The tuner keeps JSONL append-only.  A resume treats the newest
``(round, candidate_index)`` attempt as authoritative and excludes retryable
``workload_failed`` rows from completed signatures.  Restarting immediately
after such a row therefore replays that Cartesian point before the tuner can
contract the interval and write a round summary.

This supervisor owns only the child command passed after ``--``.  It never
searches for, signals, or modifies unrelated processes.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import os
import signal
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import IO, Any, Dict, List, Optional


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--target", choices=["cpu", "memory", "io", "none"])
    parser.add_argument("--poll-seconds", type=float, default=0.05)
    parser.add_argument("--max-restarts", type=int, default=32)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if not args.command:
        parser.error("child command is required after --")
    if args.poll_seconds <= 0:
        parser.error("--poll-seconds must be positive")
    if args.max_restarts < 1:
        parser.error("--max-restarts must be positive")
    if bool(args.config) != bool(args.target):
        parser.error("--config and --target must be provided together")
    return args


def retryable_failure(row: Dict[str, Any]) -> bool:
    if row.get("event") != "candidate_validation":
        return False
    if str(row.get("status") or "").strip().lower() in {"", "ok"}:
        return False
    reason = str(
        row.get("accept_reject_reason")
        or row.get("reject_reason")
        or row.get("accept_reason")
        or ""
    ).strip().lower()
    return reason == "workload_failed"


def read_complete_line(handle: IO[str]) -> Optional[str]:
    position = handle.tell()
    raw = handle.readline()
    if not raw:
        return None
    if not raw.endswith("\n"):
        handle.seek(position)
        return None
    return raw


def wait_after_interrupt(child: subprocess.Popen[Any]) -> int:
    started = time.monotonic()
    next_report = started + 60.0
    while True:
        try:
            return child.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            now = time.monotonic()
            if now >= next_report:
                print(
                    f"waiting for interrupted tuner pid={child.pid} to finish its in-flight child "
                    f"elapsed_s={now - started:.1f}",
                    flush=True,
                )
                next_report = now + 60.0


def install_retry_rewind_if_needed(
    *,
    log_path: Path,
    config_path: Path,
    target: str,
    failure_row: Dict[str, Any],
    restart_count: int,
) -> Optional[str]:
    """Refresh a rewind when retrying a round that already has an old summary."""
    failure_round = int(failure_row.get("round") or 0)
    candidate_index = int(failure_row.get("candidate_index") or 0)
    ranges = failure_row.get("ranges")
    if failure_round <= 0 or not isinstance(ranges, dict) or not ranges:
        raise RuntimeError(
            "retryable failure is missing its round or exact Cartesian ranges: "
            f"round={failure_round} candidate_index={candidate_index}"
        )

    summary_rounds: List[int] = []
    with log_path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("event") == "round_summary":
                summary_rounds.append(int(row.get("round") or 0))
    max_summary_round = max(summary_rounds, default=0)
    if max_summary_round < failure_round:
        return None

    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    tuning = cfg.get("binary_plan_tuning")
    if not isinstance(tuning, dict):
        raise RuntimeError("binary_plan_tuning config is missing")
    search = tuning.get("binary_search")
    if not isinstance(search, dict):
        raise RuntimeError("binary_search config is missing")
    overrides = search.setdefault("resume_range_overrides", {})
    if not isinstance(overrides, dict):
        raise RuntimeError("resume_range_overrides is not an object")

    previous = overrides.get(target)
    previous_id = str(previous.get("id") or "") if isinstance(previous, dict) else ""
    override_id = (
        f"retry_before_summary_{target}_r{failure_round}_c{candidate_index}_"
        f"n{restart_count}_{time.time_ns()}"
    )
    overrides[target] = {
        "id": override_id,
        "reason": "retry_authoritative_workload_failure_before_cartesian_round_summary",
        "supersedes_override_id": previous_id,
        "apply_after_completed_round": max_summary_round,
        "resume_from_round": failure_round,
        "rebaseline": False,
        "ranges": copy.deepcopy(ranges),
    }

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup = config_path.with_name(f"{config_path.name}.before_{override_id}_{stamp}.bak")
    shutil.copy2(config_path, backup)
    temporary = config_path.with_name(f".{config_path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, config_path)
    return override_id


def supervise(
    log_path: Path,
    command: List[str],
    poll_seconds: float,
    max_restarts: int,
    *,
    config_path: Optional[Path] = None,
    target: Optional[str] = None,
) -> int:
    restart_count = 0
    active_child: Optional[subprocess.Popen[Any]] = None
    stop_requested = False

    def forward_stop(signum: int, _frame: Any) -> None:
        nonlocal stop_requested
        stop_requested = True
        child = active_child
        if child is not None and child.poll() is None:
            child.send_signal(signal.SIGINT)

    signal.signal(signal.SIGTERM, forward_stop)
    signal.signal(signal.SIGINT, forward_stop)

    while True:
        if stop_requested:
            return 130
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.touch(exist_ok=True)
        offset = log_path.stat().st_size
        print(
            f"starting supervised tuner restart_count={restart_count} log_offset={offset} "
            f"command={command}",
            flush=True,
        )
        active_child = subprocess.Popen(command)
        interrupted_for_failure = False
        failure_row: Optional[Dict[str, Any]] = None

        with log_path.open("r", encoding="utf-8") as handle:
            handle.seek(offset)
            while active_child.poll() is None and not stop_requested:
                raw = read_complete_line(handle)
                if raw is None:
                    time.sleep(poll_seconds)
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict) or not retryable_failure(row):
                    continue
                interrupted_for_failure = True
                failure_row = row
                print(
                    "retryable validation failure observed; interrupting owned tuner before summary "
                    f"pid={active_child.pid} round={row.get('round')} "
                    f"candidate_index={row.get('candidate_index')} error={row.get('error')}",
                    flush=True,
                )
                active_child.send_signal(signal.SIGINT)
                break

        if active_child.poll() is None:
            return_code = wait_after_interrupt(active_child)
        else:
            return_code = int(active_child.returncode or 0)
        print(
            f"supervised tuner exited pid={active_child.pid} rc={return_code} "
            f"interrupted_for_failure={interrupted_for_failure}",
            flush=True,
        )
        active_child = None

        if stop_requested:
            return 130
        if not interrupted_for_failure:
            return return_code
        restart_count += 1
        if restart_count > max_restarts:
            print(
                f"retry restart limit exceeded: {restart_count}>{max_restarts}",
                file=sys.stderr,
                flush=True,
            )
            return 9
        if config_path is not None and target is not None and failure_row is not None:
            override_id = install_retry_rewind_if_needed(
                log_path=log_path,
                config_path=config_path,
                target=target,
                failure_row=failure_row,
                restart_count=restart_count,
            )
            if override_id:
                print(
                    "refreshed retry rewind for an already-summarized round "
                    f"target={target} override_id={override_id}",
                    flush=True,
                )
        time.sleep(3.0)


def main() -> int:
    args = parse_args()
    return supervise(
        args.log.resolve(),
        [str(value) for value in args.command],
        float(args.poll_seconds),
        int(args.max_restarts),
        config_path=args.config.resolve() if args.config else None,
        target=str(args.target) if args.target else None,
    )


if __name__ == "__main__":
    raise SystemExit(main())
