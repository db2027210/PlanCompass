#!/usr/bin/env python3
"""Supersede a premature recovery event after sealed late completion."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Dict


CONTRACT = "tpcc_v48_late_validated_completion_supersession_v1"
RECOVERY_CONTRACT = "tpcc_v48_orphan_reserved_zero_call_recovery_v1"
SUPERSESSION_EVENT = "tpcc_gradient_recovery_technical_superseded_by_late_validated_completion"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"TPC-C v48 expected JSON object: {path}")
    return value


def _atomic_bytes(path: Path, value: bytes) -> None:
    temporary = path.with_suffix(path.suffix + ".v48.tmp")
    temporary.write_bytes(value)
    os.replace(temporary, path)


def _atomic_json(path: Path, value: Dict[str, Any]) -> None:
    _atomic_bytes(
        path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    )


def supersede(
    *,
    ledger_path: Path,
    ledger_sha256: str,
    events_path: Path,
    events_sha256: str,
    runner_summary_path: Path,
    runner_summary_sha256: str,
    point_result_path: Path,
    point_result_sha256: str,
    point_summary_path: Path,
    point_summary_sha256: str,
    formal_state_path: Path,
    formal_state_sha256: str,
    output_root: Path,
) -> Dict[str, Any]:
    pins = {
        "ledger": (ledger_path.resolve(), ledger_sha256),
        "events_pre_supersession": (events_path.resolve(), events_sha256),
        "runner_summary": (runner_summary_path.resolve(), runner_summary_sha256),
        "point_result": (point_result_path.resolve(), point_result_sha256),
        "point_summary": (point_summary_path.resolve(), point_summary_sha256),
        "formal_state": (formal_state_path.resolve(), formal_state_sha256),
    }
    for label, (path, expected) in pins.items():
        if not path.is_file() or _sha(path) != expected:
            raise RuntimeError(f"TPC-C v48 sealed SHA mismatch: {label}")
    output_root = output_root.resolve()
    if output_root.exists():
        raise FileExistsError("TPC-C v48 supersession output root already exists")
    ledger = _load(ledger_path)
    summary = _load(runner_summary_path)
    point = _load(point_result_path)
    rows = [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
    ]
    recovery_lines = [
        index
        for index, row in enumerate(rows, 1)
        if row.get("event") == "tpcc_gradient_real_replay_technical_failure"
        and int(row.get("rank", -1)) == 5
        and int(row.get("attempt", -1)) == 1
        and row.get("recovery_contract") == RECOVERY_CONTRACT
    ]
    completion_lines = [
        index
        for index, row in enumerate(rows, 1)
        if row.get("event") == "tpcc_gradient_real_replay_complete"
        and int(row.get("rank", -1)) == 5
        and int(row.get("attempt", -1)) == 1
    ]
    protocol = point.get("measurement_protocol") or {}
    if (
        recovery_lines != [16]
        or completion_lines != [17]
        or recovery_lines[0] >= completion_lines[0]
        or int(ledger.get("real_workload_invocation_count", -1)) != 5
        or [int(row["rank"]) for row in ledger.get("successful") or []]
        != [1, 2, 3, 4, 5]
        or ledger.get("technical_failures") not in ([], None)
        or int(summary.get("successful_real_replay_count", -1)) != 5
        or int(summary.get("technical_failure_count", -1)) != 0
        or int(summary.get("real_workload_invocation_count", -1)) != 5
        or summary.get("baseline_params_restored") is not True
        or summary.get("baseline_snapshot_restored") is not True
        or summary.get("fatal_error") is not None
        or int(summary.get("accepted_count_delta", -1)) != 0
        or summary.get("gradient_update_applied") is not False
        or summary.get("incumbent_updated") is not False
        or summary.get("range_updated") is not False
        or int(point.get("rank", -1)) != 5
        or int(point.get("attempt", -1)) != 1
        or int(point.get("workload_returncode", -1)) != 0
        or protocol.get("stable") is not True
        or protocol.get("correctness_passed") is not True
        or protocol.get("technical_failure") is not False
        or protocol.get("validated_summary_sha256") != point_summary_sha256
        or _sha(formal_state_path) != formal_state_sha256
    ):
        raise RuntimeError("TPC-C v48 late-completion supersession gate failed")

    event = {
        "event": SUPERSESSION_EVENT,
        "contract": CONTRACT,
        "rank": 5,
        "attempt": 1,
        "superseded_recovery_contract": RECOVERY_CONTRACT,
        "superseded_technical_event_line": recovery_lines[0],
        "late_completion_event_line": completion_lines[0],
        "ledger_path": str(ledger_path.resolve()),
        "ledger_sha256": ledger_sha256,
        "runner_summary_path": str(runner_summary_path.resolve()),
        "runner_summary_sha256": runner_summary_sha256,
        "point_result_path": str(point_result_path.resolve()),
        "point_result_sha256": point_result_sha256,
        "point_summary_path": str(point_summary_path.resolve()),
        "point_summary_sha256": point_summary_sha256,
        "formal_state_path": str(formal_state_path.resolve()),
        "formal_state_sha256": formal_state_sha256,
        "effective_classification": "valid_replay",
        "physical_call_count_delta": 0,
        "workload_calls_added": 0,
        "timestamp_epoch": time.time(),
    }
    pre_bytes = events_path.read_bytes()
    if not pre_bytes.endswith(b"\n"):
        raise RuntimeError("TPC-C v48 events file lacks terminal newline")
    post_bytes = pre_bytes + (json.dumps(event, sort_keys=True) + "\n").encode("utf-8")
    post_sha = hashlib.sha256(post_bytes).hexdigest()
    output_root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{output_root.name}.stage-", dir=str(output_root.parent)
    ) as temporary_name:
        stage = Path(temporary_name)
        (stage / "events.pre_supersession.jsonl").write_bytes(pre_bytes)
        (stage / "events.superseded.jsonl").write_bytes(post_bytes)
        prepared = {
            "schema_version": "tpcc_v48_late_completion_supersession_prepared_v1",
            "contract": CONTRACT,
            "source_authority": {
                label: {"path": str(path), "sha256": expected}
                for label, (path, expected) in pins.items()
            },
            "events_post_supersession_sha256": post_sha,
            "physical_calls": 5,
            "valid_replays": 5,
            "effective_technical_failures": 0,
            "workload_calls_added": 0,
            "formal_state_mutated": False,
        }
        _atomic_json(stage / "prepared.json", prepared)
        os.replace(stage, output_root)
    _atomic_bytes(events_path, post_bytes)
    if _sha(events_path) != post_sha or _sha(formal_state_path) != formal_state_sha256:
        raise RuntimeError("TPC-C v48 supersession persistence verification failed")
    completed = {
        "schema_version": "tpcc_v48_late_completion_supersession_completed_v1",
        "contract": CONTRACT,
        "events_sha256": post_sha,
        "formal_state_sha256": formal_state_sha256,
        "physical_calls": 5,
        "valid_replays": 5,
        "effective_technical_failures": 0,
        "workload_calls_added": 0,
    }
    _atomic_json(output_root / "completed.json", completed)
    return completed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "ledger",
        "events",
        "runner-summary",
        "point-result",
        "point-summary",
        "formal-state",
    ):
        parser.add_argument(f"--{name}", type=Path, required=True)
        parser.add_argument(f"--{name}-sha256", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    result = supersede(
        ledger_path=args.ledger,
        ledger_sha256=args.ledger_sha256,
        events_path=args.events,
        events_sha256=args.events_sha256,
        runner_summary_path=args.runner_summary,
        runner_summary_sha256=args.runner_summary_sha256,
        point_result_path=args.point_result,
        point_result_sha256=args.point_result_sha256,
        point_summary_path=args.point_summary,
        point_summary_sha256=args.point_summary_sha256,
        formal_state_path=args.formal_state,
        formal_state_sha256=args.formal_state_sha256,
        output_root=args.output_root,
    )
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
