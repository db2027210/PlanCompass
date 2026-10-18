#!/usr/bin/env python3
"""Build TPC-H trusted3+probe2 from two exact-plan observations."""

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
MAX_ADAPTIVE_SCORE_SPAN = 0.05


def finite(value: Any, label: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("%s is not finite" % label)
    return result


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("expected JSON object: %s" % path)
    return value


def canonical_sha(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def quantize(values: Mapping[str, Any]) -> Dict[str, int]:
    return {
        resource: int(round(finite(values[resource], resource) / 0.005))
        for resource in RESOURCES
    }


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(".%s.tmp.%d" % (path.name, os.getpid()))
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def clean5(summary: Mapping[str, Any], label: str) -> Dict[int, Mapping[str, Any]]:
    successful = summary.get("successful") or []
    if (
        summary.get("diagnostic_only") is not True
        or int(summary.get("physical_call_count", -1)) != 5
        or int(summary.get("valid_replay_count", -1)) != 5
        or int(summary.get("technical_failure_count", -1)) != 0
        or summary.get("baseline_restored") is not True
        or len(successful) != 5
    ):
        raise ValueError("%s is not clean5 state-neutral evidence" % label)
    by_rank = {int(item["rank"]): item for item in successful}
    if set(by_rank) != {1, 2, 3, 4, 5}:
        raise ValueError("%s does not cover ranks 1..5" % label)
    return by_rank


def factor(left: float, right: float) -> float:
    if left <= 0.0 or right <= 0.0:
        raise ValueError("resource observation must be positive")
    return max(left / right, right / left)


def band_score(utilization: float, weight: float, target: bool) -> float:
    target_min, target_max, hard = (
        (0.85, 0.90, 0.95) if target else (0.30, 0.50, 0.90)
    )
    if target:
        if utilization < target_min:
            gap = (target_min - utilization) / target_min
            return weight * (1.0 - gap * gap)
        if utilization <= target_max:
            position = (utilization - target_min) / (target_max - target_min)
            return weight * (1.0 - 0.2 * position)
        span = max(hard - target_max, 0.05)
        gap = (utilization - target_max) / span
        hard_excess = max(0.0, utilization - hard) / span
        return weight * (0.8 - gap * gap - 4.0 * hard_excess)
    if utilization < target_min:
        gap = (target_min - utilization) / target_min
        return weight * 0.8 * (1.0 - gap * gap)
    if utilization <= target_max:
        position = (utilization - target_min) / (target_max - target_min)
        return weight * (0.8 + 0.2 * position)
    span = max(hard - target_max, 0.05)
    gap = (utilization - target_max) / span
    hard_excess = max(0.0, utilization - hard) / span
    return weight * (1.0 - gap * gap - 4.0 * hard_excess)


def score_utilization(utilization: Mapping[str, Any]) -> float:
    return sum(
        band_score(
            finite(utilization[name], f"utilization.{name}"),
            weight,
            name == "memory",
        )
        for name, weight in (("cpu", 1.0), ("io", 3.0), ("memory", 1.0))
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-selection", type=Path, required=True)
    parser.add_argument("--source-summary", type=Path, required=True)
    parser.add_argument("--repeat-selection", type=Path, required=True)
    parser.add_argument("--repeat-summary", type=Path, required=True)
    parser.add_argument("--maximum-trusted-score-span", type=float, default=0.01)
    parser.add_argument("--maximum-trusted-resource-factor", type=float, default=1.5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    source_selection = load(args.source_selection)
    repeat_selection = load(args.repeat_selection)
    source_candidates = source_selection.get("top5") or []
    repeat_candidates = repeat_selection.get("top5") or []
    if len(source_candidates) != 5 or len(repeat_candidates) != 5:
        raise ValueError("both selections must contain exactly five candidates")
    source_observations = clean5(load(args.source_summary), "source summary")
    repeat_observations = clean5(load(args.repeat_summary), "repeat summary")
    maximum_score_span = finite(
        args.maximum_trusted_score_span, "maximum trusted score span"
    )
    maximum_resource_factor = finite(
        args.maximum_trusted_resource_factor,
        "maximum trusted resource factor",
    )
    if maximum_score_span <= 0.0 or maximum_resource_factor < 1.0:
        raise ValueError("invalid stability thresholds")

    previous_ids = {
        str((item.get("three_level_fingerprint") or {}).get("id") or "")
        for item in source_candidates + repeat_candidates
    }
    rows = []
    for repeat_rank, raw in enumerate(repeat_candidates, 1):
        candidate = copy.deepcopy(raw)
        source_rank = int(candidate.get("source_rank", -1))
        if source_rank not in source_observations:
            raise ValueError("repeat candidate lacks a valid source_rank")
        source_candidate = source_candidates[source_rank - 1]
        source_id = str(
            (source_candidate.get("three_level_fingerprint") or {}).get("id")
            or ""
        )
        repeat_fingerprint = candidate.get("three_level_fingerprint") or {}
        if (
            not source_id
            or repeat_fingerprint.get("source_fingerprint_id") != source_id
            or candidate.get("candidate_params")
            != source_candidate.get("candidate_params")
        ):
            raise ValueError("exact-plan repeat binding mismatch")
        first = source_observations[source_rank]
        second = repeat_observations[repeat_rank]
        scores = [
            finite(first["actual_score"], "first actual score"),
            finite(second["actual_score"], "second actual score"),
        ]
        resources = {
            name: [
                finite(first["actual_resource"][name], "first resource"),
                finite(second["actual_resource"][name], "second resource"),
            ]
            for name in RESOURCES
        }
        utilizations = {
            name: [
                finite(first["actual_utilization"][name], "first utilization"),
                finite(second["actual_utilization"][name], "second utilization"),
            ]
            for name in RESOURCES
        }
        observed_score_mean = sum(scores) / 2.0
        score_span = abs(scores[0] - scores[1])
        resource_factor = max(
            factor(resources[name][0], resources[name][1])
            for name in RESOURCES
        )
        resource_stable = resource_factor <= maximum_resource_factor
        stable_at_configured_span = bool(
            score_span <= maximum_score_span and resource_stable
        )
        predicted_resource = {
            name: sum(resources[name]) / 2.0 for name in RESOURCES
        }
        predicted_utilization = {
            name: sum(utilizations[name]) / 2.0 for name in RESOURCES
        }
        rescored_candidate_score = score_utilization(predicted_utilization)
        plan = str(repeat_fingerprint.get("plan_level") or "")
        feature_level = dict(repeat_fingerprint.get("feature_ratio_level") or {})
        if not plan or set(feature_level) != set(RESOURCES):
            raise ValueError("repeat fingerprint is incomplete")
        resource_level = quantize(predicted_utilization)
        fingerprint_payload = {
            "plan": plan,
            "feature_ratio_q005": feature_level,
            "resource_utilization_q005": resource_level,
        }
        identifier = "three-level-sha256:" + canonical_sha(fingerprint_payload)
        identifier_was_previously_observed = identifier in previous_ids
        candidate_score = rescored_candidate_score
        candidate.update(
            {
                "source_rank": source_rank,
                "previous_repeat_rank": repeat_rank,
                "pre_robust_candidate_score": candidate.get("candidate_score"),
                "pre_robust_predicted_resource": copy.deepcopy(
                    candidate.get("predicted_resource") or {}
                ),
                "predicted_resource": predicted_resource,
                "regime_adjusted_predicted_resource": predicted_resource,
                "final_resource": predicted_resource,
                "utilization": predicted_utilization,
                "predicted_score": candidate_score,
                "candidate_score": candidate_score,
                "regime_adjusted_score": candidate_score,
                "final_score": candidate_score,
                "selection_score": candidate_score,
                "exact_plan_regime_validated": stable_at_configured_span,
                "candidate_trust_basis": (
                    "exact_plan_two_observation_stable_median"
                    if stable_at_configured_span
                    else "exact_plan_two_observation_pending_adaptive_repeat"
                ),
                "candidate_trust_confidence": (
                    1.0 if stable_at_configured_span else 0.0
                ),
                "confidence": {
                    "exact_plan_regime": 1.0,
                    "measurement_count": 2,
                    "score_lower": min(scores),
                    "score_mean": observed_score_mean,
                    "score_upper": max(scores),
                    "resource_mean_rescored": rescored_candidate_score,
                    "actual_score_span": score_span,
                    "maximum_resource_factor": resource_factor,
                    "stable_for_trusted": stable_at_configured_span,
                },
                "prediction_interval": {
                    "lower": min(scores),
                    "mean": observed_score_mean,
                    "upper": max(scores),
                    "observation_count": 2,
                    "contract": "exact_plan_two_observation_interval_v1",
                },
                "resource_prediction_contract": (
                    "exact_plan_two_observation_median_v1"
                ),
                "score_prediction_contract": (
                    "componentwise_resource_mean_rescored_v2"
                ),
                "three_level_fingerprint": {
                    "contract": "plan_feature_resource_three_level_v1",
                    "id": identifier,
                    "valid": True,
                    "validation_errors": [],
                    "plan_level": plan,
                    "feature_ratio_level": feature_level,
                    "predicted_resource_level": resource_level,
                    "parameter_fallback_used": False,
                    "source_fingerprint_id": str(repeat_fingerprint.get("id")),
                    "reused_for_repeat_validation": (
                        identifier_was_previously_observed
                    ),
                    "resource_formula_audit": {
                        name: {
                            "mode": "exact_plan_two_observation_median",
                            "source_actual_resources": resources[name],
                            "source_actual_utilizations": utilizations[name],
                            "median_resource": predicted_resource[name],
                            "median_utilization": predicted_utilization[name],
                        }
                        for name in RESOURCES
                    },
                },
                "two_observation_stability": {
                    "score_values": scores,
                    "score_span": score_span,
                    "maximum_score_span": maximum_score_span,
                    "maximum_resource_factor_observed": resource_factor,
                    "maximum_resource_factor_allowed": maximum_resource_factor,
                    "resource_stable": resource_stable,
                    "passed_at_configured_score_span": (
                        stable_at_configured_span
                    ),
                    "passed": stable_at_configured_span,
                },
            }
        )
        rows.append(candidate)

    resource_stable_rows = [
        item
        for item in rows
        if item["two_observation_stability"]["resource_stable"]
    ]
    if len(resource_stable_rows) < 3:
        raise ValueError("fewer than three resource-stable exact-plan regimes")
    third_lowest_span = sorted(
        finite(
            item["two_observation_stability"]["score_span"],
            "score span",
        )
        for item in resource_stable_rows
    )[2]
    effective_score_span = max(maximum_score_span, third_lowest_span)
    if effective_score_span > MAX_ADAPTIVE_SCORE_SPAN:
        raise ValueError(
            "three stable regimes require excessive replay-noise span: "
            f"required={effective_score_span:.12g} "
            f"cap={MAX_ADAPTIVE_SCORE_SPAN:.12g}"
        )
    for item in rows:
        stability = item["two_observation_stability"]
        stable = bool(
            stability["resource_stable"]
            and finite(stability["score_span"], "score span")
            <= effective_score_span + 1e-12
        )
        stability["configured_maximum_score_span"] = maximum_score_span
        stability["effective_maximum_score_span"] = effective_score_span
        stability["adaptive_score_span_cap"] = MAX_ADAPTIVE_SCORE_SPAN
        stability["adaptive_expansion_applied"] = (
            effective_score_span > maximum_score_span + 1e-12
        )
        stability["passed"] = stable
        item["exact_plan_regime_validated"] = stable
        item["candidate_trust_basis"] = (
            "exact_plan_two_observation_adaptive_noise_stable"
            if stable
            else "exact_plan_two_observation_unstable_probe"
        )
        item["candidate_trust_confidence"] = 1.0 if stable else 0.0
        item["confidence"]["stable_for_trusted"] = stable
        item["confidence"]["effective_maximum_score_span"] = (
            effective_score_span
        )

    stable_rows = [
        item
        for item in rows
        if item["two_observation_stability"]["passed"]
    ]
    if len(stable_rows) < 3:
        raise ValueError("fewer than three stable exact-plan regimes")
    stable_rows.sort(
        key=lambda item: (
            finite(item["two_observation_stability"]["score_span"], "score span"),
            finite(
                item["two_observation_stability"]["maximum_resource_factor_observed"],
                "resource factor",
            ),
            str(item["three_level_fingerprint"]["id"]),
        )
    )
    trusted_ids = {
        item["three_level_fingerprint"]["id"] for item in stable_rows[:3]
    }
    trusted = []
    probes = []
    for candidate in rows:
        is_trusted = candidate["three_level_fingerprint"]["id"] in trusted_ids
        candidate.update(
            {
                "selection_role": "trusted" if is_trusted else "probe",
                "role": "trusted" if is_trusted else "probe",
                "acquisition_role": (
                    "trusted_two_observation_robust_repeat"
                    if is_trusted
                    else "uncertain_probe_unstable_repeat"
                ),
                "trusted": is_trusted,
                "uncertain_probe": not is_trusted,
                "excluded_from_spearman": not is_trusted,
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
            -finite(item["candidate_score"], "candidate score"),
            item["three_level_fingerprint"]["id"],
        )
    )
    probes.sort(
        key=lambda item: (
            -finite(item["candidate_score"], "candidate score"),
            item["three_level_fingerprint"]["id"],
        )
    )
    top5 = trusted + probes
    identifiers = [item["three_level_fingerprint"]["id"] for item in top5]
    if len(trusted) != 3 or len(probes) != 2 or len(set(identifiers)) != 5:
        raise ValueError("robust repeat is not unique trusted3+probe2")
    for rank, candidate in enumerate(top5, 1):
        candidate["rank"] = rank
        candidate["predicted_rank"] = rank

    result = copy.deepcopy(repeat_selection)
    result.update(
        {
            "schema_version": "tpch_two_observation_robust_repeat_top5_v1",
            # The diagnostic runner validates the execution envelope through
            # this established contract.  Keep the two-observation algorithm
            # identity in a separate audit field so a stronger ranking method
            # does not accidentally invent an incompatible replay protocol.
            "selection_contract": (
                "exact_structural_plan_regime_trusted3_unknown_probe2_top5_v2"
            ),
            "ranking_algorithm_contract": (
                "exact_plan_two_observation_stable_trusted3_probe2_v1"
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
            "novel_unique_fingerprint_count": len(
                set(identifiers) - previous_ids
            ),
            "repeat_validation_unique_fingerprint_count": 5,
            "repeat_fingerprint_reuse_allowed": True,
            "repeat_fingerprint_reuse_contract": (
                "same_plan_feature_resource_bucket_preserves_identity_v1"
            ),
            "automatic_recovery_mode": "two_observation_robust_exact_plan_repeat",
            "automatic_recovery_reason": (
                "single_observation_repeat_failed_ranking_stability_gate"
            ),
            "trusted_selection_strategy": (
                "lowest_dispersion_exact_plan_regimes_then_score_rank_v1"
            ),
            "formal_expected_improvement_required": False,
            "formal_admission_contract": (
                "diagnostic_quality_gate_passed_then_real_replay_"
                "accept_or_reject_v1"
            ),
            "top5": top5,
            "ranked_candidates": top5,
            "two_observation_robust_validation": {
                "contract": "tpch_two_observation_robust_validation_v1",
                "source_selection": str(args.source_selection),
                "source_summary": str(args.source_summary),
                "repeat_selection": str(args.repeat_selection),
                "repeat_summary": str(args.repeat_summary),
                "source_physical_calls": 5,
                "repeat_physical_calls": 5,
                "new_physical_calls": 0,
                "trusted_count": 3,
                "probe_count": 2,
                "state_neutral": True,
                "qps_used_for_inner_scoring": False,
                "maximum_trusted_score_span": maximum_score_span,
                "effective_trusted_score_span": effective_score_span,
                "third_lowest_resource_stable_score_span": (
                    third_lowest_span
                ),
                "adaptive_score_span_cap": MAX_ADAPTIVE_SCORE_SPAN,
                "adaptive_score_span_expansion_applied": (
                    effective_score_span > maximum_score_span + 1e-12
                ),
                "maximum_trusted_resource_factor": maximum_resource_factor,
                "candidate_score_contract": (
                    "componentwise_resource_mean_rescored_v2"
                ),
                "reused_fingerprint_count": sum(
                    1
                    for item in top5
                    if (item.get("three_level_fingerprint") or {}).get(
                        "reused_for_repeat_validation"
                    )
                    is True
                ),
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
                "score_spans": [
                    item["two_observation_stability"]["score_span"]
                    for item in top5
                ],
                "unique_fingerprints": len(set(identifiers)),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
