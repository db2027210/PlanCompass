#!/usr/bin/env python3
"""Invalidate replay rows that reused a completed cohort wave after restart."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.run_pg_algorithm_c_scene import (  # noqa: E402
    append_jsonl,
    canonical_sha256,
    read_json,
    write_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lane-root", type=Path, required=True)
    parser.add_argument("--round-index", type=int, required=True)
    parser.add_argument("--maximum-stale-wave-sequence", type=int, required=True)
    parser.add_argument("--expected-accepted", type=int, required=True)
    parser.add_argument(
        "--incident-start-event",
        default="algorithm_c_under_target_incumbent_convergence_reopened",
    )
    return parser.parse_args()


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                value = json.loads(line)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def validation_wave_sequence(validation: Mapping[str, Any]) -> int:
    barrier = validation.get("candidate_cohort_barrier")
    return int(barrier.get("wave_sequence") or 0) if isinstance(barrier, Mapping) else 0


def main() -> int:
    args = parse_args()
    state_path = args.lane_root / "algorithm_c_state.json"
    history_path = args.lane_root / "algorithm_c.jsonl"
    state = read_json(state_path)
    if str(state.get("phase") or "") not in {
        "stopped_by_signal_clean",
        "technical_failure_stopped_clean",
    }:
        raise RuntimeError("lane must be cleanly stopped before cohort replay repair")
    if int(state.get("accepted") or 0) != args.expected_accepted:
        raise RuntimeError("accepted counter changed before cohort replay repair")

    history = read_jsonl(history_path)
    incident_rows = [
        row for row in history if row.get("event") == args.incident_start_event
    ]
    if not incident_rows:
        raise RuntimeError("incident start event is missing")
    incident_start_s = max(float(row.get("time") or 0.0) for row in incident_rows)
    invalidated_before = {
        str(value) for value in state.get("invalidated_replay_labels") or []
    }
    selected: List[Dict[str, Any]] = []
    for row in history:
        if row.get("event") != "algorithm_c_candidate_replay":
            continue
        if float(row.get("time") or 0.0) < incident_start_s:
            continue
        if int(row.get("round_index") or 0) != args.round_index:
            continue
        validation = row.get("validation")
        if not isinstance(validation, Mapping):
            continue
        label = str(validation.get("label") or "")
        wave_sequence = validation_wave_sequence(validation)
        if (
            label
            and label not in invalidated_before
            and 0 < wave_sequence <= args.maximum_stale_wave_sequence
        ):
            selected.append(
                {
                    "label": label,
                    "wave_sequence": wave_sequence,
                    "time": float(row.get("time") or 0.0),
                }
            )
    labels = sorted({row["label"] for row in selected})
    if not labels:
        print("stale cohort replay repair already applied or no rows selected", flush=True)
        return 0
    if len(labels) > 5:
        raise RuntimeError("repair would exceed one physical Top-5 batch")

    before = {
        "accepted": int(state.get("accepted") or 0),
        "valid_replays": int(state.get("valid_replays") or 0),
        "physical_workload_invocations": int(
            state.get("physical_workload_invocations") or 0
        ),
        "technical_failures": int(state.get("technical_failures") or 0),
        "incumbent_sha256": canonical_sha256(state.get("incumbent_params") or {}),
        "ranges_sha256": canonical_sha256(state.get("ranges") or {}),
        "gradient_state_sha256": canonical_sha256(state.get("gradient_state") or {}),
    }
    if before["valid_replays"] < len(labels):
        raise RuntimeError("valid replay counter is smaller than selected stale rows")

    evidence_root = args.lane_root / "recovery_evidence"
    incident_sha = canonical_sha256(
        {
            "round_index": args.round_index,
            "maximum_stale_wave_sequence": args.maximum_stale_wave_sequence,
            "labels": labels,
        }
    )[:16]
    archive_root = evidence_root / f"stale_candidate_cohort_{incident_sha}"
    archive_root.mkdir(parents=True, exist_ok=True)
    snapshot_path = archive_root / "pre_repair_state.json"
    if not snapshot_path.is_file():
        write_json(snapshot_path, state)

    repaired = copy.deepcopy(state)
    repaired["valid_replays"] = before["valid_replays"] - len(labels)
    repaired["technical_failures"] = before["technical_failures"] + len(labels)
    repaired["out_of_band_invalid_replays"] = int(
        repaired.get("out_of_band_invalid_replays") or 0
    ) + len(labels)
    repaired["invalidated_replay_labels"] = sorted(
        invalidated_before.union(labels)
    )
    memory = repaired.get("valid_replay_identity_memory")
    removed_identity_keys: List[str] = []
    if isinstance(memory, dict) and isinstance(memory.get("identities"), dict):
        for key, entry in list(memory["identities"].items()):
            if not isinstance(entry, dict):
                continue
            retained_labels = [
                str(value)
                for value in entry.get("labels") or []
                if str(value) not in labels
            ]
            if len(retained_labels) == len(entry.get("labels") or []):
                continue
            if retained_labels:
                entry["labels"] = sorted(set(retained_labels))
            else:
                memory["identities"].pop(key, None)
                removed_identity_keys.append(str(key))

    repair = {
        "contract": "algorithm_c_stale_candidate_cohort_repair_v1",
        "passed": True,
        "reason": "restart_reused_fully_completed_historical_cohort_wave",
        "round_index": args.round_index,
        "incident_start_event": args.incident_start_event,
        "incident_start_s": incident_start_s,
        "maximum_stale_wave_sequence": args.maximum_stale_wave_sequence,
        "invalidated_labels": labels,
        "invalidated_rows": selected,
        "removed_identity_keys": sorted(removed_identity_keys),
        "physical_workload_invocations_retained": True,
        "valid_replays_removed": len(labels),
        "technical_failures_added": len(labels),
        "accepted_unchanged": True,
        "incumbent_unchanged": True,
        "ranges_unchanged": True,
        "gradient_state_unchanged": True,
        "pre_repair_state_path": str(snapshot_path),
        "pre_repair_state_sha256": hashlib.sha256(snapshot_path.read_bytes()).hexdigest(),
        "time": time.time(),
    }
    repair["audit_sha256"] = canonical_sha256(repair)
    repaired["stale_candidate_cohort_repair"] = repair
    repaired["updated_at_epoch_s"] = time.time()

    if int(repaired.get("accepted") or 0) != before["accepted"]:
        raise RuntimeError("repair changed accepted")
    if int(repaired.get("physical_workload_invocations") or 0) != before[
        "physical_workload_invocations"
    ]:
        raise RuntimeError("repair changed physical invocation accounting")
    if canonical_sha256(repaired.get("incumbent_params") or {}) != before[
        "incumbent_sha256"
    ]:
        raise RuntimeError("repair changed incumbent")
    if canonical_sha256(repaired.get("ranges") or {}) != before["ranges_sha256"]:
        raise RuntimeError("repair changed ranges")
    if canonical_sha256(repaired.get("gradient_state") or {}) != before[
        "gradient_state_sha256"
    ]:
        raise RuntimeError("repair changed gradient state")

    write_json(state_path, repaired)
    append_jsonl(
        history_path,
        {
            "event": "algorithm_c_stale_candidate_cohort_replays_invalidated",
            "time": time.time(),
            "scene": repaired.get("scene"),
            "target": repaired.get("target"),
            **repair,
            "accepted": repaired.get("accepted"),
            "valid_replays": repaired.get("valid_replays"),
            "physical_workload_invocations": repaired.get(
                "physical_workload_invocations"
            ),
            "technical_failures": repaired.get("technical_failures"),
        },
    )
    print(json.dumps(repair, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
