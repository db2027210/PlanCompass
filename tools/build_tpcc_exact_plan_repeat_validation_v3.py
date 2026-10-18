#!/usr/bin/env python3
"""Build TPC-C exact repeats with score-consistent robust resources."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any, Dict

try:
    import build_tpcc_exact_plan_repeat_validation_v2 as v2
    from tpcc_score_consistent_representative import (
        score_consistent_representative,
    )
except ModuleNotFoundError:
    from tools import build_tpcc_exact_plan_repeat_validation_v2 as v2
    from tools.tpcc_score_consistent_representative import (
        score_consistent_representative,
    )


def _score(resource: Dict[str, Any]) -> float:
    return float(
        v2.SCORE_CALCULATOR.calculate_utility_score(resource, v2.CAPABILITY)
    )


def build(
    selection: Dict[str, Any],
    ledger: Dict[str, Any],
    minimum_score_span: float,
) -> Dict[str, Any]:
    output = v2.build(selection, ledger, minimum_score_span)
    base = v2.import_base()
    for item in output["top5"]:
        old_component_score = float(item["candidate_score"])
        resource, robust_score, audit = score_consistent_representative(
            item["independent_actual_scores"],
            item["independent_actual_resources"],
            _score,
        )
        representative_score = _score(resource)
        utilization = {
            name: resource[name] / v2.CAPABILITY[name]
            for name in v2.RESOURCES
        }
        item.update(
            {
                "candidate_score": representative_score,
                "candidate_score_recomputed": representative_score,
                "predicted_score": representative_score,
                "final_score": representative_score,
                "plan_regime_empirical_score": robust_score,
                "robust_acceptance_score": robust_score,
                "predicted_resource": resource,
                "uncalibrated_predicted_resource": copy.deepcopy(resource),
                "final_resource": copy.deepcopy(resource),
                "utilization": utilization,
                "prediction_source": (
                    "score_consistent_exact_point_median_v3"
                ),
                "repeat_prediction_source": (
                    "score_consistent_exact_point_median_v3"
                ),
                "score_consistent_representative_audit": audit,
            }
        )
        item["predicted_score_interval"]["center"] = representative_score
        item["predicted_score_interval"]["source"] = (
            "score_consistent_exact_point_empirical_v3"
        )
        item["confidence"].update(
            {
                "score_median": robust_score,
                "prediction_score": representative_score,
                "resource_consistent_candidate_score": representative_score,
                "componentwise_resource_median_rescored_score": (
                    old_component_score
                ),
            }
        )
        calibration = item["plan_regime_calibration"]
        calibration.update(
            {
                "actual_resource": copy.deepcopy(resource),
                "actual_score": robust_score,
                "resource_consistent_score": representative_score,
                "componentwise_resource_median_rescored_score": (
                    old_component_score
                ),
                "prediction_source": (
                    "score_consistent_exact_point_median_v3"
                ),
            }
        )
        old_fingerprint = item.get("three_level_fingerprint") or {}
        item["three_level_fingerprint"] = base.make_fingerprint(
            item,
            resource,
            utilization,
        )
        item["three_level_fingerprint"].update(
            {
                "source_fingerprint_id": old_fingerprint.get("id"),
                "repeat_prediction_source": (
                    "score_consistent_exact_point_median_v3"
                ),
            }
        )

    output["schema_version"] = "tpcc_exact_plan_repeat_validation_v3"
    output["trusted_selection_strategy"] = (
        "score_consistent_exact_point_median_best_median_worst_v3"
    )
    output["exact_plan_repeat_prediction_source"] = (
        "score_consistent_exact_point_median_v3"
    )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--minimum-score-span", type=float, default=0.0)
    args = parser.parse_args()
    result = build(
        v2.import_base().load(args.selection),
        v2.import_base().load(args.ledger),
        args.minimum_score_span,
    )
    v2.import_base().atomic_json(args.output, result)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "roles": [item["selection_role"] for item in result["top5"]],
                "measurement_counts": [
                    item["confidence"]["measurement_count"]
                    for item in result["top5"]
                ],
                "new_workload_calls": 0,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
