#!/usr/bin/env python3
"""Seal a JOB continuation gate from current authoritative counters.

This command performs no workload call and never mutates the formal state.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict


REQUIRED_CANDIDATE_FIELDS = (
    "feature_ratio",
    "predicted_resource",
    "utilization",
    "dScore/dU",
    "T",
    "confidence",
    "g_j",
    "eta",
    "delta_param",
    "candidate_score",
)


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def counter(path: Path) -> int:
    value = path.read_text(encoding="utf-8").strip()
    if not value.isdigit():
        raise RuntimeError(f"invalid counter: {path}")
    return int(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predecessor-gate", required=True, type=Path)
    parser.add_argument("--selection", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--global-counter", required=True, type=Path)
    parser.add_argument("--formal-replay-counter", required=True, type=Path)
    parser.add_argument("--accepted-counter", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    if args.output.exists():
        raise FileExistsError(args.output)
    predecessor = load(args.predecessor_gate)
    selection = load(args.selection)
    state = load(args.state)
    if predecessor.get("passed") is not True or any(
        value is not True
        for value in (predecessor.get("checks") or {}).values()
    ):
        raise RuntimeError("predecessor clean5 gate is not passed")
    candidates = selection.get("top5") or []
    if len(candidates) != 5:
        raise RuntimeError("continuation selection is not Top-5")
    roles = [str(item.get("selection_role") or "") for item in candidates]
    if roles != ["trusted", "trusted", "trusted", "probe", "probe"]:
        raise RuntimeError(f"continuation roles are invalid: {roles}")
    identifiers = []
    for rank, candidate in enumerate(candidates, 1):
        missing = [
            field for field in REQUIRED_CANDIDATE_FIELDS if field not in candidate
        ]
        if missing:
            raise RuntimeError(f"rank {rank} missing audit fields: {missing}")
        identifier = str(
            (candidate.get("three_level_fingerprint") or {}).get("id") or ""
        )
        if not identifier.startswith("three-level-sha256:"):
            raise RuntimeError(f"rank {rank} fingerprint is invalid")
        identifiers.append(identifier)
        score = float(candidate["candidate_score"])
        if not math.isfinite(score):
            raise RuntimeError(f"rank {rank} candidate score is not finite")
    if len(set(identifiers)) != 5:
        raise RuntimeError("continuation fingerprints are not unique")

    pins = {
        "global_ordinal": counter(args.global_counter),
        "formal_search_replay_count": counter(args.formal_replay_counter),
        "formal_search_accepted_count": counter(args.accepted_counter),
    }
    if int(state.get("accepted_count", -1)) != pins[
        "formal_search_accepted_count"
    ]:
        raise RuntimeError("state and accepted counter disagree")
    if int(state.get("global_ordinal", -1)) != pins["global_ordinal"]:
        raise RuntimeError("state and global counter disagree")
    if int(state.get("formal_replay_count", -1)) != pins[
        "formal_search_replay_count"
    ]:
        raise RuntimeError("state and formal replay counter disagree")

    gate = copy.deepcopy(predecessor)
    state_sha = sha256(args.state)
    gate.update(
        {
            "schema_version": "job_clean5_formal_continuation_gate_v1",
            "formal_accepted_total": pins["formal_search_accepted_count"],
            "formal_counter_pins_before_start": pins,
            "formal_state_sha256_before": state_sha,
            "formal_state_sha256_after": state_sha,
            "promoted_selection_sha256": sha256(args.selection),
            "continuation_selection_fingerprints": identifiers,
            "continuation_gate_state_neutral": True,
        }
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(gate, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "gate_sha256": sha256(args.output),
                "state_sha256": state_sha,
                "accepted_count": pins["formal_search_accepted_count"],
                "new_workload_calls": 0,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
