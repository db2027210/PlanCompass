#!/usr/bin/env python3
"""Upgrade one owned Memory v139 run to gated four-worker replay at a round boundary."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import List, Tuple


ROOT = Path(__file__).resolve().parents[1]
OWNED_PROCESS_MARKERS = (
    "run_bonect_phase2_memory_v112_answer_blind_outer_loop.py",
    "bonect_parallel_vm_replay.py",
    "bonect_full_grid_estimator_scan.py",
    "bonect_offline_rescore_scan.py",
    "bonect_real_validate_candidates.py",
    "com.hybench.HyBench",
)


def log(message: str, path: Path) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    print(line, flush=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def read_round(checkpoint: Path) -> int:
    try:
        data = json.loads(checkpoint.read_text(encoding="utf-8"))
        return int(data.get("round") or 0)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return 0


def owned_run_processes(run_id: str) -> List[Tuple[int, str]]:
    result: List[Tuple[int, str]] = []
    self_pid = os.getpid()
    for proc_dir in Path("/proc").iterdir():
        if not proc_dir.name.isdigit():
            continue
        pid = int(proc_dir.name)
        if pid == self_pid:
            continue
        try:
            cmdline = (proc_dir / "cmdline").read_bytes().replace(b"\0", b" ").decode(
                "utf-8", errors="replace"
            )
            comm = (proc_dir / "comm").read_text(encoding="utf-8").strip().lower()
        except OSError:
            continue
        if not (comm.startswith("python") or comm == "java"):
            continue
        if run_id not in cmdline:
            continue
        if any(marker in cmdline for marker in OWNED_PROCESS_MARKERS):
            result.append((pid, cmdline.strip()))
    return sorted(result)


def run_checked(command: List[str]) -> None:
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--old-tmux-session", required=True)
    parser.add_argument("--new-tmux-session", required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--controller-log", required=True)
    parser.add_argument("--primary-pool", required=True)
    parser.add_argument("--fallback-pool", required=True)
    parser.add_argument("--resource-model-dir", required=True)
    parser.add_argument("--target-round", type=int, default=1)
    parser.add_argument("--poll-s", type=float, default=5.0)
    parser.add_argument("--clean-exit-timeout-s", type=float, default=60.0)
    args = parser.parse_args()

    checkpoint = Path(args.checkpoint).resolve()
    controller_log = Path(args.controller_log).resolve()
    upgrade_log = controller_log.parent / "four_worker_round_boundary_upgrade.log"
    log(
        f"waiting for checkpoint round>={args.target_round} run_id={args.run_id}",
        upgrade_log,
    )
    while read_round(checkpoint) < int(args.target_round):
        time.sleep(max(1.0, float(args.poll_s)))

    log("round boundary reached; sending Ctrl-C to owned tmux foreground group", upgrade_log)
    run_checked(["tmux", "send-keys", "-t", args.old_tmux_session, "C-c"])
    deadline = time.time() + max(1.0, float(args.clean_exit_timeout_s))
    remaining = owned_run_processes(args.run_id)
    while remaining and time.time() < deadline:
        time.sleep(1.0)
        remaining = owned_run_processes(args.run_id)
    if remaining:
        log(
            "upgrade aborted: owned run processes did not exit cleanly: "
            + ",".join(str(pid) for pid, _ in remaining),
            upgrade_log,
        )
        return 2

    subprocess.run(["tmux", "kill-session", "-t", args.old_tmux_session], cwd=ROOT, check=False)
    command = [
        "python3",
        "-u",
        "shell/run_bonect_phase2_memory_v112_answer_blind_outer_loop.py",
        "--run-id",
        args.run_id,
        "--resume-scan",
        "--no-apply-vm-profile",
        "--worker-pool-json",
        str(Path(args.primary_pool).resolve()),
        "--replay-workers",
        "4",
        "--fallback-worker-pool-json",
        str(Path(args.fallback_pool).resolve()),
        "--fallback-replay-workers",
        "2",
        "--force-baseline-preflight",
        "--resource-model-dir",
        str(Path(args.resource_model_dir).resolve()),
        "--candidate-workers",
        "16",
        "--pg-endpoint",
        "192.0.2.12:25432/hybench_sf10x",
        "--pg-endpoint",
        "192.0.2.11:25432/hybench_sf10x",
        "--pg-endpoint",
        "192.0.2.14:25432/hybench_sf10x",
    ]
    shell_command = (
        f"cd {shlex.quote(str(ROOT))} && set -o pipefail; "
        + " ".join(shlex.quote(value) for value in command)
        + f" 2>&1 | tee -a {shlex.quote(str(controller_log))}; "
        + "echo CONTROLLER_EXIT=$?; sleep 7200"
    )
    run_checked(
        [
            "tmux",
            "new-session",
            "-d",
            "-s",
            args.new_tmux_session,
            "zsh",
            "-lc",
            shell_command,
        ]
    )
    log(
        f"launched {args.new_tmux_session} with four-worker gate and two-worker fallback",
        upgrade_log,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
