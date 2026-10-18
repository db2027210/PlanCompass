#!/usr/bin/env python3
"""Module."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List


def _load(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _atomic_dump(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    temporary.replace(path)


def _bucket(values: Dict[str, Any], scale: float) -> Dict[str, int]:
    return {name: int(round(float(values[name]) * scale)) for name in ("cpu", "io", "memory")}


def _fingerprint(candidate: Dict[str, Any], actual_resource: Dict[str, Any],
                 actual_utilization: Dict[str, Any]) -> Dict[str, Any]:
    old = candidate["three_level_fingerprint"]
    payload = {
        "contract": "plan_feature_resource_three_level_v1",
        "plan_level": old["plan_level"],
        "feature_ratio_level": _bucket(actual_utilization, 1000.0),
        "predicted_resource_level": _bucket(actual_resource, 1000.0),
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


def build(selection: Dict[str, Any], summary: Dict[str, Any]) -> Dict[str, Any]:
    if summary.get("diagnostic_only") is not True:
        raise ValueError("source summary is not diagnostic-only")
    if summary.get("physical_invocation_count") != 5:
        raise ValueError("source summary is not a five-call batch")
    if summary.get("successful_real_replay_count") != 5 or summary.get("technical_failure_count") != 0:
        raise ValueError("source summary is not clean 5/5")
    if summary.get("baseline_params_restored") is not True or summary.get("initial_state") != summary.get("final_state"):
        raise ValueError("source baseline/formal state was not restored")
    if summary.get("formal_update_flags") != {
        "accepted": False,
        "gradient": False,
        "incumbent": False,
        "range": False,
    }:
        raise ValueError("source diagnostic mutated formal state")

    source_top5 = selection.get("top5")
    successful = summary.get("successful")
    if not isinstance(source_top5, list) or len(source_top5) != 5:
        raise ValueError("source selection does not contain five candidates")
    if not isinstance(successful, list) or len(successful) != 5:
        raise ValueError("source summary does not contain five successful observations")

    by_rank = {int(item["rank"]): item for item in source_top5}
    observations = {int(item["rank"]): item for item in successful}
    if set(by_rank) != {1, 2, 3, 4, 5} or set(observations) != set(by_rank):
        raise ValueError("source rank binding mismatch")

    ordered = sorted(observations.values(), key=lambda item: (-float(item["actual_score"]), int(item["rank"])))
    repeated: List[Dict[str, Any]] = []
    for predicted_rank, observed in enumerate(ordered, start=1):
        source_rank = int(observed["rank"])
        candidate = copy.deepcopy(by_rank[source_rank])
        actual_score = float(observed["actual_score"])
        actual_resource = copy.deepcopy(observed["actual_resource"])
        actual_utilization = copy.deepcopy(observed["actual_utilization"])
        role = "trusted" if predicted_rank <= 3 else "probe"

        candidate["rank"] = predicted_rank
        candidate["predicted_rank"] = predicted_rank
        candidate["selection_role"] = role
        candidate["probe_kind"] = None if role == "trusted" else "exact_plan_repeat_uncertain_probe"
        candidate["selection_reason"] = "deterministic_exact_plan_repeat_from_first_clean_observation"
        candidate["candidate_score"] = actual_score
        candidate["calibrated_candidate_score"] = actual_score
        candidate["selection_score"] = actual_score
        candidate["final_score"] = actual_score
        candidate["predicted_resource"] = actual_resource
        candidate["final_resource"] = copy.deepcopy(actual_resource)
        candidate["utilization"] = actual_utilization
        candidate["confidence"] = {
            "exact_plan_regime": 1.0,
            "first_clean_real_observation": 1.0,
            "independent_repeat_required": 1.0,
        }
        candidate["repeat_prediction_source"] = "first_clean_real_observation"
        candidate["three_level_fingerprint"] = _fingerprint(candidate, actual_resource, actual_utilization)
        binding = copy.deepcopy(candidate["candidate_pool_binding"])
        binding["candidate_id"] = candidate["three_level_fingerprint"]["id"]
        binding["candidate_rank"] = predicted_rank
        binding["source_contract"] = "clean_diagnostic_observation_exact_plan_repeat_v1"
        candidate["candidate_pool_binding"] = binding
        candidate["used_for_gradient"] = False
        candidate["used_for_inner_incumbent_update"] = False
        candidate["gradient_update_applied"] = False
        candidate["incumbent_update_applied"] = False
        candidate["range_update_applied"] = False
        candidate["accepted"] = False
        candidate["diagnostic_only"] = True
        candidate["diagnostic_role_evaluation_only"] = True
        repeated.append(candidate)

    fingerprints = [item["three_level_fingerprint"]["id"] for item in repeated]
    params = [json.dumps(item["candidate_params"], sort_keys=True) for item in repeated]
    if len(set(fingerprints)) != 5 or len(set(params)) != 5:
        raise ValueError("repeat selection is not five-way distinct")

    output = copy.deepcopy(selection)
    output["successor_generation_contract"] = "job_exact_plan_repeat_validation_v1"
    output["exact_plan_repeat_prediction_source"] = "first_clean_real_observation"
    output["independent_repeat_required"] = True
    output["diagnostic_only"] = True
    output["diagnostic_role_evaluation_only"] = True
    output["accepted_update_allowed"] = False
    output["gradient_update_allowed"] = False
    output["incumbent_update_allowed"] = False
    output["range_update_allowed"] = False
    output["new_workload_calls"] = 0
    output["trusted_count"] = 3
    output["probe_count"] = 2
    output["novel_top5_fingerprint_count"] = 5
    output["generated_candidate_count"] = 5
    output["top5"] = repeated
    output["ranked_candidates"] = copy.deepcopy(repeated)
    output["source_first_observation_actual_scores"] = {
        str(int(item["rank"])): float(item["actual_score"]) for item in successful
    }
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build(_load(args.selection), _load(args.summary))
    _atomic_dump(args.output, result)
    print(json.dumps({
        "output": str(args.output),
        "roles": [item["selection_role"] for item in result["top5"]],
        "fingerprints": [item["three_level_fingerprint"]["id"] for item in result["top5"]],
        "new_workload_calls": 0,
    }, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
