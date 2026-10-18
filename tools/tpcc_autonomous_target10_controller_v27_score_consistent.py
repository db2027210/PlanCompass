#!/usr/bin/env python3
"""TPC-C v26 successor with score-consistent robust formal predictions.

The replay runner requires ``candidate_score`` to equal the utility of
``predicted_resource``.  Component-wise resource medians satisfy neither the
median-score ranking nor this stability objective under a nonlinear utility.
This wrapper replaces them with a deterministic resource representative whose
utility equals the median of the independent exact-point scores.  Diagnostic
isolation, the pre-third bound, SHA pins, and all replay ownership remain in
v26/v25.
"""

from __future__ import annotations

import copy
import statistics
from typing import Any, Dict, Mapping

try:
    import tpcc_autonomous_target10_controller_v26_prethird_bound as v26
    from tpcc_score_consistent_representative import (
        score_consistent_representative,
    )
except ModuleNotFoundError:
    from tools import tpcc_autonomous_target10_controller_v26_prethird_bound as v26
    from tools.tpcc_score_consistent_representative import (
        score_consistent_representative,
    )


v25 = v26.v25
_ORIGINAL_ACCUMULATED_SELECTION = v25.accumulated_observation_formal_selection


def _normalize_candidate(candidate: Dict[str, Any]) -> None:
    scores = [float(value) for value in candidate["independent_actual_scores"]]
    resources = [
        {name: float(row[name]) for name in v25.RESOURCES}
        for row in candidate["independent_actual_resources"]
    ]
    componentwise_median = {
        name: float(statistics.median(row[name] for row in resources))
        for name in v25.RESOURCES
    }
    componentwise_score = v25.tpcc_score_resource(componentwise_median)
    resource, robust_score, audit = score_consistent_representative(
        scores,
        resources,
        v25.tpcc_score_resource,
    )
    representative_score = v25.tpcc_score_resource(resource)
    utilization = {
        name: resource[name] / v25.TPCC_CAPABILITY[name]
        for name in v25.RESOURCES
    }
    resource_interval = {
        name: {
            "lower": min(row[name] for row in resources),
            "median": resource[name],
            "upper": max(row[name] for row in resources),
            "componentwise_median": componentwise_median[name],
        }
        for name in v25.RESOURCES
    }
    candidate.update(
        {
            "predicted_score": representative_score,
            "candidate_score": representative_score,
            "candidate_score_recomputed": representative_score,
            "final_score": representative_score,
            "plan_regime_empirical_score": robust_score,
            "robust_acceptance_score": robust_score,
            "predicted_resource": resource,
            "uncalibrated_predicted_resource": copy.deepcopy(resource),
            "final_resource": copy.deepcopy(resource),
            "predicted_resource_interval": resource_interval,
            "utilization": utilization,
            "prediction_source": "score_consistent_exact_point_median_v4",
            "repeat_prediction_source": (
                "score_consistent_exact_point_median_v4"
            ),
            "score_consistent_representative_audit": audit,
        }
    )
    candidate["predicted_score_interval"].update(
        {
            "center": representative_score,
            "source": "score_consistent_exact_point_empirical_v4",
        }
    )
    candidate["confidence"].update(
        {
            "score_median": robust_score,
            "prediction_score": representative_score,
            "resource_median_rescored_score": representative_score,
            "componentwise_resource_median_rescored_score": (
                componentwise_score
            ),
        }
    )
    calibration = candidate.get("plan_regime_calibration") or {}
    calibration.update(
        {
            "prediction_source": "score_consistent_exact_point_median_v4",
            "actual_resource_median": copy.deepcopy(resource),
            "actual_resource_componentwise_median": componentwise_median,
            "actual_resource_interval": resource_interval,
            "actual_score_median": robust_score,
            "prediction_score_from_resource_median": representative_score,
            "componentwise_resource_median_rescored_score": (
                componentwise_score
            ),
        }
    )
    candidate["plan_regime_calibration"] = calibration

    old_fingerprint = candidate.get("three_level_fingerprint") or {}
    feature_level = copy.deepcopy(old_fingerprint.get("feature_ratio_level") or {})
    if set(feature_level) != set(v25.RESOURCES):
        feature_level = {
            name: int(round(float(candidate["feature_ratio"][name]) * 1000.0))
            for name in v25.RESOURCES
        }
    fingerprint_payload = {
        "contract": "plan_feature_resource_three_level_v1",
        "plan_level": candidate["workload_plan_fingerprint"],
        "feature_ratio_level": feature_level,
        "predicted_resource_level": {
            name: int(round(resource[name] * 1000.0))
            for name in v25.RESOURCES
        },
        "prediction_source": "score_consistent_exact_point_median_v4",
    }
    candidate["three_level_fingerprint"] = {
        **fingerprint_payload,
        "id": "three-level-sha256:" + v25.canonical_sha256(fingerprint_payload),
        "source_fingerprint_id": old_fingerprint.get("id"),
        "parameter_fallback_used": False,
        "valid": True,
        "validation_errors": [],
    }


def accumulated_observation_formal_selection_v27(
    diagnostic_selection: Mapping[str, Any],
    diagnostic_ledger: Mapping[str, Any],
    quality_gate_payload: Mapping[str, Any],
    source_path: Any,
    incumbent_score: float,
) -> Dict[str, Any]:
    promoted = _ORIGINAL_ACCUMULATED_SELECTION(
        diagnostic_selection,
        diagnostic_ledger,
        quality_gate_payload,
        source_path,
        incumbent_score,
    )
    for candidate in promoted["top5"]:
        _normalize_candidate(candidate)

    trusted = sorted(
        (
            item
            for item in promoted["top5"]
            if item["selection_role"] == "trusted"
        ),
        key=lambda item: -float(item["robust_acceptance_score"]),
    )
    probes = sorted(
        (
            item
            for item in promoted["top5"]
            if item["selection_role"] == "uncertain_probe"
        ),
        key=lambda item: -float(item["robust_acceptance_score"]),
    )
    if len(trusted) != 3 or len(probes) != 2:
        raise RuntimeError("score-consistent promotion lost trusted3/probe2")
    promoted["top5"] = trusted + probes
    for rank, candidate in enumerate(promoted["top5"], start=1):
        candidate["rank"] = rank

    feasibility_rows = [
        {
            "rank": int(candidate["rank"]),
            "upper_bound": float(
                candidate[
                    "maximum_robust_acceptance_score_after_one_observation"
                ]
            ),
            "feasible": bool(
                float(
                    candidate[
                        "maximum_robust_acceptance_score_after_one_observation"
                    ]
                )
                > float(incumbent_score) + 1e-12
            ),
        }
        for candidate in promoted["top5"]
    ]
    promoted["formal_acceptance_feasibility_gate"] = {
        "contract": "one_additional_observation_median_upper_bound_v1",
        "incumbent_score": float(incumbent_score),
        "candidate_bounds": feasibility_rows,
        "feasible_ranks": [
            row["rank"] for row in feasibility_rows if row["feasible"]
        ],
        "passed": any(row["feasible"] for row in feasibility_rows),
    }
    promoted["formal_prediction_contract"] = (
        "score_consistent_resource_representative_plus_independent_"
        "actual_score_median_v5"
    )
    promoted["formal_ranking_source"] = (
        "median_of_independent_exact_point_actual_scores"
    )
    return promoted


def main() -> int:
    v25.accumulated_observation_formal_selection = (
        accumulated_observation_formal_selection_v27
    )
    return v26.main()


if __name__ == "__main__":
    raise SystemExit(main())
