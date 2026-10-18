#!/usr/bin/env python3
"""Try a bounded clean v18 gate, then rebind, verify accounts, and run R0."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v1 as coordination


PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
PREFLIGHT_SCRIPT = ROOT / "scripts/preflight_mysql_four_scene_live_v18.py"
REBIND_SCRIPT = ROOT / "scripts/rebind_mysql_four_scene_live_v18_formal.py"
ACCOUNTS_SCRIPT = ROOT / "scripts/provision_mysql_four_scene_benchmark_users_v18_formal.py"
R0_SCRIPT = ROOT / "scripts/run_mysql_four_scene_r0_v18.py"
ALGORITHM_LAUNCHER = ROOT / "scripts/launch_mysql_algorithm_c_boundary_resume_v2.py"
CHECKPOINT = ROOT / "history/mysql_four_scene_20260809/algorithm_c_v18_pruning_v2_checkpoint_v5_20260813T1920p0800/prepared_binding.json"
REPLAY_QUARANTINE = ROOT / "history/mysql_four_scene_20260809/algorithm_c_v18_replay_quarantine_v6_final3_20260813T1925p0800/replay_quarantine.json"
ALGORITHM_FRESH_SEARCH = False
PREFLIGHT_ROOT = ROOT / "preflight/mysql_four_scene_20260809"
HISTORY = ROOT / "history/mysql_four_scene_20260809"


def run(command: list[str], *, env: dict[str, str] | None = None, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout, check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--max-gate-attempts", type=int, default=1)
    parser.add_argument("--lease-timeout-seconds", type=float, default=10.0)
    args = parser.parse_args()
    if (
        re.fullmatch(r"[A-Za-z0-9_.-]{8,120}", args.run_id) is None
        or args.poll_seconds < 10
        or args.max_gate_attempts < 1
        or not 0 < args.lease_timeout_seconds <= 120
    ):
        parser.error("unsafe arguments")
    state_dir = HISTORY / args.run_id
    state_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
    state_path = state_dir / "wait_state.json"
    attempt = 0
    while attempt < args.max_gate_attempts:
        attempt += 1
        preflight = PREFLIGHT_ROOT / f"{args.run_id}_preflight_attempt_{attempt:04d}.json"
        try:
            lease = coordination.acquire_exclusive_blocking(
                timeout_seconds=args.lease_timeout_seconds,
            )
        except TimeoutError as error:
            state_path.write_text(json.dumps({
                "status": "GATE_FAILED_CLOSED_NO_WAIT",
                "attempt": attempt,
                "max_gate_attempts": args.max_gate_attempts,
                "formal_scope": "mysql_only",
                "cross_engine_comparison_prohibited": True,
                "formal_launch_authorized": False,
                "pilot_handoff_required": True,
                "external_container_mutation": False,
                "coordination_lock_path": str(coordination.LOCK_PATH),
                "lease_timeout_seconds": args.lease_timeout_seconds,
                "error": f"{type(error).__name__}: {error}",
            }, indent=2) + "\n")
            return 4
        state_path.write_text(json.dumps({
            "status": "LEASE_ACQUIRED_WAITING_FOR_CLEAN_GATE", "attempt": attempt,
            "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
            "external_container_mutation": False, "preflight": str(preflight),
            "coordination_lock_path": str(coordination.LOCK_PATH),
            "lease_acquired_at": lease.acquired_at,
            "lease_wait_seconds": lease.wait_seconds,
        }, indent=2) + "\n")
        try:
            completed = run([
                str(PYTHON), str(PREFLIGHT_SCRIPT), "--output", str(preflight),
                "--window-seconds", "60", "--interval-seconds", "1",
                "--coordinated-workload-lease-holder-pid", str(os.getpid()),
                "--coordination-contract", str(coordination.CONTRACT_PATH),
            ], timeout=120)
        except subprocess.TimeoutExpired as error:
            coordination.release(lease)
            lease = None
            state_path.write_text(json.dumps({
                "status": "GATE_FAILED_CLOSED_NO_WAIT",
                "attempt": attempt,
                "max_gate_attempts": args.max_gate_attempts,
                "formal_scope": "mysql_only",
                "cross_engine_comparison_prohibited": True,
                "formal_launch_authorized": False,
                "pilot_handoff_required": True,
                "external_container_mutation": False,
                "preflight": str(preflight),
                "preflight_timeout_seconds": 120,
                "coordination_lock_path": str(coordination.LOCK_PATH),
                "error": f"{type(error).__name__}: {error}",
            }, indent=2) + "\n")
            return 4
        if completed.returncode == 0:
            break
        coordination.release(lease)
        lease = None
        if attempt >= args.max_gate_attempts:
            state_path.write_text(json.dumps({
                "status": "GATE_FAILED_CLOSED_NO_WAIT",
                "attempt": attempt,
                "max_gate_attempts": args.max_gate_attempts,
                "formal_scope": "mysql_only",
                "cross_engine_comparison_prohibited": True,
                "formal_launch_authorized": False,
                "pilot_handoff_required": True,
                "external_container_mutation": False,
                "preflight": str(preflight),
                "preflight_returncode": completed.returncode,
                "preflight_stderr_tail": completed.stderr[-1000:],
                "coordination_lock_path": str(coordination.LOCK_PATH),
            }, indent=2) + "\n")
            return completed.returncode or 4
        time.sleep(args.poll_seconds)
    try:
        rebind_id = f"{args.run_id}_rebind"
        env = dict(os.environ, MYSQL_V18_PASS_PREFLIGHT=str(preflight))
        completed = run([str(PYTHON), str(REBIND_SCRIPT), "--run-id", rebind_id], env=env, timeout=900)
        if completed.returncode != 0:
            raise RuntimeError(f"v18 formal rebind failed: {completed.stderr[-1000:]}")
        prepared = HISTORY / rebind_id / "prepared_binding.json"
        accounts_id = f"{args.run_id}_accounts"
        env["MYSQL_V18_FORMAL_PREPARED"] = str(prepared)
        completed = run([
            str(PYTHON), str(ACCOUNTS_SCRIPT), "--run-id", accounts_id, "--verify-existing",
        ], env=env, timeout=900)
        if completed.returncode != 0:
            raise RuntimeError(f"v18 formal account verification failed: {completed.stderr[-1000:]}")
        accounts = HISTORY / accounts_id / "benchmark_accounts_binding.json"
        env["MYSQL_V18_FORMAL_ACCOUNTS"] = str(accounts)
        env["MYSQL_V18_COORDINATION_LEASE_HOLDER_PID"] = str(os.getpid())
        env["MYSQL_V18_COORDINATION_CONTRACT"] = str(coordination.CONTRACT_PATH)
        r0_id = f"{args.run_id}_r0"
        completed = subprocess.run(
            [str(PYTHON), str(R0_SCRIPT), "--run-id", r0_id], cwd=ROOT,
            env=env, text=True, check=False,
        )
        held_seconds = coordination.release(lease)
        lease = None
        algorithm_returncode = None
        algorithm_run_id = f"{args.run_id}_algorithm_c_v2"
        if completed.returncode == 0:
            launch_command = [
                str(PYTHON), str(ALGORITHM_LAUNCHER),
                "--run-id", algorithm_run_id,
                "--checkpoint", str(CHECKPOINT),
                "--runtime-preflight", str(preflight),
                "--prepared-binding", str(prepared),
                "--benchmark-accounts", str(accounts),
                "--fresh-r0", str(HISTORY / r0_id / "r0_binding.json"),
                "--replay-quarantine", str(REPLAY_QUARANTINE),
            ]
            if ALGORITHM_FRESH_SEARCH:
                launch_command.append("--fresh-search")
            launched = run(launch_command, timeout=120)
            algorithm_returncode = launched.returncode
            if launched.returncode != 0:
                raise RuntimeError(
                    f"Algorithm C v2 launch failed: {launched.stderr[-1000:]}"
                )
        state_path.write_text(json.dumps({
            "status": "ALGORITHM_C_STARTED" if algorithm_returncode == 0 else "R0_FAILED_CLOSED",
            "attempt": attempt, "preflight": str(preflight), "prepared": str(prepared),
            "accounts": str(accounts), "r0": str(HISTORY / r0_id / "r0_binding.json"),
            "r0_returncode": completed.returncode, "external_container_mutation": False,
            "coordination_lock_path": str(coordination.LOCK_PATH),
            "lease_held_seconds": held_seconds,
            "algorithm_run_id": algorithm_run_id,
            "algorithm_launch_returncode": algorithm_returncode,
        }, indent=2) + "\n")
        return completed.returncode
    finally:
        if lease is not None:
            coordination.release(lease)


if __name__ == "__main__":
    raise SystemExit(main())
