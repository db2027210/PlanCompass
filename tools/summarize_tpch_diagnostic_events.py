#!/usr/bin/env python3
"""Build the strict clean5 TPC-H diagnostic summary from an event ledger."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, Mapping


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--accepted-count", type=int, default=0)
    args = parser.parse_args()

    events = [
        json.loads(line)
        for line in args.events.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    successful = [
        item
        for item in events
        if item.get("event") == "tpch_diagnostic_real_replay_complete"
    ]
    technical = [
        item
        for item in events
        if item.get("technical_failure") is True
        or item.get("event") == "tpch_diagnostic_real_replay_technical_failure"
    ]
    restored = any(
        item.get("event") == "tpch_diagnostic_baseline_params_restored"
        for item in events
    )
    completed = [
        item
        for item in events
        if item.get("event") == "tpch_diagnostic_batch_complete_no_state_update"
    ]
    ranks = {int(item["rank"]) for item in successful}
    if (
        len(successful) != 5
        or ranks != {1, 2, 3, 4, 5}
        or technical
        or not restored
        or len(completed) != 1
    ):
        raise RuntimeError("TPC-H diagnostic event ledger is not strict clean5")
    final = completed[0]
    if any(
        final.get(field) is not False
        for field in (
            "accepted_update_applied",
            "incumbent_update_applied",
            "range_update_applied",
            "gradient_update_applied",
        )
    ):
        raise RuntimeError("TPC-H diagnostic completion contains state mutation")
    summary: Dict[str, Any] = {
        "schema_version": "tpch_diagnostic_event_summary_v1",
        "diagnostic_only": True,
        "accepted_count": args.accepted_count,
        "physical_call_count": 5,
        "valid_replay_count": 5,
        "technical_failure_count": 0,
        "baseline_restored": True,
        "accepted_update_applied": False,
        "incumbent_update_applied": False,
        "range_update_applied": False,
        "gradient_update_applied": False,
        "successful": sorted(successful, key=lambda item: int(item["rank"])),
    }
    atomic_json(args.output, summary)
    print(json.dumps({"output": str(args.output), "clean5": True}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
