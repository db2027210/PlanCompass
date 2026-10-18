#!/usr/bin/env python3
"""Continuously move failed formal full-grid lanes under safe retry supervision."""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Tuple

from resume_failed_fullgrid_lane import (
    LANES,
    Lane,
    authoritative_retryable_failures,
    exact_tuner_pids,
    resume_lane,
    supervised_process_exists,
)


DEFAULT_STATE_LOG = (
    Path(__file__).resolve().parents[1]
    / "history/fullgrid_matrix_failure_supervisor_20260718.jsonl"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=15.0)
    parser.add_argument("--max-restarts", type=int, default=32)
    parser.add_argument("--stop-timeout-s", type=int, default=2100)
    parser.add_argument("--state-log", type=Path, default=DEFAULT_STATE_LOG)
    args = parser.parse_args()
    if args.watch == args.once:
        parser.error("choose exactly one of --watch or --once")
    if args.poll_seconds < 1:
        parser.error("--poll-seconds must be at least 1")
    return args


def emit(path: Path, event: str, **fields: object) -> None:
    payload = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "event": event,
        **fields,
    }
    line = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    print(line, flush=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def inspect_once(*, state_log: Path, max_restarts: int, stop_timeout_s: int) -> Dict[str, str]:
    states: Dict[str, str] = {}
    takeovers: List[Tuple[str, Lane, int, Dict[str, object]]] = []
    for lane_id, lane in sorted(LANES.items()):
        if not lane.log.is_file():
            states[lane_id] = "log_missing"
            continue
        failures = authoritative_retryable_failures(lane.log)
        if not failures:
            states[lane_id] = "no_new_retryable_failure"
            continue
        if supervised_process_exists(lane):
            states[lane_id] = "already_supervised"
            continue
        pids = exact_tuner_pids(lane)
        if len(pids) != 1:
            states[lane_id] = f"failure_waiting_for_exact_tuner_count_{len(pids)}"
            continue
        first = failures[0]
        emit(
            state_log,
            "lane_takeover_start",
            lane_id=lane_id,
            tuner_pid=pids[0],
            failure_round=first.get("round"),
            candidate_index=first.get("candidate_index"),
            failure_count=len(failures),
        )
        takeovers.append((lane_id, lane, pids[0], first))

    if takeovers:
        with ThreadPoolExecutor(max_workers=len(takeovers)) as executor:
            futures = {
                executor.submit(
                    resume_lane,
                    lane,
                    max_restarts=max_restarts,
                    stop_timeout_s=stop_timeout_s,
                ): lane_id
                for lane_id, lane, _, _ in takeovers
            }
            for future in as_completed(futures):
                lane_id = futures[future]
                try:
                    future.result()
                except (Exception, SystemExit) as exc:
                    states[lane_id] = f"takeover_error:{type(exc).__name__}:{exc}"
                    emit(
                        state_log,
                        "lane_takeover_error",
                        lane_id=lane_id,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                else:
                    states[lane_id] = "takeover_complete"
                    emit(state_log, "lane_takeover_complete", lane_id=lane_id)
    return states


def main() -> int:
    args = parse_args()
    state_log = args.state_log.resolve()
    previous: Dict[str, str] = {}
    while True:
        try:
            states = inspect_once(
                state_log=state_log,
                max_restarts=int(args.max_restarts),
                stop_timeout_s=int(args.stop_timeout_s),
            )
        except Exception as exc:
            emit(state_log, "watch_error", error=f"{type(exc).__name__}: {exc}")
            states = {}
        for lane_id, state in states.items():
            if previous.get(lane_id) != state:
                emit(state_log, "lane_state", lane_id=lane_id, state=state)
        previous = states
        if args.once:
            return 0
        time.sleep(float(args.poll_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
