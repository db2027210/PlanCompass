#!/usr/bin/env python3
"""Replay historical B/C evidence through the same canonical feasible archive policy."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from algorithm_c_budget_policy import admit_feasible_evidence, best_feasible
from shell import run_pg_algorithm_c_scene as base


def audit():
    data = json.loads((ROOT / "figures/editable_search_time_figures_20260830/formal_results_data.json").read_text())
    report = {"contract": "historical_archive_redecision_diagnostic_v1", "new_physical_replays": 0,
              "historical_results_overwritten": False, "scenes": {}}
    for scene, details in data["postgresql"]["scenes"].items():
        results = {}
        for algorithm in ("Algorithm B", "Algorithm C"):
            source = details["algorithms"][algorithm]
            state_path = Path(source["state_path"])
            state = json.loads(state_path.read_text())
            cfg = json.loads((state_path.parent / "optimizer_config.json").read_text())
            capacity = base.capacity_from_config(cfg)
            canonical = state["canonical_baseline"]
            archive, valid_count = {}, 0
            with Path(source["log_path"]).open() as stream:
                for line in stream:
                    event = json.loads(line)
                    if event.get("event") != "algorithm_c_candidate_replay":
                        continue
                    row = event["validation"]
                    if not base.physical_valid(row):
                        continue
                    valid_count += 1
                    normalized = base.normalize_to_worker_baseline(row, state["baseline_by_worker"][row["worker_key"]], canonical, capacity)
                    normalized["qps_noise_model"] = state.get("qps_noise_model", {})
                    decision = base.outer_semantic_acceptance_details(normalized, canonical, canonical,
                        target=details["target_resource"], cfg=cfg, consecutive_missed_outer_rounds=0)
                    admit_feasible_evidence(archive, params=row["candidate_ranking"]["optimized_params"],
                        decision=decision, source={"label": row["label"], "worker_key": row["worker_key"],
                                                   "round": event["round_index"]})
            results[algorithm] = {"prior_selected_champion_qps": source["champion_qps"],
                                  "valid_candidate_records": valid_count, "feasible_parameter_vectors": len(archive),
                                  "observed_finalist": best_feasible(archive), "independent_revalidation_required": True}
        report["scenes"][scene] = results
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit()
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({scene: {a: {"prior": x["prior_selected_champion_qps"],
                                 "archive": (x["observed_finalist"] or {}).get("observed_qps")}
                             for a, x in rows.items()} for scene, rows in report["scenes"].items()}, indent=2), flush=True)
