#!/usr/bin/env python3
"""Migrate XP incumbent resource/score from one sealed protocol replay.

The accepted count is historical authority and is preserved.  This migration
only replaces the incumbent's measured resource/score after a clean,
state-neutral revalidation under the current workload protocol.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any, Dict, Mapping


RESOURCES = ("cpu", "io", "memory")


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


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def same_params(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return bool(
        set(left) == set(right)
        and all(
            math.isclose(
                float(left[name]),
                float(right[name]),
                rel_tol=1e-10,
                abs_tol=1e-12,
            )
            for name in left
        )
    )


def verify(path: Path, expected: str, label: str) -> None:
    if not path.is_file() or sha256(path) != expected:
        raise RuntimeError(f"{label} SHA mismatch: {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--formal-state", required=True, type=Path)
    parser.add_argument("--formal-state-sha256", required=True)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--candidate-sha256", required=True)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--ledger-sha256", required=True)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--summary-sha256", required=True)
    parser.add_argument("--expected-accepted-count", required=True, type=int)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    paths = (
        (args.formal_state.resolve(), args.formal_state_sha256, "formal state"),
        (args.candidate.resolve(), args.candidate_sha256, "candidate"),
        (args.ledger.resolve(), args.ledger_sha256, "ledger"),
        (args.summary.resolve(), args.summary_sha256, "summary"),
    )
    for path, expected, label in paths:
        verify(path, expected, label)

    state = load_json(args.formal_state)
    envelope = load_json(args.candidate)
    ledger = load_json(args.ledger)
    summary = load_json(args.summary)
    candidate = envelope.get("candidate") or {}
    successful = list(ledger.get("successful") or [])
    failures = list(ledger.get("technical_failures") or [])
    if (
        state.get("contract") != "xp_unattended_continuation_v1"
        or int(state.get("accepted_target", -1)) != 10
        or int(state.get("accepted_count", -1))
        != args.expected_accepted_count
        or state.get("status") != "running"
        or not same_params(
            state.get("incumbent_params") or {},
            (state.get("incumbent") or {}).get("params") or {},
        )
    ):
        raise RuntimeError("XP formal state contract mismatch")
    if (
        envelope.get("contract")
        != "xp_incumbent_protocol_revalidation_candidate_v1"
        or envelope.get("diagnostic_only") is not True
        or int(envelope.get("maximum_physical_workload_invocations", -1)) != 1
        or not same_params(
            candidate.get("candidate_params") or {},
            state["incumbent_params"],
        )
    ):
        raise RuntimeError("XP incumbent candidate binding mismatch")
    if (
        int(ledger.get("maximum_physical_workload_invocations", -1)) != 1
        or int(ledger.get("real_workload_invocation_count", -1)) != 1
        or len(successful) != 1
        or failures
    ):
        raise RuntimeError("XP incumbent replay accounting mismatch")
    result = successful[0]
    protocol = result.get("measurement_protocol") or {}
    if (
        result.get("technical_failure") is not False
        or result.get("state_commit_eligible") is not False
        or protocol.get("stable") is not True
        or protocol.get("correctness_passed") is not True
        or protocol.get("technical_failure") is not False
        or result.get("fingerprint_id")
        != (candidate.get("three_level_fingerprint") or {}).get("id")
        or result.get("workload_plan_fingerprint")
        != candidate.get("workload_plan_fingerprint")
        or not same_params(
            result.get("candidate_params") or {},
            state["incumbent_params"],
        )
    ):
        raise RuntimeError("XP incumbent replay evidence mismatch")
    if (
        summary.get("status") != "complete"
        or int(summary.get("real_workload_invocation_count", -1)) != 1
        or int(summary.get("valid_replay_count", -1)) != 1
        or int(summary.get("technical_failure_count", -1)) != 0
        or summary.get("baseline_params_restored") is not True
        or summary.get("baseline_snapshot_restored") is not True
        or summary.get("formal_state_unchanged") is not True
        or summary.get("formal_state_sha256_before")
        != args.formal_state_sha256
        or summary.get("formal_state_sha256_after")
        != args.formal_state_sha256
        or any(
            summary.get(field) is not False
            for field in (
                "accepted_update_applied",
                "incumbent_update_applied",
                "range_update_applied",
                "gradient_update_applied",
                "qps_used_for_inner_scoring",
            )
        )
    ):
        raise RuntimeError("XP incumbent replay summary mismatch")

    actual_resource = {
        name: float(result["actual_resource"][name]) for name in RESOURCES
    }
    actual_score = float(result["actual_score"])
    if (
        not math.isfinite(actual_score)
        or any(
            not math.isfinite(value) or value < 0.0
            for value in actual_resource.values()
        )
    ):
        raise RuntimeError("XP actual incumbent resource/score is invalid")

    migrated = copy.deepcopy(state)
    old_score = float(state["incumbent_score"])
    migrated["incumbent_score"] = actual_score
    migrated["incumbent_resource"] = copy.deepcopy(actual_resource)
    migrated["incumbent"]["score"] = actual_score
    migrated["incumbent"]["resource"] = copy.deepcopy(actual_resource)
    migrated["incumbent"]["source"] = (
        "sealed_v16_incumbent_protocol_revalidation"
    )
    migration = {
        "contract": "xp_incumbent_protocol_migration_v1",
        "reason": "current_protocol_revalidation_replaces_stale_score_only",
        "source_formal_state_sha256": args.formal_state_sha256,
        "candidate_path": str(args.candidate.resolve()),
        "candidate_sha256": args.candidate_sha256,
        "ledger_path": str(args.ledger.resolve()),
        "ledger_sha256": args.ledger_sha256,
        "summary_path": str(args.summary.resolve()),
        "summary_sha256": args.summary_sha256,
        "physical_call_count": 1,
        "valid_replay_count": 1,
        "technical_failure_count": 0,
        "baseline_params_restored": True,
        "baseline_snapshot_restored": True,
        "accepted_count_preserved": args.expected_accepted_count,
        "incumbent_params_preserved": copy.deepcopy(state["incumbent_params"]),
        "range_state_preserved": copy.deepcopy(state["range_state"]),
        "old_incumbent_score": old_score,
        "new_incumbent_score": actual_score,
        "new_incumbent_resource": copy.deepcopy(actual_resource),
        "accepted_update_applied": False,
        "range_update_applied": False,
        "gradient_update_applied": False,
        "qps_used_for_inner_scoring": False,
    }
    history = list(migrated.get("incumbent_protocol_migrations") or [])
    history.append(migration)
    migrated["incumbent_protocol_migrations"] = history

    for field in (
        "accepted_count",
        "accepted_fingerprints",
        "accepted_target",
        "incumbent_params",
        "parameter_ranges",
        "range_state",
        "replayed_fingerprints",
        "authority",
    ):
        if migrated.get(field) != state.get(field):
            raise RuntimeError(f"migration would mutate protected field: {field}")

    audit = {
        **migration,
        "apply_requested": args.apply,
        "formal_state_path": str(args.formal_state.resolve()),
    }
    if not args.apply:
        print(json.dumps(audit, indent=2, sort_keys=True), flush=True)
        return 0

    timestamp = time.strftime("%Y%m%dT%H%M%S", time.localtime())
    backup = args.formal_state.with_name(
        f"{args.formal_state.name}.pre_protocol_migration_{timestamp}.bak"
    )
    if backup.exists():
        raise FileExistsError(backup)
    shutil.copy2(args.formal_state, backup)
    if sha256(backup) != args.formal_state_sha256:
        raise RuntimeError("XP formal-state backup SHA mismatch")
    atomic_json(args.formal_state, migrated)
    final_sha = sha256(args.formal_state)
    audit.update(
        {
            "backup_path": str(backup),
            "backup_sha256": sha256(backup),
            "final_formal_state_sha256": final_sha,
        }
    )
    audit_path = args.formal_state.with_name(
        f"{args.formal_state.name}.protocol_migration_{timestamp}.json"
    )
    atomic_json(audit_path, audit)
    print(json.dumps(audit, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
