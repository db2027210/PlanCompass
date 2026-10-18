#!/usr/bin/env python3
"""SHA-bound zero-workload recovery for the XP v50 cont011 lineage stop."""

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
    import xp_v50_safe_pool_pending_permutation_patch as v50
except ModuleNotFoundError:
    from tools import xp_v39_exact_point_pending_trust_patch as pending
    from tools import xp_v47_safe_pool_pending_lineage_patch as v47
    from tools import xp_v50_safe_pool_pending_permutation_patch as v50


PROJECT = Path("/home/anonymous/program/resource_adaptive")
CAMPAIGN = PROJECT / "history/xp_final_algorithm_fresh10_v38_20260803"
STATE = CAMPAIGN / "state.json"
CONT010 = CAMPAIGN / "batches/cont010"
CONT011 = CAMPAIGN / "batches/cont011"
SELECTION = CONT010 / "selection.json"
REPLAY = CONT010 / "replay_summary.json"
REPEAT_SELECTION = CONT011 / "exact_plan_repeat/selection.json"
EXPECTED_STATE_SHA256 = (
    "74179e9d8d5f97cdb54e90d8f1da93dbf49dde5c5b4bfe791346fffeac4a65bf"
)
EXPECTED_SELECTION_SHA256 = (
    "2f79982be496cac7188870d0edfddab58a20ed10962b1721f07a85bffa2a968b"
)
EXPECTED_REPLAY_SHA256 = (
    "902ce053792c34d078d3be966b24e20f918804928544a138e73ec6c52feb9c2f"
)
EXPECTED_OFFLINE_ARTIFACTS = {
    "exact_plan_repeat/builder.log": (
        "6e34d43837a5208bd82479b166bbbe643a60a267df052de7fae9dc7794e81724"
    ),
    "exact_plan_repeat/selection.json": (
        "3c297c3861af015e06341659c7f60de8cdfabfa143f5b2b098d68b9ffb41c81a"
    ),
    "score_metadata_normalization/audit.json": (
        "e2e3cbbb9dfa756256d54f6a179400016fc41395cb511d640b8176cfdb7d5c8a"
    ),
    "score_metadata_normalization/selection.json": (
        "9a7daf1e19136e77c9fb324f60fdc1d61d66edee50a12d9f126a09b7baa72afb"
    ),
    "v37_local_noise_acquisition_audit.json": (
        "7d8e5b9aa2b94c849b084373d467a22c8b16a563650aabe19ef53ecdc0980137"
    ),
}
EXPECTED_ERROR = (
    "RuntimeError: XP v47 safe-pool lineage failed: "
    "{'source_selection_sha_bound': True, 'source_replay_sha_bound': True, "
    "'source_clean_probe5': False, 'v46_lineage_exactly_retained': True, "
    "'v42_transition_lineage_not_applicable': True}"
)
CONTRACT = "xp_v50_cont011_safe_pool_permutation_zero_call_recovery_v1"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"XP v50 expected JSON object: {path}")
    return value


def formal_snapshot(state: Mapping[str, Any]) -> dict[str, Any]:
    return {key: copy.deepcopy(state.get(key)) for key in pending.FORMAL_KEYS}


def encoded(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def validate_offline_boundary() -> None:
    observed = {
        str(path.relative_to(CONT011))
        for path in CONT011.rglob("*")
        if path.is_file()
    }
    if observed != set(EXPECTED_OFFLINE_ARTIFACTS):
        raise RuntimeError("XP v50 cont011 crossed the sealed offline boundary")
    for relative, expected in EXPECTED_OFFLINE_ARTIFACTS.items():
        path = CONT011 / relative
        if path.is_symlink() or sha256(path) != expected:
            raise RuntimeError(f"XP v50 offline artifact SHA mismatch: {path}")
    if any(
        path.exists()
        for path in (
            CONT011 / "selection.json",
            CONT011 / "replay_summary.json",
            CONT011 / "serial_replay",
            CONT011 / "restore_before.log",
            CONT011 / "restore_finally.log",
        )
    ):
        raise RuntimeError("XP v50 cont011 workload boundary was crossed")


def validate_before() -> dict[str, Any]:
    for path, expected in (
        (STATE, EXPECTED_STATE_SHA256),
        (SELECTION, EXPECTED_SELECTION_SHA256),
        (REPLAY, EXPECTED_REPLAY_SHA256),
    ):
        if not path.is_file() or path.is_symlink() or sha256(path) != expected:
            raise RuntimeError(f"XP v50 authority SHA mismatch: {path}")
    validate_offline_boundary()

    state = load(STATE)
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
    ):
        raise RuntimeError("XP v50 state is not the exact cont011 lineage stop")

    v50.install(v47, pending)
    sealed = v47.seal_safe_pool_pending_selection(pending, load(REPEAT_SELECTION))
    checks = (sealed.get("v47_safe_pool_pending_lineage") or {}).get("checks") or {}
    if not checks or not all(checks.values()):
        raise RuntimeError("XP v50 permutation lineage does not seal cleanly")
    return state


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
            "v50_recovery_contract": CONTRACT,
            "v50_recovery_source_state_sha256": EXPECTED_STATE_SHA256,
            "v50_next_action": "rebuild_and_seal_exact_point_repeat",
        }
    )
    recovered["active_phase"] = phase
    recovered["v50_zero_call_recovery"] = {
        "contract": CONTRACT,
        "source_state_sha256": EXPECTED_STATE_SHA256,
        "selection_sha256": EXPECTED_SELECTION_SHA256,
        "replay_sha256": EXPECTED_REPLAY_SHA256,
        "repeat_selection_sha256": EXPECTED_OFFLINE_ARTIFACTS[
            "exact_plan_repeat/selection.json"
        ],
        "fixed_mapping": {
            "source_order_required_to_equal_repeat_order": False,
            "one_to_one_permutation_required": True,
            "candidate_order_changed": False,
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
        raise RuntimeError("XP v50 recovery would mutate formal state")
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
    before = validate_before()
    after = recovered_state(before)
    payload = encoded(after)
    expected_after = hashlib.sha256(payload).hexdigest()
    timestamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
    backup = STATE.with_name(f"state.before_v50_cont011_recovery.{timestamp}.json")
    if backup.exists():
        raise RuntimeError(f"XP v50 backup already exists: {backup}")
    shutil.copy2(STATE, backup)
    if sha256(backup) != EXPECTED_STATE_SHA256:
        raise RuntimeError("XP v50 state backup SHA mismatch")
    atomic_write(STATE, payload, "v50-state")
    if sha256(STATE) != expected_after:
        raise RuntimeError("XP v50 recovered state SHA mismatch")
    audit = {
        "contract": CONTRACT,
        "source_state_sha256": EXPECTED_STATE_SHA256,
        "recovered_state_sha256": expected_after,
        "backup_path": str(backup.resolve()),
        "backup_sha256": sha256(backup),
        "repeat_selection_sha256": EXPECTED_OFFLINE_ARTIFACTS[
            "exact_plan_repeat/selection.json"
        ],
        "physical_workload_calls_added": 0,
        "accepted_delta": 0,
        "formal_state_mutation": False,
        "manual_candidate_injection": False,
        "status_after": after["status"],
    }
    audit_path = CAMPAIGN / "v50_cont011_permutation_zero_call_recovery.json"
    atomic_write(audit_path, encoded(audit), "v50-audit")
    return {**audit, "audit_path": str(audit_path.resolve())}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("preflight", "apply"))
    args = parser.parse_args()
    if args.mode == "preflight":
        before = validate_before()
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
