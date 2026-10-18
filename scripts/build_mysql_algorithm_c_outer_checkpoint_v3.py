#!/usr/bin/env python3
"""Build a checkpoint after a proven pre-measurement watchdog interruption."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_outer_checkpoint_v2 as predecessor


_PREDECESSOR_RECONSTRUCT = predecessor.reconstruct_completed_state
_WATCHDOG_ERROR = (
    "RuntimeError: runtime watchdog stopped formal run after "
    "runtime_interference_detected_under_exclusive_wave_lease"
)
_QUIET_GATE_ERROR = (
    "RuntimeError: cross-project quiet gate exhausted its single bounded "
    "attempt"
)


def clean_premeasurement_watchdog_boundary(
    run_root: Path, state: Mapping[str, Any], completed: int,
) -> bool:
    """Accept only an interrupted reset that cannot have mutated candidate state."""

    interruption = state.get("watchdog_interruption")
    if not isinstance(interruption, Mapping):
        return False
    runtime_stage = str(interruption.get("runtime_stage") or "")
    match = re.fullmatch(
        rf"OUTER_{completed + 1:03d}_BATCH_([1-5])_RESETTING_XP",
        runtime_stage,
    )
    if match is None:
        return False
    rank = int(match.group(1))
    failed_root = run_root / f"outer_{completed + 1:03d}"
    reset_root = run_root / "xp_resets" / (
        f"outer_{completed + 1:03d}_batch_{rank}"
    )
    interrupted_batch = failed_root / f"top5_batch_{rank}"
    return (
        state.get("status") == "FAILED_CLOSED"
        and state.get("stage") == "FAILED"
        and state.get("error") == _WATCHDOG_ERROR
        and interruption.get("event")
        == "runtime_interference_detected_under_exclusive_wave_lease"
        and interruption.get("formal_state_credit_increment") == 0
        and interruption.get("interrupted_candidate_replay_prohibited") is True
        and failed_root.is_dir()
        and not any(failed_root.glob("*.decision.json"))
        and not (interrupted_batch / "batch_result.json").exists()
        and reset_root.is_dir()
        and (reset_root / "pre_reset_session_drain.json").is_file()
        and not (reset_root / "reset_binding.json").exists()
        and not list(reset_root.glob("*/reset_worker.json"))
    )


def reconstruct_completed_state(run_root: Path):
    state_path = run_root / "run_state.json"
    state = predecessor.alg.load(state_path)
    completed = int(state.get("outer_attempt") or 0)
    if not clean_premeasurement_watchdog_boundary(run_root, state, completed):
        return _PREDECESSOR_RECONSTRUCT(run_root)

    original_load = predecessor.alg.load

    def boundary_load(path: Path):
        value = original_load(path)
        if Path(path).resolve() == state_path.resolve():
            value = copy.deepcopy(value)
            value["error"] = _QUIET_GATE_ERROR
        return value

    predecessor.alg.load = boundary_load
    try:
        return _PREDECESSOR_RECONSTRUCT(run_root)
    finally:
        predecessor.alg.load = original_load


def build_checkpoint(run_root: Path) -> dict[str, Any]:
    original = predecessor.reconstruct_completed_state
    predecessor.reconstruct_completed_state = reconstruct_completed_state
    try:
        document = predecessor.build_checkpoint(run_root)
    finally:
        predecessor.reconstruct_completed_state = original
    builder = Path(__file__).resolve()
    document["builder_source_path"] = str(builder)
    document["builder_source_sha256"] = predecessor.alg.sha(builder)
    payload = copy.deepcopy(document)
    payload.pop("checkpoint_sha256", None)
    document["checkpoint_sha256"] = predecessor._canonical_sha256(payload)
    return document


def verify_checkpoint(path: Path) -> dict[str, Any]:
    return predecessor.verify_checkpoint(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.output_dir.name) is None:
        parser.error("unsafe output directory name")
    if args.output_dir.exists():
        raise RuntimeError(f"fresh checkpoint output exists: {args.output_dir}")
    document = build_checkpoint(args.run_root)
    args.output_dir.mkdir(parents=True, mode=0o700)
    path = args.output_dir / "prepared_binding.json"
    predecessor.alg.atomic(path, document)
    print(json.dumps({
        "status": "PREPARED", "checkpoint": str(path),
        "completed_outer_attempts": document["completed_outer_attempts"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
