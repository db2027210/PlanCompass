#!/usr/bin/env python3
"""Prepare a fresh accepted=0 JOB formal campaign from a sealed warm start."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Mapping


def read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"{path} must contain one JSON object")
    return value


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_text(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    atomic_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-state", type=Path, required=True)
    parser.add_argument("--source-state-sha256", required=True)
    parser.add_argument("--source-gate", type=Path, required=True)
    parser.add_argument("--source-gate-sha256", required=True)
    parser.add_argument("--failed-summary", type=Path, required=True)
    parser.add_argument("--failed-summary-sha256", required=True)
    parser.add_argument("--global-ordinal", type=int, required=True)
    parser.add_argument("--formal-replay-count", type=int, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()

    for path, expected, label in (
        (args.source_state, args.source_state_sha256, "source state"),
        (args.source_gate, args.source_gate_sha256, "source gate"),
        (args.failed_summary, args.failed_summary_sha256, "failed summary"),
    ):
        if not path.is_file() or sha256_file(path) != expected:
            raise RuntimeError(f"{label} SHA mismatch")
    if args.output_root.exists():
        raise RuntimeError("fresh campaign output root already exists")

    source_state = read_json(args.source_state)
    source_gate = read_json(args.source_gate)
    failed_summary = read_json(args.failed_summary)
    checks = source_gate.get("checks")
    if (
        source_gate.get("passed") is not True
        or not isinstance(checks, dict)
        or not checks
        or any(value is not True for value in checks.values())
    ):
        raise RuntimeError("source JOB entry gate is not fully passed")
    if int(source_state.get("accepted_count", -1)) != 2:
        raise RuntimeError("unexpected source accepted count")
    if (
        failed_summary.get("technical_failure_count") != 1
        or failed_summary.get("physical_invocation_count") != 1
        or failed_summary.get("successful_real_replay_count") != 0
        or failed_summary.get("baseline_params_restored") is not True
    ):
        raise RuntimeError("failed-batch audit does not match the observed failure")

    package = args.output_root / "package"
    package.mkdir(parents=True)
    state_path = package / "formal_state.json"
    accepted_counter_path = package / "formal_search_accepted_counter.txt"
    gate_path = package / "entry_gate.json"
    manifest_path = package / "fresh_campaign_manifest.json"

    fresh_state = dict(source_state)
    fresh_state["accepted_count"] = 0
    fresh_state["formal_valid_replay_count"] = 0
    fresh_state["global_ordinal"] = args.global_ordinal
    fresh_state["formal_replay_count"] = args.formal_replay_count
    fresh_state["snapshot_only"] = False
    fresh_state.pop("last_formal_batch", None)
    fresh_state["fresh_formal_campaign"] = {
        "campaign_id": "job_final_algorithm_fresh10_v33",
        "accepted_target": 10,
        "accepted_start": 0,
        "warm_start_model_state": True,
        "source_accepted_excluded_from_new_total": 2,
        "source_state_sha256": args.source_state_sha256,
        "global_and_formal_replay_counters_preserved": True,
        "qps_used_for_inner_scoring": False,
    }
    atomic_json(state_path, fresh_state)
    atomic_text(accepted_counter_path, "0\n")
    state_sha = sha256_file(state_path)

    fresh_gate = dict(source_gate)
    fresh_gate["formal_state_sha256_before"] = state_sha
    fresh_gate["formal_state_sha256_after"] = state_sha
    fresh_gate["formal_counter_pins_before_start"] = {
        "global_ordinal": args.global_ordinal,
        "formal_search_replay_count": args.formal_replay_count,
        "formal_search_accepted_count": 0,
    }
    fresh_gate["formal_accepted_total"] = 0
    fresh_gate["accepted_target"] = 10
    fresh_gate["fresh_formal_campaign"] = True
    fresh_gate["source_entry_gate_sha256"] = args.source_gate_sha256
    fresh_gate["source_accepted_excluded_from_new_total"] = 2
    fresh_gate["technical_failure_audit_sha256"] = args.failed_summary_sha256
    fresh_gate["passed"] = all(value is True for value in checks.values())
    atomic_json(gate_path, fresh_gate)

    manifest = {
        "contract": "job_final_algorithm_fresh10_v33",
        "accepted_start": 0,
        "accepted_target": 10,
        "source_accepted_excluded_from_new_total": 2,
        "source_state_sha256": args.source_state_sha256,
        "source_entry_gate_sha256": args.source_gate_sha256,
        "technical_failure_audit_sha256": args.failed_summary_sha256,
        "formal_state_sha256": state_sha,
        "entry_gate_sha256": sha256_file(gate_path),
        "accepted_counter_sha256": sha256_file(accepted_counter_path),
        "global_ordinal_start": args.global_ordinal,
        "formal_replay_count_start": args.formal_replay_count,
        "global_and_formal_replay_counters_preserved": True,
        "warm_start_model_state": True,
        "new_formal_results_must_be_real_replays": True,
        "qps_used_for_inner_scoring": False,
    }
    atomic_json(manifest_path, manifest)
    print(json.dumps(manifest, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
