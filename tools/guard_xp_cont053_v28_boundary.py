#!/usr/bin/env python3
"""Stop the old XP controller at the v28 boundary after cont053 commits.

The caller must first stop only the controller while rank 5 is active.  Its
existing child replay is allowed to finish.  This guard then resumes the
controller only long enough to perform final baseline recovery and atomically
commit cont053, and stops it again before a cont054 physical call can begin.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import time
from pathlib import Path
from typing import Any, Dict, List


EXPECTED_INCUMBENT_SCORE = -49.201616429624124


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def process_state(pid: int) -> str:
    status = Path(f"/proc/{pid}/status")
    if not status.exists():
        return "absent"
    for line in status.read_text(encoding="utf-8").splitlines():
        if line.startswith("State:"):
            return line.split()[1]
    raise RuntimeError(f"cannot read state for PID {pid}")


def children(pid: int) -> List[int]:
    path = Path(f"/proc/{pid}/task/{pid}/children")
    if not path.exists():
        return []
    return [int(item) for item in path.read_text(encoding="utf-8").split()]


def descendants(pid: int) -> List[int]:
    pending = children(pid)
    found: List[int] = []
    while pending:
        child = pending.pop()
        if child in found:
            continue
        found.append(child)
        pending.extend(children(child))
    return sorted(found)


def verify_precommit(campaign: Path, pid: int, expected_state_sha256: str) -> None:
    state_path = campaign / "state.json"
    if sha256(state_path) != expected_state_sha256:
        raise RuntimeError("pre-cont053 state SHA mismatch")
    state = load(state_path)
    last_batch = state.get("last_batch") or {}
    if not (
        int(state.get("accepted_count", -1)) == 4
        and int(state.get("accepted_target", -1)) == 10
        and int(state.get("next_batch_index", -1)) == 53
        and state.get("status") == "running"
        and float(state.get("incumbent_score")) == EXPECTED_INCUMBENT_SCORE
        and last_batch.get("batch_id") == "cont052"
        and last_batch.get("status") == "complete"
    ):
        raise RuntimeError("controller is not at the expected pre-cont053 state")
    batch = campaign / "batches" / "cont053" / "serial_replay"
    for rank in range(1, 5):
        if not (batch / f"rank_{rank:02d}" / "result.json").is_file():
            raise RuntimeError(f"cont053 rank {rank} is not complete")
    rank5 = batch / "rank_05"
    if not (rank5 / "candidate.json").is_file() or (rank5 / "result.json").exists():
        raise RuntimeError("cont053 rank 5 is not the sole in-flight replay")
    if (campaign / "batches" / "cont053" / "replay_summary.json").exists():
        raise RuntimeError("cont053 was already committed before boundary guard")
    if process_state(pid) not in {"T", "t"}:
        raise RuntimeError("caller must SIGSTOP only the old controller first")


def verify_postcommit(campaign: Path) -> Dict[str, Any]:
    state = load(campaign / "state.json")
    last_batch = state.get("last_batch") or {}
    if not (
        int(state.get("accepted_count", -1)) == 4
        and int(state.get("accepted_target", -1)) == 10
        and int(state.get("next_batch_index", -1)) == 54
        and state.get("status") == "running"
        and float(state.get("incumbent_score")) == EXPECTED_INCUMBENT_SCORE
        and last_batch.get("batch_id") == "cont053"
        and last_batch.get("mode") == "diagnostic_validation"
        and last_batch.get("status") == "complete"
        and int(last_batch.get("physical_calls", -1)) == 5
        and int(last_batch.get("accepted", -1)) == 0
        and 0 <= int(last_batch.get("technical_failures", -1)) <= 5
    ):
        raise RuntimeError("cont053 post-commit state contract mismatch")
    return state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-root", required=True, type=Path)
    parser.add_argument("--controller-pid", required=True, type=int)
    parser.add_argument("--expected-pre-state-sha256", required=True)
    parser.add_argument("--child-timeout-seconds", default=1200.0, type=float)
    parser.add_argument("--commit-timeout-seconds", default=30.0, type=float)
    parser.add_argument("--poll-seconds", default=0.01, type=float)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    campaign = args.campaign_root.resolve()
    pid = args.controller_pid
    verify_precommit(campaign, pid, args.expected_pre_state_sha256)
    result: Dict[str, Any] = {
        "status": "dry_run_passed",
        "controller_pid": pid,
        "pre_state_sha256": sha256(campaign / "state.json"),
        "controller_state": process_state(pid),
        "physical_workload_calls_started_by_guard": 0,
    }
    if not args.apply:
        print(json.dumps(result, indent=2, sort_keys=True), flush=True)
        return 0

    child_deadline = time.monotonic() + args.child_timeout_seconds
    while time.monotonic() < child_deadline:
        active = [
            child
            for child in descendants(pid)
            if process_state(child) not in {"Z", "X", "absent"}
        ]
        if not active:
            break
        time.sleep(min(0.5, args.poll_seconds * 10.0))
    else:
        raise RuntimeError("cont053 rank 5 did not quiesce before timeout")

    os.kill(pid, signal.SIGCONT)
    commit_deadline = time.monotonic() + args.commit_timeout_seconds
    try:
        while time.monotonic() < commit_deadline:
            state = load(campaign / "state.json")
            if (
                int(state.get("next_batch_index", -1)) == 54
                and (state.get("last_batch") or {}).get("batch_id") == "cont053"
                and (state.get("last_batch") or {}).get("status") == "complete"
            ):
                os.kill(pid, signal.SIGSTOP)
                break
            if process_state(pid) == "absent":
                raise RuntimeError("old XP controller exited before cont053 commit")
            time.sleep(args.poll_seconds)
        else:
            os.kill(pid, signal.SIGSTOP)
            raise RuntimeError("cont053 state commit was not observed before timeout")
    except Exception:
        if process_state(pid) not in {"T", "t", "absent"}:
            os.kill(pid, signal.SIGSTOP)
        raise

    for _ in range(100):
        if process_state(pid) in {"T", "t"}:
            break
        time.sleep(args.poll_seconds)
    else:
        raise RuntimeError("old XP controller did not stop after cont053 commit")

    verify_postcommit(campaign)
    result.update(
        {
            "status": "boundary_stopped_and_verified",
            "post_state_sha256": sha256(campaign / "state.json"),
            "controller_state_after": process_state(pid),
            "cont054_exists": (campaign / "batches" / "cont054").exists(),
            "old_controller_must_exit_before_v27_launch": True,
        }
    )
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
