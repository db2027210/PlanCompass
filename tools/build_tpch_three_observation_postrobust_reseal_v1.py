#!/usr/bin/env python3
"""Reseal a TPC-H Top-5 after its third exact-plan observation.

The two-observation selector intentionally gives uncertain regimes probe slots.
After the robust repeat has supplied a third observation, keeping those old
roles can hide a now-stable and high-scoring regime from the formal-entry gate.
This tool deterministically recomputes stability, resource predictions, score,
roles, ranks, and three-level fingerprints without issuing a workload call.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import statistics
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence


RESOURCES = ("cpu", "io", "memory")
DEFAULT_MAXIMUM_SCORE_SPAN = 0.01
DEFAULT_ADAPTIVE_SCORE_SPAN_CAP = 0.05
DEFAULT_MAXIMUM_RESOURCE_FACTOR = 1.5


def finite(value: Any, label: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} is not finite")
    return result


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def quantize(values: Mapping[str, Any]) -> Dict[str, int]:
    return {
        resource: int(round(finite(values[resource], resource) / 0.005))
        for resource in RESOURCES
    }


def factor(values: Sequence[float], label: str) -> float:
    if len(values) != 3 or any(value <= 0.0 for value in values):
        raise ValueError(f"{label} requires three positive observations")
    return max(values) / min(values)


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


def clean5(summary: Mapping[str, Any]) -> Dict[int, Mapping[str, Any]]:
    successful = summary.get("successful") or []
    if not (
        summary.get("diagnostic_only") is True
        and int(summary.get("physical_call_count", -1)) == 5
        and int(summary.get("valid_replay_count", -1)) == 5
        and int(summary.get("technical_failure_count", -1)) == 0
        and summary.get("baseline_restored") is True
        and summary.get("accepted_update_applied") is False
        and summary.get("incumbent_update_applied") is False
        and summary.get("range_update_applied") is False
        and summary.get("gradient_update_applied") is False
        and len(successful) == 5
    ):
        raise ValueError("third-observation summary is not clean5 state-neutral evidence")
    by_rank = {int(item["rank"]): item for item in successful}
    if set(by_rank) != {1, 2, 3, 4, 5}:
        raise ValueError("third-observation summary does not cover ranks 1..5")
    return by_rank


def observation_history(
    candidate: Mapping[str, Any],
    third: Mapping[str, Any],
) -> tuple[List[float], Dict[str, List[float]], Dict[str, List[float]]]:
    stability = candidate.get("two_observation_stability") or {}
    score_values = [
        finite(value, "prior score") for value in stability.get("score_values") or []
    ]
    if len(score_values) != 2:
        raise ValueError("candidate lacks exactly two prior score observations")
    score_values.append(finite(third["actual_score"], "third actual score"))

    resource_audit = (
        (candidate.get("three_level_fingerprint") or {}).get(
            "resource_formula_audit"
        )
        or {}
    )
    resources: Dict[str, List[float]] = {}
    utilizations: Dict[str, List[float]] = {}
    for name in RESOURCES:
        audit = resource_audit.get(name) or {}
        resource_values = [
            finite(value, f"{name} prior resource")
            for value in audit.get("source_actual_resources") or []
        ]
        utilization_values = [
            finite(value, f"{name} prior utilization")
            for value in audit.get("source_actual_utilizations") or []
        ]
        if len(resource_values) != 2 or len(utilization_values) != 2:
            raise ValueError(f"candidate lacks two prior {name} observations")
        resource_values.append(
            finite(third["actual_resource"][name], f"{name} third resource")
        )
        utilization_values.append(
            finite(third["actual_utilization"][name], f"{name} third utilization")
        )
        resources[name] = resource_values
        utilizations[name] = utilization_values
    return score_values, resources, utilizations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--third-observation-summary", type=Path, required=True)
    parser.add_argument(
        "--maximum-trusted-score-span",
        type=float,
        default=DEFAULT_MAXIMUM_SCORE_SPAN,
    )
    parser.add_argument(
        "--adaptive-score-span-cap",
        type=float,
        default=DEFAULT_ADAPTIVE_SCORE_SPAN_CAP,
    )
    parser.add_argument(
        "--maximum-trusted-resource-factor",
        type=float,
        default=DEFAULT_MAXIMUM_RESOURCE_FACTOR,
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--output-summary", type=Path, required=True)
    parser.add_argument("--output-state", type=Path, required=True)
    args = parser.parse_args()

    selection = load(args.selection)
    summary = load(args.third_observation_summary)
    source_state_path = args.third_observation_summary.parent / "diagnostic_state.json"
    source_state = load(source_state_path)
    candidates = selection.get("top5") or []
    if len(candidates) != 5:
        raise ValueError("selection must contain exactly five candidates")
    if summary.get("selection_sha256") != file_sha256(args.selection):
        raise ValueError("third-observation summary is not bound to selection SHA")
    if not (
        source_state.get("selection_sha256") == file_sha256(args.selection)
        and source_state.get("status") == "complete"
        and source_state.get("diagnostic_only") is True
        and int(source_state.get("physical_call_count", -1)) == 5
        and int(source_state.get("valid_replay_count", -1)) == 5
        and int(source_state.get("technical_failure_count", -1)) == 0
        and source_state.get("baseline_restored") is True
        and source_state.get("accepted_update_allowed") is False
        and source_state.get("incumbent_update_allowed") is False
        and source_state.get("range_update_allowed") is False
        and source_state.get("gradient_update_allowed") is False
    ):
        raise ValueError("third-observation diagnostic state is not clean5")
    third_observations = clean5(summary)

    maximum_score_span = finite(
        args.maximum_trusted_score_span,
        "maximum trusted score span",
    )
    adaptive_score_span_cap = finite(
        args.adaptive_score_span_cap,
        "adaptive score span cap",
    )
    maximum_resource_factor = finite(
        args.maximum_trusted_resource_factor,
        "maximum trusted resource factor",
    )
    if not (
        0.0 < maximum_score_span <= adaptive_score_span_cap
        and maximum_resource_factor >= 1.0
    ):
        raise ValueError("invalid stability thresholds")

    rows: List[Dict[str, Any]] = []
    for prior_rank, raw_candidate in enumerate(candidates, 1):
        candidate = copy.deepcopy(raw_candidate)
        if int(candidate.get("rank", -1)) != prior_rank:
            raise ValueError("selection ranks are not contiguous")
        score_values, resources, utilizations = observation_history(
            candidate,
            third_observations[prior_rank],
        )
        score_span = max(score_values) - min(score_values)
        maximum_factor_observed = max(
            factor(resources[name], f"{name} resource") for name in RESOURCES
        )
        resource_stable = maximum_factor_observed <= maximum_resource_factor
        predicted_resource = {
            name: statistics.median(resources[name]) for name in RESOURCES
        }
        predicted_utilization = {
            name: statistics.median(utilizations[name]) for name in RESOURCES
        }
        candidate_score = score_utilization(predicted_utilization)

        old_fingerprint = candidate.get("three_level_fingerprint") or {}
        plan = str(old_fingerprint.get("plan_level") or "")
        feature_level = dict(old_fingerprint.get("feature_ratio_level") or {})
        if not plan or set(feature_level) != set(RESOURCES):
            raise ValueError("candidate three-level fingerprint is incomplete")
        resource_level = quantize(predicted_utilization)
        fingerprint_payload = {
            "plan": plan,
            "feature_ratio_q005": feature_level,
            "resource_utilization_q005": resource_level,
        }
        identifier = f"three-level-sha256:{canonical_sha(fingerprint_payload)}"

        candidate.update(
            {
                "pre_postrobust_rank": prior_rank,
                "pre_postrobust_role": candidate.get("selection_role"),
                "pre_postrobust_candidate_score": candidate.get("candidate_score"),
                "pre_postrobust_predicted_resource": copy.deepcopy(
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
                "resource_prediction_contract": (
                    "exact_plan_three_observation_componentwise_median_v1"
                ),
                "score_prediction_contract": (
                    "componentwise_three_observation_resource_median_rescored_v1"
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
                    "source_fingerprint_id": str(old_fingerprint.get("id") or ""),
                    "reused_for_repeat_validation": True,
                    "resource_formula_audit": {
                        name: {
                            "mode": "exact_plan_three_observation_median",
                            "source_actual_resources": resources[name],
                            "source_actual_utilizations": utilizations[name],
                            "median_resource": predicted_resource[name],
                            "median_utilization": predicted_utilization[name],
                        }
                        for name in RESOURCES
                    },
                },
                "three_observation_stability": {
                    "score_values": score_values,
                    "score_median": statistics.median(score_values),
                    "score_span": score_span,
                    "maximum_resource_factor_observed": maximum_factor_observed,
                    "maximum_resource_factor_allowed": maximum_resource_factor,
                    "resource_stable": resource_stable,
                },
                "prediction_interval": {
                    "lower": min(score_values),
                    "mean": statistics.median(score_values),
                    "upper": max(score_values),
                    "observation_count": 3,
                    "contract": "exact_plan_three_observation_interval_v1",
                },
            }
        )
        rows.append(candidate)

    resource_stable_rows = [
        candidate
        for candidate in rows
        if candidate["three_observation_stability"]["resource_stable"]
    ]
    if len(resource_stable_rows) < 3:
        raise ValueError("fewer than three resource-stable three-observation regimes")
    third_lowest_span = sorted(
        finite(
            candidate["three_observation_stability"]["score_span"],
            "three-observation score span",
        )
        for candidate in resource_stable_rows
    )[2]
    effective_score_span = max(maximum_score_span, third_lowest_span)
    if effective_score_span > adaptive_score_span_cap + 1e-12:
        raise ValueError(
            "three stable regimes require excessive replay-noise span: "
            f"required={effective_score_span:.12g} "
            f"cap={adaptive_score_span_cap:.12g}"
        )

    for candidate in rows:
        stability = candidate["three_observation_stability"]
        stable = bool(
            stability["resource_stable"]
            and finite(stability["score_span"], "score span")
            <= effective_score_span + 1e-12
        )
        stability.update(
            {
                "configured_maximum_score_span": maximum_score_span,
                "effective_maximum_score_span": effective_score_span,
                "adaptive_score_span_cap": adaptive_score_span_cap,
                "adaptive_expansion_applied": (
                    effective_score_span > maximum_score_span + 1e-12
                ),
                "passed": stable,
            }
        )
        candidate["exact_plan_regime_validated"] = stable
        candidate["candidate_trust_basis"] = (
            "exact_plan_three_observation_adaptive_noise_stable"
            if stable
            else "exact_plan_three_observation_unstable_probe"
        )
        candidate["candidate_trust_confidence"] = 1.0 if stable else 0.0
        confidence = dict(candidate.get("confidence") or {})
        confidence.update(
            {
                "measurement_count": 3,
                "score_lower": min(stability["score_values"]),
                "score_mean": statistics.median(stability["score_values"]),
                "score_upper": max(stability["score_values"]),
                "resource_median_rescored": candidate["candidate_score"],
                "actual_score_span": stability["score_span"],
                "maximum_resource_factor": stability[
                    "maximum_resource_factor_observed"
                ],
                "stable_for_trusted": stable,
                "effective_maximum_score_span": effective_score_span,
            }
        )
        candidate["confidence"] = confidence

    stable_rows = [
        candidate
        for candidate in rows
        if candidate["three_observation_stability"]["passed"]
    ]
    if len(stable_rows) < 3:
        raise ValueError("fewer than three stable three-observation regimes")
    stable_rows.sort(
        key=lambda candidate: (
            -finite(candidate["candidate_score"], "candidate score"),
            finite(
                candidate["three_observation_stability"]["score_span"],
                "score span",
            ),
            str(candidate["three_level_fingerprint"]["id"]),
        )
    )
    trusted_ids = {
        candidate["three_level_fingerprint"]["id"]
        for candidate in stable_rows[:3]
    }

    trusted: List[Dict[str, Any]] = []
    probes: List[Dict[str, Any]] = []
    for candidate in rows:
        is_trusted = candidate["three_level_fingerprint"]["id"] in trusted_ids
        candidate.update(
            {
                "selection_role": "trusted" if is_trusted else "probe",
                "role": "trusted" if is_trusted else "probe",
                "acquisition_role": (
                    "trusted_three_observation_postrobust_reseal"
                    if is_trusted
                    else "uncertain_probe_three_observation_postrobust_reseal"
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

    sort_key = lambda candidate: (
        -finite(candidate["candidate_score"], "candidate score"),
        str(candidate["three_level_fingerprint"]["id"]),
    )
    trusted.sort(key=sort_key)
    probes.sort(key=sort_key)
    top5 = trusted + probes
    identifiers = [candidate["three_level_fingerprint"]["id"] for candidate in top5]
    if len(trusted) != 3 or len(probes) != 2 or len(set(identifiers)) != 5:
        raise ValueError("post-robust reseal is not unique trusted3+probe2")
    for rank, candidate in enumerate(top5, 1):
        candidate["rank"] = rank
        candidate["predicted_rank"] = rank

    result = copy.deepcopy(selection)
    result.update(
        {
            "schema_version": "tpch_three_observation_postrobust_reseal_top5_v1",
            "selection_contract": (
                "exact_structural_plan_regime_trusted3_unknown_probe2_top5_v2"
            ),
            "ranking_algorithm_contract": (
                "exact_plan_three_observation_postrobust_reseal_v1"
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
            "repeat_validation_unique_fingerprint_count": 5,
            "repeat_fingerprint_reuse_allowed": True,
            "repeat_fingerprint_reuse_contract": (
                "same_plan_feature_resource_bucket_preserves_identity_v1"
            ),
            "automatic_recovery_mode": "three_observation_postrobust_reseal",
            "automatic_recovery_reason": (
                "third_observation_must_reclassify_stable_regimes_before_gate"
            ),
            "trusted_selection_strategy": (
                "three_observation_stable_regimes_then_median_resource_score_v1"
            ),
            "top5": top5,
            "ranked_candidates": top5,
            "three_observation_postrobust_reseal": {
                "contract": "tpch_three_observation_postrobust_reseal_v1",
                "source_selection": str(args.selection),
                "source_selection_sha256": file_sha256(args.selection),
                "third_observation_summary": str(args.third_observation_summary),
                "third_observation_summary_sha256": file_sha256(
                    args.third_observation_summary
                ),
                "source_physical_calls": 5,
                "new_physical_calls": 0,
                "trusted_count": 3,
                "probe_count": 2,
                "state_neutral": True,
                "qps_used_for_inner_scoring": False,
                "configured_maximum_score_span": maximum_score_span,
                "effective_maximum_score_span": effective_score_span,
                "third_lowest_resource_stable_score_span": third_lowest_span,
                "adaptive_score_span_cap": adaptive_score_span_cap,
                "adaptive_score_span_expansion_applied": (
                    effective_score_span > maximum_score_span + 1e-12
                ),
                "maximum_trusted_resource_factor": maximum_resource_factor,
                "candidate_score_contract": (
                    "componentwise_three_observation_resource_median_rescored_v1"
                ),
            },
        }
    )
    atomic_json(args.output, result)

    resealed_summary = copy.deepcopy(summary)
    resealed_successful = []
    for rank, candidate in enumerate(top5, 1):
        prior_rank = int(candidate["pre_postrobust_rank"])
        observation = copy.deepcopy(third_observations[prior_rank])
        observation["pre_postrobust_rank"] = prior_rank
        observation["rank"] = rank
        resealed_successful.append(observation)
    resealed_summary.update(
        {
            "schema_version": "tpch_postrobust_resealed_diagnostic_summary_v1",
            "selection_sha256": file_sha256(args.output),
            "successful": resealed_successful,
            "postrobust_reseal": {
                "contract": "tpch_three_observation_postrobust_reseal_v1",
                "source_selection_sha256": file_sha256(args.selection),
                "source_summary_sha256": file_sha256(
                    args.third_observation_summary
                ),
                "resealed_selection_sha256": file_sha256(args.output),
                "rank_mapping": [
                    {
                        "rank": rank,
                        "pre_postrobust_rank": int(
                            candidate["pre_postrobust_rank"]
                        ),
                        "three_level_fingerprint": candidate[
                            "three_level_fingerprint"
                        ]["id"],
                    }
                    for rank, candidate in enumerate(top5, 1)
                ],
                "new_physical_calls": 0,
                "state_neutral": True,
            },
        }
    )
    atomic_json(args.output_summary, resealed_summary)

    resealed_state = copy.deepcopy(source_state)
    resealed_state.update(
        {
            "schema_version": "tpch_postrobust_resealed_diagnostic_state_v1",
            "selection_sha256": file_sha256(args.output),
            "postrobust_reseal": {
                "contract": "tpch_three_observation_postrobust_reseal_v1",
                "source_selection_sha256": file_sha256(args.selection),
                "source_summary_sha256": file_sha256(
                    args.third_observation_summary
                ),
                "source_state_sha256": file_sha256(source_state_path),
                "resealed_selection_sha256": file_sha256(args.output),
                "new_physical_calls": 0,
                "state_neutral": True,
            },
        }
    )
    atomic_json(args.output_state, resealed_state)
    print(
        json.dumps(
            {
                "status": "ready",
                "output": str(args.output),
                "output_summary": str(args.output_summary),
                "output_state": str(args.output_state),
                "roles": [candidate["selection_role"] for candidate in top5],
                "prior_ranks": [candidate["pre_postrobust_rank"] for candidate in top5],
                "predicted_scores": [candidate["candidate_score"] for candidate in top5],
                "score_spans": [
                    candidate["three_observation_stability"]["score_span"]
                    for candidate in top5
                ],
                "unique_fingerprints": len(set(identifiers)),
                "physical_workload_calls": 0,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
