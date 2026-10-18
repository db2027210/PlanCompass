#!/usr/bin/env python3
"""Verify and seal the zero-call XP v27 handover after a complete cont053."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Set


EXPECTED_ACCEPTED_COUNT = 4
EXPECTED_ACCEPTED_TARGET = 10
EXPECTED_INCUMBENT_SCORE = -49.201616429624124
EXPECTED_BATCH_ID = "cont053"
EXPECTED_NEXT_BATCH_INDEX = 54
EXPECTED_CPUSET = "32-51,288-307"
EXPECTED_BUNDLE_MANIFEST_SHA256 = (
    "8de8a6018d9ff7079ba878e1eb747b84c79319f181952bbf76334eb75b58afcb"
)


def sha256(path: Path) -> str:
    """Return the SHA-256 of one regular file."""

    if not path.is_file():
        raise RuntimeError(f"required file is missing: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest(value: Any) -> str:
    """Match the continuation controller's canonical JSON digest."""

    encoded = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load(path: Path) -> Dict[str, Any]:
    """Load one JSON object."""

    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def atomic_json(path: Path, value: Dict[str, Any]) -> None:
    """Atomically write and fsync a JSON object."""

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


def process_state(pid: int) -> str:
    """Read one Linux process state, or return ``absent``."""

    status = Path(f"/proc/{pid}/status")
    if not status.exists():
        return "absent"
    for line in status.read_text(encoding="utf-8").splitlines():
        if line.startswith("State:"):
            return line.split()[1]
    raise RuntimeError(f"cannot read process state for PID {pid}")


def process_children(pid: int) -> List[int]:
    """Return direct children of one process without changing their state."""

    children_path = Path(f"/proc/{pid}/task/{pid}/children")
    if not children_path.exists():
        return []
    return [int(item) for item in children_path.read_text(encoding="utf-8").split()]


def verify_controller_boundary(pid: int) -> str:
    """Require a stopped/absent controller and no surviving descendants."""

    state = process_state(pid)
    if state not in {"T", "t", "Z", "X", "absent"}:
        raise RuntimeError(
            f"XP controller PID {pid} is not stopped at the boundary: state={state}"
        )
    children = process_children(pid)
    if children:
        raise RuntimeError(
            f"XP controller still has child processes at handover: {children}"
        )
    return state


def selection_fingerprints(selection: Dict[str, Any]) -> List[str]:
    """Extract five valid, unique three-level fingerprint identifiers."""

    top5 = selection.get("top5")
    if not isinstance(top5, list) or len(top5) != 5:
        raise RuntimeError("cont053 selection is not a sealed Top-5")
    identifiers: List[str] = []
    for item in top5:
        if not isinstance(item, dict):
            raise RuntimeError("cont053 Top-5 contains a non-object")
        fingerprint = item.get("three_level_fingerprint") or {}
        identifier = str(fingerprint.get("id") or "")
        if fingerprint.get("valid") is not True or not identifier.startswith(
            "three-level-sha256:"
        ):
            raise RuntimeError("cont053 contains an invalid three-level fingerprint")
        identifiers.append(identifier)
    if len(set(identifiers)) != 5:
        raise RuntimeError("cont053 Top-5 fingerprints are not unique")
    return identifiers


def physical_fingerprints(summary: Dict[str, Any]) -> List[str]:
    """Verify five physical ledger records and return their identifiers."""

    ledger = summary.get("ledger")
    if not isinstance(ledger, list) or len(ledger) != 5:
        raise RuntimeError("cont053 does not have a complete five-call ledger")
    identifiers: List[str] = []
    ranks: List[int] = []
    for item in ledger:
        if not isinstance(item, dict):
            raise RuntimeError("cont053 physical ledger contains a non-object")
        if not (
            item.get("physical_call") is True
            and item.get("execution") == "ran_once"
            and item.get("updates_incumbent_range_or_accepted") is False
        ):
            raise RuntimeError("cont053 physical replay violated diagnostic isolation")
        if item.get("technical_failure") is True:
            if item.get("consumes_valid_replay_budget") is not False:
                raise RuntimeError("cont053 technical failure counted as a valid replay")
        elif not (
            item.get("baseline_restore_verified") is True
            and item.get("accepted") is False
            and item.get("accepted_update_applied") is False
            and item.get("gradient_update_applied") is False
            and item.get("incumbent_update_applied") is False
            and item.get("range_update_applied") is False
        ):
            raise RuntimeError("cont053 valid replay violated diagnostic isolation")
        identifiers.append(str(item.get("fingerprint_id") or ""))
        ranks.append(int(item.get("rank", -1)))
    if sorted(ranks) != [1, 2, 3, 4, 5]:
        raise RuntimeError("cont053 physical ranks are incomplete")
    if len(set(identifiers)) != 5:
        raise RuntimeError("cont053 physical fingerprints are not unique")
    return identifiers


