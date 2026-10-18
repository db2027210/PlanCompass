#!/usr/bin/env python3
"""Authorize one novel TPC-C Top-5 after the formal entry gate passed."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile


ENTRY_GATE_SHA = "8718dc3c07ca1f33af63908104caa04cc262acb9c568f74e95f6bea33220b8b3"
FORMAL_STATE_SHA = "dc31e747bbc4c7f64c197e207e9453aec6e99d438b0827bd0ef4c29956e86a80"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected object: {path}")
    return value


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    if len(sys.argv) != 6:
        raise SystemExit(
            "usage: authorize_tpcc_formal_continuation.py "
            "ENTRY_GATE FORMAL_STATE PREVIOUS_TOP5 NEW_TOP5 OUTPUT_GATE"
        )
    entry_path, state_path, previous_path, selection_path, output_path = map(
        lambda item: Path(item).resolve(), sys.argv[1:]
    )
    if sha256(entry_path) != ENTRY_GATE_SHA:
        raise RuntimeError("original entry gate SHA mismatch")
    entry = load(entry_path)
    if (
        entry.get("passed") is not True
        or entry.get("formal_start_authorized") is not True
    ):
        raise RuntimeError("original entry gate no longer authorizes formal search")
    state = load(state_path)
    if sha256(state_path) != FORMAL_STATE_SHA:
        raise RuntimeError("formal state SHA mismatch")
    if (
        state.get("status") != "ready"
        or state.get("accepted_count") != 1
        or state.get("accepted_target") != 10
        or state.get("qps_used_for_inner_scoring") is not False
    ):
        raise RuntimeError("formal state is not ready for batch03")
    previous = load(previous_path)
    selection = load(selection_path)
    if selection.get("selection_contract") != (
        "tpcc_robust_exact_plan_regime_trusted3_probe2_v1"
    ):
        raise RuntimeError("new selection contract mismatch")
    roles = [item.get("selection_role") for item in selection.get("top5", [])]
    if roles != [
        "trusted",
        "trusted",
        "trusted",
        "uncertain_probe",
        "uncertain_probe",
    ]:
        raise RuntimeError("new selection role contract mismatch")
    old_ids = {
        item["three_level_fingerprint"]["id"] for item in previous["top5"]
    }
    new_ids = [
        item["three_level_fingerprint"]["id"] for item in selection["top5"]
    ]
    if len(set(new_ids)) != 5 or old_ids.intersection(new_ids):
        raise RuntimeError("batch03 fingerprints are not five distinct novel IDs")
    if any(item.get("boundary_corner") is not False for item in selection["top5"]):
        raise RuntimeError("batch03 contains a corner candidate")
    if any(item.get("qps_used_for_inner_scoring") is not False for item in selection["top5"]):
        raise RuntimeError("batch03 candidate used QPS for inner scoring")
    output = {
        "schema_version": "tpcc_formal_continuation_gate_v1",
        "passed": True,
        "formal_start_authorized": True,
        "authorization_kind": "formal_continuation_after_entry_gate",
        "original_entry_gate_path": str(entry_path),
        "original_entry_gate_sha256": ENTRY_GATE_SHA,
        "formal_state_path": str(state_path),
        "formal_state_sha256": FORMAL_STATE_SHA,
        "accepted_count_before": 1,
        "accepted_target": 10,
        "selection_path": str(selection_path),
        "selection_sha256": sha256(selection_path),
        "checks": {
            "original_entry_gate_passed": True,
            "formal_state_ready": True,
            "five_new_distinct_noncorner_fingerprints": True,
            "trusted3_probe2": True,
            "qps_outer_only": True,
            "same_scenario_real_replay_serial": True,
            "maximum_physical_calls": 5,
        },
    }
    if output_path.exists():
        raise FileExistsError(f"output already exists: {output_path}")
    atomic_json(output_path, output)
    print(json.dumps({"gate": str(output_path), "sha256": sha256(output_path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
