#!/usr/bin/env python3
"""Launch the next full Algorithm C outer round after a clean terminal boundary."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def run(command: list[str], *, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=True,
    )


def unit_inactive(unit: str) -> bool:
    result = subprocess.run(
        ["systemctl", "--user", "is-active", unit],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=15,
        check=False,
    )
    return result.stdout.strip() not in {"active", "activating", "reloading"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current-run-id", required=True)
    parser.add_argument("--next-run-id", required=True)
    parser.add_argument("--checkpoint-output-dir", type=Path, required=True)
    parser.add_argument("--quarantine-output", type=Path, required=True)
    parser.add_argument("--prior-quarantine", type=Path, required=True)
    parser.add_argument("--runtime-preflight", type=Path, required=True)
    parser.add_argument("--prepared-binding", type=Path, required=True)
    parser.add_argument("--benchmark-accounts", type=Path, required=True)
    parser.add_argument("--fresh-r0", type=Path, required=True)
    parser.add_argument("--state-output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=7200)
    args = parser.parse_args()

    for run_id in (args.current_run_id, args.next_run_id):
        if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", run_id) is None:
            parser.error(f"unsafe run id: {run_id}")
    if args.current_run_id == args.next_run_id:
        parser.error("next run id must be fresh")

    current_root = ROOT / "history/mysql_four_scene_20260809" / args.current_run_id
    current_state = current_root / "run_state.json"
    current_unit = f"mysql-four-scene-algc-{args.current_run_id}.service"
    watchdog_unit = f"mysql-four-scene-algc-watchdog-{args.current_run_id}.service"
    affinity_unit = f"mysql-four-scene-algc-affinity-{args.current_run_id}.service"
    started = time.monotonic()
    atomic(args.state_output, {
        "status": "WAITING",
        "current_run_id": args.current_run_id,
        "next_run_id": args.next_run_id,
        "started_at": datetime.now().astimezone().isoformat(),
    })

    terminal: dict[str, Any] | None = None
    while time.monotonic() - started < args.timeout_seconds:
        if current_state.is_file():
            state = load(current_state)
            if state.get("status") in {"COMPLETE", "NON_CONVERGED", "FAILED_CLOSED"}:
                terminal = state
                break
        time.sleep(5)
    if terminal is None:
        atomic(args.state_output, {"status": "TIMEOUT", "formal_launch_performed": False})
        return 2

    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if (
            unit_inactive(current_unit)
            and unit_inactive(watchdog_unit)
            and unit_inactive(affinity_unit)
        ):
            break
        time.sleep(2)
    else:
        atomic(args.state_output, {
            "status": "BLOCKED_ACTIVE_PREDECESSOR",
            "terminal_status": terminal.get("status"),
            "formal_launch_performed": False,
        })
        return 2

    if terminal.get("status") == "COMPLETE":
        atomic(args.state_output, {
            "status": "ALGORITHM_C_COMPLETE",
            "terminal_status": "COMPLETE",
            "completed_outer_attempts": terminal.get("outer_attempt"),
            "formal_launch_performed": False,
            "algorithm_b_handoff_required": True,
        })
        return 0
    if terminal.get("status") != "NON_CONVERGED" or terminal.get("error") is not None:
        atomic(args.state_output, {
            "status": "BLOCKED_FAIL_CLOSED",
            "terminal_status": terminal.get("status"),
            "error": terminal.get("error"),
            "formal_launch_performed": False,
        })
        return 2

    if args.checkpoint_output_dir.exists() or args.quarantine_output.parent.exists():
        raise RuntimeError("fresh checkpoint and quarantine output paths required")
    checkpoint_result = run([
        str(PYTHON), "-m", "scripts.build_mysql_algorithm_c_outer_checkpoint_v4",
        "--run-root", str(current_root),
        "--output-dir", str(args.checkpoint_output_dir),
    ])
    checkpoint = args.checkpoint_output_dir / "prepared_binding.json"
    quarantine_result = run([
        str(PYTHON), "scripts/rebind_mysql_algorithm_c_empty_replay_quarantine_v2.py",
        "--prior", str(args.prior_quarantine),
        "--checkpoint", str(checkpoint),
        "--output", str(args.quarantine_output),
    ])
    launch_result = run([
        str(PYTHON), "scripts/launch_mysql_algorithm_c_boundary_resume_v26.py",
        "--run-id", args.next_run_id,
        "--checkpoint", str(checkpoint),
        "--runtime-preflight", str(args.runtime_preflight),
        "--prepared-binding", str(args.prepared_binding),
        "--benchmark-accounts", str(args.benchmark_accounts),
        "--fresh-r0", str(args.fresh_r0),
        "--replay-quarantine", str(args.quarantine_output),
    ], timeout=300)
    atomic(args.state_output, {
        "status": "NEXT_OUTER_LAUNCHED",
        "terminal_status": "NON_CONVERGED",
        "completed_outer_attempts": terminal.get("outer_attempt"),
        "formal_launch_performed": True,
        "next_run_id": args.next_run_id,
        "checkpoint_stdout": checkpoint_result.stdout.strip(),
        "quarantine_stdout": quarantine_result.stdout.strip(),
        "launch_stdout": launch_result.stdout.strip(),
        "completed_at": datetime.now().astimezone().isoformat(),
    })
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        # The waiter never mutates or retries a failed formal predecessor.
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        raise
