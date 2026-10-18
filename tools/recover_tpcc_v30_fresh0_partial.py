#!/usr/bin/env python3
"""Quarantine a sealed zero-call TPC-C v30 partial preparation."""

from __future__ import annotations

import argparse
import shutil
import time
from pathlib import Path

try:
    import tpcc_autonomous_target10_controller_v25 as v25
except ModuleNotFoundError:
    from tools import tpcc_autonomous_target10_controller_v25 as v25


EXPECTED_MANIFEST_SHA256 = (
    "9bc07b4a35aa3135c33e794bedf5e9e591f450506db273d6abba4ae47397c942"
)
EXPECTED_PARTIAL_STATE_SHA256 = (
    "225d46d94f0c63d3201bef294a63206afe0ab2eb211b9b25cc364d27df8eb341"
)
EXPECTED_PARTIAL_FILES = {
    "evidence_manifest.json": (
        "6beba6c3e8142d765a8a6a6a2ce4ebd355056299f3838a8a5c6749d5ad1d0e00"
    ),
    "exact_regime_calibration.json": (
        "11ba37081fcac6404b2425a34df94f1456985f6906107dbc18f79ae2758009ff"
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest_path = args.manifest.resolve()
    if v25.sha256(manifest_path) != EXPECTED_MANIFEST_SHA256:
        raise RuntimeError("manifest SHA mismatch")
    manifest = v25.load_json(manifest_path)
    v25.verify_pins(manifest)
    root = Path(manifest["controller_root"]).resolve()
    state_path = Path(manifest["controller_state_path"]).resolve()
    formal_path = Path(manifest["formal_state_path"]).resolve()
    partial = root / "prepare_01"
    if v25.sha256(state_path) != EXPECTED_PARTIAL_STATE_SHA256:
        raise RuntimeError("partial controller state SHA mismatch")
    if v25.sha256(formal_path) != manifest["initial_formal_state_sha256"]:
        raise RuntimeError("formal state moved during partial launch")
    formal = v25.load_json(formal_path)
    state = v25.load_json(state_path)
    if (
        int(formal.get("accepted_count", -1)) != 0
        or int(formal.get("formal_physical_call_count", -1)) != 0
        or int(formal.get("formal_valid_replay_count", -1)) != 0
        or int(formal.get("formal_technical_failure_count", -1)) != 0
        or state.get("status") != "preparing_next_batch"
        or state.get("next_mode") != "diagnostic"
        or int(state.get("next_ordinal", -1)) != 1
        or state.get("current_run") is not None
        or state.get("last_launcher_returncode") is not None
    ):
        raise RuntimeError("partial boundary fields do not match zero-call state")
    observed_names = sorted(item.name for item in partial.iterdir())
    if observed_names != sorted(EXPECTED_PARTIAL_FILES):
        raise RuntimeError(f"unsafe partial file set: {observed_names}")
    for name, expected_sha256 in EXPECTED_PARTIAL_FILES.items():
        if v25.sha256(partial / name) != expected_sha256:
            raise RuntimeError(f"partial file SHA mismatch: {name}")

    timestamp = time.strftime("%Y%m%dT%H%M%S")
    recovery_root = root / f"zero_call_partial_recovery_{timestamp}"
    quarantine = root / f"quarantine_prepare_01_zero_call_{timestamp}"
    recovery_root.mkdir(parents=False, exist_ok=False)
    shutil.copy2(state_path, recovery_root / "controller_state.before.json")
    shutil.copy2(formal_path, recovery_root / "formal_state.verified_unchanged.json")
    shutil.copy2(manifest_path, recovery_root / "manifest.sealed.json")
    partial.replace(quarantine)

    state["status"] = "dry_run_next_preparation_ready"
    v25.atomic_json(state_path, state)
    if v25.sha256(state_path) != manifest["initial_controller_state_sha256"]:
        raise RuntimeError("restored controller state does not match frozen SHA")
    audit = {
        "schema_version": "tpcc_v30_zero_call_partial_recovery_v1",
        "manifest_path": str(manifest_path),
        "manifest_sha256": EXPECTED_MANIFEST_SHA256,
        "controller_state_sha256_before": EXPECTED_PARTIAL_STATE_SHA256,
        "controller_state_sha256_after": v25.sha256(state_path),
        "formal_state_sha256_before_after": v25.sha256(formal_path),
        "quarantine_path": str(quarantine),
        "quarantined_file_sha256": EXPECTED_PARTIAL_FILES,
        "physical_workload_calls_added": 0,
        "formal_state_mutated": False,
        "safe_to_resume_from_frozen_manifest": True,
    }
    audit_path = recovery_root / "recovery_audit.json"
    v25.atomic_json(audit_path, audit)
    print(f"recovery_audit={audit_path}", flush=True)
    print(f"recovery_audit_sha256={v25.sha256(audit_path)}", flush=True)
    print(f"quarantine={quarantine}", flush=True)
    print(f"controller_state_sha256={v25.sha256(state_path)}", flush=True)
    print(f"formal_state_sha256={v25.sha256(formal_path)}", flush=True)
    print("physical_workload_calls_added=0", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
