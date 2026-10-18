#!/usr/bin/env python3
"""Zero-call audit of TPC-C v27 accumulated-observation promotion."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path

try:
    import tpcc_autonomous_target10_controller_v27_score_consistent as v27
except ModuleNotFoundError:
    from tools import tpcc_autonomous_target10_controller_v27_score_consistent as v27


def load(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--incumbent-score", type=float, required=True)
    args = parser.parse_args()
    promoted = v27.accumulated_observation_formal_selection_v27(
        load(args.selection),
        load(args.ledger),
        {"passed": True, "preflight_only": True},
        args.selection.resolve(),
        args.incumbent_score,
    )
    v27.v25.validate_selection(promoted, mode="formal")
    rows = []
    for candidate in promoted["top5"]:
        empirical = statistics.median(candidate["independent_actual_scores"])
        predicted = v27.v25.tpcc_score_resource(candidate["predicted_resource"])
        if not math.isclose(
            float(candidate["candidate_score"]),
            predicted,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise RuntimeError("candidate score/resource mismatch")
        if not math.isclose(empirical, predicted, rel_tol=0.0, abs_tol=1e-12):
            raise RuntimeError("candidate score/empirical median mismatch")
        rows.append(
            {
                "rank": candidate["rank"],
                "role": candidate["selection_role"],
                "candidate_score": candidate["candidate_score"],
                "empirical_score_median": empirical,
                "componentwise_rescored_score": candidate["confidence"][
                    "componentwise_resource_median_rescored_score"
                ],
                "method": candidate[
                    "score_consistent_representative_audit"
                ]["method"],
            }
        )
    print(
        json.dumps(
            {
                "rows": rows,
                "roles": [row["role"] for row in rows],
                "formal_feasibility": promoted[
                    "formal_acceptance_feasibility_gate"
                ],
                "physical_workload_calls_added": 0,
                "manual_candidate_injection": False,
            },
            indent=2,
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
