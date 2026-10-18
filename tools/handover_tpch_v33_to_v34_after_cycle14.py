#!/usr/bin/env python3
"""Fail-closed remote-local handover from TPC-H v33 to v34."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
from typing import Any, Dict, Mapping


V33_TOKEN = "tpch_fresh10_target10_loop_v33_prethird_bound.py"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
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


def cmdline(pid: int) -> str:
    path = Path(f"/proc/{pid}/cmdline")
    if not path.is_file():
        return ""
    return path.read_bytes().replace(b"\0", b" ").decode(
        "utf-8", errors="replace"
    ).strip()


def process_state(pid: int) -> str:
    path = Path(f"/proc/{pid}/status")
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("State:"):
            fields = line.split()
            return fields[1] if len(fields) >= 2 else ""
    return ""


def direct_children(pid: int) -> list[int]:
    path = Path(f"/proc/{pid}/task/{pid}/children")
    if not path.is_file():
        return []
    return [int(value) for value in path.read_text(encoding="utf-8").split()]


def boundary_matches(
    state: Mapping[str, Any], completed_cycle: int, accepted: int
) -> bool:
    cycles = state.get("cycles") or []
    last = cycles[-1] if cycles else {}
    return bool(
        state.get("schema_version") == "tpch_fresh10_target10_loop_v33"
        and int(state.get("controller_version", -1)) == 33
        # v33 records the completed cycle and then immediately advances the
        # controller-level status to ``preparing_cycle``.  The completed-cycle
        # record is the durable boundary; requiring the transient controller
        # status makes the watcher lose a race that lasts only one state write.
        and state.get("status")
        in {"reexplore_prethird_acceptance_impossible", "preparing_cycle"}
        and int(state.get("accepted_count", -1)) == accepted
        and int(state.get("next_cycle", -1)) == completed_cycle + 1
        and int(last.get("cycle", -1)) == completed_cycle
        and last.get("status") == "reexplore_prethird_acceptance_impossible"
        and int(last.get("accepted_before", -1)) == accepted
        and int(last.get("accepted_after", -1)) == accepted
        and last.get("formal_started") is False
        and last.get("formal_state_mutated") is False
    )


def clean_diagnostic_state(root: Path, accepted: int) -> bool:
    state_path = root / "diagnostic_state.json"
    summary_path = root / "diagnostic_summary.json"
    if not state_path.is_file() or not summary_path.is_file():
        return False
    state = load(state_path)
    summary = load(summary_path)
    return bool(
        state.get("status") == "complete"
        and int(state.get("accepted_count", -1)) == accepted
        and int(state.get("physical_call_count", -1)) == 5
        and int(state.get("valid_replay_count", -1)) == 5
        and int(state.get("technical_failure_count", -1)) == 0
        and state.get("baseline_restored") is True
        and int(summary.get("physical_call_count", -1)) == 5
        and int(summary.get("valid_replay_count", -1)) == 5
        and int(summary.get("technical_failure_count", -1)) == 0
        and summary.get("baseline_restored") is True
        and summary.get("accepted_update_applied") is False
        and summary.get("incumbent_update_applied") is False
        and summary.get("range_update_applied") is False
        and summary.get("gradient_update_applied") is False
    )


def partial_has_runtime(root: Path) -> bool:
    return any(
        (root / name).exists()
        for name in (
            "diagnostic_probe",
            "diagnostic_exact_repeat",
            "diagnostic_robust_repeat",
            "formal_package",
            "formal_run",
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-pid", type=int, required=True)
    parser.add_argument("--controller-state", type=Path, required=True)
    parser.add_argument("--accepted-counter", type=Path, required=True)
    parser.add_argument("--expected-accepted", type=int, required=True)
    parser.add_argument("--resume-state", type=Path, required=True)
    parser.add_argument("--expected-resume-sha256", required=True)
    parser.add_argument("--loop-root", type=Path, required=True)
    parser.add_argument("--completed-cycle", type=int, required=True)
    parser.add_argument("--v34", type=Path, required=True)
    parser.add_argument("--expected-v34-sha256", required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--wait-seconds", type=int, default=10800)
    args = parser.parse_args()

    state_path = args.controller_state.resolve()
    counter = args.accepted_counter.resolve()
    resume = args.resume_state.resolve()
    loop_root = args.loop_root.resolve()
    v34 = args.v34.resolve()
    python = args.python.resolve()
    completed_root = loop_root / f"cycle_{args.completed_cycle:03d}"
    next_root = loop_root / f"cycle_{args.completed_cycle + 1:03d}"
    gate_path = completed_root / "prethird_acceptance_feasibility.json"
    audit_path = loop_root / (
        f"v33_to_v34_cycle{args.completed_cycle:03d}_handover.json"
    )

    if sha256(resume) != args.expected_resume_sha256:
        raise RuntimeError("resume state SHA mismatch before wait")
    if sha256(v34) != args.expected_v34_sha256:
        raise RuntimeError("v34 SHA mismatch")
    if not python.is_file():
        raise RuntimeError("pinned Python is missing")
    old_command = cmdline(args.old_pid)
    if V33_TOKEN not in old_command:
        raise RuntimeError("old controller command mismatch")

    deadline = time.monotonic() + args.wait_seconds
    while time.monotonic() < deadline:
        if not alive(args.old_pid):
            raise RuntimeError("v33 exited before the handover boundary")
        accepted = int(counter.read_text(encoding="utf-8").strip())
        if accepted != args.expected_accepted:
            raise RuntimeError("accepted counter changed before handover")
        state = load(state_path)
        if not boundary_matches(state, args.completed_cycle, accepted):
            time.sleep(0.05)
            continue
        gate = load(gate_path)
        if not (
            gate.get("passed") is False
            and int(gate.get("physical_workload_calls", -1)) == 0
            and gate.get("formal_state_mutated") is False
            and clean_diagnostic_state(
                completed_root / "diagnostic_probe",
                accepted,
            )
            and clean_diagnostic_state(
                completed_root / "diagnostic_exact_repeat",
                accepted,
            )
            and sha256(resume) == args.expected_resume_sha256
        ):
            raise RuntimeError("cycle boundary evidence is not clean")

        os.kill(args.old_pid, signal.SIGSTOP)
        for _ in range(100):
            if process_state(args.old_pid) in {"T", "t"}:
                break
            time.sleep(0.01)
        else:
            raise RuntimeError("v33 could not be frozen")
        try:
            frozen = load(state_path)
            if not boundary_matches(frozen, args.completed_cycle, accepted):
                raise RuntimeError("v33 crossed boundary before freeze")
            child_deadline = time.monotonic() + 120
            while direct_children(args.old_pid) and time.monotonic() < child_deadline:
                time.sleep(0.05)
            if direct_children(args.old_pid):
                raise RuntimeError("offline child did not finish while v33 was frozen")
            if partial_has_runtime(next_root):
                raise RuntimeError("next cycle crossed the runtime boundary")
            frozen_sha = sha256(state_path)
            os.kill(args.old_pid, signal.SIGTERM)
            os.kill(args.old_pid, signal.SIGCONT)
            for _ in range(200):
                if not alive(args.old_pid):
                    break
                time.sleep(0.01)
            if alive(args.old_pid):
                raise RuntimeError("v33 did not exit")

            log_path = loop_root / "controller_v34_hierarchical.log"
            log_handle = log_path.open("ab", buffering=0)
            process = subprocess.Popen(
                [
                    str(python),
                    "-u",
                    str(v34),
                    "--expected-state-sha256",
                    frozen_sha,
                    "--expected-next-cycle",
                    str(args.completed_cycle + 1),
                ],
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            log_handle.close()
            time.sleep(3.0)
            if process.poll() is not None:
                raise RuntimeError(f"v34 exited early: {process.returncode}")
            if sha256(resume) != args.expected_resume_sha256:
                raise RuntimeError("formal resume state changed during handover")
            result = {
                "contract": "tpch_v33_to_v34_cycle_boundary_handover_v1",
                "old_pid": args.old_pid,
                "old_cmdline": old_command,
                "new_pid": process.pid,
                "new_cmdline": cmdline(process.pid),
                "completed_cycle": args.completed_cycle,
                "next_cycle": args.completed_cycle + 1,
                "controller_state_sha256": frozen_sha,
                "resume_state_sha256": sha256(resume),
                "v34_sha256": sha256(v34),
                "prethird_gate_sha256": sha256(gate_path),
                "physical_workload_calls_added": 0,
                "formal_state_mutated": False,
                "manual_candidate_injection": False,
                "status": "v34_running",
                "completed_at_epoch": time.time(),
            }
            atomic_json(audit_path, result)
            print(json.dumps(result, indent=2, sort_keys=True), flush=True)
            return 0
        except Exception:
            if alive(args.old_pid):
                os.kill(args.old_pid, signal.SIGCONT)
            raise
    raise RuntimeError("timed out waiting for cycle boundary")


if __name__ == "__main__":
    raise SystemExit(main())
