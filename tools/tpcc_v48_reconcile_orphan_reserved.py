#!/usr/bin/env python3
"""Zero-call reconciliation for the interrupted TPC-C v47 rank-4 call."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any, Dict, Iterable, Tuple


CONTRACT = "tpcc_v48_orphan_reserved_zero_call_recovery_v1"
LEDGER_CONTRACT = "tpcc_round17_v47_calibrated_trusted3_strict_serial_replay_v1"
TECHNICAL_ERROR = "InterruptedInvocation: reserved call lacked a terminal event"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"TPC-C v48 expected JSON object: {path}")
    return value


def _events(path: Path) -> list[Dict[str, Any]]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"TPC-C v48 invalid event line {line_number}") from exc
        if not isinstance(row, dict):
            raise RuntimeError(f"TPC-C v48 non-object event line {line_number}")
        rows.append(row)
    return rows


def _bindings(
    rows: Iterable[Dict[str, Any]], event_name: str
) -> set[Tuple[int, int]]:
    return {
        (int(row["rank"]), int(row["attempt"]))
        for row in rows
        if row.get("event") == event_name
    }


def _atomic_bytes(path: Path, value: bytes) -> None:
    temporary = path.with_suffix(path.suffix + ".v48.tmp")
    temporary.write_bytes(value)
    os.replace(temporary, path)


def _atomic_json(path: Path, value: Dict[str, Any]) -> None:
    _atomic_bytes(
        path,
        (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )


def reconcile(
    *,
    selection_path: Path,
    selection_sha256: str,
    selection_manifest_path: Path,
    selection_manifest_sha256: str,
    ledger_path: Path,
    ledger_sha256: str,
    events_path: Path,
    events_sha256: str,
    formal_state_path: Path,
    formal_state_sha256: str,
    output_root: Path,
    orphan_rank: int,
) -> Dict[str, Any]:
    pins = {
        "selection": (selection_path.resolve(), selection_sha256),
        "selection_manifest": (
            selection_manifest_path.resolve(),
            selection_manifest_sha256,
        ),
        "ledger_pre_recovery": (ledger_path.resolve(), ledger_sha256),
        "events_pre_recovery": (events_path.resolve(), events_sha256),
        "formal_state": (formal_state_path.resolve(), formal_state_sha256),
    }
    for label, (path, expected) in pins.items():
        if not path.is_file() or _sha(path) != expected:
            raise RuntimeError(f"TPC-C v48 sealed SHA mismatch: {label}")
    output_root = output_root.resolve()
    if output_root.exists():
        raise FileExistsError("TPC-C v48 recovery output root already exists")

    selection = _load(selection_path)
    ledger = _load(ledger_path)
    rows = _events(events_path)
    formal = _load(formal_state_path)
    started = _bindings(rows, "tpcc_gradient_real_replay_started")
    reserved = _bindings(
        rows, "tpcc_gradient_real_replay_workload_invocation_reserved"
    )
    completed = _bindings(rows, "tpcc_gradient_real_replay_complete")
    technical = _bindings(rows, "tpcc_gradient_real_replay_technical_failure")
    successful_bindings = {
        (int(row["rank"]), int(row["attempt"]))
        for row in ledger.get("successful") or []
    }
    ledger_technical = {
        (int(row["rank"]), int(row["attempt"]))
        for row in ledger.get("technical_failures") or []
    }
    if orphan_rank not in {4, 5}:
        raise RuntimeError("TPC-C v48 orphan rank must be 4 or 5")
    orphan = (orphan_rank, 1)
    successful_expected = {(rank, 1) for rank in range(1, orphan_rank)}
    invoked_expected = successful_expected | {orphan}
    remaining_budget = 5 - orphan_rank
    remaining_rank = orphan_rank + 1 if remaining_budget == 1 else None
    attempt_root = (
        events_path.parent / f"rank_{orphan_rank:02d}" / "attempt_01"
    )
    if (
        selection.get("selection_contract")
        != "tpcc_v47_exact_plan_regime_calibrated_trusted3_probe2_v1"
        or ledger.get("contract") != LEDGER_CONTRACT
        or int(ledger.get("real_workload_invocation_count", -1)) != orphan_rank
        or int(ledger.get("maximum_real_workload_invocations", -1)) != 5
        or successful_bindings != successful_expected
        or ledger_technical
        or started != invoked_expected
        or reserved != started
        or completed != successful_expected
        or technical
        or orphan not in reserved - completed
        or (attempt_root / "point_result.json").exists()
        or bool(list(attempt_root.glob("point_*/summary.json")))
        or int(formal.get("accepted_count", -1)) != 0
        or int(formal.get("formal_physical_call_count", -1)) != 0
        or int(formal.get("formal_valid_replay_count", -1)) != 0
        or int(formal.get("formal_technical_failure_count", -1)) != 0
    ):
        raise RuntimeError("TPC-C v48 orphan-reservation precondition mismatch")

    failure = {
        "rank": orphan_rank,
        "attempt": 1,
        "error": TECHNICAL_ERROR,
        "consumes_physical_call_budget": True,
        "consumes_successful_replay_budget": False,
        "consumes_technical_failure_budget": True,
        "recovered_unsettled_reservation": True,
        "recovery_contract": CONTRACT,
        "same_rank_retry_allowed": False,
    }
    recovered_ledger = json.loads(json.dumps(ledger))
    recovered_ledger["technical_failures"] = [failure]
    recovered_ledger["real_workload_invocation_count"] = orphan_rank
    recovered_ledger["v48_recovery"] = {
        "contract": CONTRACT,
        "orphan_binding": [orphan_rank, 1],
        "remaining_physical_budget": remaining_budget,
        "only_permitted_remaining_rank": remaining_rank,
        "orphan_rank_retry_allowed": False,
        "batch_clean5_eligible": False,
        "batch_formal_gate_eligible": False,
        "workload_calls_added": 0,
    }
    terminal_event = {
        "attempt": 1,
        "consumes_physical_call_budget": True,
        "consumes_successful_replay_budget": False,
        "consumes_technical_failure_budget": True,
        "error": TECHNICAL_ERROR,
        "event": "tpcc_gradient_real_replay_technical_failure",
        "rank": orphan_rank,
        "recovered_unsettled_reservation": True,
        "recovery_contract": CONTRACT,
        "same_rank_retry_allowed": False,
        "timestamp_epoch": time.time(),
    }
    recovered_event_bytes = events_path.read_bytes()
    if recovered_event_bytes and not recovered_event_bytes.endswith(b"\n"):
        raise RuntimeError("TPC-C v48 events file lacks terminal newline")
    recovered_event_bytes += (
        json.dumps(terminal_event, sort_keys=True) + "\n"
    ).encode("utf-8")
    recovered_ledger_bytes = (
        json.dumps(recovered_ledger, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    recovered_events_sha = hashlib.sha256(recovered_event_bytes).hexdigest()
    recovered_ledger_sha = hashlib.sha256(recovered_ledger_bytes).hexdigest()

    output_root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{output_root.name}.stage-", dir=str(output_root.parent)
    ) as temporary_name:
        stage = Path(temporary_name)
        shutil.copy2(ledger_path, stage / "ledger.pre_recovery.json")
        shutil.copy2(events_path, stage / "events.pre_recovery.jsonl")
        (stage / "ledger.recovered.json").write_bytes(recovered_ledger_bytes)
        (stage / "events.recovered.jsonl").write_bytes(recovered_event_bytes)
        prepared = {
            "schema_version": "tpcc_v48_orphan_recovery_prepared_v1",
            "contract": CONTRACT,
            "source_authority": {
                label: {"path": str(path), "sha256": expected}
                for label, (path, expected) in pins.items()
            },
            "post_recovery_sha256": {
                "ledger": recovered_ledger_sha,
                "events": recovered_events_sha,
            },
            "physical_calls_before": orphan_rank,
            "physical_calls_after": orphan_rank,
            "valid_replays": orphan_rank - 1,
            "technical_failures_before": 0,
            "technical_failures_after": 1,
            "orphan_binding": [orphan_rank, 1],
            "orphan_rank_retry_allowed": False,
            "remaining_physical_budget": remaining_budget,
            "only_permitted_remaining_rank": remaining_rank,
            "batch_clean5_eligible": False,
            "batch_formal_gate_eligible": False,
            "baseline_restore_evidence_after_crash": False,
            "formal_state_mutated": False,
            "workload_calls_added": 0,
        }
        _atomic_json(stage / "prepared.json", prepared)
        os.replace(stage, output_root)

    # Event-first ordering makes an interrupted recovery conservative: an
    # event-only intermediate state still prevents a retry and can be audited.
    _atomic_bytes(events_path, recovered_event_bytes)
    _atomic_bytes(ledger_path, recovered_ledger_bytes)
    if (
        _sha(events_path) != recovered_events_sha
        or _sha(ledger_path) != recovered_ledger_sha
        or _sha(formal_state_path) != formal_state_sha256
    ):
        raise RuntimeError("TPC-C v48 recovered authority verification failed")
    completed_record = {
        "schema_version": "tpcc_v48_orphan_recovery_completed_v1",
        "contract": CONTRACT,
        "ledger_sha256": recovered_ledger_sha,
        "events_sha256": recovered_events_sha,
        "formal_state_sha256": formal_state_sha256,
        "workload_calls_added": 0,
        "physical_calls": orphan_rank,
        "valid_replays": orphan_rank - 1,
        "technical_failures": 1,
        "remaining_rank": remaining_rank,
        "orphan_rank": orphan_rank,
        "orphan_rank_retry_allowed": False,
        "batch_clean5_eligible": False,
        "batch_formal_gate_eligible": False,
    }
    _atomic_json(output_root / "completed.json", completed_record)
    return completed_record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "selection",
        "selection-manifest",
        "ledger",
        "events",
        "formal-state",
    ):
        parser.add_argument(f"--{name}", type=Path, required=True)
        parser.add_argument(f"--{name}-sha256", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--orphan-rank", type=int, choices=(4, 5), required=True)
    args = parser.parse_args()
    result = reconcile(
        selection_path=args.selection,
        selection_sha256=args.selection_sha256,
        selection_manifest_path=args.selection_manifest,
        selection_manifest_sha256=args.selection_manifest_sha256,
        ledger_path=args.ledger,
        ledger_sha256=args.ledger_sha256,
        events_path=args.events,
        events_sha256=args.events_sha256,
        formal_state_path=args.formal_state,
        formal_state_sha256=args.formal_state_sha256,
        output_root=args.output_root,
        orphan_rank=args.orphan_rank,
    )
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
