#!/usr/bin/env python3
"""Seal two clean JOB diagnostics into a robust formal Top-5 selection."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import statistics
from typing import Any, Mapping


RESOURCES = ("cpu", "io", "memory")
PARAMETER_NAMES = (
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "cpu_tuple_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
    "random_page_cost",
    "seq_page_cost",
)
CAPABILITY = {"cpu": 3.0, "io": 256.0, "memory": 2.91015625}
POLICY_CONTRACT = "job_three_independent_exact_point_median_acceptance_v2"


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def finite(value: Any, label: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} is not finite")
    return number


def params(value: Mapping[str, Any]) -> dict[str, float]:
    raw = value.get("candidate_params")
    if not isinstance(raw, Mapping) or set(raw) != set(PARAMETER_NAMES):
        raise ValueError("candidate_params is incomplete")
    return {name: finite(raw[name], name) for name in PARAMETER_NAMES}


def same_params(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return all(
        math.isclose(
            float(left[name]),
            float(right[name]),
            rel_tol=0.0,
            abs_tol=max(1e-15, abs(float(left[name])) * 1e-12),
        )
        for name in PARAMETER_NAMES
    )


def candidates(selection: Mapping[str, Any]) -> list[dict[str, Any]]:
    result = selection.get("top5")
    if not isinstance(result, list) or len(result) != 5:
        raise ValueError("selection is not Top-5")
    if not all(isinstance(item, dict) for item in result):
        raise ValueError("selection contains a non-object candidate")
    return result


def successful(summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    result = summary.get("successful")
    if not isinstance(result, list) or len(result) != 5:
        raise ValueError("diagnostic summary is not clean5")
    if not all(isinstance(item, dict) for item in result):
        raise ValueError("diagnostic summary contains a non-object row")
    return result


def validate_clean_diagnostic(
    selection_path: Path,
    selection: Mapping[str, Any],
    summary: Mapping[str, Any],
) -> None:
    if (
        summary.get("diagnostic_only") is not True
        or int(summary.get("physical_invocation_count", -1)) != 5
        or int(summary.get("successful_real_replay_count", -1)) != 5
        or int(summary.get("technical_failure_count", -1)) != 0
        or summary.get("baseline_params_restored") is not True
        or (summary.get("baseline_restore") or {}).get("success") is not True
        or summary.get("initial_state") != summary.get("final_state")
        or summary.get("formal_update_flags")
        != {
            "accepted": False,
            "gradient": False,
            "incumbent": False,
            "range": False,
        }
        or summary.get("selection_sha256") != file_sha256(selection_path)
    ):
        raise ValueError("diagnostic summary violates clean5 state-neutral contract")
    if len(candidates(selection)) != len(successful(summary)):
        raise ValueError("diagnostic selection/summary cardinality mismatch")


def row_params(row: Mapping[str, Any]) -> Mapping[str, Any]:
    binding = row.get("candidate_binding")
    if not isinstance(binding, Mapping):
        raise ValueError("diagnostic row lacks candidate binding")
    value = binding.get("candidate_params")
    if not isinstance(value, Mapping):
        raise ValueError("diagnostic row lacks bound candidate_params")
    return value


def unique_match(
    target_params: Mapping[str, Any],
    values: list[Mapping[str, Any]],
    extractor: Any,
    label: str,
) -> Mapping[str, Any]:
    matches = [item for item in values if same_params(target_params, extractor(item))]
    if len(matches) != 1:
        raise ValueError(f"{label} parameter match count is {len(matches)}")
    return matches[0]


def median_resource(
    first: Mapping[str, Any],
    second: Mapping[str, Any],
) -> tuple[dict[str, float], dict[str, float]]:
    actual_resource = {}
    utilization = {}
    for name in RESOURCES:
        actual_resource[name] = float(
            statistics.median(
                [
                    finite(first["actual_resource"][name], f"first {name}"),
                    finite(second["actual_resource"][name], f"second {name}"),
                ]
            )
        )
        utilization[name] = actual_resource[name] / CAPABILITY[name]
    return actual_resource, utilization


def fingerprint(
    plan: str,
    feature_ratio: Mapping[str, Any],
    resource: Mapping[str, float],
) -> dict[str, Any]:
    feature_level = {
        name: int(round(finite(feature_ratio[name], name) / 0.005))
        for name in RESOURCES
    }
    resource_level = {
        name: int(round((float(resource[name]) / CAPABILITY[name]) / 0.005))
        for name in RESOURCES
    }
    identity = {
        "plan": plan,
        "feature_ratio_q005": feature_level,
        "resource_utilization_q005": resource_level,
    }
    return {
        "contract": "plan_feature_resource_three_level_v1",
        "id": "three-level-sha256:" + canonical_sha256(identity),
        "valid": True,
        "validation_errors": [],
        "plan_level": plan,
        "feature_ratio_level": feature_level,
        "predicted_resource_level": resource_level,
        "parameter_fallback_used": False,
        "repeat_prediction_source": "two_independent_exact_point_observation_median",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--first-selection", required=True, type=Path)
    parser.add_argument("--first-summary", required=True, type=Path)
    parser.add_argument("--second-selection", required=True, type=Path)
    parser.add_argument("--second-summary", required=True, type=Path)
    parser.add_argument("--gate", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    first_selection = load(args.first_selection)
    first_summary = load(args.first_summary)
    second_selection = load(args.second_selection)
    second_summary = load(args.second_summary)
    gate = load(args.gate)
    validate_clean_diagnostic(args.first_selection, first_selection, first_summary)
    validate_clean_diagnostic(args.second_selection, second_selection, second_summary)
    checks = gate.get("checks")
    if (
        gate.get("passed") is not True
        or not isinstance(checks, Mapping)
        or not checks
        or any(value is not True for value in checks.values())
    ):
        raise ValueError("JOB formal entry gate is not fully passed")

    first_candidates = candidates(first_selection)
    second_candidates = candidates(second_selection)
    first_rows = successful(first_summary)
    second_rows = successful(second_summary)
    roles = [str(item.get("selection_role") or "") for item in second_candidates]
    if roles != ["trusted", "trusted", "trusted", "probe", "probe"]:
        raise ValueError("second diagnostic must be ordered trusted3+probe2")

    formal_top5 = []
    source_pairs = []
    for rank, source in enumerate(second_candidates, 1):
        candidate = copy.deepcopy(source)
        candidate_params = params(candidate)
        first_candidate = unique_match(
            candidate_params,
            first_candidates,
            params,
            "first selection",
        )
        first_row = unique_match(
            candidate_params,
            first_rows,
            row_params,
            "first summary",
        )
        second_row = unique_match(
            candidate_params,
            second_rows,
            row_params,
            "second summary",
        )
        first_plan = str(
            (first_candidate.get("three_level_fingerprint") or {}).get("plan_level")
            or first_candidate.get("workload_plan_fingerprint")
            or ""
        )
        second_plan = str(
            (candidate.get("three_level_fingerprint") or {}).get("plan_level")
            or candidate.get("workload_plan_fingerprint")
            or ""
        )
        if not first_plan or first_plan != second_plan:
            raise ValueError(f"rank {rank} exact plan regime changed between diagnostics")
        first_score = finite(first_row.get("actual_score"), "first actual_score")
        second_score = finite(second_row.get("actual_score"), "second actual_score")
        predicted_resource, utilization = median_resource(first_row, second_row)
        predicted_score = float(statistics.median([first_score, second_score]))
        new_fingerprint = fingerprint(
            second_plan,
            candidate["feature_ratio"],
            predicted_resource,
        )
        binding = copy.deepcopy(candidate.get("candidate_pool_binding") or {})
        binding["candidate_id"] = new_fingerprint["id"]
        trusted = candidate.get("selection_role") == "trusted"
        candidate.update(
            {
                "rank": rank,
                "diagnostic_only": False,
                "diagnostic_role_evaluation_only": False,
                "candidate_score": predicted_score,
                "calibrated_candidate_score": predicted_score,
                "selection_score": predicted_score,
                "final_score": predicted_score,
                "predicted_resource": predicted_resource,
                "calibrated_predicted_resource": copy.deepcopy(predicted_resource),
                "final_resource": copy.deepcopy(predicted_resource),
                "utilization": utilization,
                "repeat_prediction_source": (
                    "two_independent_exact_point_observation_median"
                ),
                "three_level_fingerprint": new_fingerprint,
                "candidate_plan_signature": new_fingerprint["id"],
                "candidate_pool_binding": binding,
                "accepted": False,
                "incumbent_update_eligible": trusted,
                "used_for_inner_incumbent_update": trusted,
                "used_for_gradient": bool(
                    trusted and gate.get("gradient_rank_update_allowed") is True
                ),
                "accepted_update_applied": False,
                "incumbent_update_applied": False,
                "range_update_applied": False,
                "gradient_update_applied": False,
                "robust_acceptance_policy": {
                    "contract": POLICY_CONTRACT,
                    "candidate_params_sha256": canonical_sha256(candidate_params),
                    "minimum_total_observation_count": 3,
                    "aggregation": "median",
                    "qps_used_for_inner_scoring": False,
                    "prior_observations": [
                        {
                            "ordinal": 1,
                            "selection_path": str(args.first_selection.resolve()),
                            "selection_sha256": file_sha256(args.first_selection),
                            "summary_path": str(args.first_summary.resolve()),
                            "summary_sha256": file_sha256(args.first_summary),
                            "actual_score": first_score,
                        },
                        {
                            "ordinal": 2,
                            "selection_path": str(args.second_selection.resolve()),
                            "selection_sha256": file_sha256(args.second_selection),
                            "summary_path": str(args.second_summary.resolve()),
                            "summary_sha256": file_sha256(args.second_summary),
                            "actual_score": second_score,
                        },
                    ],
                },
            }
        )
        formal_top5.append(candidate)
        source_pairs.append(
            {
                "rank": rank,
                "candidate_params_sha256": canonical_sha256(candidate_params),
                "prior_actual_scores": [first_score, second_score],
                "predicted_score": predicted_score,
            }
        )

    fingerprint_ids = [
        item["three_level_fingerprint"]["id"] for item in formal_top5
    ]
    if len(set(fingerprint_ids)) != 5:
        raise ValueError("robust formal Top-5 fingerprints are not distinct")
    result = copy.deepcopy(second_selection)
    result.update(
        {
            "diagnostic_only": False,
            "diagnostic_role_evaluation_only": False,
            "accepted_update_allowed": True,
            "incumbent_update_allowed": True,
            "range_update_allowed": True,
            "gradient_update_allowed": bool(
                gate.get("gradient_rank_update_allowed") is True
            ),
            "formal_update_flags": {
                "accepted": True,
                "incumbent": True,
                "range": True,
                "gradient": bool(
                    gate.get("gradient_rank_update_allowed") is True
                ),
            },
            "three_observation_acceptance_contract": POLICY_CONTRACT,
            "formal_entry_gate_path": str(args.gate.resolve()),
            "formal_entry_gate_sha256": file_sha256(args.gate),
            "source_diagnostic_pairs": source_pairs,
            "top5": formal_top5,
        }
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "selection_sha256": file_sha256(args.output),
                "trusted_count": 3,
                "probe_count": 2,
                "new_workload_calls": 0,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
