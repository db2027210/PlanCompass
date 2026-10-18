#!/usr/bin/env python3
"""Fail-closed zero-call recovery for the XP cont051 fingerprint collision."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict


COLLISION_ID = (
    "three-level-sha256:"
    "803f0b28523780740e8713a28483b98ac2761943f5a96a5adfbae79e71f7da0d"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def atomic_json(path: Path, value: Dict[str, Any]) -> None:
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-root", required=True, type=Path)
    parser.add_argument("--expected-state-sha256", required=True)
    parser.add_argument("--expected-selection-sha256", required=True)
    parser.add_argument("--quarantine-name", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    campaign = args.campaign_root.resolve()
    state_path = campaign / "state.json"
    batch = campaign / "batches/cont051"
    selection_path = batch / "exact_plan_repeat/selection.json"
    quarantine = campaign / "batches" / args.quarantine_name
    if sha256(state_path) != args.expected_state_sha256:
        raise RuntimeError("XP cont051 recovery state SHA mismatch")
    if not selection_path.is_file() or sha256(selection_path) != args.expected_selection_sha256:
        raise RuntimeError("XP cont051 collision selection SHA mismatch")
    if quarantine.exists():
        raise RuntimeError("XP cont051 quarantine already exists")

    observed_files = {
        str(path.relative_to(batch)) for path in batch.rglob("*") if path.is_file()
    }
    if observed_files != {
        "exact_plan_repeat/builder.log",
        "exact_plan_repeat/selection.json",
    }:
        raise RuntimeError(
            f"XP cont051 crossed the offline-only boundary: {sorted(observed_files)}"
        )
    selection = load(selection_path)
    identifiers = {
        str((item.get("three_level_fingerprint") or {}).get("id") or "")
        for item in selection.get("top5") or []
    }
    state = load(state_path)
    if not (
        len(identifiers) == 5
        and COLLISION_ID in identifiers
        and COLLISION_ID in set(state.get("replayed_fingerprints") or [])
        and int(state.get("accepted_count", -1)) == 4
        and int(state.get("accepted_target", -1)) == 10
        and int(state.get("next_batch_index", -1)) == 51
        and state.get("status") == "blocked_contract"
        and state.get("last_error")
        == "selection repeats a previously replayed fingerprint"
        and (state.get("last_batch") or {}).get("batch_id") == "cont050"
    ):
        raise RuntimeError("XP cont051 observed state does not match collision boundary")

    result = {
        "status": "dry_run_passed",
        "campaign_root": str(campaign),
        "state_sha256_before": sha256(state_path),
        "collision_selection_sha256": sha256(selection_path),
        "collision_fingerprint_id": COLLISION_ID,
        "quarantine": str(quarantine),
        "physical_workload_calls_added": 0,
        "formal_state_fields_mutated": False,
        "manual_candidate_injection": False,
    }
    if args.apply:
        backup = campaign / (
            "state.pre_cont051_collision_reexplore."
            f"{args.expected_state_sha256[:12]}.json"
        )
        if backup.exists():
            raise RuntimeError("XP cont051 state backup already exists")
        atomic_json(backup, state)
        if sha256(backup) != args.expected_state_sha256:
            raise RuntimeError("XP cont051 state backup SHA mismatch")
        immutable_before = copy.deepcopy(
            {key: value for key, value in state.items() if key not in {"status", "last_error"}}
        )
        batch.rename(quarantine)
        updated = copy.deepcopy(state)
        updated["status"] = "running"
        updated.pop("last_error", None)
        atomic_json(state_path, updated)
        immutable_after = {
            key: value for key, value in load(state_path).items() if key not in {"status", "last_error"}
        }
        if immutable_after != immutable_before:
            raise RuntimeError("XP cont051 recovery changed formal continuation fields")
        result.update(
            {
                "status": "applied_and_verified",
                "backup": str(backup),
                "backup_sha256": sha256(backup),
                "state_sha256_after": sha256(state_path),
                "quarantine_inventory": sorted(observed_files),
            }
        )

    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
