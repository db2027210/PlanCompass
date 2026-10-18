#!/usr/bin/env python3
"""SHA-bound zero-call recovery for the XP v44 schema-compatibility stop."""

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


PROJECT = Path("/home/anonymous/program/resource_adaptive")
CAMPAIGN = PROJECT / "history/xp_final_algorithm_fresh10_v38_20260803"
STATE = CAMPAIGN / "state.json"
SELECTION = CAMPAIGN / "batches/cont007/selection.json"
REPLAY = CAMPAIGN / "batches/cont007/replay_summary.json"
V44_REROUTE = CAMPAIGN / "batches/cont008/v44_pending_post_replay_reroute.json"

EXPECTED_STATE_BEFORE_SHA256 = "e39dfc261c799c3b36dc40b8d5c9979401db8d900f9091c49fbc22a50f173c7d"
EXPECTED_SELECTION_SHA256 = "bf0d54962de658b97d80fd0a5f0373c6180902938d17efef74d1f4372ca086ca"
EXPECTED_REPLAY_SHA256 = "e9931aac7742e76a810f25607ca1bedea36394ae1c80745b43dad0cf1364defc"
EXPECTED_V44_REROUTE_SHA256 = "134a6c6cf38f80680576ab239c54b3c51f07650aef7dfe6b6dd435d87da3a04a"
CONTRACT = "xp_v45_blocked_preworkload_zero_call_recovery_v1"
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


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"XP v45 non-object JSON: {path}")
    return value


def formal_snapshot(state: Mapping[str, Any]) -> dict[str, Any]:
    return {key: copy.deepcopy(state.get(key)) for key in FORMAL_KEYS}


def validate_before() -> dict[str, Any]:
    expected = {
        STATE: EXPECTED_STATE_BEFORE_SHA256,
        SELECTION: EXPECTED_SELECTION_SHA256,
        REPLAY: EXPECTED_REPLAY_SHA256,
        V44_REROUTE: EXPECTED_V44_REROUTE_SHA256,
    }
    for path, expected_sha in expected.items():
        if not path.is_file() or sha256(path) != expected_sha:
            raise RuntimeError(f"XP v45 authority SHA mismatch: {path}")
    state = load(STATE)
    phase = state.get("active_phase") or {}
    last = state.get("last_batch") or {}
    reroute = load(V44_REROUTE)
    if not (
        state.get("status") == "blocked_preworkload"
        and state.get("last_error") == "KeyError: 'trusted_spearman_positive'"
        and int(state.get("accepted_count", -1)) == 0
        and int(state.get("accepted_target", -1)) == 10
        and int(state.get("next_batch_index", -1)) == 8
        and phase.get("batch_id") == "cont008"
        and phase.get("stage") == "offline_reexploration"
        and phase.get("terminal_status") == "blocked_preworkload"
        and int(phase.get("physical_workload_calls", -1)) == 0
        and last.get("batch_id") == "cont007"
        and last.get("status") == "complete"
        and last.get("mode") == "diagnostic_validation"
        and int(last.get("physical_calls", -1)) == 5
        and int(last.get("technical_failures", -1)) == 0
        and int(last.get("accepted", -1)) == 0
        and reroute.get("contract")
        == "xp_v44_failed_pending_gate_routes_to_new_fingerprint_exploration_v1"
        and reroute.get("failed_checks") == ["actual_best_in_predicted_top3"]
        and reroute.get("formal_admission_allowed") is False
        and reroute.get("state_mutation") is False
        and int(reroute.get("new_workload_calls", -1)) == 0
    ):
        raise RuntimeError("XP v45 authority state is not the exact schema-stop boundary")
    return state


