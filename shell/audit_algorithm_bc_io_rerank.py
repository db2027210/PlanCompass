#!/usr/bin/env python3
"""Offline audit of old versus weak-IO-cost accepted-batch ordering."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.run_pg_algorithm_c_scene import accepted_batch_selection_key


TIER_RANK = {"strict": 3, "secondary": 2, "fallback": 1}
SCENES = ("job_memory", "tpch_memory", "xp_cpu", "xp_io")


def old_key(row: Dict[str, Any]) -> Tuple[Any, ...]:
    decision = row["decision"]
    return (
        TIER_RANK.get(str(decision.get("acceptance_tier") or ""), 0),
        float(decision.get("target_drop_vs_reference") or float("-inf")),
        float(decision.get("qps_gate_gain") or float("-inf")),
    )


def io_up(row: Dict[str, Any]) -> float:
    decision = row["decision"]
    if decision.get("target_resource") == "io":
        return 0.0
    gains = decision.get("other_gain_vs_reference") or {}
    value = gains.get("io")
    return max(0.0, float(value)) if value is not None else 0.0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("campaign_root", type=Path)
    args = parser.parse_args()
    report: Dict[str, Any] = {
        "contract": "algorithm_bc_weak_io_cost_offline_rerank_v1",
        "physical_workload_invocations": 0,
        "experience_table_modified": False,
        "scenes": {},
    }
    for scene in SCENES:
        path = args.campaign_root / scene / "algorithm_c.jsonl"
        batches: Dict[Tuple[int, str], List[Dict[str, Any]]] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if (
                row.get("event") != "algorithm_c_outer_candidate_decision"
                or (row.get("decision") or {}).get("accepted") is not True
            ):
                continue
            batches.setdefault(
                (int(row.get("round_index") or 0), str(row.get("batch_kind") or "")),
                [],
            ).append(row)
        comparisons: List[Dict[str, Any]] = []
        for (round_index, batch_kind), rows in sorted(batches.items()):
            old = max(rows, key=old_key)
            new = max(rows, key=accepted_batch_selection_key)
            comparisons.append(
                {
                    "round_index": round_index,
                    "batch_kind": batch_kind,
                    "winner_changed": old.get("label") != new.get("label"),
                    "old_label": old.get("label"),
                    "new_label": new.get("label"),
                    "old_tier": old["decision"].get("acceptance_tier"),
                    "new_tier": new["decision"].get("acceptance_tier"),
                    "old_positive_io_gain": io_up(old),
                    "new_positive_io_gain": io_up(new),
                }
            )
        report["scenes"][scene] = {
            "accepted_batches": len(comparisons),
            "winner_changes": sum(row["winner_changed"] for row in comparisons),
            "old_positive_io_gain_sum": sum(
                row["old_positive_io_gain"] for row in comparisons
            ),
            "new_positive_io_gain_sum": sum(
                row["new_positive_io_gain"] for row in comparisons
            ),
            "tier_changed": any(
                row["old_tier"] != row["new_tier"] for row in comparisons
            ),
            "comparisons": comparisons,
        }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
