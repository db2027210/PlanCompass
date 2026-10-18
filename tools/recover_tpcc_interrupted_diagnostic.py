#!/usr/bin/env python3
"""Fail-closed recovery for a TPC-C diagnostic interrupted after reservation."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List


RESERVED_EVENT = "tpcc_gradient_real_replay_workload_invocation_reserved"
COMPLETE_EVENT = "tpcc_gradient_real_replay_complete"
TECHNICAL_EVENT = "tpcc_gradient_real_replay_technical_failure"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def load_events(path: Path) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise RuntimeError(
                f"event line {line_number} is not a JSON object"
            )
        events.append(value)
    return events


def atomic_json(path: Path, value: Dict[str, Any]) -> None:
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
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_events(path: Path, events: List[Dict[str, Any]]) -> None:
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--formal-state", required=True, type=Path)
    parser.add_argument("--formal-state-sha256", required=True)
    parser.add_argument("--selection-sha256", required=True)
    parser.add_argument("--rank", required=True, type=int)
    parser.add_argument("--attempt", required=True, type=int)
    parser.add_argument("--expected-physical", required=True, type=int)
    parser.add_argument("--expected-valid", required=True, type=int)
    parser.add_argument("--audit-output", required=True, type=Path)
    args = parser.parse_args()

    run_root = args.run_root.resolve()
    replay_root = run_root / "top5_real_replay_round14_diagnostic_v1"
    ledger_path = (
        run_root / "strict_top5_real_replay_round14_ledger_v1.json"
    )
    selection_path = run_root / "top5.json"
    events_path = replay_root / "events.jsonl"
    point_root = (
        replay_root
        / f"rank_{args.rank:02d}"
        / f"attempt_{args.attempt:02d}"
    )

    run_manifests = list(point_root.glob("point_*/run_manifest.json"))
    required = [
        args.formal_state,
        selection_path,
        ledger_path,
        events_path,
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(f"required evidence missing: {missing}")
    if len(run_manifests) != 1:
        raise RuntimeError(
            "interrupted point must have exactly one run manifest"
        )
    if sha256(args.formal_state) != args.formal_state_sha256:
        raise RuntimeError("formal state SHA mismatch")
    if sha256(selection_path) != args.selection_sha256:
        raise RuntimeError("selection SHA mismatch")
    if list(point_root.glob("point_*/summary.json")):
        raise RuntimeError("interrupted point has a measurement summary")

    ledger = load_json(ledger_path)
    events = load_events(events_path)
    reservations = [
        event for event in events if event.get("event") == RESERVED_EVENT
    ]
    successful_events = [
        event for event in events if event.get("event") == COMPLETE_EVENT
    ]
    binding = (args.rank, args.attempt)
    matching_reservations = [
        event
        for event in reservations
        if (int(event.get("rank", -1)), int(event.get("attempt", -1)))
        == binding
    ]
    matching_terminal = [
        event
        for event in events
        if event.get("event") in {COMPLETE_EVENT, TECHNICAL_EVENT}
        and (int(event.get("rank", -1)), int(event.get("attempt", -1)))
        == binding
    ]
    if len(reservations) != args.expected_physical:
        raise RuntimeError("physical reservation count mismatch")
    if len(successful_events) != args.expected_valid:
        raise RuntimeError("valid replay count mismatch")
    if len(matching_reservations) != 1 or matching_terminal:
        raise RuntimeError("interrupted binding is not uniquely unterminated")
    if int(ledger.get("real_workload_invocation_count", -1)) != (
        args.expected_physical
    ):
        raise RuntimeError("ledger physical count mismatch")
    successful = ledger.get("successful") or []
    technical_failures = ledger.get("technical_failures") or []
    if len(successful) != args.expected_valid:
        raise RuntimeError("ledger valid replay count mismatch")
    if any(
        int(item.get("rank", -1)) == args.rank
        and int(item.get("attempt", -1)) == args.attempt
        for item in [*successful, *technical_failures]
    ):
        raise RuntimeError("interrupted binding already represented in ledger")

    timestamp = time.strftime("%Y%m%dT%H%M%S")
    backup_root = args.audit_output.resolve() / f"backup_{timestamp}"
    backup_root.mkdir(parents=True, exist_ok=False)
    shutil.copy2(ledger_path, backup_root / ledger_path.name)
    shutil.copy2(events_path, backup_root / events_path.name)

    failure = {
        "rank": args.rank,
        "attempt": args.attempt,
        "error": (
            "InterruptedAfterPhysicalInvocationReserved: controller and "
            "workload process exited before a measurement summary was emitted"
        ),
        "technical_failure": True,
        "valid_replay": False,
        "consumes_successful_replay_budget": False,
        "consumes_physical_invocation_budget": True,
        "quarantine_reason": (
            "reserved_physical_invocation_without_terminal_summary"
        ),
        "recovery_timestamp_epoch": time.time(),
    }
    updated_ledger = copy.deepcopy(ledger)
    updated_ledger.setdefault("technical_failures", []).append(failure)
    updated_events = list(events)
    updated_events.append({"event": TECHNICAL_EVENT, **failure})

    atomic_json(ledger_path, updated_ledger)
    atomic_events(events_path, updated_events)

    audit = {
        "contract": "tpcc_interrupted_diagnostic_recovery_v1",
        "formal_state_path": str(args.formal_state.resolve()),
        "formal_state_sha256_before": args.formal_state_sha256,
        "formal_state_sha256_after": sha256(args.formal_state),
        "selection_path": str(selection_path),
        "selection_sha256": sha256(selection_path),
        "ledger_path": str(ledger_path),
        "ledger_sha256_before": sha256(backup_root / ledger_path.name),
        "ledger_sha256_after": sha256(ledger_path),
        "events_path": str(events_path),
        "events_sha256_before": sha256(backup_root / events_path.name),
        "events_sha256_after": sha256(events_path),
        "backup_root": str(backup_root),
        "quarantined_binding": {
            "rank": args.rank,
            "attempt": args.attempt,
        },
        "physical_invocations_preserved": args.expected_physical,
        "valid_replays_preserved": args.expected_valid,
        "formal_updates_applied": False,
    }
    if audit["formal_state_sha256_after"] != args.formal_state_sha256:
        raise RuntimeError("formal state changed during recovery")
    args.audit_output.mkdir(parents=True, exist_ok=True)
    audit_path = args.audit_output / f"recovery_audit_{timestamp}.json"
    atomic_json(audit_path, audit)
    print(json.dumps(audit, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
