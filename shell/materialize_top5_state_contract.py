#!/usr/bin/env python3
"""Module."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(
            "usage: materialize_top5_state_contract.py SUMMARY OUTPUT"
        )
    summary_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    successful = list(summary.get("successful") or [])
    if len(successful) > 5:
        raise SystemExit("successful replay count exceeds Top-5 contract")
    if int(summary.get("technical_failure_count", 0)) != 0:
        raise SystemExit("cannot commit state while technical failures remain")
    if not summary.get("baseline_params_restored"):
        raise SystemExit("cannot commit state before baseline params restore")
    if not summary.get("baseline_snapshot_restored"):
        raise SystemExit("cannot commit state before baseline snapshot restore")
    baseline_score = float(summary["baseline_actual_score"])
    best = (
        max(successful, key=lambda item: float(item["actual_score"]))
        if successful
        else None
    )
    improved = bool(best and float(best["actual_score"]) > baseline_score)
    state = {
        "contract": "top5_real_replay_incumbent_state_v1",
        "source_summary": str(summary_path),
        "successful_real_replay_count": len(successful),
        "technical_failure_count": 0,
        "baseline_actual_score": baseline_score,
        "incumbent_updated": improved,
        "accepted_count_delta": 1 if improved else 0,
        "selected_predicted_rank": int(best["rank"]) if improved else None,
        "incumbent_actual_score": (
            float(best["actual_score"]) if improved else baseline_score
        ),
        "incumbent_params": (
            best["candidate_params"] if improved else None
        ),
        "range_updated": False,
        "rejected_candidates_update_state": False,
        "baseline_params_restored": True,
        "baseline_snapshot_restored": True,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, output_path)
    print(json.dumps(state, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
