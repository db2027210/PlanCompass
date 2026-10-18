"""Diagnose final and per-outer-candidate gates for the dedicated 18-run matrix."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from experiment_matrix_mysql import evaluate_acceptance, expand_runs, load_manifest


def candidate_document(document: dict, index: int) -> dict:
    candidate = copy.deepcopy(document)
    candidate["outer_loop"] = [candidate["outer_loop"][0], candidate["outer_loop"][index]]
    row = candidate["outer_loop"][-1]
    candidate["result"]["qps_after"] = row["qps"]
    candidate["result"]["p99_after_ms"] = row["latency_p99_ms"]
    candidate["result"]["final_params"] = row["params"]
    return candidate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("config/mysql_experiment_manifest_A25_E25_B7.json"))
    parser.add_argument("--root", type=Path, default=Path("/disk2/resource_adaptive_mysql/gd_20260718/formal_dynamic"))
    args = parser.parse_args()
    manifest = load_manifest(args.manifest)
    rows = []
    missing = []
    for run in expand_runs(manifest):
        path = args.root / f"{run.run_id}.json"
        if not path.is_file():
            missing.append(run.run_id)
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        final = evaluate_acceptance(document, manifest["acceptance"])
        candidates = []
        for index in range(1, len(document["outer_loop"])):
            evaluation = evaluate_acceptance(candidate_document(document, index), manifest["acceptance"])
            row = document["outer_loop"][index]
            candidates.append({
                "outer_index": index, "label": row.get("label"), "qps": row["qps"],
                "params": row["params"], "evaluation": evaluation,
            })
        passing = [item for item in candidates if item["evaluation"]["passed"]]
        candidates.sort(
            key=lambda item: (
                item["evaluation"]["passed"],
                item["evaluation"].get("qps_gain_pct", float("-inf")),
            ),
            reverse=True,
        )
        rows.append({
            "run_id": run.run_id, "benchmark": run.benchmark, "bottleneck": run.bottleneck,
            "final": final, "passing_outer_candidates": passing,
            "top_outer_candidates": candidates[:3],
        })
    print(json.dumps({
        "total_expected": len(rows) + len(missing), "total_analyzed": len(rows), "missing": missing,
        "final_passed": sum(row["final"]["passed"] for row in rows),
        "runs_with_passing_outer_candidate": sum(bool(row["passing_outer_candidates"]) for row in rows),
        "rows": rows,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
