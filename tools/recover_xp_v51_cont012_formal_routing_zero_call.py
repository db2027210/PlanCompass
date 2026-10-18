#!/usr/bin/env python3
"""Fail-closed zero-call recovery for XP cont012's formal-routing stop."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
import shutil
import sys


CAMPAIGN = Path(
    "/home/anonymous/program/resource_adaptive/"
    "history/xp_final_algorithm_fresh10_v38_20260803"
)
STATE = CAMPAIGN / "state.json"
SOURCE_STATE_SHA256 = "649f8ca7fa559b11f8ac70f728ee9ebb77bc5b1797b2e98b84ff00d7142e2b6d"
SELECTION = CAMPAIGN / "batches/cont012/score_metadata_normalization/selection.json"
SELECTION_SHA256 = "2ae7ad9cac609b03c527a38d14bc62d044b23ee354105b53cd51c690e90a1dd8"
HANDOFF = CAMPAIGN / "batches/cont012/preformal_robust_handoff.json"
HANDOFF_SHA256 = "f26a4a3df79cdc8230223a0bbffc439d8a3c9603ffb34ed7ccc77ba061f9cff8"
CONTRACT = "xp_v51_cont012_formal_routing_zero_call_recovery_v1"
FORMAL_KEYS = (
    "accepted_count",
    "accepted_fingerprints",
    "incumbent",
    "incumbent_score",
    "incumbent_params",
    "parameter_ranges",
    "range_state",
    "gradient_state",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"XP v51 expected JSON object: {path}")
    return value


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def validate_source(state: dict) -> None:
    if sha256(STATE) != SOURCE_STATE_SHA256:
        raise RuntimeError("XP v51 source state SHA mismatch")
    if sha256(SELECTION) != SELECTION_SHA256 or sha256(HANDOFF) != HANDOFF_SHA256:
        raise RuntimeError("XP v51 cont012 preworkload artifact SHA mismatch")
    phase = state.get("active_phase") or {}
    last = state.get("last_batch") or {}
    if not (
        state.get("status") == "blocked_preworkload"
        and state.get("last_error")
        == "RuntimeError: XP v39 pending selection must be diagnostic trusted3+probe2"
        and int(state.get("accepted_count", -1)) == 0
        and int(state.get("next_batch_index", -1)) == 12
        and last.get("batch_id") == "cont011"
        and int(last.get("physical_calls", -1)) == 5
        and int(last.get("technical_failures", -1)) == 0
        and phase.get("batch_id") == "cont012"
        and phase.get("stage") == "offline_reexploration"
        and int(phase.get("physical_workload_calls", -1)) == 0
        and not (CAMPAIGN / "batches/cont012/selection.json").exists()
        and not (CAMPAIGN / "batches/cont012/replay_summary.json").exists()
        and not (CAMPAIGN / "batches/cont012/serial_replay").exists()
    ):
        raise RuntimeError("XP v51 observed state is not the sealed zero-call boundary")


def recovered(state: dict) -> dict:
    result = copy.deepcopy(state)
    before = {key: copy.deepcopy(state.get(key)) for key in FORMAL_KEYS}
    result["status"] = "automatic_reexploration"
    result["last_error"] = None
    phase = copy.deepcopy(result["active_phase"])
    phase.pop("terminal_status", None)
    phase["stage"] = "offline_reexploration"
    phase["physical_workload_calls"] = 0
    result["active_phase"] = phase
    result["v51_zero_call_recovery"] = {
        "contract": CONTRACT,
        "source_state_sha256": SOURCE_STATE_SHA256,
        "source_selection_sha256": SELECTION_SHA256,
        "source_handoff_sha256": HANDOFF_SHA256,
        "source_batch_id": "cont011",
        "next_batch_id": "cont012",
        "cont011_rerun": False,
        "cont012_preworkload_only": True,
        "source_diagnostic_batches": 2,
        "source_diagnostic_physical_calls": 10,
        "formal_candidates_changed": False,
        "formal_state_mutation": False,
        "physical_workload_calls_added": 0,
        "manual_candidate_injection": False,
        "threshold_relaxation": False,
        "formal_snapshot_sha256": digest(before),
    }
    after = {key: copy.deepcopy(result.get(key)) for key in FORMAL_KEYS}
    if before != after:
        raise RuntimeError("XP v51 recovery changed formal state")
    return result


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {"preflight", "apply"}:
        raise SystemExit("usage: recover_xp_v51_cont012_formal_routing_zero_call.py preflight|apply")
    state = load(STATE)
    validate_source(state)
    target = recovered(state)
    if sys.argv[1] == "preflight":
        print(json.dumps({"contract": CONTRACT, "status": "ready", "target_state_digest": digest(target)}, sort_keys=True), flush=True)
        return 0

    stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
    backup = CAMPAIGN / f"state.before_v51_cont012_recovery.{stamp}.json"
    shutil.copy2(STATE, backup)
    if sha256(backup) != SOURCE_STATE_SHA256:
        raise RuntimeError("XP v51 backup SHA mismatch")
    atomic_json(STATE, target)
    audit = {
        "contract": CONTRACT,
        "backup_path": str(backup),
        "backup_sha256": sha256(backup),
        "recovered_state_sha256": sha256(STATE),
        "physical_workload_calls_added": 0,
        "formal_state_mutation": False,
    }
    atomic_json(CAMPAIGN / "v51_cont012_formal_routing_zero_call_recovery.json", audit)
    print(json.dumps(audit, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
