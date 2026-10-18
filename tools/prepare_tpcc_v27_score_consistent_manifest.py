#!/usr/bin/env python3
"""Seal a zero-call TPC-C v27 manifest from a clean v26 boundary."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict


PROJECT = Path("/home/anonymous/program/resource_adaptive")
V27_FILES = (
    PROJECT / "tools/tpcc_score_consistent_representative.py",
    PROJECT / "tools/build_tpcc_exact_plan_repeat_validation_v3.py",
    PROJECT / "tools/tpcc_autonomous_target10_controller_v27_score_consistent.py",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def atomic_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
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
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--controller-state", type=Path, required=True)
    parser.add_argument("--formal-state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    source_path = args.source_manifest.resolve()
    state_path = args.controller_state.resolve()
    formal_path = args.formal_state.resolve()
    output_path = args.output.resolve()
    source = load_json(source_path)
    state = load_json(state_path)
    formal_sha = sha256(formal_path)
    if (
        state.get("current_run") is not None
        or state.get("status") != "preparing_next_batch"
        or state.get("next_mode") != "diagnostic"
        or int(state.get("next_ordinal", -1)) != 89
        or state.get("formal_state_sha256") != formal_sha
    ):
        raise RuntimeError("v27 handover boundary does not match clean v26 state")
    last = (state.get("audits") or [])[-1]
    if not (
        last.get("clean") is True
        and int(last.get("physical_calls", -1)) == 5
        and int(last.get("valid_replays", -1)) == 5
        and int(last.get("technical_failures", -1)) == 0
        and last.get("baseline_restored") is True
        and last.get("mode") == "diagnostic_validation"
    ):
        raise RuntimeError("v27 handover lacks a clean completed validation")
    if any(not path.is_file() for path in V27_FILES):
        raise RuntimeError("v27 source file is missing")

    manifest = copy.deepcopy(source)
    manifest.update(
        {
            "schema_version": "tpcc_autonomous_target10_manifest_v1",
            "controller_version": 27,
            "controller_entrypoint": str(V27_FILES[2]),
            "exact_repeat_builder_path": str(V27_FILES[1]),
            "initial_formal_state_sha256": formal_sha,
            "next_ordinal": 89,
            "current_run": None,
            "pending_completed_run_at_handover": None,
            "resumed_from_manifest": str(source_path),
            "resumed_from_manifest_sha256": sha256(source_path),
            "controller_handover_contract": (
                "clean_v26_boundary_zero_call_score_consistent_v27"
            ),
            "score_consistent_prediction_contract": (
                "utility_representative_equals_independent_actual_score_"
                "median_within_1e-12_v1"
            ),
            "controller_launch_cwd": str(PROJECT),
            "manual_candidate_injection": False,
        }
    )
    pinned = [
        row
        for row in manifest.get("pinned_files") or []
        if row.get("path") not in {str(path) for path in V27_FILES}
    ]
    pinned.extend(
        {"path": str(path), "sha256": sha256(path)} for path in V27_FILES
    )
    manifest["pinned_files"] = pinned
    atomic_json(output_path, manifest)
    print(
        json.dumps(
            {
                "output": str(output_path),
                "manifest_sha256": sha256(output_path),
                "formal_state_sha256": formal_sha,
                "next_ordinal": 89,
                "new_workload_calls": 0,
                "manual_candidate_injection": False,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
