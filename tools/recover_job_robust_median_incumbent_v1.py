#!/usr/bin/env python3
"""Fail-closed zero-call correction of JOB's single-score incumbent."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any, Dict, Mapping

from audit_job_three_observation_acceptance import (
    audit,
    load_events,
    load_json,
)


SINGLE_SCORE_INCUMBENT = 4.540453524964638


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def exact_number(left: Any, right: Any) -> bool:
    return isinstance(left, (int, float)) and float(left) == float(right)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--expected-state-sha256", required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--expected-selection-sha256", required=True)
    parser.add_argument("--first-events", type=Path, required=True)
    parser.add_argument("--expected-first-events-sha256", required=True)
    parser.add_argument("--formal-events", type=Path, required=True)
    parser.add_argument("--expected-formal-events-sha256", required=True)
    parser.add_argument("--audit-root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    expected = {
        args.state: args.expected_state_sha256,
        args.selection: args.expected_selection_sha256,
        args.first_events: args.expected_first_events_sha256,
        args.formal_events: args.expected_formal_events_sha256,
    }
    for path, expected_sha in expected.items():
        if not path.is_file() or sha256(path) != expected_sha:
            raise RuntimeError(f"input missing or SHA mismatch: {path}")

    state = load_json(args.state)
    selection = load_json(args.selection)
    reconstructed = audit(
        selection,
        args.first_events,
        load_events(args.first_events),
        load_events(args.formal_events),
    )
    reconstructed["selection_sha256"] = sha256(args.selection)
    accepted_rows = [
        row for row in reconstructed["trusted_rows"] if row["accepted"]
    ]
    if (
        reconstructed["reconstructed_accepted_delta"] != 2
        or len(accepted_rows) != 2
        or int(state.get("accepted_count", -1)) != 2
        or int(state.get("accepted_target", -1)) != 10
        or state.get("snapshot_only") is not False
        or not exact_number(state.get("incumbent_score"), SINGLE_SCORE_INCUMBENT)
        or state.get("incumbent_params") != accepted_rows[-1]["candidate_params"]
    ):
        raise RuntimeError(
            "observed state does not exactly match the single-score pollution"
        )

    protected_fields = {
        name: copy.deepcopy(state.get(name))
        for name in (
            "accepted_count",
            "formal_replay_count",
            "formal_valid_replay_count",
            "global_ordinal",
            "gradient_observation_count",
            "incumbent_params",
            "range_by_parameter",
        )
    }
    corrected = copy.deepcopy(state)
    corrected["incumbent_score"] = float(
        reconstructed["reconstructed_incumbent_score"]
    )
    corrected["robust_acceptance_correction"] = {
        "contract": reconstructed["contract"],
        "applied_at_epoch": time.time() if args.apply else None,
        "selection_sha256": sha256(args.selection),
        "first_events_sha256": sha256(args.first_events),
        "formal_events_sha256": sha256(args.formal_events),
        "single_score_incumbent_before": SINGLE_SCORE_INCUMBENT,
        "robust_incumbent_after": corrected["incumbent_score"],
        "independent_observation_count_per_trusted_point": 3,
        "qps_used_for_inner_scoring": False,
        "accepted_count_delta": 0,
        "physical_workload_calls_added": 0,
    }
    if any(corrected.get(name) != value for name, value in protected_fields.items()):
        raise RuntimeError("correction attempted to mutate protected state")

    preview: Dict[str, Any] = {
        "status": "ready_to_apply" if args.apply else "dry_run_verified",
        "state_path": str(args.state.resolve()),
        "state_sha256_before": sha256(args.state),
        "accepted_count_preserved": state["accepted_count"],
        "formal_replay_count_preserved": state["formal_replay_count"],
        "global_ordinal_preserved": state["global_ordinal"],
        "incumbent_score_before": state["incumbent_score"],
        "incumbent_score_after": corrected["incumbent_score"],
        "physical_workload_calls_added": 0,
        "reconstructed_acceptance": reconstructed,
    }
    if not args.apply:
        print(json.dumps(preview, indent=2, sort_keys=True), flush=True)
        return 0

    timestamp = time.strftime("%Y%m%dT%H%M%S%z")
    audit_dir = args.audit_root / f"robust_median_correction_{timestamp}"
    audit_dir.mkdir(parents=True, exist_ok=False)
    backup = audit_dir / args.state.name
    shutil.copy2(args.state, backup)
    if sha256(backup) != args.expected_state_sha256:
        raise RuntimeError("state backup SHA mismatch")
    (audit_dir / "reconstructed_acceptance.json").write_text(
        json.dumps(reconstructed, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    atomic_json(args.state, corrected)
    observed = load_json(args.state)
    if (
        observed != corrected
        or any(observed.get(name) != value for name, value in protected_fields.items())
    ):
        raise RuntimeError("atomic correction verification failed")
    preview.update(
        {
            "status": "applied_and_verified",
            "audit_dir": str(audit_dir.resolve()),
            "backup_path": str(backup.resolve()),
            "backup_sha256": sha256(backup),
            "state_sha256_after": sha256(args.state),
        }
    )
    (audit_dir / "correction_audit.json").write_text(
        json.dumps(preview, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(preview, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
