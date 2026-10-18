#!/usr/bin/env python3
"""Module."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path("/home/anonymous/miniconda3/envs/resource_adaptive/bin/python")
THREAD_SENDER = ROOT / "shell/codex_app_server_thread_message.py"
CURRENT_RUN_ROOT = Path(
    "/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/"
    "20260811_algorithm_c_scored_resource_gate_v2_clean_o1"
)
XP_IO_RUN_ROOT = Path(
    "/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/"
    "20260811_algorithm_c_scored_resource_gate_v2_xpio_clean_o2"
)
LANES: Tuple[Tuple[str, Path], ...] = (
    ("JOB", CURRENT_RUN_ROOT / "job_memory"),
    ("TPC-H", CURRENT_RUN_ROOT / "tpch_memory"),
    ("XP-CPU", CURRENT_RUN_ROOT / "xp_cpu"),
    ("XP-I/O", XP_IO_RUN_ROOT / "xp_io"),
)
BAD_PHASE_MARKERS = (
    "technical_failure_stopped",
    "calibration_holdout_budget_exhausted",
    "calibration_expansion_plan_gate_failed",
    "stability_confirmation_failed",
    "normalized_width_threshold_not_reached",
    "candidate_space_exhausted",
)
DEFAULT_MESSAGE = (
    " PostgreSQL  JOBTPC-HXP-CPUXP-I/O "
    " Algorithm C  state workload //"
    "Top-5 accepted/1% /"
    " wave  clean stop"
    " baseline  lane lane"
    " SSH MySQL valid  accepted"
)


@dataclass
class LaneSnapshot:
    """LaneSnapshot."""

    scene: str
    path: str
    pid: Optional[int]
    alive: bool
    calibration_phase: Optional[str]
    calibration_physical: int
    calibration_valid: int
    calibration_technical: int
    formal_phase: Optional[str]
    formal_started: bool
    formal_round: int
    accepted: int
    accepted_target: int
    formal_physical: int
    formal_valid: int
    formal_technical: int
    stable_rounds: int
    required_stable_rounds: int
    anomaly: Optional[str]
    complete: bool


def read_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def read_pid(path: Path) -> Optional[int]:
    try:
        value = int(path.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, OSError, ValueError):
        return None
    return value if value > 0 else None


def first_bad_phase(*phases: Optional[str]) -> Optional[str]:
    for phase in phases:
        normalized = str(phase or "").lower()
        for marker in BAD_PHASE_MARKERS:
            if marker in normalized:
                return f"terminal_phase:{phase}"
    return None


def lane_snapshot(scene: str, lane: Path) -> LaneSnapshot:
    calibration = read_json(lane / "calibration_state.json")
    formal = read_json(lane / "algorithm_c_state.json")
    config = read_json(lane / "optimizer_config.json")
    algorithm = config.get("algorithm_c") if isinstance(config.get("algorithm_c"), dict) else {}
    pid = read_pid(lane / "controller.pid")
    alive = pid is not None and Path("/proc", str(pid)).exists()
    accepted_target = int(
        formal.get("accepted_target")
        or algorithm.get("accepted_target")
        or 7
    )
    required_stable = int(
        formal.get("stability_confirmation_rounds")
        or algorithm.get("stability_confirmation_rounds")
        or 2
    )
    accepted = int(formal.get("accepted") or 0)
    stable = int(formal.get("consecutive_stable_rounds") or 0)
    complete = accepted >= accepted_target and stable >= required_stable
    anomaly = first_bad_phase(
        calibration.get("phase"),
        formal.get("phase"),
    )
    if not alive and not complete:
        anomaly = anomaly or "controller_not_alive"
    if not lane.is_dir():
        anomaly = "lane_directory_missing"
    return LaneSnapshot(
        scene=scene,
        path=str(lane),
        pid=pid,
        alive=alive,
        calibration_phase=calibration.get("phase"),
        calibration_physical=int(calibration.get("physical_workload_invocations") or 0),
        calibration_valid=int(calibration.get("valid_replays") or 0),
        calibration_technical=int(calibration.get("technical_failures") or 0),
        formal_phase=formal.get("phase"),
        formal_started=bool(formal.get("formal_started")),
        formal_round=int(formal.get("round_index") or 0),
        accepted=accepted,
        accepted_target=accepted_target,
        formal_physical=int(formal.get("physical_workload_invocations") or 0),
        formal_valid=int(formal.get("valid_replays") or 0),
        formal_technical=int(formal.get("technical_failures") or 0),
        stable_rounds=stable,
        required_stable_rounds=required_stable,
        anomaly=anomaly,
        complete=complete,
    )


def snapshot() -> List[LaneSnapshot]:
    return [lane_snapshot(scene, lane) for scene, lane in LANES]


def write_json_atomic(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def append_jsonl(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def anomaly_signature(rows: List[LaneSnapshot]) -> str:
    return "|".join(
        f"{row.scene}:{row.anomaly}"
        for row in rows
        if row.anomaly
    )


def trigger_thread(
    *,
    thread_id: str,
    message: str,
    timeout_s: float,
    log_path: Path,
    reason: str,
    rows: List[LaneSnapshot],
) -> bool:
    payload = {
        "event": "algorithm_c_thread_monitor_trigger_start",
        "time": time.time(),
        "reason": reason,
        "lanes": [asdict(row) for row in rows],
    }
    append_jsonl(log_path, payload)
    command = [
        str(PYTHON),
        str(THREAD_SENDER),
        "--thread-id",
        thread_id,
        "--message",
        message,
        "--wait",
        "--timeout-s",
        str(timeout_s),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=timeout_s + 60.0,
            check=False,
        )
        success = completed.returncode == 0
        append_jsonl(
            log_path,
            {
                "event": "algorithm_c_thread_monitor_trigger_finish",
                "time": time.time(),
                "reason": reason,
                "success": success,
                "returncode": completed.returncode,
                "stdout_tail": completed.stdout[-4000:],
                "stderr_tail": completed.stderr[-4000:],
            },
        )
        return success
    except subprocess.TimeoutExpired as exc:
        append_jsonl(
            log_path,
            {
                "event": "algorithm_c_thread_monitor_trigger_timeout",
                "time": time.time(),
                "reason": reason,
                "timeout_s": timeout_s,
                "stdout_tail": str(exc.stdout or "")[-4000:],
                "stderr_tail": str(exc.stderr or "")[-4000:],
            },
        )
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--thread-id", required=True)
    parser.add_argument("--interval-seconds", type=float, default=900.0)
    parser.add_argument("--scan-seconds", type=float, default=60.0)
    parser.add_argument("--initial-delay-seconds", type=float, default=900.0)
    parser.add_argument("--turn-timeout-seconds", type=float, default=14400.0)
    parser.add_argument("--message", default=DEFAULT_MESSAGE)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "history/pg_algorithm_c_four_thread_monitor_20260811",
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.interval_seconds < 60.0 or args.scan_seconds < 10.0:
        raise ValueError("monitor intervals are too small")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    heartbeat_path = args.output_dir / "heartbeat.json"
    log_path = args.output_dir / "thread_monitor.jsonl"
    lock_path = Path("/tmp/pg_algorithm_c_four_thread_monitor.lock")
    lock_handle = lock_path.open("w", encoding="utf-8")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("another Algorithm C thread monitor is already active", flush=True)
        return 0
    lock_handle.write(f"{os.getpid()}\n")
    lock_handle.flush()
    stopping = False

    def request_stop(_signum: int, _frame: Any) -> None:
        nonlocal stopping
        stopping = True

    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, request_stop)

    rows = snapshot()
    write_json_atomic(
        heartbeat_path,
        {
            "event": "algorithm_c_thread_monitor_heartbeat",
            "time": time.time(),
            "pid": os.getpid(),
            "dry_run": bool(args.dry_run),
            "lanes": [asdict(row) for row in rows],
        },
    )
    print(json.dumps([asdict(row) for row in rows], ensure_ascii=False), flush=True)
    if args.once or args.dry_run:
        return 0

    next_periodic = time.monotonic() + max(0.0, args.initial_delay_seconds)
    last_anomaly = ""
    last_anomaly_trigger = 0.0
    while not stopping:
        rows = snapshot()
        now = time.monotonic()
        signature = anomaly_signature(rows)
        reason: Optional[str] = None
        if signature and (
            signature != last_anomaly
            or now - last_anomaly_trigger >= args.interval_seconds
        ):
            reason = f"anomaly:{signature}"
            last_anomaly = signature
            last_anomaly_trigger = now
        elif now >= next_periodic:
            reason = "periodic_full_audit"
        write_json_atomic(
            heartbeat_path,
            {
                "event": "algorithm_c_thread_monitor_heartbeat",
                "time": time.time(),
                "pid": os.getpid(),
                "next_periodic_in_s": max(0.0, next_periodic - now),
                "anomaly_signature": signature,
                "lanes": [asdict(row) for row in rows],
            },
        )
        if all(row.complete for row in rows):
            trigger_thread(
                thread_id=args.thread_id,
                message=DEFAULT_MESSAGE + " ",
                timeout_s=args.turn_timeout_seconds,
                log_path=log_path,
                reason="all_lanes_report_complete",
                rows=rows,
            )
            return 0
        if reason:
            delivered = trigger_thread(
                thread_id=args.thread_id,
                message=args.message,
                timeout_s=args.turn_timeout_seconds,
                log_path=log_path,
                reason=reason,
                rows=rows,
            )
            if not delivered and reason.startswith("anomaly:"):
                last_anomaly_trigger = 0.0
            retry_delay = (
                args.interval_seconds if delivered else args.scan_seconds
            )
            next_periodic = time.monotonic() + retry_delay
        deadline = time.monotonic() + args.scan_seconds
        while not stopping and time.monotonic() < deadline:
            time.sleep(min(1.0, deadline - time.monotonic()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