def verify_zero_call_cont054(batch: Path) -> List[str]:
    """Allow only offline selection/restore files in a speculative cont054."""

    if not batch.exists():
        return []
    files = sorted(
        str(path.relative_to(batch)) for path in batch.rglob("*") if path.is_file()
    )
    forbidden_names = {
        "replay_summary.json",
        "result.json",
        "warmup.log",
        "recovery.log",
        "workload.log",
        "measurement_summary.json",
    }
    for name in files:
        if Path(name).name in forbidden_names:
            raise RuntimeError(
                f"cont054 may have crossed the physical-call boundary: {name}"
            )
        if name.startswith("serial_replay/") and Path(name).name != "candidate.json":
            raise RuntimeError(f"unexpected cont054 replay artifact: {name}")
    candidate_files = [
        name
        for name in files
        if name.startswith("serial_replay/") and Path(name).name == "candidate.json"
    ]
    if len(candidate_files) > 1:
        raise RuntimeError("cont054 contains more than one prepared replay candidate")
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-root", required=True, type=Path)
    parser.add_argument("--bundle-root", required=True, type=Path)
    parser.add_argument("--expected-state-sha256", required=True)
    parser.add_argument("--expected-selection-sha256", required=True)
    parser.add_argument("--expected-replay-sha256", required=True)
    parser.add_argument("--controller-pid", required=True, type=int)
    parser.add_argument("--quarantine-name", required=True)
    parser.add_argument("--audit-name", default="v27_boundary_handover.json")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    campaign = args.campaign_root.resolve()
    bundle = args.bundle_root.resolve()
    state_path = campaign / "state.json"
    selection_path = campaign / "batches" / EXPECTED_BATCH_ID / "selection.json"
    replay_path = campaign / "batches" / EXPECTED_BATCH_ID / "replay_summary.json"
    cont054 = campaign / "batches" / "cont054"
    quarantine = campaign / "batches" / args.quarantine_name
    audit_path = campaign / args.audit_name

    observed_state_sha = sha256(state_path)
    if observed_state_sha != args.expected_state_sha256:
        raise RuntimeError("XP v27 boundary state SHA mismatch")
    if sha256(selection_path) != args.expected_selection_sha256:
        raise RuntimeError("XP cont053 selection SHA mismatch")
    if sha256(replay_path) != args.expected_replay_sha256:
        raise RuntimeError("XP cont053 replay SHA mismatch")
    if sha256(bundle / "bundle_manifest.json") != EXPECTED_BUNDLE_MANIFEST_SHA256:
        raise RuntimeError("XP v27 bundle manifest SHA mismatch")
    if audit_path.exists() or quarantine.exists():
        raise RuntimeError("XP v27 handover output already exists")

    controller_state = verify_controller_boundary(args.controller_pid)
    state = load(state_path)
    selection = load(selection_path)
    replay = load(replay_path)
    selection_ids = selection_fingerprints(selection)
    physical_ids = physical_fingerprints(replay)
    technical_failures = int(replay.get("technical_failure_count", -1))
    valid_replays = int(replay.get("successful_real_replay_count", -1))
    replayed_ids: Set[str] = {str(item) for item in state.get("replayed_fingerprints") or []}
    last_batch = state.get("last_batch") or {}
    batch_history = state.get("batch_history") or []

    if not (
        int(state.get("accepted_count", -1)) == EXPECTED_ACCEPTED_COUNT
        and int(state.get("accepted_target", -1)) == EXPECTED_ACCEPTED_TARGET
        and int(state.get("next_batch_index", -1)) == EXPECTED_NEXT_BATCH_INDEX
        and state.get("status") == "running"
        and "last_error" not in state
        and float(state.get("incumbent_score")) == EXPECTED_INCUMBENT_SCORE
        and last_batch.get("batch_id") == EXPECTED_BATCH_ID
        and last_batch.get("mode") == "diagnostic_validation"
        and last_batch.get("status") == "complete"
        and int(last_batch.get("physical_calls", -1)) == 5
        and int(last_batch.get("accepted", -1)) == 0
        and int(last_batch.get("technical_failures", -1)) == technical_failures
        and int(last_batch.get("rejected", -1)) == 0
        and isinstance(batch_history, list)
        and batch_history
        and batch_history[-1] == last_batch
    ):
        raise RuntimeError("XP state is not the exact complete cont053 boundary")
    if not (
        replay.get("diagnostic_only") is True
        and int(replay.get("physical_workload_invocation_count", -1)) == 5
        and valid_replays >= 0
        and technical_failures >= 0
        and valid_replays + technical_failures == 5
        and replay.get("baseline_restore_verified") is True
        and int(replay.get("accepted_count_delta", -1)) == 0
        and replay.get("accepted_update_applied") is False
        and replay.get("gradient_update_applied") is False
        and replay.get("incumbent_updated") is False
        and replay.get("range_updated") is False
        and replay.get("state_mutations_applied_by_this_bundle") is False
        and replay.get("cpuset_cpus") == EXPECTED_CPUSET
    ):
        raise RuntimeError("XP cont053 replay summary violated the fixed contract")
    if set(selection_ids) != set(physical_ids):
        raise RuntimeError("XP cont053 selection/replay fingerprints differ")
    if not set(physical_ids).issubset(replayed_ids):
        raise RuntimeError("XP cont053 physical fingerprints were not committed to state")
    if last_batch.get("fingerprint_digest") != digest(sorted(physical_ids)):
        raise RuntimeError("XP cont053 fingerprint digest does not match replay order")

    cont054_inventory = verify_zero_call_cont054(cont054)
    result: Dict[str, Any] = {
        "status": "dry_run_passed",
        "campaign_root": str(campaign),
        "controller_pid": args.controller_pid,
        "controller_state": controller_state,
        "state_sha256": observed_state_sha,
        "cont053_selection_sha256": sha256(selection_path),
        "cont053_replay_sha256": sha256(replay_path),
        "v27_bundle_manifest_sha256": EXPECTED_BUNDLE_MANIFEST_SHA256,
        "accepted_count": EXPECTED_ACCEPTED_COUNT,
        "accepted_target": EXPECTED_ACCEPTED_TARGET,
        "cont053_physical_calls": 5,
        "cont053_valid_replays": valid_replays,
        "cont053_technical_failures": technical_failures,
        "cont053_fingerprints": physical_ids,
        "cont053_fingerprints_committed_and_excluded": True,
        "cont054_inventory": cont054_inventory,
        "physical_workload_calls_added": 0,
        "formal_state_fields_mutated": False,
    }
    if args.apply:
        backup = campaign / f"state.pre_v27_handover.{observed_state_sha[:12]}.json"
        if backup.exists():
            raise RuntimeError("XP v27 state backup already exists")
        shutil.copy2(state_path, backup)
        if sha256(backup) != observed_state_sha:
            raise RuntimeError("XP v27 state backup SHA mismatch")
        if cont054.exists():
            cont054.rename(quarantine)
        result.update(
            {
                "status": "applied_and_verified",
                "backup": str(backup),
                "backup_sha256": sha256(backup),
                "cont054_quarantine": str(quarantine) if cont054_inventory else None,
            }
        )
        atomic_json(audit_path, result)
        if sha256(state_path) != observed_state_sha:
            raise RuntimeError("XP v27 handover changed state.json")
        result["audit_path"] = str(audit_path)
        result["audit_sha256"] = sha256(audit_path)

    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
