#!/usr/bin/env python3
"""SHA-bound zero-workload recovery for the XP v49 cont011 metadata stop."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Mapping

try:
    import xp_v39_exact_point_pending_trust_patch as pending
    import xp_v49_fresh_selection_metadata_patch as v49
except ModuleNotFoundError:
    from tools import xp_v39_exact_point_pending_trust_patch as pending
    from tools import xp_v49_fresh_selection_metadata_patch as v49


PROJECT = Path("/home/anonymous/program/resource_adaptive")
CAMPAIGN = PROJECT / "history/xp_final_algorithm_fresh10_v38_20260803"
STATE = CAMPAIGN / "state.json"
CONT010 = CAMPAIGN / "batches/cont010"
CONT011 = CAMPAIGN / "batches/cont011"
SELECTION = CONT010 / "selection.json"
REPLAY = CONT010 / "replay_summary.json"
EXPECTED_STATE_SHA256 = (
    "0c464a7e1001dfd72be5b9bc1da597838eed2d6a19215fed2c0c4ce951815846"
)
EXPECTED_SELECTION_SHA256 = (
    "2f79982be496cac7188870d0edfddab58a20ed10962b1721f07a85bffa2a968b"
)
EXPECTED_REPLAY_SHA256 = (
    "902ce053792c34d078d3be966b24e20f918804928544a138e73ec6c52feb9c2f"
)
EXPECTED_ERROR = "score metadata normalization requires fresh selection/replay"
CONTRACT = "xp_v49_cont011_fresh_selection_metadata_zero_call_recovery_v1"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"XP v49 expected JSON object: {path}")
    return value


def formal_snapshot(state: Mapping[str, Any]) -> dict[str, Any]:
    return {key: copy.deepcopy(state.get(key)) for key in pending.FORMAL_KEYS}


def encoded(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _cont011_has_workload_or_selection() -> bool:
    if not CONT011.exists():
        return False
    forbidden_names = {
        "selection.json",
        "replay_summary.json",
        "restore_before.log",
        "restore_finally.log",
    }
    return any(
        path.name in forbidden_names
        or "serial_replay" in path.parts
        or "workload" in path.name
        for path in CONT011.rglob("*")
    )


def validate_before() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    for path, expected in (
        (STATE, EXPECTED_STATE_SHA256),
        (SELECTION, EXPECTED_SELECTION_SHA256),
        (REPLAY, EXPECTED_REPLAY_SHA256),
    ):
        if not path.is_file() or path.is_symlink() or sha256(path) != expected:
            raise RuntimeError(f"XP v49 authority SHA mismatch: {path}")
    if _cont011_has_workload_or_selection():
        raise RuntimeError("XP v49 cont011 crossed the preworkload boundary")

    state = load(STATE)
    selection = load(SELECTION)
    replay = load(REPLAY)
    phase = state.get("active_phase") or {}
    last = state.get("last_batch") or {}
    if not (
        state.get("status") == "blocked_preworkload"
        and state.get("last_error") == EXPECTED_ERROR
        and int(state.get("accepted_count", -1)) == 0
        and int(state.get("accepted_target", -1)) == 10
        and int(state.get("next_batch_index", -1)) == 11
        and phase.get("contract")
        == "xp_v38_atomic_offline_reexploration_phase_state_v1"
        and phase.get("batch_id") == "cont011"
        and phase.get("stage") == "offline_reexploration"
        and phase.get("terminal_status") == "blocked_preworkload"
        and int(phase.get("physical_workload_calls", -1)) == 0
        and phase.get("formal_state_sha256_before")
        == pending.formal_snapshot_digest(state)
        and phase.get("manual_candidate_injection") is False
        and last.get("batch_id") == "cont010"
        and last.get("status") == "complete"
        and last.get("mode") == "diagnostic_validation"
        and int(last.get("physical_calls", -1)) == 5
        and int(last.get("technical_failures", -1)) == 0
        and int(last.get("accepted", -1)) == 0
        and last.get("score_regime") == v49.FRESH_SCORE_REGIME
        and last.get("score_admission_eligible") is True
        and last.get("historical_scores_used_for_fresh_admission") is False
        and v49._clean_v48_probe5(selection, replay)
    ):
        raise RuntimeError("XP v49 state is not the exact cont011 metadata-stop boundary")
    return state, selection, replay


def recovered_state(before: Mapping[str, Any]) -> dict[str, Any]:
    recovered = copy.deepcopy(dict(before))
    snapshot = formal_snapshot(before)
    recovered["status"] = "automatic_reexploration"
    recovered["last_error"] = None
    phase = copy.deepcopy(dict(recovered.get("active_phase") or {}))
    phase.pop("terminal_status", None)
    phase.update(
        {
            "stage": "offline_reexploration",
            "physical_workload_calls": 0,
            "v49_recovery_contract": CONTRACT,
            "v49_recovery_source_state_sha256": EXPECTED_STATE_SHA256,
            "v49_next_action": "normalize_cont010_then_build_exact_point_repeat",
        }
    )
    recovered["active_phase"] = phase
    recovered["v49_zero_call_recovery"] = {
        "contract": CONTRACT,
        "source_state_sha256": EXPECTED_STATE_SHA256,
        "selection_sha256": EXPECTED_SELECTION_SHA256,
        "replay_sha256": EXPECTED_REPLAY_SHA256,
        "fixed_mapping": {
            "selection_missing_top_level_fresh_epoch_fields": True,
            "replay_fresh_epoch_fields_verified": True,
            "candidate_values_changed": False,
            "candidate_roles_changed": False,
            "candidate_fingerprints_changed": False,
        },
        "cont010_rerun": False,
        "cont011_only": True,
        "formal_admission_allowed_by_recovery": False,
        "trusted_candidates_promoted_by_recovery": 0,
        "threshold_relaxation": False,
        "manual_candidate_injection": False,
        "physical_workload_calls_added": 0,
        "accepted_delta": 0,
        "formal_state_mutation": False,
        "formal_snapshot_sha256": digest(snapshot),
    }
    if formal_snapshot(recovered) != snapshot:
        raise RuntimeError("XP v49 recovery would mutate formal state")
    return recovered


def atomic_write(path: Path, payload: bytes, label: str) -> None:
    descriptor, raw = tempfile.mkstemp(prefix=f".{path.name}.{label}.", dir=str(path.parent))
    temporary = Path(raw)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def apply_recovery() -> dict[str, Any]:
    before, _selection, _replay = validate_before()
    after = recovered_state(before)
    payload = encoded(after)
    expected_after = hashlib.sha256(payload).hexdigest()
    timestamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
    backup = STATE.with_name(f"state.before_v49_cont011_recovery.{timestamp}.json")
    if backup.exists():
        raise RuntimeError(f"XP v49 backup already exists: {backup}")
    shutil.copy2(STATE, backup)
    if sha256(backup) != EXPECTED_STATE_SHA256:
        raise RuntimeError("XP v49 state backup SHA mismatch")
    atomic_write(STATE, payload, "v49-state")
    if sha256(STATE) != expected_after:
        raise RuntimeError("XP v49 recovered state SHA mismatch")
    audit = {
        "contract": CONTRACT,
        "source_state_sha256": EXPECTED_STATE_SHA256,
        "recovered_state_sha256": expected_after,
        "backup_path": str(backup.resolve()),
        "backup_sha256": sha256(backup),
        "selection_sha256": EXPECTED_SELECTION_SHA256,
        "replay_sha256": EXPECTED_REPLAY_SHA256,
        "physical_workload_calls_added": 0,
        "accepted_delta": 0,
        "formal_state_mutation": False,
        "manual_candidate_injection": False,
        "status_after": after["status"],
    }
    audit_path = CAMPAIGN / "v49_cont011_fresh_metadata_zero_call_recovery.json"
    atomic_write(audit_path, encoded(audit), "v49-audit")
    return {**audit, "audit_path": str(audit_path.resolve())}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("preflight", "apply"))
    args = parser.parse_args()
    if args.mode == "preflight":
        before, _selection, _replay = validate_before()
        after = recovered_state(before)
        result = {
            "status": "ready_zero_call_recovery",
            "contract": CONTRACT,
            "source_state_sha256": EXPECTED_STATE_SHA256,
            "expected_recovered_state_sha256": hashlib.sha256(encoded(after)).hexdigest(),
            "physical_workload_calls_added": 0,
            "formal_state_mutation": False,
        }
    else:
        result = apply_recovery()
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

