#!/usr/bin/env python3
"""Verify one complete Top-5 round per scene, without waiting or running workloads."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.verify_pg_algorithm_c_budgeted_formal_startup import verify
from shell.prepare_pg_algorithm_c_budgeted_formal import SCENES, write_json


def audit(root: Path) -> dict:
    # This verifier checks the complete first Top-5 search batch below.  Do not
    # also require support-expansion evidence from the generic startup verifier:
    # expansion is conditional on a missed batch and a first-round acceptance
    # correctly has no such event.
    startup = verify(root, require_search_wave=False)
    scenes = {}
    for scene in SCENES:
        log = root / scene / "algorithm_c.jsonl"
        starts, replays, decisions, evidence = [], [], [], []
        if log.is_file():
            with log.open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # Startup verifier reports malformed records.
                    event = row.get("event")
                    if event == "algorithm_c_adaptive_batch_evidence":
                        evidence.append(row)
                    if row.get("round_index") != 1:
                        continue
                    if event == "algorithm_c_physical_batch_start":
                        starts.append(row)
                    elif event == "algorithm_c_candidate_replay":
                        replays.append(row.get("validation") or {})
                    elif event == "algorithm_c_outer_candidate_decision":
                        decisions.append(row)
        labels = [row.get("label") for row in replays]
        workers = sorted({row.get("worker_key", "") for row in replays})
        first = evidence[0] if evidence else {}
        passed = bool(
            len(starts) == 1 and starts[0].get("candidate_count") == 5
            and starts[0].get("wave_shape") == [2, 2, 1]
            and len(replays) == 5 and len(set(labels)) == 5
            and all(row.get("status") == "ok" and row.get("technical_failure") is False for row in replays)
            and workers == ["w1", "w2"] and len(decisions) == 5
            and first.get("valid_replays") == 5 and first.get("completed_wave_count") == 3
            and first.get("completed_valid_batch_count") == 1
        )
        scenes[scene] = {
            "passed": passed, "first_round_valid_replays": len(replays),
            "wave_shape": starts[0].get("wave_shape") if starts else None,
            "decision_count": len(decisions), "workers": workers,
            "accepted": first.get("outer_qps_accepted"),
            "miss_count": first.get("consecutive_rejected_batches"),
            "labels": labels,
        }
    return {
        "contract": "four_scene_complete_first_top5_batch_v1",
        "campaign_root": str(root.resolve()),
        "passed": startup["passed"] and all(row["passed"] for row in scenes.values()),
        "startup": startup, "scenes": scenes,
        "terminal_quality_or_efficiency_claim": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = audit(args.campaign_root)
    if args.output:
        write_json(args.output, report)
    print(json.dumps({"passed": report["passed"], "scenes": report["scenes"]}, ensure_ascii=False), flush=True)
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
