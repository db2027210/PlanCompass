#!/usr/bin/env python3
"""Build TPC-C exact repeats ranked by accumulated exact-point medians."""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from resource_utility import ResourceUtilityCalculator


BASE_PATH = Path(__file__).with_name("build_tpcc_exact_plan_repeat_validation.py")
RESOURCES = ("cpu", "io", "memory")
CAPABILITY = {"cpu": 3.8, "memory": 6.0511731087, "io": 54.0}
SCORE_CALCULATOR = ResourceUtilityCalculator(
    score_mode="band_target",
    resource_weights={"cpu": 1.0, "memory": 1.0, "io": 3.0},
    resource_target_ranges={
        "cpu": {
            "target_min_pct": 85.0,
            "target_max_pct": 90.0,
            "hard_overload_pct": 95.0,
            "role": "target_bottleneck",
        },
        "memory": {
            "target_min_pct": 30.0,
            "target_max_pct": 50.0,
            "hard_overload_pct": 90.0,
            "role": "non_bottleneck",
        },
        "io": {
            "target_min_pct": 30.0,
            "target_max_pct": 50.0,
            "hard_overload_pct": 90.0,
            "role": "non_bottleneck",
        },
    },
)


def import_base() -> Any:
    specification = importlib.util.spec_from_file_location(
        "tpcc_exact_repeat_v1", BASE_PATH
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("cannot import sealed TPC-C exact-repeat builder")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def numeric_list(value: Any, label: str) -> List[float]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise RuntimeError(f"{label} must be a list")
    result = [float(item) for item in value]
    if any(not (-float("inf") < item < float("inf")) for item in result):
        raise RuntimeError(f"{label} contains a non-finite value")
    return result


def resource_history(value: Any, label: str) -> List[Dict[str, float]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise RuntimeError(f"{label} must be a list")
    result: List[Dict[str, float]] = []
    for row in value:
        if not isinstance(row, dict) or set(RESOURCES) - set(row):
            raise RuntimeError(f"{label} contains an invalid resource row")
        result.append({name: float(row[name]) for name in RESOURCES})
    return result


def same_params(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return json.dumps(left, sort_keys=True, separators=(",", ":")) == json.dumps(
        right, sort_keys=True, separators=(",", ":")
    )


def build(
    selection: Dict[str, Any],
    ledger: Dict[str, Any],
    minimum_score_span: float,
) -> Dict[str, Any]:
    base = import_base()
    # Run all v1 evidence, rank, plan, fingerprint, and clean5 checks first.
    provisional = base.build(selection, ledger, minimum_score_span=0.0)
    source_candidates = {
        int(item["rank"]): item for item in selection.get("top5") or []
    }
    observations = {
        int(item["rank"]): item for item in ledger.get("successful") or []
    }

    accumulated: List[Dict[str, Any]] = []
    for item in provisional["top5"]:
        source_rank = int(item["source_provenance"]["source_rank"])
        source = source_candidates[source_rank]
        observation = observations[source_rank]
        if not same_params(source["params"], item["params"]):
            raise RuntimeError("exact-repeat candidate parameters changed")
        scores = numeric_list(
            source.get("independent_actual_scores"),
            f"source rank {source_rank} scores",
        )
        resources = resource_history(
            source.get("independent_actual_resources"),
            f"source rank {source_rank} resources",
        )
        if len(scores) != len(resources):
            raise RuntimeError("exact-repeat score/resource history length mismatch")
        # v1 exact-repeat selections predate explicit observation arrays.  In
        # that schema, and only that schema, predicted_score/resource are a
        # verbatim copy of the first clean real observation.  Preserve it at
        # the v1->v2 handover; never treat a normal model prediction as real.
        first_observation_contract = bool(
            selection.get("schema_version")
            == "tpcc_exact_plan_repeat_validation_v1"
            and selection.get("exact_plan_repeat_prediction_source")
            == "first_clean_real_observation"
            and source.get("prediction_source")
            == "first_clean_real_observation_exact_plan_repeat"
            and (source.get("plan_regime_calibration") or {}).get(
                "observation_count"
            )
            == 1
        )
        if not scores and not resources and first_observation_contract:
            scores.append(float(source["predicted_score"]))
            resources.append(
                {
                    name: float(source["predicted_resource"][name])
                    for name in RESOURCES
                }
            )
        scores.append(float(observation["actual_score"]))
        resources.append(
            {
                name: float(observation["actual_resource"][name])
                for name in RESOURCES
            }
        )
        robust_acceptance_score = float(statistics.median(scores))
        robust_resource = {
            name: float(statistics.median(row[name] for row in resources))
            for name in RESOURCES
        }
        capability = {
            name: float(observation["actual_resource"][name])
            / float(observation["actual_utilization"][name])
            for name in RESOURCES
        }
        robust_utilization = {
            name: robust_resource[name] / capability[name] for name in RESOURCES
        }
        if any(
            not abs(capability[name] - CAPABILITY[name])
            <= max(1e-9, abs(CAPABILITY[name]) * 1e-9)
            for name in RESOURCES
        ):
            raise RuntimeError("exact-repeat observation capability changed")
        resource_consistent_score = float(
            SCORE_CALCULATOR.calculate_utility_score(robust_resource, CAPABILITY)
        )
        lower = min(scores)
        upper = max(scores)
        item.update(
            {
                # The utility is nonlinear, so utility(median(resource)) is
                # generally not median(utility(resource)).  Keep the former
                # in the candidate contract enforced by the replay runner,
                # and the latter solely as the robust acceptance statistic.
                "candidate_score": resource_consistent_score,
                "predicted_score": resource_consistent_score,
                "final_score": resource_consistent_score,
                "plan_regime_empirical_score": robust_acceptance_score,
                "robust_acceptance_score": robust_acceptance_score,
                "robust_acceptance_statistic": (
                    "median_of_independent_exact_point_actual_scores"
                ),
                "formal_lcb_score": lower,
                "predicted_score_interval": {
                    "lower": lower,
                    "center": robust_acceptance_score,
                    "upper": upper,
                    "source": "independent_exact_point_empirical_v2",
                },
                "predicted_resource": robust_resource,
                "uncalibrated_predicted_resource": copy.deepcopy(robust_resource),
                "final_resource": copy.deepcopy(robust_resource),
                "utilization": robust_utilization,
                "independent_actual_scores": scores,
                "independent_actual_resources": resources,
                "confidence": {
                    "exact_plan_regime": 1.0,
                    "measurement_count": len(scores),
                    "score_lower": lower,
                    "score_median": robust_acceptance_score,
                    "score_upper": upper,
                    "actual_score_span": upper - lower,
                    "resource_consistent_candidate_score": resource_consistent_score,
                    "independent_repeat_complete": float(len(scores) >= 2),
                },
                "prediction_source": "independent_exact_point_median_v2",
                "repeat_prediction_source": "independent_exact_point_median_v2",
            }
        )
        item["three_level_fingerprint"] = base.make_fingerprint(
            item, robust_resource, robust_utilization
        )
        item["three_level_fingerprint"]["repeat_prediction_source"] = (
            "independent_exact_point_median_v2"
        )
        accumulated.append(item)

    ordered = sorted(
        accumulated,
        key=lambda item: (
            -float(item["robust_acceptance_score"]),
            int(item["rank"]),
        ),
    )
    trusted = [ordered[0], ordered[2], ordered[4]]
    probes = [ordered[1], ordered[3]]
    selected = trusted + probes
    for rank, item in enumerate(selected, start=1):
        role = "trusted" if rank <= 3 else "uncertain_probe"
        calibration = item.get("plan_regime_calibration")
        if not isinstance(calibration, dict):
            raise RuntimeError("exact-repeat candidate lacks plan-regime calibration")
        regime = calibration.get("regime")
        if not isinstance(regime, dict):
            raise RuntimeError("exact-repeat candidate lacks plan-regime identity")
        if regime.get("plan") != item.get("workload_plan_fingerprint"):
            raise RuntimeError("exact-repeat plan-regime identity changed")
        item["rank"] = rank
        item["selection_role"] = role
        item["trusted"] = role == "trusted"
        item["uncertain_probe"] = role != "trusted"
        item["exact_plan_regime_validated"] = role == "trusted"
        # The v1 builder copied the source role's eligibility.  Once exact
        # points are re-ranked by their accumulated observations, a former
        # probe may legitimately become one of the three trusted points (and
        # vice versa).  Seal calibration eligibility from the accumulated
        # evidence instead of retaining the stale source role.  This changes
        # diagnostic metadata only; formal admission still requires a third
        # independent observation in the controller and formal runner.
        calibration.update(
            {
                "actual_resource": copy.deepcopy(item["predicted_resource"]),
                "actual_score": float(item["robust_acceptance_score"]),
                "resource_consistent_score": float(item["candidate_score"]),
                "observation_count": int(item["confidence"]["measurement_count"]),
                "prediction_source": "independent_exact_point_median_v2",
                "trusted_eligible": role == "trusted",
            }
        )
        item["probe_kind"] = (
            None if role == "trusted" else "exact_plan_repeat_uncertain_probe"
        )
        item["probe_reason"] = (
            None if role == "trusted" else "held_out_exact_plan_repeat"
        )

    score_span = max(float(item["robust_acceptance_score"]) for item in trusted) - min(
        float(item["robust_acceptance_score"]) for item in trusted
    )
    repeated_spans = [
        float(item["confidence"]["actual_score_span"])
        for item in accumulated
        if int(item["confidence"]["measurement_count"]) >= 2
    ]
    noise_floor = (
        float(statistics.median(repeated_spans)) if repeated_spans else 0.0
    )
    effective_floor = max(float(minimum_score_span), noise_floor)

    output = copy.deepcopy(provisional)
    output["schema_version"] = "tpcc_exact_plan_repeat_validation_v2"
    output["trusted_selection_strategy"] = (
        "accumulated_exact_point_median_best_median_worst_v2"
    )
    output["exact_plan_repeat_prediction_source"] = (
        "independent_exact_point_median_v2"
    )
    output["source_actual_score_span"] = score_span
    output["observed_exact_repeat_score_noise_floor"] = noise_floor
    output["minimum_required_score_span"] = effective_floor
    output["score_span_gate_passed"] = score_span > effective_floor
    output["trusted_source_ranks"] = [
        int(item["source_provenance"]["source_rank"]) for item in trusted
    ]
    output["probe_source_ranks"] = [
        int(item["source_provenance"]["source_rank"]) for item in probes
    ]
    output["top5"] = selected
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--minimum-score-span", type=float, default=0.0)
    args = parser.parse_args()
    base = import_base()
    result = build(
        base.load(args.selection),
        base.load(args.ledger),
        args.minimum_score_span,
    )
    base.atomic_json(args.output, result)
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
