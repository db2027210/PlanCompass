#!/usr/bin/env python3
"""SHA-bound zero-call recovery of XP cont009 into pending replay-ready state."""

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
    import xp_v47_safe_pool_pending_lineage_patch as v47
except ModuleNotFoundError:
    from tools import xp_v39_exact_point_pending_trust_patch as pending
    from tools import xp_v47_safe_pool_pending_lineage_patch as v47


PROJECT = Path("/home/anonymous/program/resource_adaptive")
CAMPAIGN = PROJECT / "history/xp_final_algorithm_fresh10_v38_20260803"
STATE = CAMPAIGN / "state.json"
CONT008 = CAMPAIGN / "batches/cont008"
CONT009 = CAMPAIGN / "batches/cont009"
SOURCE_SELECTION = CONT009 / "score_metadata_normalization/selection.json"
SOURCE_REPLAY = CONT008 / "replay_summary.json"
CONT008_SELECTION = CONT008 / "selection.json"
EXACT_SELECTION = CONT009 / "exact_plan_repeat/selection.json"
BUILDER_LOG = CONT009 / "exact_plan_repeat/builder.log"
NOISE_AUDIT = CONT009 / "v37_local_noise_acquisition_audit.json"
BATCH_SELECTION = CONT009 / "selection.json"

EXPECTED = {
    STATE: "4f08a85264c8cc023fe6acd791a14231928d1cf8f6cad2c9678c6607df1695c3",
    CONT008_SELECTION: "e176b61449684e34231b0dbcfd8edbc8bd7e05432747f36a8d58ee5653f8ec42",
    SOURCE_REPLAY: "1ee8971bb6247db9c49954f71ee4f553a66194c3f5b103676e61f2217f3e4d4e",
    SOURCE_SELECTION: "54861dceb16cd367c3457c2af502ef92e54b9da0a3654f103ac519531a513ea7",
    EXACT_SELECTION: "5b4d4e5823bc5b9ba87320a71f17a983f879fcf06fb0e53b24a2c04f03e17ea0",
    BUILDER_LOG: "6ca685bae28de83578c6c570b8ec6ca15304169c2693cf10e8c6ac75347e9752",
    NOISE_AUDIT: "c6380f5f751f5e14acca9b826a6e2cf2d1f68e06d9f52939b77a0770e98bca5e",
}
CONTRACT = "xp_v47_cont009_pending_zero_call_recovery_v1"
FAILED_CHECKS = {
    "role_stage_contract": False,
    "v42_transition_audit_retained_as_lineage_only": False,
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"XP v47 non-object JSON: {path}")
    return value


def formal_snapshot(state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(state.get(key))
        for key in pending.FORMAL_KEYS
    }


def validate_before() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    for path, expected in EXPECTED.items():
        if not path.is_file() or path.is_symlink() or sha256(path) != expected:
            raise RuntimeError(f"XP v47 authority SHA mismatch: {path}")
    if BATCH_SELECTION.exists():
        raise RuntimeError("XP v47 refuses to overwrite a cont009 batch selection")
    forbidden = [
        CONT009 / "replay_summary.json",
        CONT009 / "serial_replay",
        CONT009 / "restore_before.log",
        CONT009 / "restore_finally.log",
    ]
    if any(path.exists() for path in forbidden):
        raise RuntimeError("XP v47 cont009 crossed the preworkload boundary")
    state = load(STATE)
    phase = state.get("active_phase") or {}
    last = state.get("last_batch") or {}
    if not (
        state.get("status") == "blocked_preworkload"
        and int(state.get("accepted_count", -1)) == 0
        and int(state.get("accepted_target", -1)) == 10
        and int(state.get("next_batch_index", -1)) == 9
        and phase.get("batch_id") == "cont009"
        and phase.get("stage") == "offline_reexploration"
        and phase.get("terminal_status") == "blocked_preworkload"
        and int(phase.get("physical_workload_calls", -1)) == 0
        and last.get("batch_id") == "cont008"
        and last.get("status") == "complete"
        and last.get("mode") == "diagnostic_validation"
        and int(last.get("physical_calls", -1)) == 5
        and int(last.get("technical_failures", -1)) == 0
        and int(last.get("accepted", -1)) == 0
    ):
        raise RuntimeError("XP v47 state is not the exact cont009 schema-stop boundary")
    error = str(state.get("last_error") or "")
    if not all(f'"{name}": false' in error for name in FAILED_CHECKS):
        raise RuntimeError("XP v47 blocked error does not contain the two schema failures")
    for required_true in (
        "exactly_five",
        "unique_three_level_fingerprints",
        "all_repeat_fingerprints_not_previously_replayed",
        "all_noncorner",
        "diagnostic_only",
        "trusted3_probe2_pending_roles",
        "three_pending_not_formal_trusted",
        "unknown_regimes_remain_probes",
        "replay_authorized",
        "exact_point_first_observation_sealed",
        "same_plan_different_params_history_forbidden",
        "stage_specific_pending_gate_selected",
        "diagnostic_only_not_formal",
    ):
        if f'"{required_true}": true' not in error:
            raise RuntimeError(f"XP v47 expected passing gate missing: {required_true}")
    source_replay = load(SOURCE_REPLAY)
    if not pending._clean_diagnostic_replay(source_replay):
        raise RuntimeError("XP v47 cont008 is not clean 5/5/0 state-neutral evidence")
    exact = load(EXACT_SELECTION)
    sealed = v47.seal_safe_pool_pending_selection(pending, exact)
    gate = v47.safe_pool_pending_gate(
        pending,
        sealed,
        state.get("replayed_fingerprints") or [],
    )
    if gate.get("passed") is not True:
        raise RuntimeError(f"XP v47 corrected pending gate failed: {gate['checks']}")
    return state, sealed, gate


