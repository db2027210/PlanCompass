#!/usr/bin/env python3
"""Run one owned command and durably record its exit status and orphan cleanup."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


PR_SET_CHILD_SUBREAPER = 36


def write_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def enable_subreaper() -> bool:
    try:
        return ctypes.CDLL(None).prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) == 0
    except (AttributeError, OSError):
        return False


def direct_children(parent_pid: int) -> List[int]:
    children: List[int] = []
    for status_path in Path("/proc").glob("[0-9]*/status"):
        try:
            lines = status_path.read_text(encoding="utf-8", errors="replace").splitlines()
            ppid_line = next(line for line in lines if line.startswith("PPid:"))
            if int(ppid_line.split()[1]) == int(parent_pid):
                children.append(int(status_path.parent.name))
        except (FileNotFoundError, PermissionError, OSError, StopIteration, ValueError):
            continue
    return sorted(children)


def terminate_owned_orphans(parent_pid: int) -> List[int]:
    terminated: List[int] = []
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        children = direct_children(parent_pid)
        if not children:
            break
        for pid in children:
            try:
                pgid = os.getpgid(pid)
                os.killpg(pgid, signal.SIGTERM)
                terminated.append(pid)
            except (ProcessLookupError, PermissionError, OSError):
                continue
        time.sleep(0.2)
    for pid in direct_children(parent_pid):
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
            terminated.append(pid)
        except (ProcessLookupError, PermissionError, OSError):
            continue
    return sorted(set(terminated))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-path", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("a command is required after --")

    started = time.time()
    audit: Dict[str, Any] = {
        "event": "owned_runner_exit_audit",
        "guard_pid": os.getpid(),
        "started_at": started,
        "started_at_text": time.strftime("%Y-%m-%d %H:%M:%S"),
        "command": command,
        "subreaper_enabled": enable_subreaper(),
        "status": "running",
    }
    child: Optional[subprocess.Popen[Any]] = None

    def handle_signal(signum: int, _frame: Any) -> None:
        event = {
            "signal": int(signum),
            "signal_name": signal.Signals(signum).name,
            "received_at": time.time(),
        }
        audit.setdefault("guard_signal_events", []).append(event)
        audit["guard_signal"] = int(signum)
        audit["guard_signal_name"] = signal.Signals(signum).name
        forward_term = os.environ.get("RUNNER_EXIT_AUDIT_FORWARD_TERM", "0") == "1"
        should_forward = signum == signal.SIGINT or (
            signum in (signal.SIGTERM, signal.SIGHUP) and forward_term
        )
        event["forwarded_to_runner"] = should_forward
        write_json(args.audit_path, audit)
        # Detached formal runs have repeatedly received infrastructure-level
        # TERM/HUP while their owned VM and scheduler remained healthy.  Keep
        # those signals as audit evidence instead of turning them into false
        # optimizer failures.  SIGINT remains the explicit operator stop path;
        # TERM forwarding can be restored for a controlled caller via env.
        if should_forward and child is not None and child.poll() is None:
            try:
                os.killpg(child.pid, signum)
            except (ProcessLookupError, PermissionError, OSError):
                pass

    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, handle_signal)
    write_json(args.audit_path, audit)

    try:
        child = subprocess.Popen(command, start_new_session=True)
        audit["runner_pid"] = child.pid
        write_json(args.audit_path, audit)
        returncode = child.wait()
        audit["returncode"] = int(returncode)
        audit["exit_signal"] = int(-returncode) if returncode < 0 else None
        audit["exit_signal_name"] = (
            signal.Signals(-returncode).name if returncode < 0 else None
        )
        audit["status"] = "finished" if returncode == 0 else "failed"
        return int(returncode) if returncode >= 0 else 128 + int(-returncode)
    except Exception as exc:
        audit["status"] = "guard_failed"
        audit["guard_error"] = f"{type(exc).__name__}: {exc}"
        returncode = 125
        return returncode
    finally:
        audit["owned_orphan_pids_terminated"] = terminate_owned_orphans(os.getpid())
        audit["finished_at"] = time.time()
        audit["elapsed_s"] = audit["finished_at"] - started
        write_json(args.audit_path, audit)


if __name__ == "__main__":
    raise SystemExit(main())
