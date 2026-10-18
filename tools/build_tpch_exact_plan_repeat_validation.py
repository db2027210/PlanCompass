#!/usr/bin/env python3
"""Build deterministic TPC-H exact-plan repeat-validation trusted3+probe2."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Dict, Mapping


RESOURCES = ("cpu", "io", "memory")


def finite(value: Any, label: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} is not finite")
    return result


def read_object(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def canonical_sha(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def quantize(values: Mapping[str, Any]) -> Dict[str, int]:
    return {
        resource: int(round(finite(values[resource], resource) / 0.005))
        for resource in RESOURCES
    }


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--minimum-score-span", type=float, default=0.0)
    args = parser.parse_args()

    selection = read_object(args.selection)
    summary = read_object(args.summary)
    source = selection.get("top5") or []
    successful = summary.get("successful") or []
    if len(source) != 5:
        raise ValueError("source selection must contain exactly five candidates")
    if (
        summary.get("diagnostic_only") is not True
        or int(summary.get("physical_call_count", -1)) != 5
        or int(summary.get("valid_replay_count", -1)) != 5
        or int(summary.get("technical_failure_count", -1)) != 0
        or summary.get("baseline_restored") is not True
        or len(successful) != 5
    ):
        raise ValueError("source diagnostic is not clean5")
    by_rank = {int(item["rank"]): item for item in successful}
    if set(by_rank) != {1, 2, 3, 4, 5}:
        raise ValueError("source observations do not cover ranks 1..5")

    repeated = []
    for old_rank, raw in enumerate(source, 1):
        candidate = copy.deepcopy(raw)
        observed = by_rank[old_rank]
        old_fingerprint = candidate.get("three_level_fingerprint") or {}
        old_id = str(old_fingerprint.get("id") or "")
        plan = str(old_fingerprint.get("plan_level") or "")
        feature_level = dict(old_fingerprint.get("feature_ratio_level") or {})
        if not old_id or not plan or set(feature_level) != set(RESOURCES):
            raise ValueError(f"rank {old_rank} has invalid fingerprint")
        actual_resource = {
            name: finite(observed["actual_resource"][name], f"actual_resource.{name}")
            for name in RESOURCES
        }
        actual_utilization = {
            name: finite(
                observed["actual_utilization"][name],
                f"actual_utilization.{name}",
            )
            for name in RESOURCES
        }
        actual_score = finite(observed["actual_score"], "actual_score")
        resource_level = quantize(actual_utilization)
        fingerprint_payload = {
            "plan": plan,
            "feature_ratio_q005": feature_level,
            "resource_utilization_q005": resource_level,
        }
        new_id = "three-level-sha256:" + canonical_sha(fingerprint_payload)
        candidate.update(
            {
                "source_rank": old_rank,
                "source_selection_role": "probe",
                "pre_repeat_predicted_resource": copy.deepcopy(
                    candidate.get("predicted_resource") or {}
                ),
                "pre_repeat_candidate_score": candidate.get("candidate_score"),
                "predicted_resource": actual_resource,
                "regime_adjusted_predicted_resource": actual_resource,
                "final_resource": actual_resource,
                "utilization": actual_utilization,
                "predicted_score": actual_score,
                "candidate_score": actual_score,
                "regime_adjusted_score": actual_score,
                "final_score": actual_score,
                "selection_score": actual_score,
                "exact_plan_regime_validated": True,
                "candidate_trust_basis": (
                    "exact_plan_first_observation_repeat_validation"
                ),
                "candidate_trust_confidence": 1.0,
                "confidence": {
                    "exact_plan_regime": 1.0,
                    "measurement_count": 1,
                    "score_lower": actual_score,
                    "score_median": actual_score,
                    "score_upper": actual_score,
                    "actual_score_span": 0.0,
                },
                "prediction_interval": {
                    "lower": actual_score,
                    "median": actual_score,
                    "upper": actual_score,
                    "observation_count": 1,
                    "contract": "exact_plan_first_observation_repeat_v1",
                },
                "three_level_fingerprint": {
                    "contract": "plan_feature_resource_three_level_v1",
                    "id": new_id,
                    "valid": True,
                    "validation_errors": [],
                    "plan_level": plan,
                    "feature_ratio_level": feature_level,
                    "predicted_resource_level": resource_level,
                    "parameter_fallback_used": False,
                    "source_fingerprint_id": old_id,
                    "resource_formula_audit": {
                        name: {
                            "mode": "exact_plan_first_real_observation",
                            "source_actual_resource": actual_resource[name],
                            "source_actual_utilization": actual_utilization[name],
                        }
                        for name in RESOURCES
                    },
                },
            }
        )
        repeated.append(candidate)

    ranked = sorted(
        repeated,
        key=lambda item: (
            -finite(item["candidate_score"], "candidate_score"),
            item["three_level_fingerprint"]["id"],
        ),
    )
    source_score_span = finite(
        ranked[0]["candidate_score"], "best candidate_score"
    ) - finite(ranked[-1]["candidate_score"], "worst candidate_score")
    minimum_score_span = finite(args.minimum_score_span, "minimum_score_span")
    if minimum_score_span < 0:
        raise ValueError("minimum_score_span must be non-negative")
    if source_score_span <= minimum_score_span:
        raise ValueError(
            "source score span is not distinguishable from replay noise: "
            f"span={source_score_span:.12g} noise_bound={minimum_score_span:.12g}"
        )

    # Adjacent top scores can be closer than replay noise even when the full
    # batch is distinguishable.  Use deterministic anchors across the full
    # observed score span: best, median, and worst.  The interleaving points
    # remain held-out uncertain probes.
    trusted_ranked = [ranked[0], ranked[2], ranked[4]]
    probe_ranked = [ranked[1], ranked[3]]
    trusted_ids = {
        item["three_level_fingerprint"]["id"] for item in trusted_ranked
    }
    trusted = []
    probes = []
    for candidate in repeated:
        is_trusted = candidate["three_level_fingerprint"]["id"] in trusted_ids
        candidate.update(
            {
                "selection_role": "trusted" if is_trusted else "probe",
                "role": "trusted" if is_trusted else "probe",
                "acquisition_role": (
                    "trusted_repeat_validation"
                    if is_trusted
                    else "uncertain_probe_repeat_validation"
                ),
                "trusted": is_trusted,
                "uncertain_probe": not is_trusted,
                "eligible_for_best_candidate": False,
                "incumbent_update_eligible": False,
                "diagnostic_only": True,
                "accepted_update_allowed": False,
                "incumbent_update_allowed": False,
                "range_update_allowed": False,
                "gradient_update_allowed": False,
                "used_for_gradient": False,
                "used_for_inner_incumbent_update": False,
                "probe_updates_forbidden": not is_trusted,
            }
        )
        (trusted if is_trusted else probes).append(candidate)
    trusted.sort(
        key=lambda item: (
            -finite(item["candidate_score"], "candidate_score"),
            item["three_level_fingerprint"]["id"],
        )
    )
    probes.sort(
        key=lambda item: (
            -finite(item["candidate_score"], "candidate_score"),
            item["three_level_fingerprint"]["id"],
        )
    )
    top5 = trusted + probes
    if len(trusted) != 3 or len(probes) != 2:
        raise ValueError("repeat selection is not trusted3+probe2")
    identifiers = [item["three_level_fingerprint"]["id"] for item in top5]
    if len(set(identifiers)) != 5:
        raise ValueError("repeat fingerprints are not distinct")
    for rank, candidate in enumerate(top5, 1):
        candidate["rank"] = rank
        candidate["predicted_rank"] = rank

    validated_registry = copy.deepcopy(
        selection.get("validated_plan_regimes") or {}
    )
    for candidate in probes:
        validated_registry.pop(str(candidate["exact_plan_regime_id"]), None)
    for candidate in trusted:
        regime = str(candidate["exact_plan_regime_id"])
        validated_registry[regime] = {
            "contract": "tpch_exact_plan_first_observation_repeat_v1",
            "plan_level": candidate["three_level_fingerprint"]["plan_level"],
            "observation_count_before_repeat": 1,
            "source_rank": candidate["source_rank"],
            "source_summary": str(args.summary),
            "source_actual_resource": copy.deepcopy(
                candidate["predicted_resource"]
            ),
            "source_actual_utilization": copy.deepcopy(
                candidate["utilization"]
            ),
            "source_actual_score": candidate["candidate_score"],
            "trusted_for_repeat_validation": True,
            "formal_trust_requires_repeat_gate": True,
        }

    result = copy.deepcopy(selection)
    result.update(
        {
            "schema_version": "tpch_exact_plan_repeat_validation_top5_v1",
            "selection_contract": (
                "exact_structural_plan_regime_trusted3_unknown_probe2_top5_v2"
            ),
            "diagnostic_only": True,
            "accepted_update_allowed": False,
            "incumbent_update_allowed": False,
            "range_update_allowed": False,
            "gradient_update_allowed": False,
            "qps_used_for_inner_scoring": False,
            "maximum_real_replay_invocations": 5,
            "trusted_slots": 3,
            "probe_slots": 2,
            "unknown_plan_regime_trusted_allowed": False,
            "novel_unique_fingerprint_count": 5,
            "automatic_recovery_mode": "exact_plan_first_observation_repeat",
            "automatic_recovery_reason": (
                "successor_probe5_requires_independent_exact_plan_repeat"
            ),
            "trusted_selection_strategy": (
                "deterministic_observed_score_span_best_median_worst_v1"
            ),
            "source_actual_score_span": source_score_span,
            "minimum_required_score_span": minimum_score_span,
            "score_span_gate_passed": True,
            "trusted_source_ranks": [item["source_rank"] for item in trusted_ranked],
            "probe_source_ranks": [item["source_rank"] for item in probe_ranked],
            "formal_expected_improvement_required": False,
            "formal_admission_contract": (
                "diagnostic_quality_gate_passed_then_real_replay_"
                "accept_or_reject_v1"
            ),
            "validated_plan_regimes": validated_registry,
            "top5": top5,
            "ranked_candidates": top5,
            "previous_batch_feedback_audit": copy.deepcopy(successful),
            "exact_plan_repeat_validation": {
                "contract": "tpch_exact_plan_first_observation_repeat_v1",
                "source_selection": str(args.selection),
                "source_summary": str(args.summary),
                "source_physical_calls": 5,
                "source_valid_replays": 5,
                "source_technical_failures": 0,
                "trusted_count": 3,
                "probe_count": 2,
                "state_neutral": True,
                "qps_used_for_inner_scoring": False,
            },
        }
    )
    atomic_json(args.output, result)
    print(
        json.dumps(
            {
                "status": "ready",
                "output": str(args.output),
                "roles": [item["selection_role"] for item in top5],
                "source_ranks": [item["source_rank"] for item in top5],
                "unique_fingerprints": len(set(identifiers)),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
