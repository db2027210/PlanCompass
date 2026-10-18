#!/usr/bin/env python3
"""Fail-closed recovery for JOB round8 diagnostic-only state pollution."""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any, Dict, List, Mapping


EXPECTED_SHA256 = {
    "events": "5ed5c8f3888c8dbfc444670f91b4a9885ad7781e3f2c87be796ed67fda988d40",
    "summary": "46f1ac12f5f2b369099ecbaeb9b1da255dc2beb2159c8da76a4eddef3a187f08",
    "accepted_counter": "9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa",
    "formal_replay_counter": "f5bde7eb9f6c71611dc5726e8aca3eb4eba3e386da49e0a4ed5c295a90a73a0d",
    "global_replay_counter": "c33566042a5306a04e70d80cba5639590fc5acc47a32ade17cb260677bdf40e8",
    "formal_ledger": "1cff35ba6d827c1a0797f3b277a28780c776ed30bea1a0085a66ddab65ab61ad",
}
BASELINE_PARAMS = {
    "cpu_index_tuple_cost": 0.0005,
    "cpu_operator_cost": 0.0025,
    "cpu_tuple_cost": 0.01,
    "parallel_setup_cost": 7000.0,
    "parallel_tuple_cost": 0.001,
    "random_page_cost": 4.0,
    "seq_page_cost": 1.0,
}
BASELINE_SCORE = 4.416709205648302
POLLUTED_FINAL_SCORE = 4.543691012816539


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} must contain an object")
    return value


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} must contain an object")
            output.append(value)
    return output


def canonical_state() -> Dict[str, Any]:
    return {
        "accepted_count": 0,
        "incumbent_score": BASELINE_SCORE,
        "incumbent_params": copy.deepcopy(BASELINE_PARAMS),
        "range_by_parameter": {
            name: {"min": value, "max": value}
            for name, value in BASELINE_PARAMS.items()
        },
    }


