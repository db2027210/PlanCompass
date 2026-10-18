#!/usr/bin/env python3
"""Wait for sealed Algorithm C, then start the post-terminal ABC replay.

The waiting phase intentionally reads only Algorithm C's launcher status and
the optional launcher PID.  It does not import the replay runner and does not
resolve any Algorithm A/B authority before C has reached a clean terminal.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence


ROOT = Path(__file__).resolve().parents[1]
SCENES = ("job_memory", "tpch_memory", "xp_cpu", "xp_io")
CONTRACT = "algorithm_c_terminal_wait_then_abc_validation_v1"


def read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def process_live(pid: Optional[int]) -> bool:
    if pid is None:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def clean_terminal(launcher: Mapping[str, Any]) -> bool:
    if launcher.get("phase") != "finished":
        return False
    returncodes = launcher.get("returncodes")
    return bool(
        isinstance(returncodes, Mapping)
        and set(returncodes) == set(SCENES)
        and all(returncodes.get(scene) == 0 for scene in SCENES)
        and not launcher.get("failures")
        and not launcher.get("deadline_signals")
    )


def wait_for_clean_terminal(
    *,
    formal_root: Path,
    status_path: Path,
    launcher_pid: Optional[int],
    poll_s: float,
    maximum_wait_s: float,
) -> Dict[str, Any]:
    launcher_path = formal_root / "launcher_status.json"
    started_at = time.time()
    while True:
        if not launcher_path.is_file():
            raise RuntimeError("Algorithm C launcher status is missing")
        launcher = read_json(launcher_path)
        phase = str(launcher.get("phase") or "")
        if clean_terminal(launcher):
            atomic_json(status_path, {
                "contract": CONTRACT,
                "phase": "algorithm_c_clean_terminal_observed",
                "algorithm_c_formal_root": str(formal_root),
                "algorithm_c_launcher_pid": launcher_pid,
                "algorithm_c_launcher": launcher,
                "a_b_authority_loaded_during_wait": False,
                "wait_elapsed_s": time.time() - started_at,
                "updated_at_epoch_s": time.time(),
            })
            return launcher
        if phase in {"failed", "aborted"}:
            raise RuntimeError(f"Algorithm C reached non-clean terminal phase: {phase}")
        if not process_live(launcher_pid):
            raise RuntimeError(
                "Algorithm C launcher exited without a committed clean terminal"
            )
        elapsed_s = time.time() - started_at
        if elapsed_s >= maximum_wait_s:
            raise TimeoutError("timed out waiting for Algorithm C clean terminal")
        atomic_json(status_path, {
            "contract": CONTRACT,
            "phase": "waiting_for_algorithm_c_clean_terminal",
            "algorithm_c_formal_root": str(formal_root),
            "algorithm_c_launcher_pid": launcher_pid,
            "algorithm_c_launcher_live": True,
            "algorithm_c_launcher_phase": phase,
            "a_b_authority_loaded_during_wait": False,
            "wait_elapsed_s": elapsed_s,
            "updated_at_epoch_s": time.time(),
        })
        time.sleep(poll_s)


def run_relay(
    *,
    formal_root: Path,
    output_root: Path,
    status_path: Path,
    log_path: Path,
    launcher_pid: Optional[int],
    seeds: Sequence[int],
    poll_s: float,
    maximum_wait_s: float,
    handoff_script: Path,
    python_bin: Path,
) -> int:
    if len(seeds) != 2 or len(set(seeds)) != 2:
        raise RuntimeError("exactly two distinct validation seeds are required")
    if output_root.exists() and any(output_root.iterdir()):
        raise RuntimeError(f"output root is not empty: {output_root}")

    launcher = wait_for_clean_terminal(
        formal_root=formal_root,
        status_path=status_path,
        launcher_pid=launcher_pid,
        poll_s=poll_s,
        maximum_wait_s=maximum_wait_s,
    )
    command = [
        str(python_bin),
        "-B",
        "-u",
        str(handoff_script),
        "--algorithm-c-formal-root",
        str(formal_root),
        "--output-root",
        str(output_root),
    ]
    for seed in seeds:
        command.extend(("--seed", str(seed)))
    status = {
        "contract": CONTRACT,
        "phase": "postterminal_abc_validation_running",
        "algorithm_c_formal_root": str(formal_root),
        "algorithm_c_launcher": launcher,
        "algorithm_c_terminal_gate_passed_before_a_b_load": True,
        "a_b_authority_loaded_during_wait": False,
        "output_root": str(output_root),
        "seeds": list(seeds),
        "command": command,
        "started_at_epoch_s": time.time(),
    }
    atomic_json(status_path, status)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as stream:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            stdout=stream,
            stderr=subprocess.STDOUT,
            check=False,
        )
    status.update({
        "phase": (
            "postterminal_abc_validation_complete"
            if completed.returncode == 0
            else "postterminal_abc_validation_failed_closed"
        ),
        "returncode": completed.returncode,
        "completed_at_epoch_s": time.time(),
        "log_path": str(log_path),
    })
    atomic_json(status_path, status)
    return completed.returncode


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--algorithm-c-formal-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--status-path", type=Path, required=True)
    parser.add_argument("--log-path", type=Path, required=True)
    parser.add_argument("--launcher-pid", type=int)
    parser.add_argument("--seed", type=int, action="append", required=True)
    parser.add_argument("--poll-s", type=float, default=30.0)
    parser.add_argument("--maximum-wait-s", type=float, default=176400.0)
    parser.add_argument(
        "--handoff-script",
        type=Path,
        default=ROOT / "shell" / "run_pg_algorithm_c_postterminal_abc_validation.py",
    )
    parser.add_argument("--python-bin", type=Path, default=Path(sys.executable))
    return parser.parse_args(argv)


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parse_args(argv)
    try:
        return run_relay(
            formal_root=args.algorithm_c_formal_root.resolve(),
            output_root=args.output_root.resolve(),
            status_path=args.status_path.resolve(),
            log_path=args.log_path.resolve(),
            launcher_pid=args.launcher_pid,
            seeds=args.seed,
            poll_s=max(1.0, args.poll_s),
            maximum_wait_s=max(1.0, args.maximum_wait_s),
            handoff_script=args.handoff_script.resolve(),
            python_bin=args.python_bin.resolve(),
        )
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        atomic_json(args.status_path.resolve(), {
            "contract": CONTRACT,
            "phase": "failed_closed",
            "error": str(exc),
            "a_b_authority_loaded_during_wait": False,
            "failed_at_epoch_s": time.time(),
        })
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
