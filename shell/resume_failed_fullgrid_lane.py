#!/usr/bin/env python3
"""Safely move one run-owned full-grid lane under retry supervision.

The command is intentionally restricted to entries in the formal 18-run queue.
It requires exactly one matching tuner, sends SIGINT only to that
process, preserves the append-only JSONL, installs a rewind when the failed
round already has an old summary, and starts the same tuner under
``supervise_fullgrid_retry_before_summary.py``.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from supervise_fullgrid_retry_before_summary import install_retry_rewind_if_needed


PYTHON = Path("/home/anonymous/miniconda3/bin/python3")
SUPERVISOR = Path(__file__).resolve().with_name("supervise_fullgrid_retry_before_summary.py")
DISK1_SOURCE = Path(
    "/disk1/resource_adaptive/bruteforce_pg17_framework_20260717/binary_plan_resource_tuner.py"
)
QUEUE_CONFIG = Path(__file__).resolve().parents[1] / "config/bruteforce_repeat_queues_pg17.json"


@dataclass(frozen=True)
class Lane:
    lane_id: str
    source: Path
    config: Path
    output_dir: Path
    log: Path
    target: str
    environment: Dict[str, str] = field(default_factory=dict)

    @property
    def controller_log(self) -> Path:
        return self.output_dir / "controller_retry_supervised.log"

    @property
    def tmux_socket(self) -> str:
        return "fullgrid-" + self.lane_id.lower().replace("-", "_") + "-retry-supervised"


def option_value(arguments: List[str], name: str, default: str) -> str:
    try:
        return str(arguments[arguments.index(name) + 1])
    except (ValueError, IndexError):
        return default


def generic_environment(
    *,
    launcher_args: List[str],
    config_payload: Dict[str, Any],
    output_dir: Path,
) -> Dict[str, str]:
    workload = option_value(launcher_args, "--generic-workload", "").strip()
    if not workload:
        return {}
    workload_context = (config_payload.get("binary_plan_tuning") or {}).get("workload_context") or {}
    configured_files = workload_context.get("job_query_files") or []
    job_files = option_value(launcher_args, "--job-query-files", "").strip()
    if not job_files and isinstance(configured_files, list):
        job_files = ",".join(str(value).strip() for value in configured_files if str(value).strip())
    timeout_ms = option_value(launcher_args, "--job-statement-timeout-ms", "0")
    if int(timeout_ms or 0) <= 0:
        timeout_ms = str(max(0, int(workload_context.get("job_statement_timeout_ms") or 0)))
    return {
        "GENERIC_WORKLOAD": workload,
        "GENERIC_RUN_SECONDS": option_value(launcher_args, "--generic-run-seconds", "60"),
        "BENCHBASE_SOURCE_ROOT": option_value(
            launcher_args, "--benchbase-source", "/home/anonymous/benchbase-postgres"
        ),
        "BENCHBASE_RUNTIME_ROOT": option_value(
            launcher_args, "--benchbase-runtime-root", str(output_dir.parent / "benchbase_runtime")
        ),
        "BENCHBASE_SCALEFACTOR": option_value(launcher_args, "--benchbase-scalefactor", "1"),
        "BENCHBASE_TERMINALS": option_value(launcher_args, "--benchbase-terminals", "8"),
        "BENCHBASE_JAVA_XMX": option_value(launcher_args, "--benchbase-java-xmx", "-Xmx4g"),
        "JOB_SQL_DIR": option_value(
            launcher_args, "--job-sql-dir", "/home/anonymous/dataset/pg_data/join-order-benchmark"
        ),
        "JOB_QUERY_FILES": job_files,
        "JOB_MAX_QUERIES": option_value(launcher_args, "--job-max-queries", "0"),
        "JOB_STATEMENT_TIMEOUT_MS": timeout_ms,
    }


def load_formal_lanes(queue_config: Path = QUEUE_CONFIG) -> Dict[str, Lane]:
    payload = json.loads(queue_config.read_text(encoding="utf-8"))
    lanes: Dict[str, Lane] = {}
    for queue in payload.get("queues") or []:
        for entry in queue.get("entries") or []:
            lane_id = str(entry["exp_id"])
            config = Path(str(entry["config"])).resolve()
            log = Path(str(entry["log"])).resolve()
            target = str(entry["target"])
            config_payload = json.loads(config.read_text(encoding="utf-8"))
            output_raw = str((config_payload.get("binary_plan_tuning") or {}).get("output_dir") or "")
            if not output_raw:
                raise RuntimeError(f"lane config has no output_dir: {config}")
            output_dir = Path(output_raw).resolve()
            source = DISK1_SOURCE
            if lane_id == "B3-R1":
                special_source = output_dir / "binary_plan_resource_tuner.py"
                if special_source.is_file():
                    source = special_source
            launcher_args = [str(value) for value in (entry.get("launcher_args") or [])]
            lane = Lane(
                lane_id=lane_id,
                source=source,
                config=config,
                output_dir=output_dir,
                log=log,
                target=target,
                environment=generic_environment(
                    launcher_args=launcher_args,
                    config_payload=config_payload,
                    output_dir=output_dir,
                ),
            )
            previous = lanes.get(lane_id)
            if previous is not None and previous != lane:
                raise RuntimeError(f"duplicate lane has conflicting queue definitions: {lane_id}")
            lanes[lane_id] = lane
    if len(lanes) != 18:
        raise RuntimeError(f"formal queue must contain 18 unique physical lanes, found {len(lanes)}")
    return lanes


LANES = load_formal_lanes()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lane_id", choices=sorted(LANES))
    parser.add_argument("--max-restarts", type=int, default=32)
    parser.add_argument("--stop-timeout-s", type=int, default=2100)
    args = parser.parse_args()
    if args.max_restarts < 1:
        parser.error("--max-restarts must be positive")
    if args.stop_timeout_s < 30:
        parser.error("--stop-timeout-s must be at least 30")
    return args


def process_rows() -> List[Tuple[int, str, str]]:
    proc = subprocess.run(
        ["ps", "-eo", "pid=,comm=,args="],
        check=True,
        capture_output=True,
        text=True,
    )
    rows: List[Tuple[int, str, str]] = []
    for raw in proc.stdout.splitlines():
        parts = raw.strip().split(None, 2)
        if len(parts) != 3:
            continue
        try:
            rows.append((int(parts[0]), parts[1], parts[2]))
        except ValueError:
            continue
    return rows


def command_tokens(args_text: str) -> List[str]:
    try:
        return shlex.split(args_text)
    except ValueError:
        return []


def exact_tuner_pids(lane: Lane) -> List[int]:
    source = str(lane.source)
    config = str(lane.config)
    output_dir = str(lane.output_dir)
    supervisor = str(SUPERVISOR)
    found: List[int] = []
    for pid, command, args_text in process_rows():
        if not command.startswith("python"):
            continue
        tokens = command_tokens(args_text)
        if supervisor in tokens or source not in tokens:
            continue
        required = ["--config", config, "--output-dir", output_dir, "binary-search", "--target", lane.target, "--resume"]
        if all(value in tokens for value in required):
            found.append(pid)
    return found


def supervised_process_exists(lane: Lane) -> bool:
    supervisor = str(SUPERVISOR)
    log = str(lane.log)
    for _, command, args_text in process_rows():
        if not command.startswith("python"):
            continue
        tokens = command_tokens(args_text)
        if supervisor in tokens and "--log" in tokens and log in tokens:
            return True
    return False


def authoritative_retryable_failures(log_path: Path) -> List[Dict[str, Any]]:
    latest: Dict[Tuple[int, int], Tuple[int, Dict[str, Any]]] = {}
    last_resume_index = -1
    with log_path.open("r", encoding="utf-8") as handle:
        for row_index, raw in enumerate(handle):
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            if row.get("event") == "stage_resume":
                last_resume_index = row_index
            if row.get("event") != "candidate_validation":
                continue
            key = (int(row.get("round") or 0), int(row.get("candidate_index") or 0))
            latest[key] = (row_index, row)
    failures = []
    for row_index, row in latest.values():
        # Failures from before the current resume are already being replayed by
        # that tuner.  Only a new failure in the active resume needs takeover.
        if row_index <= last_resume_index:
            continue
        status = str(row.get("status") or "").strip().lower()
        reason = str(
            row.get("accept_reject_reason") or row.get("reject_reason") or ""
        ).strip().lower()
        if status not in {"", "ok"} and reason == "workload_failed":
            failures.append(row)
    return sorted(
        failures,
        key=lambda row: (int(row.get("round") or 0), int(row.get("candidate_index") or 0)),
    )


def child_command(lane: Lane, max_restarts: int) -> List[str]:
    return [
        str(PYTHON),
        "-B",
        str(SUPERVISOR),
        "--log",
        str(lane.log),
        "--config",
        str(lane.config),
        "--target",
        lane.target,
        "--poll-seconds",
        "0.05",
        "--max-restarts",
        str(max_restarts),
        "--",
        str(PYTHON),
        "-B",
        str(lane.source),
        "--config",
        str(lane.config),
        "--output-dir",
        str(lane.output_dir),
        "binary-search",
        "--target",
        lane.target,
        "--resume",
    ]


def launch_supervised(lane: Lane, max_restarts: int) -> None:
    if lane.environment:
        command = ["env", *[f"{key}={value}" for key, value in sorted(lane.environment.items())]]
    else:
        command = []
    command.extend(child_command(lane, max_restarts))
    shell_command = (
        "exec " + shlex.join(command) + " >> " + shlex.quote(str(lane.controller_log)) + " 2>&1"
    )
    proc = subprocess.run(
        [
            "tmux",
            "-L",
            lane.tmux_socket,
            "new-session",
            "-d",
            "-s",
            "controller",
            shell_command,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"tmux launch failed: {(proc.stderr or proc.stdout).strip()}")


def resume_lane(lane: Lane, *, max_restarts: int = 32, stop_timeout_s: int = 2100) -> int:
    if supervised_process_exists(lane):
        print(f"{lane.lane_id} is already supervised; no action taken", flush=True)
        return 0

    failures = authoritative_retryable_failures(lane.log)
    if not failures:
        raise SystemExit(f"{lane.lane_id} has no authoritative retryable workload failure")
    pids = exact_tuner_pids(lane)
    if len(pids) != 1:
        raise SystemExit(
            f"refusing restart: expected exactly one {lane.lane_id} tuner, found {len(pids)}: {pids}"
        )

    old_pid = pids[0]
    first_failure = failures[0]
    print(
        f"requesting safe stop lane={lane.lane_id} pid={old_pid} "
        f"failure_round={first_failure.get('round')} candidate={first_failure.get('candidate_index')} "
        f"authoritative_failures={len(failures)}",
        flush=True,
    )
    os.kill(old_pid, signal.SIGINT)
    deadline = time.monotonic() + int(stop_timeout_s)
    while True:
        try:
            os.kill(old_pid, 0)
        except ProcessLookupError:
            break
        if time.monotonic() >= deadline:
            raise SystemExit("tuner did not exit before the safe-stop deadline; no replacement was launched")
        time.sleep(2.0)

    if exact_tuner_pids(lane):
        raise SystemExit("another exact tuner appeared after the safe stop; no replacement was launched")
    failures = authoritative_retryable_failures(lane.log)
    if not failures:
        raise SystemExit("the in-flight attempt repaired all failures; no replacement was launched")
    first_failure = failures[0]
    override_id = install_retry_rewind_if_needed(
        log_path=lane.log,
        config_path=lane.config,
        target=lane.target,
        failure_row=first_failure,
        restart_count=0,
    )
    if override_id:
        print(f"installed initial retry rewind lane={lane.lane_id} id={override_id}", flush=True)

    launch_supervised(lane, int(max_restarts))
    time.sleep(3.0)
    pids = exact_tuner_pids(lane)
    if len(pids) != 1 or not supervised_process_exists(lane):
        raise SystemExit(
            f"supervised launch verification failed lane={lane.lane_id} tuner_pids={pids}"
        )
    print(
        f"supervised resume started lane={lane.lane_id} pid={pids[0]} "
        f"controller_log={lane.controller_log}",
        flush=True,
    )
    return 0


def main() -> int:
    args = parse_args()
    return resume_lane(
        LANES[args.lane_id],
        max_restarts=int(args.max_restarts),
        stop_timeout_s=int(args.stop_timeout_s),
    )


if __name__ == "__main__":
    raise SystemExit(main())