def atomic_write_text(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.recovery.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def validate_sha(path: Path, expected: str, label: str) -> None:
    observed = sha256_file(path)
    if observed != expected:
        raise ValueError(f"{label} SHA mismatch: expected {expected}, observed {observed}")


def validate_original(
    summary: Mapping[str, Any],
    events: List[Mapping[str, Any]],
) -> None:
    if (
        summary.get("physical_invocation_count") != 5
        or summary.get("successful_real_replay_count") != 2
        or summary.get("technical_failure_count") != 3
        or summary.get("baseline_params_restored") is not True
    ):
        raise ValueError("round8 summary counters or baseline restoration do not match pollution state")
    successful = summary.get("successful")
    if not isinstance(successful, list) or [item.get("rank") for item in successful] != [1, 2]:
        raise ValueError("round8 successful ranks are not exactly rank1/rank2")
    for item in successful:
        for field in (
            "accepted",
            "gradient_update_applied",
            "incumbent_update_applied",
            "range_update_applied",
        ):
            if item.get(field) is not True:
                raise ValueError(f"rank {item.get('rank')} does not match polluted {field}=true")
    expected = canonical_state()
    if successful[0].get("incumbent_state_before") != expected:
        raise ValueError("rank1 pre-state does not match authoritative baseline")
    rank2_after = successful[1].get("incumbent_state_after") or {}
    if rank2_after.get("accepted_count") != 2:
        raise ValueError("rank2 polluted accepted_count is not 2")
    if float(rank2_after.get("incumbent_score")) != POLLUTED_FINAL_SCORE:
        raise ValueError("rank2 polluted incumbent score does not match")
    completed = [
        item for item in events
        if item.get("event") == "job_round8_candidate_replay_complete"
    ]
    failures = [
        item for item in events
        if item.get("event") == "job_round8_candidate_technical_failure"
    ]
    if [item.get("rank") for item in completed] != [1, 2]:
        raise ValueError("events do not contain exactly successful rank1/rank2")
    if [item.get("rank") for item in failures] != [3, 4, 5]:
        raise ValueError("events do not contain exactly technical failure rank3/rank4/rank5")


def corrected_record(record: Mapping[str, Any]) -> Dict[str, Any]:
    output = copy.deepcopy(dict(record))
    output.update({
        "accepted": False,
        "gradient_update_applied": False,
        "incumbent_update_applied": False,
        "range_update_applied": False,
        "incumbent_state_before": canonical_state(),
        "incumbent_state_after": canonical_state(),
        "diagnostic_only": True,
        "quarantined_from_formal_state": True,
        "quarantine_reason": "diagnostic replay must not mutate accepted/incumbent/range/gradient",
    })
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.attempt_root.resolve()
    diagnostic = root / "gradient_debug_round8_runtimefix_v1_20260729T0808"
    events_path = diagnostic / "replay/events.jsonl"
    summary_path = diagnostic / "replay/summary.json"
    accepted_counter = root / "formal_search_accepted_counter.txt"
    formal_replay_counter = root / "formal_search_replay_counter.txt"
    global_replay_counter = root / "replay_counter.txt"
    formal_ledger = root / "formal_replay_ledger.jsonl"
    paths = {
        "events": events_path,
        "summary": summary_path,
        "accepted_counter": accepted_counter,
        "formal_replay_counter": formal_replay_counter,
        "global_replay_counter": global_replay_counter,
        "formal_ledger": formal_ledger,
    }
    for label, path in paths.items():
        if not path.is_file():
            raise FileNotFoundError(f"missing {label}: {path}")
        validate_sha(path, EXPECTED_SHA256[label], label)
    if accepted_counter.read_text(encoding="utf-8").strip() != "0":
        raise ValueError("formal accepted counter is not 0")
    if formal_replay_counter.read_text(encoding="utf-8").strip() != "130":
        raise ValueError("formal replay counter is not 130")
    if global_replay_counter.read_text(encoding="utf-8").strip() != "179":
        raise ValueError("global replay ordinal is not 179")
    summary = read_json(summary_path)
    events = read_jsonl(events_path)
    validate_original(summary, events)

    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_root = diagnostic / f"pollution_recovery_backup_{timestamp}"
    if backup_root.exists():
        raise FileExistsError(f"backup already exists: {backup_root}")
    backup_root.mkdir(parents=False)
    for label in ("events", "summary", "accepted_counter"):
        shutil.copy2(paths[label], backup_root / paths[label].name)
    backup_manifest = {
        "created_at_utc": timestamp,
        "contract": "job_round8_diagnostic_pollution_backup_v1",
        "source_sha256": {
            label: EXPECTED_SHA256[label]
            for label in paths
        },
    }
    atomic_write_text(
        backup_root / "manifest.json",
        json.dumps(backup_manifest, indent=2, sort_keys=True) + "\n",
    )

    quarantine_records = []
    corrected_events = []
    for event in events:
        if event.get("event") == "job_round8_candidate_replay_complete":
            quarantine_records.append({
                "rank": event.get("rank"),
                "candidate_binding": event.get("candidate_binding"),
                "actual_resource": event.get("actual_resource"),
                "actual_utilization": event.get("actual_utilization"),
                "actual_score": event.get("actual_score"),
                "measurement_protocol": event.get("measurement_protocol"),
                "original_state_before": event.get("incumbent_state_before"),
                "original_state_after": event.get("incumbent_state_after"),
            })
            corrected_events.append(corrected_record(event))
        else:
            corrected_events.append(copy.deepcopy(event))
    corrected_summary = copy.deepcopy(dict(summary))
    corrected_summary["successful"] = [
        corrected_record(item) for item in summary["successful"]
    ]
    for invocation in corrected_summary.get("invocations") or []:
        if invocation.get("rank") in (1, 2):
            invocation["accepted"] = False
            invocation["diagnostic_only"] = True
            invocation["quarantined_from_formal_state"] = True
    corrected_summary.update({
        "diagnostic_only": True,
        "authoritative_state_after_diagnostic": canonical_state(),
        "inherited_accepted_count": 0,
        "diagnostic_batch_added_accepted_count": 0,
        "formal_countable_accepted_count": 0,
        "rank1_rank2_quarantined": True,
        "rejected_candidate_updates_incumbent_range_or_accepted": False,
        "diagnostic_updates_gradient": False,
    })
    quarantine = {
        "created_at_utc": timestamp,
        "contract": "job_round8_rank1_rank2_diagnostic_quarantine_v1",
        "diagnostic_batch": str(diagnostic),
        "formal_state_files": {
            "accepted_counter": str(accepted_counter),
            "formal_replay_counter": str(formal_replay_counter),
            "global_replay_counter": str(global_replay_counter),
        },
        "authoritative_state": canonical_state(),
        "physical_accounting_preserved": {
            "physical_invocations": 5,
            "valid_replays": 2,
            "technical_failures": 3,
            "formal_search_replay_count": 130,
            "global_replay_ordinal": 179,
        },
        "quarantined_records": quarantine_records,
        "original_sha256": {
            "events": EXPECTED_SHA256["events"],
            "summary": EXPECTED_SHA256["summary"],
        },
    }
    quarantine_root = diagnostic / "quarantine"
    quarantine_root.mkdir(exist_ok=True)
    quarantine_path = quarantine_root / f"rank1_rank2_diagnostic_audit_{timestamp}.json"

    atomic_write_text(
        events_path,
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in corrected_events),
    )
    atomic_write_text(
        summary_path,
        json.dumps(corrected_summary, indent=2, sort_keys=True) + "\n",
    )
    atomic_write_text(
        accepted_counter,
        "0\n",
    )
    atomic_write_text(
        quarantine_path,
        json.dumps(quarantine, indent=2, sort_keys=True) + "\n",
    )

    if accepted_counter.read_text(encoding="utf-8").strip() != "0":
        raise RuntimeError("accepted counter verification failed after recovery")
    if formal_replay_counter.read_text(encoding="utf-8").strip() != "130":
        raise RuntimeError("formal replay counter changed during recovery")
    if global_replay_counter.read_text(encoding="utf-8").strip() != "179":
        raise RuntimeError("global replay ordinal changed during recovery")
    verified_summary = read_json(summary_path)
    if verified_summary.get("authoritative_state_after_diagnostic") != canonical_state():
        raise RuntimeError("corrected summary state verification failed")
    print(json.dumps({
        "status": "recovered",
        "backup_root": str(backup_root),
        "quarantine_audit": str(quarantine_path),
        "corrected_sha256": {
            "events": sha256_file(events_path),
            "summary": sha256_file(summary_path),
            "accepted_counter": sha256_file(accepted_counter),
        },
        "preserved_counters": {
            "formal_search_replay_count": 130,
            "global_replay_ordinal": 179,
        },
        "formal_accepted_count": 0,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
