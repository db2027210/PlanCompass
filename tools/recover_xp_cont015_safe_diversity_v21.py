#!/usr/bin/env python3
"""Quarantine zero-call XP cont015 and reopen it after safe-diversity fix."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
from typing import Any, Dict


ROOT = Path(
    "/home/anonymous/program/resource_adaptive/history/"
    "xp_final_algorithm_fresh10_v26_20260731T1935"
)
STATE = ROOT / "state.json"
PARTIAL = ROOT / "batches" / "cont015"
MANIFEST = PARTIAL / "diagnostic_bundle" / "manifest.json"
SELECTION = PARTIAL / "diagnostic_bundle" / "selection.json"
REEXPLORATION = PARTIAL / "exact_plan_repeat" / "automatic_reexploration.json"

EXPECTED_STATE_SHA = (
    "7a2d1c821a8baacc2559238f5313de3e0986b7a6f9073d8a0e2887f055b929db"
)
EXPECTED_MANIFEST_SHA = (
    "9959b6452ccece42053f155d40e4c1ac27c625a7caa029d0e40163baf3c51df2"
)
EXPECTED_SELECTION_SHA = (
    "75108dd1e21b1c6881eb1b9119c8b60b2054b8a70977b3dba3f407bfafd72f24"
)
EXPECTED_REEXPLORATION_SHA = (
    "7b068d6035a1600a5b4d2b581ee2583daf1595b78bfcce82333a24675183744a"
)

FORMAL_FIELDS = (
    "accepted_count",
    "accepted_fingerprints",
    "incumbent",
    "incumbent_params",
    "incumbent_score",
    "parameter_ranges",
    "range_state",
    "replayed_fingerprints",
    "training_state_sha256",
    "batch_history",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def atomic_json(path: Path, value: Dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def formal_projection(state: Dict[str, Any]) -> Dict[str, Any]:
    return {name: copy.deepcopy(state.get(name)) for name in FORMAL_FIELDS}


def main() -> int:
    expected = {
        STATE: EXPECTED_STATE_SHA,
        MANIFEST: EXPECTED_MANIFEST_SHA,
        SELECTION: EXPECTED_SELECTION_SHA,
        REEXPLORATION: EXPECTED_REEXPLORATION_SHA,
    }
    for path, expected_sha in expected.items():
        if not path.is_file() or sha256(path) != expected_sha:
            raise RuntimeError(f"sealed XP recovery input changed: {path}")

    state = load(STATE)
    history = state.get("batch_history") or []
    boundary = state.get("last_batch") or {}
    if not (
        state.get("status") == "blocked_no_candidates"
        and int(state.get("accepted_count", -1)) == 1
        and int(state.get("accepted_target", -1)) == 10
        and int(state.get("next_batch_index", -1)) == 15
        and boundary == {"batch_id": "cont015", "status": "no_candidates"}
        and history
        and history[-1].get("batch_id") == "cont014"
        and history[-1].get("mode") == "diagnostic_validation"
        and history[-1].get("status") == "complete"
        and int(history[-1].get("physical_calls", -1)) == 5
        and int(history[-1].get("technical_failures", -1)) == 0
        and int(history[-1].get("accepted", -1)) == 0
    ):
        raise RuntimeError("XP state is not at the sealed cont015 boundary")

    manifest = load(MANIFEST)
    selection = load(SELECTION)
    counts = manifest.get("counts") or {}
    if not (
        manifest.get("status") == "blocked_missing_input"
        and int(counts.get("novel_non_corner_fingerprints", -1)) == 10
        and int(counts.get("resource_safe_probe_candidates", -1)) == 2
        and int(counts.get("selected_top5", -1)) == 0
        and selection.get("replay_authorized") is False
        and len(selection.get("top5") or []) == 0
    ):
        raise RuntimeError("XP cont015 does not match safe-diversity failure")

    forbidden = []
    for name in ("result.json", "replay_summary.json", "workload.log"):
        forbidden.extend(PARTIAL.rglob(name))
    if forbidden or (PARTIAL / "serial_replay").exists():
        raise RuntimeError("XP cont015 crossed the physical workload boundary")

    before_projection = formal_projection(state)
    timestamp = time.strftime("%Y%m%dT%H%M%S", time.localtime())
    quarantine = ROOT / f"quarantine_cont015_safe_diversity_{timestamp}"
    if quarantine.exists():
        raise RuntimeError(f"quarantine already exists: {quarantine}")
    shutil.move(str(PARTIAL), str(quarantine))
    atomic_json(quarantine / "state_before_recovery.json", state)

    recovered = copy.deepcopy(state)
    recovered["status"] = "running"
    recovered["last_batch"] = copy.deepcopy(history[-1])
    recovered["last_error"] = None
    if formal_projection(recovered) != before_projection:
        raise RuntimeError("recovery would mutate formal optimizer state")
    atomic_json(STATE, recovered)

    audit = {
        "contract": "xp_cont015_safe_diversity_zero_call_recovery_v1",
        "status": "reopened_same_batch_after_algorithm_fix",
        "accepted_count": 1,
        "next_batch_index": 15,
        "physical_workload_calls": 0,
        "valid_replays": 0,
        "technical_failures": 0,
        "formal_state_mutated": False,
        "state_sha256_before": EXPECTED_STATE_SHA,
        "state_sha256_after": sha256(STATE),
        "failed_manifest_sha256": EXPECTED_MANIFEST_SHA,
        "failed_selection_sha256": EXPECTED_SELECTION_SHA,
        "quarantine_root": str(quarantine),
        "reason": (
            "raw fingerprint headroom reached 10 while calibrated-safe "
            "diversity remained 2; acquisition now expands clean anchors"
        ),
    }
    atomic_json(quarantine / "recovery_audit.json", audit)
    print(json.dumps(audit, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
