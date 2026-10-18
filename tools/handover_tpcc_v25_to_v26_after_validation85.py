#!/usr/bin/env python3
"""Fail-closed live handover from TPC-C v25 to v26 at validation85 end."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict

try:
    import tpcc_autonomous_target10_controller_v25 as v25
except ModuleNotFoundError:
    from tools import tpcc_autonomous_target10_controller_v25 as v25


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def atomic_json(path: Path, value: Dict[str, Any]) -> None:
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def process_state(pid: int) -> str:
    """Return the one-letter Linux process state, or an empty string."""
    try:
        lines = Path(f"/proc/{pid}/status").read_text(
            encoding="utf-8",
            errors="replace",
        ).splitlines()
    except FileNotFoundError:
        return ""
    for line in lines:
        if line.startswith("State:"):
            fields = line.split()
            return fields[1] if len(fields) >= 2 else ""
    return ""


def ensure_stopped(pid: int, attempts: int = 20) -> None:
    """Fail closed unless *pid* is observably stopped.

    A controller launched as a foreground tmux job can receive an automatic
    SIGCONT after the first stop notification.  Reasserting SIGSTOP and
    checking /proc closes that job-control race without stopping the child
    workload process.
    """
    for _ in range(attempts):
        if not alive(pid):
            raise RuntimeError("old controller disappeared while freezing")
        if process_state(pid) in {"T", "t"}:
            return
        os.kill(pid, signal.SIGSTOP)
        time.sleep(0.05)
    raise RuntimeError("old controller could not be observably frozen")


def cmdline(pid: int) -> str:
    path = Path(f"/proc/{pid}/cmdline")
    if not path.is_file():
        return ""
    return path.read_bytes().replace(b"\0", b" ").decode(
        "utf-8",
        errors="replace",
    ).strip()


def current_identity_matches(
    current: Dict[str, Any],
    expected_root: Path,
    expected_mode: str,
) -> bool:
    return bool(
        current.get("mode") == expected_mode
        and Path(str(current.get("root"))).resolve() == expected_root
        # The ledger is created only by the child runner.  Requiring it before
        # SIGSTOP proves subprocess.run has launched the workload, so freezing
        # the parent cannot prevent the child from starting.
        and Path(str(current.get("ledger_path"))).is_file()
    )


def current_complete(current: Dict[str, Any]) -> bool:
    return bool(
        Path(str(current.get("summary_path"))).is_file()
        and Path(str(current.get("ledger_path"))).is_file()
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-pid", type=int, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--formal-state", type=Path, required=True)
    parser.add_argument("--expected-formal-sha256", required=True)
    parser.add_argument("--expected-run-root", type=Path, required=True)
    parser.add_argument(
        "--expected-mode",
        choices=("diagnostic", "diagnostic_validation", "formal"),
        required=True,
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--wrapper", type=Path, required=True)
    parser.add_argument("--expected-wrapper-sha256", required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--wait-seconds", type=int, default=1800)
    args = parser.parse_args()

    state_path = args.state.resolve()
    formal_state = args.formal_state.resolve()
    expected_root = args.expected_run_root.resolve()
    manifest = args.manifest.resolve()
    wrapper = args.wrapper.resolve()
    python = args.python.resolve()
    audit_root = state_path.parent

    if sha256(formal_state) != args.expected_formal_sha256:
        raise RuntimeError("formal state SHA mismatch before wait")
    if sha256(manifest) != args.expected_manifest_sha256:
        raise RuntimeError("v26 manifest SHA mismatch")
    if sha256(wrapper) != args.expected_wrapper_sha256:
        raise RuntimeError("v26 wrapper SHA mismatch")
    if not python.is_file():
        raise RuntimeError("pinned Python interpreter is missing")
    old_command = cmdline(args.old_pid)
    if (
        "tpcc_autonomous_target10_controller_v25.py" not in old_command
        or "--armed" not in old_command
    ):
        raise RuntimeError("old PID command line mismatch")

    deadline = time.monotonic() + args.wait_seconds
    stopped = False
    old_terminated = False
    while time.monotonic() < deadline:
        if not alive(args.old_pid):
            raise RuntimeError("old controller exited before handover boundary")
        state = load_json(state_path)
        current = state.get("current_run")
        if isinstance(current, dict) and current_identity_matches(
            current,
            expected_root,
            args.expected_mode,
        ):
            frozen_current = copy.deepcopy(current)
            # Do not SIGSTOP a foreground tmux pane: tmux immediately sends a
            # SIGCONT for job-control recovery.  Instead, poll the atomic
            # summary publication at high frequency.  The summary is written
            # only after baseline restoration and before subprocess.run can
            # return to the controller, which provides the safe handover edge.
            while time.monotonic() < deadline and not current_complete(frozen_current):
                if not alive(args.old_pid):
                    raise RuntimeError("old controller disappeared before clean summary")
                observed_state = load_json(state_path)
                observed_current = observed_state.get("current_run")
                if (
                    not isinstance(observed_current, dict)
                    or not current_identity_matches(
                        observed_current,
                        expected_root,
                        args.expected_mode,
                    )
                ):
                    raise RuntimeError("controller crossed expected run boundary")
                time.sleep(0.01)
            if not current_complete(frozen_current):
                raise RuntimeError(
                    "timed out waiting for run clean summary"
                )
            # Terminate immediately at the summary publication edge, before
            # v25 can prepare or launch another sealed run.  The completed
            # child has already restored baseline and published its ledger.
            os.kill(args.old_pid, signal.SIGTERM)
            for _ in range(100):
                if not alive(args.old_pid):
                    break
                time.sleep(0.01)
            if alive(args.old_pid):
                raise RuntimeError("old controller did not exit at summary edge")
            old_terminated = True
            break
        time.sleep(0.25)
    else:
        raise RuntimeError("timed out waiting for expected clean boundary")

    try:
        if sha256(formal_state) != args.expected_formal_sha256:
            raise RuntimeError("formal state changed at handover boundary")
        current = copy.deepcopy(frozen_current)
        summary_path = Path(current["summary_path"]).resolve()
        ledger_path = Path(current["ledger_path"]).resolve()
        current["summary_sha256"] = sha256(summary_path)
        current["ledger_sha256"] = sha256(ledger_path)
        audit = v25.audit_completed_run(current, formal_state)
        if not (
            audit.get("clean") is True
            and int(audit.get("physical_calls", -1)) == v25.CAP
            and int(audit.get("valid_replays", -1)) == v25.CAP
            and int(audit.get("technical_failures", -1)) == 0
            and audit.get("baseline_restored") is True
        ):
            raise RuntimeError("validation85 boundary is not clean")

        preflight = {
            "contract": "tpcc_v25_to_v26_clean_boundary_handover_v1",
            "old_pid": args.old_pid,
            "old_cmdline": old_command,
            "controller_state_sha256": sha256(state_path),
            "formal_state_sha256": sha256(formal_state),
            "manifest_sha256": sha256(manifest),
            "wrapper_sha256": sha256(wrapper),
            "completed_run_audit": audit,
            "physical_workload_calls_added": 0,
            "formal_state_mutated": False,
            "manual_candidate_injection": False,
            "timestamp_epoch": time.time(),
        }
        atomic_json(
            audit_root / "handover_v26_preflight.json",
            preflight,
        )

        if not old_terminated:
            raise RuntimeError("old controller was not terminated at summary edge")

        log_path = audit_root / "controller_v26_prethird.log"
        log_handle = log_path.open("ab", buffering=0)
        process = subprocess.Popen(
            [
                str(python),
                "-u",
                str(wrapper),
                str(manifest),
                "--armed",
                "--manifest-sha256",
                args.expected_manifest_sha256,
            ],
            cwd=str(audit_root.parent.parent),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        log_handle.close()
        time.sleep(3.0)
        if process.poll() is not None:
            raise RuntimeError(
                f"v26 controller exited early: returncode={process.returncode}"
            )
        new_command = cmdline(process.pid)
        if str(wrapper) not in new_command or "--armed" not in new_command:
            raise RuntimeError("new controller command line mismatch")
        if sha256(formal_state) != args.expected_formal_sha256:
            raise RuntimeError("formal state changed during zero-call handover")

        result = {
            **preflight,
            "new_pid": process.pid,
            "new_cmdline": new_command,
            "log_path": str(log_path),
            "status": "v26_running",
            "completed_at_epoch": time.time(),
        }
        atomic_json(audit_root / "handover_v26_result.json", result)
        (audit_root / "controller_v26_prethird.pid").write_text(
            f"{process.pid}\n",
            encoding="utf-8",
        )
        print(json.dumps(result, indent=2, sort_keys=True), flush=True)
        return 0
    except Exception:
        if stopped and alive(args.old_pid):
            os.kill(args.old_pid, signal.SIGCONT)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
