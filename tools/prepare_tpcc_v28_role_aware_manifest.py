#!/usr/bin/env python3
"""Seal a zero-call TPC-C v28 manifest at a completed-run boundary."""

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
V28_FILES = (
    PROJECT / "tools/tpcc_autonomous_target10_controller_v28_role_aware.py",
    PROJECT / "tools/preflight_tpcc_v28_role_aware.py",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
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
    formal = load_json(formal_path)
    formal_sha = sha256(formal_path)
    current = state.get("current_run")
    if not isinstance(current, dict):
        raise RuntimeError("v28 handover requires one completed pending run")
    if (
        state.get("status") != "launching_sealed_run"
        or current.get("mode") != "diagnostic_validation"
        or current.get("formal_state_sha256_before") != formal_sha
        or formal.get("status") != "ready"
        or int(formal.get("accepted_count", -1))
        != int(state.get("accepted_count", formal.get("accepted_count", -2)))
    ):
        raise RuntimeError("v28 handover controller/formal state mismatch")

    ledger_path = Path(str(current["ledger_path"])).resolve()
    summary_path = Path(str(current["summary_path"])).resolve()
    selection_path = Path(str(current["selection_path"])).resolve()
    if not all(path.is_file() for path in (ledger_path, summary_path, selection_path)):
        raise RuntimeError("v28 handover run evidence is incomplete")
    ledger = load_json(ledger_path)
    summary = load_json(summary_path)
    if not (
        int(ledger.get("real_workload_invocation_count", -1)) == 5
        and len(ledger.get("successful") or []) == 5
        and len(ledger.get("technical_failures") or []) == 0
        and int(summary.get("real_workload_invocation_count", -1)) == 5
        and int(summary.get("successful_real_replay_count", -1)) == 5
        and int(summary.get("technical_failure_count", -1)) == 0
        and summary.get("full_top5_complete") is True
        and summary.get("baseline_params_restored") is True
        and summary.get("baseline_snapshot_restored") is True
        and summary.get("gradient_update_applied") is False
        and summary.get("incumbent_updated") is False
        and summary.get("range_updated") is False
        and int(summary.get("accepted_count_delta", -1)) == 0
    ):
        raise RuntimeError("v28 handover run is not clean and state-neutral")
    if any(not path.is_file() for path in V28_FILES):
        raise RuntimeError("v28 source file is missing")

    next_ordinal = int(state["next_ordinal"])
    preparation = Path(str(source["controller_root"])) / f"prepare_{next_ordinal}"
    if preparation.exists():
        raise RuntimeError("v28 handover crossed the next preparation boundary")

    manifest = copy.deepcopy(source)
    manifest.update(
        {
            "controller_version": 28,
            "controller_entrypoint": str(V28_FILES[0]),
            "initial_formal_state_sha256": formal_sha,
            "next_ordinal": next_ordinal,
            "current_run": None,
            "pending_completed_run_at_handover": copy.deepcopy(current),
            "resumed_from_manifest": str(source_path),
            "resumed_from_manifest_sha256": sha256(source_path),
            "controller_handover_contract": (
                "completed_validation_zero_call_role_aware_v28"
            ),
            "role_aware_feasibility_contract": (
                "prospective_trusted_roles_only_prethird_and_formal_v2"
            ),
            "controller_launch_cwd": str(PROJECT),
            "manual_candidate_injection": False,
        }
    )
    pinned = [
        row
        for row in manifest.get("pinned_files") or []
        if row.get("path") not in {str(path) for path in V28_FILES}
    ]
    pinned.extend(
        {"path": str(path), "sha256": sha256(path)} for path in V28_FILES
    )
    manifest["pinned_files"] = pinned
    atomic_json(output_path, manifest)
    print(
        json.dumps(
            {
                "output": str(output_path),
                "manifest_sha256": sha256(output_path),
                "controller_state_sha256": sha256(state_path),
                "formal_state_sha256": formal_sha,
                "pending_run_root": current["root"],
                "next_ordinal": next_ordinal,
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