def recovered_state(
    before: Mapping[str, Any],
    selection: Mapping[str, Any],
    gate: Mapping[str, Any],
) -> dict[str, Any]:
    recovered = copy.deepcopy(dict(before))
    snapshot = formal_snapshot(before)
    recovered["status"] = "diagnostic_ready_preworkload"
    recovered["last_error"] = None
    phase = copy.deepcopy(dict(recovered.get("active_phase") or {}))
    phase.pop("terminal_status", None)
    phase.update(
        {
            "stage": "diagnostic_ready_preworkload",
            "selection_gate": copy.deepcopy(dict(gate)),
            "selection_sha256": digest(selection),
            "candidate_count": 5,
            "maximum_physical_calls": 5,
            "physical_workload_calls": 0,
            "v47_recovery_contract": CONTRACT,
            "v47_source_state_sha256": EXPECTED[STATE],
            "v47_next_action": "resume_sealed_cont009_exact_repeat_only",
        }
    )
    recovered["active_phase"] = phase
    recovered["v47_zero_call_recovery"] = {
        "contract": CONTRACT,
        "source_state_sha256": EXPECTED[STATE],
        "cont008_selection_sha256": EXPECTED[CONT008_SELECTION],
        "cont008_replay_sha256": EXPECTED[SOURCE_REPLAY],
        "cont009_source_selection_sha256": EXPECTED[SOURCE_SELECTION],
        "cont009_exact_selection_sha256": EXPECTED[EXACT_SELECTION],
        "sealed_batch_selection_sha256": sha256_bytes(encoded_json(selection)),
        "selection_canonical_sha256": digest(selection),
        "gate_contract": v47.CONTRACT,
        "gate_checks": copy.deepcopy(gate.get("checks") or {}),
        "schema_fix": {
            "v46_safe_pool_routing_role": "first_observation_lineage_only",
            "v46_probe5_role_gate_reapplied": False,
            "v42_transition_lineage_required": False,
            "pending_repeat_role_gate": "trusted3_pending_plus_probe2",
        },
        "cont008_rerun": False,
        "cont009_only": True,
        "rank1_to_rank3_formal_trusted": False,
        "probes_upgraded": False,
        "top3_gate_relaxed": False,
        "resource_gate_relaxed": False,
        "novel_fingerprint_gate_relaxed": False,
        "manual_candidate_injection": False,
        "physical_workload_calls_added": 0,
        "accepted_delta": 0,
        "formal_state_mutation": False,
        "formal_snapshot_sha256": digest(snapshot),
    }
    if formal_snapshot(recovered) != snapshot:
        raise RuntimeError("XP v47 recovery would mutate formal state")
    return recovered


def encoded_json(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


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


def expected_after_sha256() -> tuple[str, str]:
    before, selection, gate = validate_before()
    return (
        sha256_bytes(encoded_json(selection)),
        sha256_bytes(encoded_json(recovered_state(before, selection, gate))),
    )


def apply_recovery() -> dict[str, Any]:
    before, selection, gate = validate_before()
    after = recovered_state(before, selection, gate)
    selection_payload = encoded_json(selection)
    state_payload = encoded_json(after)
    expected_selection_sha = sha256_bytes(selection_payload)
    expected_state_sha = sha256_bytes(state_payload)
    timestamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
    backup = STATE.with_name(f"state.before_v47_cont009_recovery.{timestamp}.json")
    if backup.exists():
        raise RuntimeError(f"XP v47 backup already exists: {backup}")
    shutil.copy2(STATE, backup)
    if sha256(backup) != EXPECTED[STATE]:
        raise RuntimeError("XP v47 state backup SHA mismatch")
    atomic_write(BATCH_SELECTION, selection_payload, "v47-selection")
    if sha256(BATCH_SELECTION) != expected_selection_sha:
        raise RuntimeError("XP v47 sealed batch selection SHA mismatch")
    try:
        atomic_write(STATE, state_payload, "v47-state")
        if sha256(STATE) != expected_state_sha:
            raise RuntimeError("XP v47 recovered state SHA mismatch")
    except Exception:
        if sha256(STATE) == EXPECTED[STATE] and BATCH_SELECTION.is_file():
            BATCH_SELECTION.unlink()
        raise
    audit = {
        "contract": CONTRACT,
        "source_state_sha256": EXPECTED[STATE],
        "recovered_state_sha256": expected_state_sha,
        "sealed_batch_selection": str(BATCH_SELECTION),
        "sealed_batch_selection_sha256": expected_selection_sha,
        "backup_path": str(backup),
        "backup_sha256": sha256(backup),
        "cont008_replay_sha256": EXPECTED[SOURCE_REPLAY],
        "cont008_rerun": False,
        "cont009_only": True,
        "physical_workload_calls_added": 0,
        "accepted_delta": 0,
        "formal_state_mutation": False,
        "manual_candidate_injection": False,
        "status_after": after["status"],
    }
    audit_path = CAMPAIGN / "v47_cont009_pending_zero_call_recovery.json"
    atomic_write(audit_path, encoded_json(audit), "v47-audit")
    return {**audit, "audit_path": str(audit_path), "audit_sha256": sha256(audit_path)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("preflight", "apply"))
    args = parser.parse_args()
    if args.mode == "preflight":
        selection_sha, state_sha = expected_after_sha256()
        result = {
            "status": "ready_zero_call_recovery",
            "contract": CONTRACT,
            "source_state_sha256": EXPECTED[STATE],
            "expected_selection_sha256": selection_sha,
            "expected_recovered_state_sha256": state_sha,
            "cont008_rerun": False,
            "cont009_only": True,
            "physical_workload_calls_added": 0,
            "formal_state_mutation": False,
        }
    else:
        result = apply_recovery()
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
