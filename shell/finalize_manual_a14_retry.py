#!/usr/bin/env python3
"""Finalize the externally launched A14 retry into auditable suite state."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict


ROOT = Path(__file__).resolve().parents[1]


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def write_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        status = Path(f"/proc/{pid}/status").read_text(
            encoding="utf-8",
            errors="replace",
        )
        state = next(
            (line.split()[1] for line in status.splitlines() if line.startswith("State:")),
            "",
        )
        if state in {"Z", "X"}:
            return False
        os.kill(pid, 0)
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return False
    return True


def guest_exit_failure(run_dir: Path) -> str:
    """Reject a formally successful summary when the guest was unstable."""
    task_root = run_dir.parents[1]
    diagnostics_path = task_root / "guest_exit_diagnostics.json"
    outputs = []
    try:
        if diagnostics_path.is_file():
            outputs.append(str(read_json(diagnostics_path).get("output") or ""))
    except FileNotFoundError:
        pass
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        return f"guest diagnostics unreadable: {type(exc).__name__}: {exc}"
    serial_path = task_root / "rootless_vm" / "serial.log"
    try:
        if serial_path.is_file():
            outputs.append(serial_path.read_text(encoding="utf-8", errors="replace"))
    except OSError as exc:
        return f"guest serial diagnostics unreadable: {type(exc).__name__}: {exc}"
    output = "\n".join(outputs).lower()
    if "out of memory: killed process" in output or "oom-killer" in output:
        return "guest OOM occurred during formal real replay"
    if "no space left on device" in output:
        return "guest disk became full during formal real replay"
    if "terminated by signal 9: killed" in output:
        return "guest process was killed by SIGKILL during formal real replay"
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manual-root", type=Path, required=True)
    parser.add_argument("--worker-result", type=Path, required=True)
    parser.add_argument("--worker-pid", type=int, required=True)
    parser.add_argument("--poll-s", type=float, default=10.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    manual_root = args.manual_root.resolve()

    while process_alive(args.worker_pid):
        print(f"waiting worker_pid={args.worker_pid}", flush=True)
        if args.dry_run:
            return 0
        time.sleep(max(2.0, args.poll_s))

    result = read_json(args.worker_result.resolve())
    run_dir = Path(str(result.get("run_dir") or "")).resolve()
    scenario_dir = run_dir / "memory"
    summary_path = scenario_dir / "scenario_summary.json"
    try:
        summary = read_json(summary_path)
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError) as exc:
        summary = {"successful": False, "error": f"{type(exc).__name__}: {exc}"}

    export_path = manual_root / "exports/a14_r1.json"
    successful = bool(summary.get("successful") is True)
    error = str(summary.get("error") or summary.get("stop_reason") or "")
    guest_failure = guest_exit_failure(run_dir)
    if guest_failure:
        successful = False
        error = guest_failure
    if successful:
        command = [
            sys.executable,
            str(ROOT / "shell/export_double_loop_experiment_log.py"),
            str(scenario_dir),
            "--exp-id",
            "A14",
            "--benchmark",
            "job",
            "--bottleneck",
            "memory",
            "--repeat-id",
            "1",
            "--output",
            str(export_path),
            "--require-complete",
        ]
        completed = subprocess.run(
            command,
            cwd=str(ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if completed.returncode != 0:
            successful = False
            error = f"standard export failed: {completed.stdout.strip()}"

    task: Dict[str, Any] = {
        "task_id": "a14_r1",
        "exp_id": "A14",
        "repeat_id": 1,
        "benchmark": "job",
        "target": "memory",
        "attempt": 4,
        "run_dir": str(run_dir),
        "status": "completed" if successful else "needs_review",
        "history": [
            {
                "event": "manual_retry_finalized",
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "successful": successful,
            }
        ],
    }
    if successful:
        task["export_path"] = str(export_path)
        task["completed_at"] = time.time()
    else:
        task["last_error"] = error or str(result.get("error") or "manual retry failed")
    write_json(
        manual_root / "scheduler_state.json",
        {
            "schema_version": 1,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "tasks": {"a14_r1": task},
        },
    )
    print(
        f"finalized status={task['status']} export={task.get('export_path')}",
        flush=True,
    )
    return 0 if successful else 2


if __name__ == "__main__":
    raise SystemExit(main())
