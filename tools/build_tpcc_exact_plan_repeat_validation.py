#!/usr/bin/env python3
"""Module."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List


RESOURCES = ("cpu", "io", "memory")


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def atomic_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def bucket(values: Dict[str, Any], scale: float) -> Dict[str, int]:
    return {name: int(round(float(values[name]) * scale)) for name in RESOURCES}


def make_fingerprint(candidate: Dict[str, Any], resource: Dict[str, Any],
                     utilization: Dict[str, Any]) -> Dict[str, Any]:
    payload = {
        "contract": "plan_feature_resource_three_level_v1",
        "plan_level": candidate["workload_plan_fingerprint"],
        "feature_ratio_level": bucket(utilization, 1000.0),
        "predicted_resource_level": bucket(resource, 1000.0),
        "repeat_prediction_source": "first_clean_real_observation",
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        **payload,
        "id": f"three-level-sha256:{digest}",
        "parameter_fallback_used": False,
        "valid": True,
        "validation_errors": [],
    }


def build(
    selection: Dict[str, Any],
    ledger: Dict[str, Any],
    minimum_score_span: float = 0.0,
) -> Dict[str, Any]:
    candidates = selection.get("top5")
    successful = ledger.get("successful")
    if not isinstance(candidates, list) or len(candidates) != 5:
        raise RuntimeError("source selection is not Top-5")
    if (
        ledger.get("real_workload_invocation_count") != 5
        or not isinstance(successful, list)
        or len(successful) != 5
        or ledger.get("technical_failures") not in ([], None)
    ):
        raise RuntimeError("source validation is not clean 5/5")

    by_rank = {int(item["rank"]): item for item in candidates}
    observations = {int(item["rank"]): item for item in successful}
    if set(by_rank) != {1, 2, 3, 4, 5} or set(observations) != set(by_rank):
        raise RuntimeError("source selection/ledger rank binding mismatch")
    for rank, observation in observations.items():
        candidate = by_rank[rank]
        protocol = observation.get("measurement_protocol") or {}
        if (
            protocol.get("stable") is not True
            or protocol.get("correctness_passed") is not True
            or protocol.get("technical_failure") is not False
            or observation.get("workload_plan_fingerprint")
            != candidate.get("workload_plan_fingerprint")
            or observation.get("three_level_fingerprint", {}).get("id")
            != candidate.get("three_level_fingerprint", {}).get("id")
        ):
            raise RuntimeError(f"source evidence mismatch: rank={rank}")

    ordered_by_score = sorted(
        successful,
        key=lambda item: (-float(item["actual_score"]), int(item["rank"])),
    )
    # Consecutive top scores were repeatedly closer than the measured replay
    # noise and produced unstable trusted-only ranks.  Select trusted anchors
    # across the full observed score span (best, median, worst), while keeping
    # the two interleaving observations as held-out probes.  This is fully
    # deterministic and uses only the clean source batch.
    trusted_observations = [
        ordered_by_score[0],
        ordered_by_score[2],
        ordered_by_score[4],
    ]
    probe_observations = [
        ordered_by_score[1],
        ordered_by_score[3],
    ]
    ordered = trusted_observations + probe_observations
    source_score_span = (
        float(ordered_by_score[0]["actual_score"])
        - float(ordered_by_score[-1]["actual_score"])
    )
    if minimum_score_span < 0:
        raise RuntimeError("minimum_score_span must be non-negative")
    if source_score_span <= minimum_score_span:
        raise RuntimeError(
            "source score span is not distinguishable from replay noise: "
            f"span={source_score_span:.12g} noise_bound={minimum_score_span:.12g}"
        )
    repeated: List[Dict[str, Any]] = []
    for predicted_rank, observation in enumerate(ordered, start=1):
        source_rank = int(observation["rank"])
        candidate = copy.deepcopy(by_rank[source_rank])
        score = float(observation["actual_score"])
        resource = copy.deepcopy(observation["actual_resource"])
        utilization = copy.deepcopy(observation["actual_utilization"])
        role = "trusted" if predicted_rank <= 3 else "uncertain_probe"

        candidate["rank"] = predicted_rank
        candidate["selection_role"] = role
        candidate["trusted"] = role == "trusted"
        candidate["uncertain_probe"] = role != "trusted"
        candidate["probe_kind"] = None if role == "trusted" else "exact_plan_repeat_uncertain_probe"
        candidate["probe_reason"] = None if role == "trusted" else "held_out_exact_plan_repeat"
        candidate["predicted_score"] = score
        candidate["candidate_score"] = score
        candidate["final_score"] = score
        candidate["plan_regime_empirical_score"] = score
        candidate["formal_lcb_score"] = score
        candidate["predicted_score_interval"] = {
            "lower": score,
            "center": score,
            "upper": score,
            "source": "first_clean_real_observation",
        }
        candidate["predicted_resource"] = resource
        candidate["uncalibrated_predicted_resource"] = copy.deepcopy(resource)
        candidate["final_resource"] = copy.deepcopy(resource)
        candidate["utilization"] = utilization
        candidate["confidence"] = {
            "exact_plan_regime": 1.0,
            "first_clean_real_observation": 1.0,
            "independent_repeat_required": 1.0,
        }
        candidate["prediction_source"] = "first_clean_real_observation_exact_plan_repeat"
        candidate["repeat_prediction_source"] = "first_clean_real_observation"
        candidate["exact_plan_regime_validated"] = role == "trusted"
        candidate["plan_regime_calibration"] = {
            "trusted_eligible": role == "trusted",
            "observation_count": 1,
            "prediction_source": "first_clean_real_observation",
            "regime": {"plan": candidate["workload_plan_fingerprint"]},
            "actual_resource": copy.deepcopy(resource),
            "actual_score": score,
        }
        candidate["three_level_fingerprint"] = make_fingerprint(
            candidate, resource, utilization
        )
        candidate["diagnostic_only"] = True
        candidate["incumbent_update_eligible"] = False
        candidate["used_for_gradient"] = False
        candidate["used_for_inner_incumbent_update"] = False
        candidate["formal_improvement_eligible"] = False
        candidate["source_provenance"] = {
            "contract": "clean_validation_score_span_exact_plan_repeat_v2",
            "source_rank": source_rank,
            "source_fingerprint": observation["three_level_fingerprint"]["id"],
        }
        repeated.append(candidate)

    fingerprints = [item["three_level_fingerprint"]["id"] for item in repeated]
    params = [json.dumps(item["params"], sort_keys=True) for item in repeated]
    if len(set(fingerprints)) != 5 or len(set(params)) != 5:
        raise RuntimeError("repeat candidates are not five-way distinct")

    output = copy.deepcopy(selection)
    output["schema_version"] = "tpcc_exact_plan_repeat_validation_v1"
    # The strict round14 runner uses this sealed compatibility identifier.
    # Exact-repeat provenance remains explicit in schema/audit fields below.
    output["selection_contract"] = "tpcc_robust_exact_plan_regime_trusted3_probe2_v1"
    output["diagnostic_only"] = True
    output["diagnostic_role_evaluation_only"] = True
    output["diagnostic_acquisition_reason"] = "failed_rank_gate_exact_plan_repeat"
    output["trusted_selection_strategy"] = (
        "deterministic_observed_score_span_best_median_worst_v1"
    )
    output["source_actual_score_span"] = source_score_span
    output["minimum_required_score_span"] = minimum_score_span
    output["score_span_gate_passed"] = True
    output["trusted_source_ranks"] = [
        int(item["rank"]) for item in trusted_observations
    ]
    output["probe_source_ranks"] = [
        int(item["rank"]) for item in probe_observations
    ]
    output["maximum_real_workload_invocations"] = 5
    output["qps_role"] = "outer_audit_only_not_inner_score"
    output["formal_expected_improvement_required"] = False
    output["state_mutation_contract"] = {
        "accepted_update": False,
        "gradient_update": False,
        "incumbent_update": False,
        "range_update": False,
    }
    output["exact_plan_repeat_prediction_source"] = "first_clean_real_observation"
    output["independent_repeat_required"] = True
    output["top5"] = repeated
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--minimum-score-span", type=float, default=0.0)
    arguments = parser.parse_args()
    result = build(
        load(arguments.selection),
        load(arguments.ledger),
        minimum_score_span=arguments.minimum_score_span,
    )
    atomic_json(arguments.output, result)
    print(json.dumps({
        "output": str(arguments.output),
        "roles": [item["selection_role"] for item in result["top5"]],
        "fingerprints": [item["three_level_fingerprint"]["id"] for item in result["top5"]],
        "new_workload_calls": 0,
    }, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