def recovered_state(before: Mapping[str, Any]) -> dict[str, Any]:
    recovered = copy.deepcopy(dict(before))
    snapshot_before = formal_snapshot(before)
    recovered["status"] = "automatic_reexploration"
    recovered["last_error"] = None
    phase = copy.deepcopy(dict(recovered.get("active_phase") or {}))
    phase.pop("terminal_status", None)
    phase.update(
        {
            "stage": "offline_reexploration",
            "physical_workload_calls": 0,
            "v45_recovery_contract": CONTRACT,
            "v45_recovery_source_state_sha256": EXPECTED_STATE_BEFORE_SHA256,
            "v45_next_action": "deterministic_explain_new_fingerprint_generation",
        }
    )
    recovered["active_phase"] = phase
    recovered["v45_zero_call_recovery"] = {
        "contract": CONTRACT,
        "source_state_sha256": EXPECTED_STATE_BEFORE_SHA256,
        "selection_sha256": EXPECTED_SELECTION_SHA256,
        "replay_sha256": EXPECTED_REPLAY_SHA256,
        "v44_reroute_sha256": EXPECTED_V44_REROUTE_SHA256,
        "reason": "v44_gate_schema_alias_missing_after_correct_reroute",
        "fixed_mapping": {
            "source": "trusted_only_spearman_positive",
            "legacy_consumer": "trusted_spearman_positive",
            "values_identical_required": True,
        },
        "actual_best_predicted_rank": 4,
        "actual_best_top3_gate_bypassed": False,
        "threshold_relaxation": False,
        "manual_candidate_injection": False,
        "physical_workload_calls_added": 0,
        "explain_calls_added": 0,
        "accepted_delta": 0,
        "formal_state_mutation": False,
        "formal_snapshot_sha256": digest(snapshot_before),
    }
    if formal_snapshot(recovered) != snapshot_before:
        raise RuntimeError("XP v45 recovery would mutate formal state")
    return recovered


def encoded_state(state: Mapping[str, Any]) -> bytes:
    return (json.dumps(state, indent=2, sort_keys=True) + "\n").encode("utf-8")


def expected_after_sha256(before: Mapping[str, Any]) -> str:
    return hashlib.sha256(encoded_state(recovered_state(before))).hexdigest()


def atomic_write(path: Path, payload: bytes) -> None:
    descriptor, raw_path = tempfile.mkstemp(prefix=f".{path.name}.v45.", dir=str(path.parent))
    temporary = Path(raw_path)
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
    before = validate_before()
    after = recovered_state(before)
    expected_after = expected_after_sha256(before)
    timestamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
    backup = STATE.with_name(f"state.before_v45_recovery.{timestamp}.json")
    if backup.exists():
        raise RuntimeError(f"XP v45 backup already exists: {backup}")
    shutil.copy2(STATE, backup)
    if sha256(backup) != EXPECTED_STATE_BEFORE_SHA256:
        raise RuntimeError("XP v45 backup SHA mismatch")
    atomic_write(STATE, encoded_state(after))
    if sha256(STATE) != expected_after:
        raise RuntimeError("XP v45 recovered state SHA mismatch")
    audit = {
        "contract": CONTRACT,
        "source_state_sha256": EXPECTED_STATE_BEFORE_SHA256,
        "recovered_state_sha256": expected_after,
        "backup_path": str(backup.resolve()),
        "backup_sha256": sha256(backup),
        "physical_workload_calls_added": 0,
        "explain_calls_added": 0,
        "accepted_delta": 0,
        "formal_state_mutation": False,
        "actual_best_top3_gate_bypassed": False,
        "threshold_relaxation": False,
        "manual_candidate_injection": False,
        "status_after": after["status"],
    }
    audit_path = CAMPAIGN / "v45_blocked_preworkload_zero_call_recovery.json"
    atomic_write(audit_path, (json.dumps(audit, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return {**audit, "audit_path": str(audit_path.resolve())}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("preflight", "apply"))
    args = parser.parse_args()
    if args.mode == "preflight":
        before = validate_before()
        result = {
            "status": "ready_zero_call_recovery",
            "contract": CONTRACT,
            "source_state_sha256": EXPECTED_STATE_BEFORE_SHA256,
            "expected_recovered_state_sha256": expected_after_sha256(before),
            "physical_workload_calls_added": 0,
            "formal_state_mutation": False,
        }
    else:
        result = apply_recovery()
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
