#!/usr/bin/env python3
"""Quarantine zero-call XP cont015 and reopen after axis-boundary tracing."""

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
EXPECTED_STATE_SHA = (
    "4c5c692049b2cb9cf5c09fb29f780144c5c648fd30af688d5aad1875584caa64"
)
EXPECTED_MANIFEST_SHA = (
    "0879c236d0e79924cb61af9268ff0820cac0688bd389fe1cfa64a50b68d19ddc"
)
EXPECTED_SELECTION_SHA = (
    "afadfc00e90263cf9f648a6f8a76ec4b702faeff47050e0213cd69f6ed6dd30a"
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


def projection(state: Dict[str, Any]) -> Dict[str, Any]:
    return {name: copy.deepcopy(state.get(name)) for name in FORMAL_FIELDS}


def main() -> int:
    for path, expected in (
        (STATE, EXPECTED_STATE_SHA),
        (MANIFEST, EXPECTED_MANIFEST_SHA),
        (SELECTION, EXPECTED_SELECTION_SHA),
    ):
        if not path.is_file() or sha256(path) != expected:
            raise RuntimeError(f"sealed XP axis recovery input changed: {path}")
    state = load(STATE)
    history = state.get("batch_history") or []
    if not (
        state.get("status") == "blocked_no_candidates"
        and int(state.get("accepted_count", -1)) == 1
        and int(state.get("next_batch_index", -1)) == 15
        and (state.get("last_batch") or {})
        == {"batch_id": "cont015", "status": "no_candidates"}
        and history
        and history[-1].get("batch_id") == "cont014"
    ):
        raise RuntimeError("XP state is not at the axis-rescue boundary")
    counts = load(MANIFEST).get("counts") or {}
    selection = load(SELECTION)
    if not (
        int(counts.get("novel_non_corner_fingerprints", -1)) == 12
        and int(counts.get("resource_safe_probe_candidates", -1)) == 3
        and int(counts.get("selected_top5", -1)) == 0
        and selection.get("replay_authorized") is False
        and len(selection.get("top5") or []) == 0
    ):
        raise RuntimeError("XP cont015 is not the sealed safe3 failure")
    forbidden = []
    for name in ("result.json", "replay_summary.json", "workload.log"):
        forbidden.extend(PARTIAL.rglob(name))
    if forbidden or (PARTIAL / "serial_replay").exists():
        raise RuntimeError("XP cont015 crossed the physical workload boundary")

    before = projection(state)
    timestamp = time.strftime("%Y%m%dT%H%M%S", time.localtime())
    quarantine = ROOT / f"quarantine_cont015_axis_trace_{timestamp}"
    if quarantine.exists():
        raise RuntimeError(f"quarantine already exists: {quarantine}")
    shutil.move(str(PARTIAL), str(quarantine))
    atomic_json(quarantine / "state_before_recovery.json", state)
    recovered = copy.deepcopy(state)
    recovered["status"] = "running"
    recovered["last_batch"] = copy.deepcopy(history[-1])
    recovered["last_error"] = None
    if projection(recovered) != before:
        raise RuntimeError("axis recovery would mutate formal state")
    atomic_json(STATE, recovered)
    audit = {
        "contract": "xp_cont015_axis_trace_zero_call_recovery_v1",
        "status": "reopened_same_batch_after_axis_trace_fix",
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
    }
    atomic_json(quarantine / "recovery_audit.json", audit)
    print(json.dumps(audit, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
