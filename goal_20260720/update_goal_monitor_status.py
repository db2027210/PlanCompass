#!/usr/bin/env python3
"""Write a durable status snapshot for the Codex MySQL formal experiment goal.

Reads process/Docker/batch JSON only; does not launch optimizers. Suitable for
one-shot or short-interval loop use while fixed-protocol batches run.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GOAL_DIR = ROOT / "goal_20260720"
DEFAULT_STATUS = DEFAULT_GOAL_DIR / "goal_monitor_status.json"
DEFAULT_BATCHES = DEFAULT_GOAL_DIR / "formal_batches"
DEFAULT_CONTAINER = "ra_mysql_goal_hybench_xp"
DEFAULT_PORT = 13366

# Codex session goal tables (remaining work beyond accepted B5 calibrated).
CODEX_MATRIX_PENDING = [
    {"id": "A13", "benchmark": "HyBench AP", "bottleneck": "Memory", "runs": 1},
    {"id": "A14", "benchmark": "JOB", "bottleneck": "Memory", "runs": 1},
    {"id": "A15", "benchmark": "TPC-H", "bottleneck": "Memory", "runs": 1},
    {"id": "A16", "benchmark": "HyBench TP", "bottleneck": "CPU", "runs": 1},
    {"id": "A17", "benchmark": "TPC-C", "bottleneck": "CPU", "runs": 1},
    {"id": "A18", "benchmark": "HyBench XP", "bottleneck": "IO", "runs": 1},
    {"id": "E13", "benchmark": "HyBench AP", "bottleneck": "none", "runs": 1},
    {"id": "E14", "benchmark": "JOB", "bottleneck": "none", "runs": 1},
    {"id": "E15", "benchmark": "TPC-H", "bottleneck": "none", "runs": 1},
    {"id": "E16", "benchmark": "HyBench TP", "bottleneck": "none", "runs": 1},
    {"id": "E17", "benchmark": "TPC-C", "bottleneck": "none", "runs": 1},
    {"id": "E18", "benchmark": "HyBench XP", "bottleneck": "none", "runs": 1},
    {"id": "B4", "benchmark": "HyBench TP", "bottleneck": "CPU", "runs": 3},
    {"id": "B5", "benchmark": "HyBench AP", "bottleneck": "Memory", "runs": 3, "note": "calibrated formal majority-pass done"},
    {"id": "B6", "benchmark": "HyBench XP", "bottleneck": "IO", "runs": 3, "note": "active recovery path (explicitcap v3 n=2)"},
]

SESSION_TARGETS = {
    "bottleneck": {
        "min_qps_gain_ratio": 0.15,
        "min_target_drop_ratio": 0.05,
        "max_target_drop_ratio": 0.10,
        "target_should_leave_bottleneck": True,
        "prefer_non_target_resource_up": True,
    },
    "none": {
        "min_qps_gain_ratio": 0.15,
        "min_resource_up_count": 1,
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def attempt_outcome(attempt_dir: Path) -> dict[str, Any]:
    """Classify one fixed attempt: formal result, failure, or in-progress."""
    formal = sorted(attempt_dir.glob("*_r*.json"))
    failure = attempt_dir / "run_failure.json"
    summary = load_json(attempt_dir / "run_summary.json")
    if formal:
        result = load_json(formal[0]) or {}
        result_block = result.get("result") if isinstance(result, dict) else {}
        decision = None
        if isinstance(summary, dict):
            decision = summary.get("final_goal_decision")
        return {
            "attempt_dir": str(attempt_dir),
            "state": "formal_complete",
            "formal_json": str(formal[0]),
            "run_summary_present": isinstance(summary, dict),
            "qps_before": (result_block or {}).get("qps_before") if isinstance(result_block, dict) else None,
            "qps_after": (result_block or {}).get("qps_after") if isinstance(result_block, dict) else None,
            "bad_config": (result_block or {}).get("bad_config") if isinstance(result_block, dict) else None,
            "final_goal_decision": decision,
            "session_target_met": bool(
                isinstance(decision, dict) and decision.get("successful") is True
            ),
        }
    if failure.is_file():
        fail = load_json(failure) or {}
        return {
            "attempt_dir": str(attempt_dir),
            "state": "failed",
            "run_failure": str(failure),
            "error_type": fail.get("error_type") if isinstance(fail, dict) else None,
            "error": (fail.get("error") if isinstance(fail, dict) else None),
            "session_target_met": False,
        }
    # progress hints
    labels: list[str] = []
    for base in (attempt_dir / "real_validations", attempt_dir / "real_point_warmups"):
        if base.is_dir():
            labels.extend(sorted(p.name for p in base.iterdir() if p.is_dir())[-3:])
    return {
        "attempt_dir": str(attempt_dir),
        "state": "in_progress",
        "recent_measure_dirs": labels,
        "session_target_met": False,
    }


def summarize_batch(batch_dir: Path) -> dict[str, Any]:
    """Summarize one formal_batches/* directory."""
    protocol = load_json(batch_dir / "protocol.json") or {}
    summary = load_json(batch_dir / "batch_summary.json")
    audit = load_json(batch_dir / "acceptance_audit.json")
    attempts = []
    for attempt in sorted(batch_dir.glob("attempt_*")):
        if attempt.is_dir():
            attempts.append({"name": attempt.name, **attempt_outcome(attempt)})
    finished = summary is not None
    formal_ok = sum(1 for a in attempts if a.get("state") == "formal_complete")
    failed = sum(1 for a in attempts if a.get("state") == "failed")
    in_progress = sum(1 for a in attempts if a.get("state") == "in_progress")
    target_met = sum(1 for a in attempts if a.get("session_target_met") is True)
    return {
        "batch_dir": str(batch_dir),
        "protocol_id": protocol.get("protocol_id") or (summary or {}).get("protocol_id"),
        "exp_id": protocol.get("exp_id"),
        "finished": finished,
        "majority_passed": (summary or {}).get("majority_passed") if isinstance(summary, dict) else None,
        "passed_attempts": (summary or {}).get("passed_attempts") if isinstance(summary, dict) else None,
        "acceptance_met": (audit or {}).get("acceptance_met") if isinstance(audit, dict) else None,
        "attempt_counts": {
            "total": len(attempts),
            "formal_complete": formal_ok,
            "failed": failed,
            "in_progress": in_progress,
            "session_target_met": target_met,
        },
        "attempts": attempts,
    }


def classify_batches(batch_root: Path) -> dict[str, Any]:
    batches = []
    if batch_root.is_dir():
        for path in sorted(batch_root.iterdir()):
            if path.is_dir() and not path.name.startswith("."):
                batches.append(summarize_batch(path))
    b5 = [b for b in batches if "B5_" in (b.get("protocol_id") or path_name(b))]
    b6 = [b for b in batches if "B6_" in (b.get("protocol_id") or path_name(b))]
    active = [b for b in batches if b["attempt_counts"]["in_progress"] > 0 and not b["finished"]]
    return {
        "all_batches": batches,
        "b5_batches": b5,
        "b6_batches": b6,
        "active_batches": active,
        "b5_accepted": any(b.get("acceptance_met") is True for b in b5),
        "b6_accepted": any(b.get("acceptance_met") is True for b in b6),
    }


def path_name(batch_summary: dict[str, Any]) -> str:
    return Path(str(batch_summary.get("batch_dir") or "")).name


def pgrep_formal(patterns: list[str] | None = None) -> list[dict[str, Any]]:
    patterns = patterns or [
        "run_fixed_protocol_batch.py",
        "mysql_double_loop_grid_runner.py",
        "B6_io_explicitcap",
        "goal_20260720",
    ]
    try:
        completed = subprocess.run(
            ["ps", "-eo", "pid,ppid,etime,cmd"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return [{"error": str(exc)}]
    rows: list[dict[str, Any]] = []
    for line in completed.stdout.splitlines()[1:]:
        if not any(p in line for p in patterns):
            continue
        if "update_goal_monitor_status.py" in line:
            continue
        parts = line.strip().split(None, 3)
        if len(parts) < 4:
            continue
        rows.append({
            "pid": int(parts[0]),
            "ppid": int(parts[1]),
            "etime": parts[2],
            "cmd": parts[3][:500],
        })
    return rows


def docker_container_status(name: str) -> dict[str, Any]:
    try:
        fmt = "{{.State.Status}}|{{.State.Pid}}|{{.NetworkSettings.Ports}}"
        completed = subprocess.run(
            ["docker", "inspect", "-f", fmt, name],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return {"name": name, "error": str(exc), "running": False}
    if completed.returncode != 0:
        return {
            "name": name,
            "running": False,
            "error": (completed.stderr or completed.stdout or "inspect_failed").strip()[:400],
        }
    status, pid_s, ports = (completed.stdout.strip().split("|", 2) + ["", ""])[:3]
    mysqld_pid = None
    try:
        top = subprocess.run(
            ["docker", "top", name, "-eo", "pid,comm"],
            check=False,
            capture_output=True,
            text=True,
        )
        for line in top.stdout.splitlines()[1:]:
            bits = line.split()
            if len(bits) >= 2 and bits[1] == "mysqld":
                mysqld_pid = int(bits[0])
                break
    except OSError:
        mysqld_pid = None
    return {
        "name": name,
        "status": status,
        "running": status == "running",
        "container_pid": int(pid_s) if pid_s.isdigit() else None,
        "mysqld_pid": mysqld_pid,
        "mysqld_pid_alive": bool(mysqld_pid and Path(f"/proc/{mysqld_pid}").is_dir()),
        "ports": ports,
    }


def extract_protocol_mysqld_pid(protocol_path: Path) -> int | None:
    data = load_json(protocol_path)
    if not isinstance(data, dict):
        return None
    args = data.get("runner_args")
    if not isinstance(args, list):
        return None
    try:
        idx = args.index("--mysqld-pid")
        return int(args[idx + 1])
    except (ValueError, IndexError, TypeError):
        return None


def detect_active_protocol(batch_root: Path, processes: list[dict[str, Any]]) -> dict[str, Any]:
    """Infer active protocol/batch/attempt from process list and unfinished batches."""
    protocol_id = None
    batch_dir = None
    attempt = None
    # Prefer the grid runner (--protocol-id + attempt output-dir) over the batch parent.
    ordered = sorted(
        processes,
        key=lambda p: (
            0 if "mysql_double_loop_grid_runner.py" in (p.get("cmd") or "") else 1,
            0 if "--output-dir" in (p.get("cmd") or "") else 1,
        ),
    )
    for proc in ordered:
        cmd = proc.get("cmd") or ""
        m = re.search(r"--protocol-id\s+(\S+)", cmd)
        if m:
            protocol_id = m.group(1)
        m = re.search(r"--output-dir\s+(\S+)", cmd)
        if m:
            out = Path(m.group(1))
            if out.name.startswith("attempt_"):
                attempt = out.name
                batch_dir = str(out.parent)
            elif batch_dir is None:
                batch_dir = str(out)
        m = re.search(r"--protocol\s+(\S+)", cmd)
        if m and protocol_id is None:
            protocol_path = Path(m.group(1))
            loaded = load_json(protocol_path)
            if isinstance(loaded, dict):
                protocol_id = loaded.get("protocol_id")
                out = loaded.get("output_dir")
                if out and batch_dir is None:
                    batch_dir = str(Path(out) if Path(out).is_absolute() else (ROOT / out).resolve())
        if protocol_id and attempt and batch_dir:
            break
    if batch_dir is None or attempt is None:
        for path in sorted(batch_root.glob("B6_*"), reverse=True):
            if not path.is_dir():
                continue
            if (path / "batch_summary.json").is_file():
                continue
            for attempt_path in sorted(path.glob("attempt_*")):
                outcome = attempt_outcome(attempt_path)
                if outcome["state"] == "in_progress":
                    batch_dir = str(path)
                    attempt = attempt_path.name
                    proto = load_json(path / "protocol.json") or {}
                    protocol_id = protocol_id or proto.get("protocol_id")
                    break
            if batch_dir and attempt:
                break
    protocol_path = None
    if protocol_id:
        candidate = DEFAULT_GOAL_DIR / "protocols" / f"{protocol_id}.json"
        if candidate.is_file():
            protocol_path = str(candidate)
    wired_pid = extract_protocol_mysqld_pid(Path(protocol_path)) if protocol_path else None
    return {
        "protocol_id": protocol_id,
        "protocol_path": protocol_path,
        "batch_dir": batch_dir,
        "attempt": attempt,
        "protocol_mysqld_pid": wired_pid,
        "protocol_mysqld_pid_alive": bool(wired_pid and Path(f"/proc/{wired_pid}").is_dir()),
    }


def build_status(
    *,
    goal_dir: Path,
    batch_root: Path,
    container_name: str,
    port: int,
    processes: list[dict[str, Any]] | None = None,
    container: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pure-ish status assembly; process/container can be injected for tests."""
    processes = processes if processes is not None else pgrep_formal()
    container = container if container is not None else docker_container_status(container_name)
    batches = classify_batches(batch_root)
    active = detect_active_protocol(batch_root, processes)
    b5_note = "accepted" if batches["b5_accepted"] else "not_accepted"
    b6_note = "accepted" if batches["b6_accepted"] else "not_accepted"
    recovery = load_json(goal_dir / "recovery_schedule_status.json")
    formal_runner_live = any("run_fixed_protocol_batch.py" in (p.get("cmd") or "") for p in processes)
    grid_runner_live = any("mysql_double_loop_grid_runner.py" in (p.get("cmd") or "") for p in processes)
    endpoint_ok = bool(container.get("running") and container.get("mysqld_pid_alive"))
    pid_wired_ok = active.get("protocol_mysqld_pid_alive")
    if active.get("protocol_mysqld_pid") and container.get("mysqld_pid"):
        pid_match = int(active["protocol_mysqld_pid"]) == int(container["mysqld_pid"])
    else:
        pid_match = None
    flow_live = bool(
        (formal_runner_live or grid_runner_live or any(not b["finished"] for b in batches["b6_batches"]))
        and endpoint_ok
    )
    return {
        "updated_at": utc_now(),
        "session_goal": {
            "title": "Codex MySQL formal double-loop grid experiments",
            "method": "outer_binary_inner_estimated_grid",
            "targets": SESSION_TARGETS,
        },
        "endpoint": {
            "container": container_name,
            "port": port,
            **container,
        },
        "processes": processes,
        "liveness": {
            "formal_batch_runner": formal_runner_live,
            "grid_runner": grid_runner_live,
            "endpoint_usable": endpoint_ok,
            "protocol_mysqld_pid_match": pid_match,
            "experiment_flow_live": flow_live,
        },
        "active": active,
        "batches": batches,
        "b5_summary": {
            "accepted": batches["b5_accepted"],
            "status": b5_note,
            "batches": [
                {
                    "protocol_id": b.get("protocol_id"),
                    "acceptance_met": b.get("acceptance_met"),
                    "majority_passed": b.get("majority_passed"),
                    "session_target_met_count": b["attempt_counts"]["session_target_met"],
                    "batch_dir": b.get("batch_dir"),
                }
                for b in batches["b5_batches"]
            ],
        },
        "b6_summary": {
            "accepted": batches["b6_accepted"],
            "status": b6_note,
            "batches": [
                {
                    "protocol_id": b.get("protocol_id"),
                    "finished": b.get("finished"),
                    "acceptance_met": b.get("acceptance_met"),
                    "majority_passed": b.get("majority_passed"),
                    "attempt_counts": b.get("attempt_counts"),
                    "batch_dir": b.get("batch_dir"),
                }
                for b in batches["b6_batches"]
            ],
        },
        "recovery_schedule_status": recovery,
        "remaining_matrix": CODEX_MATRIX_PENDING,
        "notes": [
            "B5 memory calibrated formal batch is the accepted recovery result; do not re-run for churn.",
            "B6 uses fixed-protocol superseding dirs on failure; never overwrite finished failure trees.",
            "Monitoring only reads artifacts; launch recovery via run_fixed_protocol_batch.py with a new protocol_id.",
        ],
    }


def update_once(
    *,
    goal_dir: Path = DEFAULT_GOAL_DIR,
    status_path: Path = DEFAULT_STATUS,
    batch_root: Path = DEFAULT_BATCHES,
    container_name: str = DEFAULT_CONTAINER,
    port: int = DEFAULT_PORT,
) -> dict[str, Any]:
    status = build_status(
        goal_dir=goal_dir,
        batch_root=batch_root,
        container_name=container_name,
        port=port,
    )
    write_json_atomic(status_path, status)
    return status


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--goal-dir", type=Path, default=DEFAULT_GOAL_DIR)
    parser.add_argument("--status-path", type=Path, default=None)
    parser.add_argument("--batch-root", type=Path, default=None)
    parser.add_argument("--container", default=DEFAULT_CONTAINER)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--loop", action="store_true", help="refresh until interrupted")
    parser.add_argument("--interval-s", type=float, default=60.0)
    parser.add_argument("--max-ticks", type=int, default=0, help="0 = unlimited when --loop")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    goal_dir = args.goal_dir.resolve()
    status_path = (args.status_path or (goal_dir / "goal_monitor_status.json")).resolve()
    batch_root = (args.batch_root or (goal_dir / "formal_batches")).resolve()
    ticks = 0
    while True:
        status = update_once(
            goal_dir=goal_dir,
            status_path=status_path,
            batch_root=batch_root,
            container_name=args.container,
            port=args.port,
        )
        ticks += 1
        print(
            json.dumps(
                {
                    "updated_at": status["updated_at"],
                    "flow_live": status["liveness"]["experiment_flow_live"],
                    "active": status["active"],
                    "b5_accepted": status["b5_summary"]["accepted"],
                    "b6_accepted": status["b6_summary"]["accepted"],
                    "status_path": str(status_path),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        if not args.loop:
            return 0
        if args.max_ticks and ticks >= args.max_ticks:
            return 0
        time.sleep(max(1.0, float(args.interval_s)))


if __name__ == "__main__":
    raise SystemExit(main())
