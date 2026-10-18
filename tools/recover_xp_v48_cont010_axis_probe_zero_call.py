#!/usr/bin/env python3
"""SHA-bound, zero-workload recovery of XP cont010 after the v47 schema stop."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any, Mapping

try:
    import xp_v39_exact_point_pending_trust_patch as pending
    import xp_v46_safe_novel_pool_selector_patch as v46
    import xp_v48_axis_boundary_trace_probe_patch as v48
except ModuleNotFoundError:
    from tools import xp_v39_exact_point_pending_trust_patch as pending
    from tools import xp_v46_safe_novel_pool_selector_patch as v46
    from tools import xp_v48_axis_boundary_trace_probe_patch as v48


PROJECT = Path("/home/anonymous/program/resource_adaptive")
CAMPAIGN = PROJECT / "history/xp_final_algorithm_fresh10_v38_20260803"
STATE = CAMPAIGN / "state.json"
CONT010 = CAMPAIGN / "batches/cont010"
BUNDLE_ROOT = Path(
    "/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v33_plan_quarantine"
)
BUNDLE_SOURCE = BUNDLE_ROOT / "xp_round10_diagnostic_bundle.py"
DIAGNOSTIC = CONT010 / "diagnostic_bundle"
MANIFEST = DIAGNOSTIC / "manifest.json"
SOURCE_SELECTION = DIAGNOSTIC / "selection.json"
CONFIG_OVERLAY = DIAGNOSTIC / "config_overlay.json"
ACCEPTED_INCUMBENT = CONT010 / "accepted_incumbent.json"
CANDIDATE_POOL = CONT010 / "explain_only/explain_only_generation.json"
NORMALIZED_SELECTION = CONT010 / "score_metadata_normalization/selection.json"
SOURCE_REPLAY = CAMPAIGN / "batches/cont009/replay_summary.json"
BATCH_SELECTION = CONT010 / "selection.json"

EXPECTED = {
    STATE: "59897476ddc8c81830a4963268c1f4d1027e5304776487b8159d35f8e07e8d8d",
    BUNDLE_SOURCE: v46.EXPECTED_BUNDLE_SHA256,
    MANIFEST: "d6564eb6a26e037eb5e7f2a472f73fa69f6c54f1599625bba1726fe83cb3f29e",
    SOURCE_SELECTION: "6103f9c48ee82cab10c7ddde5cf1fd09530aebe5278d599fcf784a840d1e7e6b",
    CONFIG_OVERLAY: "c020a52517aa6655514d6713a090b40c0a06d9ec8cfb67489854970a3b34c22a",
    ACCEPTED_INCUMBENT: "bd1d45a446dc2db337c584d29372a1ac87244bd89132519f1c6a661036fd4ca3",
    CANDIDATE_POOL: "f17032a71cb2346efc32b7a504f70a52822d5695d8e479d70df5d5263f5d7ba2",
    NORMALIZED_SELECTION: "3cdebf753dc073e41cc005b92979b6d04400dde3759a56f0561d9ca16454b735",
    SOURCE_REPLAY: "a55300499af0e5ddd6caf7c84dacbed417f8972085ec6fca4d4317f91d8e66e8",
}
CONTRACT = "xp_v48_cont010_axis_probe_zero_call_recovery_v1"
EXPECTED_ERROR = (
    "RuntimeError: XP v46 ready probe5 audit failed: "
    "{'exactly_five': True, 'unique_three_level_fingerprints': True, "
    "'excluded_fingerprints_absent': True, 'all_uncertain_probe': True, "
    "'all_fresh_safe_noncorner': False, 'no_gradient_or_incumbent_use': True}"
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
        raise RuntimeError(f"XP v48 non-object JSON: {path}")
    return value


def encoded_json(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def formal_snapshot(state: Mapping[str, Any]) -> dict[str, Any]:
    return {key: copy.deepcopy(state.get(key)) for key in pending.FORMAL_KEYS}


def _load_bundle_module() -> Any:
    if str(BUNDLE_ROOT) not in sys.path:
        sys.path.insert(0, str(BUNDLE_ROOT))
    specification = importlib.util.spec_from_file_location(
        "xp_v48_sealed_diagnostic_bundle", BUNDLE_SOURCE
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("XP v48 cannot load sealed diagnostic bundle")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def validate_before() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    for path, expected in EXPECTED.items():
        if not path.is_file() or path.is_symlink() or sha256(path) != expected:
            raise RuntimeError(f"XP v48 authority SHA mismatch: {path}")
    if BATCH_SELECTION.exists():
        raise RuntimeError("XP v48 refuses to overwrite cont010 selection")
    forbidden = [
        CONT010 / "replay_summary.json",
        CONT010 / "serial_replay",
        CONT010 / "restore_before.log",
        CONT010 / "restore_finally.log",
    ]
    if any(path.exists() for path in forbidden):
        raise RuntimeError("XP v48 cont010 crossed the preworkload boundary")

    state = load(STATE)
    phase = state.get("active_phase") or {}
    last = state.get("last_batch") or {}
    snapshot_digest = pending.formal_snapshot_digest(state)
    if not (
        state.get("status") == "blocked_preworkload"
        and state.get("last_error") == EXPECTED_ERROR
        and int(state.get("accepted_count", -1)) == 0
        and int(state.get("accepted_target", -1)) == 10
        and int(state.get("next_batch_index", -1)) == 10
        and phase.get("batch_id") == "cont010"
        and phase.get("contract") == "xp_v38_atomic_offline_reexploration_phase_state_v1"
        and phase.get("stage") == "offline_reexploration"
        and phase.get("terminal_status") == "blocked_preworkload"
        and phase.get("manual_candidate_injection") is False
        and int(phase.get("physical_workload_calls", -1)) == 0
        and phase.get("formal_state_sha256_before") == snapshot_digest
        and last.get("batch_id") == "cont009"
        and last.get("status") == "complete"
        and last.get("mode") == "diagnostic_validation"
        and int(last.get("physical_calls", -1)) == 5
        and int(last.get("technical_failures", -1)) == 0
        and int(last.get("accepted", -1)) == 0
    ):
        raise RuntimeError("XP v48 state is not the exact cont010 schema-stop boundary")

    manifest = load(MANIFEST)
    selection = load(SOURCE_SELECTION)
    overlay = load(CONFIG_OVERLAY)
    if digest(manifest.get("selected_selection")) != digest(selection):
        raise RuntimeError("XP v48 manifest/selection mismatch")
    if digest(manifest.get("optimizer_options")) != digest(overlay):
        raise RuntimeError("XP v48 manifest/config overlay mismatch")
    source = manifest.get("source") or {}
    expected_sources = {
        "selection": NORMALIZED_SELECTION,
        "replay": SOURCE_REPLAY,
        "candidate_pool": CANDIDATE_POOL,
        "accepted_incumbent": ACCEPTED_INCUMBENT,
    }
    if any(Path(str(source.get(name) or "")) != path for name, path in expected_sources.items()):
        raise RuntimeError("XP v48 manifest source path mismatch")

    excluded = list(source.get("round9_excluded_fingerprints") or [])
    if len(excluded) != int(source.get("round9_excluded_fingerprint_count", -1)):
        raise RuntimeError("XP v48 excluded fingerprint count mismatch")
    bundle_module = _load_bundle_module()
    sealed, checks = v48.seal_ready_probe5(
        v46, bundle_module, manifest, selection, excluded
    )
    identifiers = [bundle_module.fingerprint_id(item) for item in sealed["top5"]]
    replayed = {str(value) for value in state.get("replayed_fingerprints") or []}
    if set(identifiers) & replayed:
        raise RuntimeError("XP v48 selected fingerprint is not novel")
    if sealed.get("batch_mode") != "diagnostic_validation":
        raise RuntimeError("XP v48 recovered selection is not diagnostic-only")
    if sealed.get("replay_authorized") is not True:
        raise RuntimeError("XP v48 recovered selection is not replay-authorized")
    return state, sealed, checks


def recovered_state(
    before: Mapping[str, Any],
    selection: Mapping[str, Any],
    checks: Mapping[str, Any],
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
            "selection_gate": {
                "contract": v48.CONTRACT,
                "passed": True,
                "checks": copy.deepcopy(dict(checks)),
                "maximum_physical_calls": 5,
                "selection_stage": "safe_pool_discovery_probe5",
            },
            "selection_sha256": digest(selection),
            "candidate_count": 5,
            "maximum_physical_calls": 5,
            "physical_workload_calls": 0,
            "v48_recovery_contract": CONTRACT,
            "v48_source_state_sha256": EXPECTED[STATE],
            "v48_next_action": "resume_sealed_cont010_diagnostic_probe5_only",
        }
    )
    recovered["active_phase"] = phase
    recovered["v48_zero_call_recovery"] = {
        "contract": CONTRACT,
        "source_state_sha256": EXPECTED[STATE],
        "source_manifest_sha256": EXPECTED[MANIFEST],
        "source_selection_sha256": EXPECTED[SOURCE_SELECTION],
        "source_candidate_pool_sha256": EXPECTED[CANDIDATE_POOL],
        "sealed_batch_selection_sha256": sha256_bytes(encoded_json(selection)),
        "selection_canonical_sha256": digest(selection),
        "gate_contract": v48.CONTRACT,
        "gate_checks": copy.deepcopy(dict(checks)),
        "cont009_rerun": False,
        "cont010_only": True,
        "source_selection_reused": True,
        "candidate_regeneration": False,
        "axis_contract_only": v48.AXIS_CONTRACT,
        "top3_gate_relaxed": False,
        "resource_gate_relaxed": False,
        "parameter_step_limit_relaxed": False,
        "novel_fingerprint_gate_relaxed": False,
        "manual_candidate_injection": False,
        "physical_workload_calls_added": 0,
        "accepted_delta": 0,
        "formal_state_mutation": False,
        "formal_snapshot_sha256": digest(snapshot),
    }
    if formal_snapshot(recovered) != snapshot:
        raise RuntimeError("XP v48 recovery would mutate formal state")
    return recovered


def expected_after_sha256() -> tuple[str, str]:
    before, selection, checks = validate_before()
    return (
        sha256_bytes(encoded_json(selection)),
        sha256_bytes(encoded_json(recovered_state(before, selection, checks))),
    )


def atomic_write(path: Path, payload: bytes, label: str) -> None:
    descriptor, raw = tempfile.mkstemp(
        prefix=f".{path.name}.{label}.", dir=str(path.parent)
    )
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
    before, selection, checks = validate_before()
    after = recovered_state(before, selection, checks)
    selection_payload = encoded_json(selection)
    state_payload = encoded_json(after)
    selection_sha = sha256_bytes(selection_payload)
    state_sha = sha256_bytes(state_payload)
    timestamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
    backup = STATE.with_name(f"state.before_v48_cont010_recovery.{timestamp}.json")
    if backup.exists():
        raise RuntimeError(f"XP v48 backup already exists: {backup}")
    shutil.copy2(STATE, backup)
    if sha256(backup) != EXPECTED[STATE]:
        raise RuntimeError("XP v48 state backup SHA mismatch")
    atomic_write(BATCH_SELECTION, selection_payload, "v48-selection")
    if sha256(BATCH_SELECTION) != selection_sha:
        raise RuntimeError("XP v48 sealed selection SHA mismatch")
    try:
        atomic_write(STATE, state_payload, "v48-state")
        if sha256(STATE) != state_sha:
            raise RuntimeError("XP v48 recovered state SHA mismatch")
    except Exception:
        if sha256(STATE) == EXPECTED[STATE] and BATCH_SELECTION.is_file():
            BATCH_SELECTION.unlink()
        raise
    audit = {
        "contract": CONTRACT,
        "source_state_sha256": EXPECTED[STATE],
        "recovered_state_sha256": state_sha,
        "sealed_batch_selection": str(BATCH_SELECTION),
        "sealed_batch_selection_sha256": selection_sha,
        "backup_path": str(backup),
        "backup_sha256": sha256(backup),
        "source_manifest_sha256": EXPECTED[MANIFEST],
        "source_selection_sha256": EXPECTED[SOURCE_SELECTION],
        "physical_workload_calls_added": 0,
        "accepted_delta": 0,
        "formal_state_mutation": False,
        "manual_candidate_injection": False,
        "status_after": after["status"],
    }
    audit_path = CAMPAIGN / "v48_cont010_axis_probe_zero_call_recovery.json"
    atomic_write(audit_path, encoded_json(audit), "v48-audit")
    return audit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("preflight", "expected-sha", "apply"))
    args = parser.parse_args()
    if args.action == "preflight":
        _, selection, checks = validate_before()
        print(json.dumps({
            "contract": CONTRACT,
            "status": "preflight_passed",
            "selection_sha256_after": sha256_bytes(encoded_json(selection)),
            "checks": checks,
            "physical_workload_calls_added": 0,
        }, sort_keys=True), flush=True)
    elif args.action == "expected-sha":
        selection_sha, state_sha = expected_after_sha256()
        print(json.dumps({
            "selection_sha256_after": selection_sha,
            "state_sha256_after": state_sha,
        }, sort_keys=True), flush=True)
    else:
        print(json.dumps(apply_recovery(), sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
