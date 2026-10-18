#!/usr/bin/env python3
"""Capture a read-only JOB v49 point liveness snapshot from procfs and artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Mapping, Optional


EVENT_NAMES = (
    "job_round8_candidate_invocation_started",
    "job_round8_candidate_replay_complete",
    "job_round8_candidate_technical_failure",
    "job_round8_baseline_restored",
)


def load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root is not an object: {path}")
    return value


def read_counter(path: Path) -> int:
    raw = path.read_text(encoding="utf-8").strip()
    if not raw.isdigit():
        raise ValueError(f"invalid counter: {path}")
    return int(raw)


def boot_time_seconds() -> float:
    uptime = float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0])
    return time.time() - uptime


def proc_snapshot(pid: int, boot_time: float) -> Optional[Dict[str, Any]]:
    root = Path("/proc") / str(pid)
    try:
        raw_stat = (root / "stat").read_text(encoding="utf-8")
        close = raw_stat.rfind(")")
        if close < 0:
            raise ValueError("malformed proc stat")
        fields = raw_stat[close + 2 :].split()
        # Fields after comm begin at proc field 3 (state).
        ppid = int(fields[1])
        state = fields[0]
        user_ticks = int(fields[11])
        system_ticks = int(fields[12])
        start_ticks = int(fields[19])
        ticks_per_second = int(os.sysconf("SC_CLK_TCK"))
        started_epoch = boot_time + start_ticks / ticks_per_second
        io_values: Dict[str, int] = {}
        try:
            for line in (root / "io").read_text(encoding="utf-8").splitlines():
                name, raw = line.split(":", 1)
                if name in {"read_bytes", "write_bytes", "syscr", "syscw"}:
                    io_values[name] = int(raw.strip())
        except (FileNotFoundError, PermissionError, ValueError):
            pass
        try:
            command = (root / "cmdline").read_bytes().replace(b"\x00", b" ").decode(
                errors="replace"
            ).strip()
        except (FileNotFoundError, PermissionError):
            command = ""
        try:
            wchan = (root / "wchan").read_text(encoding="utf-8").strip()
        except (FileNotFoundError, PermissionError):
            wchan = ""
        return {
            "pid": pid,
            "ppid": ppid,
            "state": state,
            "started_epoch": started_epoch,
            "elapsed_seconds": max(0.0, time.time() - started_epoch),
            "cpu_ticks": user_ticks + system_ticks,
            "io": io_values,
            "wchan": wchan,
            "command": command,
        }
    except (FileNotFoundError, ProcessLookupError, PermissionError, ValueError, IndexError):
        return None


def process_tree(root_pid: int, boot_time: float) -> List[Dict[str, Any]]:
    snapshots: Dict[int, Dict[str, Any]] = {}
    for path in Path("/proc").iterdir():
        if not path.name.isdigit():
            continue
        value = proc_snapshot(int(path.name), boot_time)
        if value is not None:
            snapshots[int(path.name)] = value
    descendants: set[int] = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, value in snapshots.items():
            if pid not in descendants and int(value["ppid"]) in descendants:
                descendants.add(pid)
                changed = True
    return [snapshots[pid] for pid in sorted(descendants) if pid in snapshots]


def event_snapshot(path: Path) -> Dict[str, Any]:
    counts = {name: 0 for name in EVENT_NAMES}
    rows: List[Dict[str, Any]] = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(value, dict):
                continue
            name = value.get("event")
            if name in counts:
                counts[str(name)] += 1
            rows.append(value)
    last_started = next(
        (
            row
            for row in reversed(rows)
            if row.get("event") == "job_round8_candidate_invocation_started"
        ),
        None,
    )
    stat = path.stat() if path.is_file() else None
    return {
        "path": str(path),
        "exists": path.is_file(),
        "size": stat.st_size if stat else None,
        "mtime_ns": stat.st_mtime_ns if stat else None,
        "counts": counts,
        "last_started_rank": last_started.get("rank") if last_started else None,
        "tail": rows[-10:],
    }


def artifact_snapshot(replay_root: Path) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    if not replay_root.exists():
        return result
    for path in sorted(replay_root.rglob("*"), key=str):
        if not path.is_file():
            continue
        stat = path.stat()
        result.append(
            {
                "path": str(path.relative_to(replay_root)),
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
        )
    return result


def delta(current: Mapping[str, Any], previous: Mapping[str, Any]) -> Dict[str, Any]:
    current_processes = {
        int(item["pid"]): item for item in current.get("process_tree") or []
    }
    previous_processes = {
        int(item["pid"]): item for item in previous.get("process_tree") or []
    }
    process_deltas = []
    for pid, value in current_processes.items():
        old = previous_processes.get(pid)
        if old is None or value.get("started_epoch") != old.get("started_epoch"):
            continue
        io_now = value.get("io") or {}
        io_old = old.get("io") or {}
        process_deltas.append(
            {
                "pid": pid,
                "elapsed_delta": float(value["elapsed_seconds"])
                - float(old["elapsed_seconds"]),
                "cpu_ticks_delta": int(value["cpu_ticks"]) - int(old["cpu_ticks"]),
                "read_bytes_delta": int(io_now.get("read_bytes", 0))
                - int(io_old.get("read_bytes", 0)),
                "write_bytes_delta": int(io_now.get("write_bytes", 0))
                - int(io_old.get("write_bytes", 0)),
                "syscr_delta": int(io_now.get("syscr", 0)) - int(io_old.get("syscr", 0)),
                "syscw_delta": int(io_now.get("syscw", 0)) - int(io_old.get("syscw", 0)),
            }
        )
    current_artifacts = {
        str(item["path"]): (item.get("size"), item.get("mtime_ns"))
        for item in current.get("artifacts") or []
    }
    previous_artifacts = {
        str(item["path"]): (item.get("size"), item.get("mtime_ns"))
        for item in previous.get("artifacts") or []
    }
    changed_artifacts = sorted(
        path
        for path, signature in current_artifacts.items()
        if previous_artifacts.get(path) != signature
    )
    progress = bool(
        changed_artifacts
        or any(
            item["cpu_ticks_delta"] > 0
            or item["read_bytes_delta"] > 0
            or item["write_bytes_delta"] > 0
            or item["syscr_delta"] > 0
            or item["syscw_delta"] > 0
            for item in process_deltas
        )
        or current.get("global_counter") != previous.get("global_counter")
        or (current.get("events") or {}).get("mtime_ns")
        != (previous.get("events") or {}).get("mtime_ns")
    )
    return {
        "seconds_between_snapshots": float(current["captured_epoch"])
        - float(previous["captured_epoch"]),
        "process_deltas": process_deltas,
        "changed_artifacts": changed_artifacts,
        "observable_progress": progress,
    }


def run(args: argparse.Namespace) -> Dict[str, Any]:
    boot_time = boot_time_seconds()
    runner = proc_snapshot(args.runner_pid, boot_time)
    child = proc_snapshot(args.child_pid, boot_time)
    events_path = args.replay_root / "events.jsonl"
    current: Dict[str, Any] = {
        "schema_version": "job_v49_live_point_read_only_snapshot_v1",
        "captured_epoch": time.time(),
        "runner": runner,
        "child": child,
        "process_tree": process_tree(args.child_pid, boot_time),
        "global_counter": read_counter(args.global_counter),
        "events": event_snapshot(events_path),
        "artifacts": artifact_snapshot(args.replay_root),
        "summary_exists": (args.replay_root / "summary.json").is_file(),
        "remote_actions_executed": False,
    }
    if args.previous_snapshot is not None:
        previous = load_json(args.previous_snapshot)
        current["delta_from_previous"] = delta(current, previous)
    child_elapsed = float(child["elapsed_seconds"]) if child else None
    if child is None:
        classification = "child_exited_or_pid_changed"
    elif child_elapsed is not None and child_elapsed > args.hard_timeout_seconds + 60:
        classification = "hard_timeout_cleanup_anomaly"
    elif child_elapsed is not None and child_elapsed > 900:
        classification = "slow_but_below_hard_timeout"
    else:
        classification = "in_flight_within_timeout"
    current["classification"] = classification
    current["hard_timeout_seconds"] = args.hard_timeout_seconds
    return current


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner-pid", type=int, required=True)
    parser.add_argument("--child-pid", type=int, required=True)
    parser.add_argument("--replay-root", type=Path, required=True)
    parser.add_argument("--global-counter", type=Path, required=True)
    parser.add_argument("--previous-snapshot", type=Path)
    parser.add_argument("--hard-timeout-seconds", type=int, default=1800)
    args = parser.parse_args()
    result = run(args)
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    return 2 if result["classification"] == "hard_timeout_cleanup_anomaly" else 0


if __name__ == "__main__":
    raise SystemExit(main())
