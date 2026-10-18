#!/usr/bin/env python3
"""Select a fresh diagnostic-only JOB Top-5 from the sealed round-7 ranking."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Set


RESOURCES = ("cpu", "io", "memory")
RESOURCE_BASELINE = {"cpu": 3.0, "io": 256.0, "memory": 2.91015625}
REQUIRED_DIAGNOSTICS = (
    "feature_ratio",
    "predicted_resource",
    "utilization",
    "dScore/dU",
    "T",
    "confidence",
    "g_j",
    "eta",
    "delta_param",
    "candidate_score",
)
SELECTION_CONTRACT = "job_round8_robust_calibrated_trusted_probe_top5_v1"


def load_object(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def finite(value: Any, label: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def fingerprint_id(candidate: Mapping[str, Any]) -> str:
    fingerprint = candidate.get("three_level_fingerprint")
    if not isinstance(fingerprint, Mapping) or not fingerprint.get("id"):
        raise ValueError("candidate is missing three_level_fingerprint.id")
    return str(fingerprint["id"])


def directional_band_score(utilization: float, weight: float, target: bool) -> float:
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


def calibrated_score(utilization: Mapping[str, float]) -> float:
    return sum(
        directional_band_score(utilization[resource], weight, resource == "memory")
        for resource, weight in (("cpu", 1.0), ("io", 3.0), ("memory", 1.0))
    )


def enrich(
    raw: Mapping[str, Any],
    *,
    replay_rank: int,
    role: str,
    ranking_sha256: str,
    scales: Mapping[str, float],
    uncertainty_penalty: float,
    score_deadband: float,
    reason: str,
) -> Dict[str, Any]:
    candidate = copy.deepcopy(dict(raw))
    if "utilization" not in candidate:
        gradient_components = candidate.get("gradient_components")
        if not isinstance(gradient_components, Mapping):
            raise ValueError("candidate lacks utilization and gradient_components")
        candidate["utilization"] = {
            parameter: {
                resource: finite(
                    components[resource]["utilization"],
                    f"gradient_components.{parameter}.{resource}.utilization",
                )
                for resource in RESOURCES
            }
            for parameter, components in gradient_components.items()
        }
        candidate["utilization_source"] = (
            "gradient_components_per_active_parameter"
        )
    missing = [field for field in REQUIRED_DIAGNOSTICS if field not in candidate]
    if missing:
        raise ValueError(f"candidate is missing diagnostics: {missing}")
    predicted = candidate.get("predicted_resource")
    if not isinstance(predicted, Mapping):
        raise ValueError("predicted_resource must be an object")
    calibrated_resource = {
        resource: finite(predicted[resource], f"predicted_resource.{resource}")
        * finite(scales[resource], f"scale.{resource}")
        for resource in RESOURCES
    }
    utilization = {
        resource: calibrated_resource[resource] / RESOURCE_BASELINE[resource]
        for resource in RESOURCES
    }
    identifier = fingerprint_id(candidate)
    original_rank = int(candidate.get("predicted_rank", candidate.get("rank", 0)))
    if original_rank <= 0:
        raise ValueError("candidate rank must be positive")
    score = calibrated_score(utilization)
    candidate.update(
        {
            "rank": replay_rank,
            "predicted_rank": original_rank,
            "raw_candidate_score": finite(
                candidate["candidate_score"], "candidate_score"
            ),
            "calibrated_predicted_resource": calibrated_resource,
            "calibrated_utilization": utilization,
            "calibrated_candidate_score": score,
            "calibration_uncertainty_penalty": uncertainty_penalty,
            "calibration_score_deadband": score_deadband,
            "selection_score": score - uncertainty_penalty,
            "selection_role": role,
            "selection_reason": reason,
            "diagnostic_fields_complete": True,
            "missing_diagnostic_fields": [],
            "candidate_pool_binding": {
                "candidate_id": identifier,
                "candidate_rank": original_rank,
                "source_contract": "ranking_analysis.json.ranked_candidates",
                "source_path": "authority/ranking_analysis.json",
                "source_sha256": ranking_sha256,
            },
            "qps_used_for_inner_scoring": False,
        }
    )
    if role == "probe":
        candidate["used_for_gradient"] = False
        candidate["used_for_inner_incumbent_update"] = False
        candidate["incumbent_update_eligible"] = False
    return candidate


def fingerprint_vector(candidate: Mapping[str, Any]) -> List[float]:
    fingerprint = candidate["three_level_fingerprint"]
    feature = fingerprint["feature_ratio_level"]
    resource = fingerprint["predicted_resource_level"]
    values = [
        finite(feature[name], f"feature.{name}") for name in RESOURCES
    ] + [finite(resource[name], f"resource.{name}") for name in RESOURCES]
    params = candidate["candidate_params"]
    values.extend(math.log1p(finite(params[name], f"params.{name}")) for name in sorted(params))
    return values


def normalize(vectors: Sequence[Sequence[float]]) -> List[List[float]]:
    width = len(vectors[0])
    low = [min(vector[index] for vector in vectors) for index in range(width)]
    high = [max(vector[index] for vector in vectors) for index in range(width)]
    return [
        [
            0.0
            if high[index] - low[index] <= 1e-12
            else (vector[index] - low[index]) / (high[index] - low[index])
            for index in range(width)
        ]
        for vector in vectors
    ]


def distance(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    left_vector: Sequence[float],
    right_vector: Sequence[float],
) -> float:
    numeric = math.sqrt(
        sum((a - b) ** 2 for a, b in zip(left_vector, right_vector))
    )
    left_plan = left["three_level_fingerprint"]["plan_level"]
    right_plan = right["three_level_fingerprint"]["plan_level"]
    return numeric + (1.0 if left_plan != right_plan else 0.0)


def excluded_ids(round8: Mapping[str, Any], earlier: Set[str]) -> Set[str]:
    candidates = round8.get("top5")
    if not isinstance(candidates, list) or len(candidates) != 5:
        raise ValueError("round8 selection must contain five attempted candidates")
    return earlier | {fingerprint_id(candidate) for candidate in candidates}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ranking", type=Path, required=True)
    parser.add_argument("--round8-selection", type=Path, required=True)
    parser.add_argument("--round7-exclusion", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    ranking = load_object(args.ranking)
    prior = load_object(args.round8_selection)
    exclusion = load_object(args.round7_exclusion)
    earlier_values = exclusion.get("round7_replayed_three_level_fingerprints")
    if not isinstance(earlier_values, list) or len(set(earlier_values)) != 5:
        raise ValueError("round7 exclusion must contain five unique fingerprints")
    excluded = excluded_ids(prior, {str(value) for value in earlier_values})

    raw_candidates = ranking.get("ranked_candidates")
    if not isinstance(raw_candidates, list):
        raise ValueError("ranking lacks ranked_candidates")
    fresh: List[Mapping[str, Any]] = []
    seen: Set[str] = set()
    for candidate in raw_candidates:
        if candidate.get("boundary_plateau") is True:
            continue
        identifier = fingerprint_id(candidate)
        if identifier in excluded or identifier in seen:
            continue
        seen.add(identifier)
        fresh.append(candidate)
    if len(fresh) < 5:
        raise ValueError("fewer than five fresh non-corner fingerprints remain")

    calibration = prior.get("resource_calibration")
    if not isinstance(calibration, Mapping):
        raise ValueError("prior selection lacks resource calibration")
    scales = calibration.get("effective_scale_by_resource")
    if not isinstance(scales, Mapping):
        raise ValueError("resource calibration lacks scales")
    score_deadband = finite(prior["score_deadband"], "score_deadband")
    uncertainty_penalty = finite(
        prior["top5"][0]["calibration_uncertainty_penalty"],
        "uncertainty_penalty",
    )

    ranked_enriched = [
        enrich(
            candidate,
            replay_rank=1,
            role="trusted",
            ranking_sha256=sha256_file(args.ranking),
            scales=scales,
            uncertainty_penalty=uncertainty_penalty,
            score_deadband=score_deadband,
            reason="fresh_calibrated_trusted_candidate",
        )
        for candidate in fresh
    ]
    ranked_enriched.sort(
        key=lambda candidate: (
            -candidate["selection_score"],
            candidate["predicted_rank"],
            fingerprint_id(candidate),
        )
    )
    trusted = ranked_enriched[:3]

    probe_pool = ranked_enriched[3:]
    combined = trusted + probe_pool
    vectors = normalize([fingerprint_vector(candidate) for candidate in combined])
    vector_by_id = {
        fingerprint_id(candidate): vector
        for candidate, vector in zip(combined, vectors)
    }
    selected: List[Mapping[str, Any]] = list(trusted)
    probes: List[Mapping[str, Any]] = []
    while len(probes) < 2:
        remaining = [
            candidate
            for candidate in probe_pool
            if fingerprint_id(candidate)
            not in {fingerprint_id(item) for item in selected}
        ]
        chosen = max(
            remaining,
            key=lambda candidate: (
                min(
                    distance(
                        candidate,
                        item,
                        vector_by_id[fingerprint_id(candidate)],
                        vector_by_id[fingerprint_id(item)],
                    )
                    for item in selected
                ),
                candidate["selection_score"],
                -candidate["predicted_rank"],
            ),
        )
        probes.append(chosen)
        selected.append(chosen)

    top5: List[Dict[str, Any]] = []
    for replay_rank, candidate in enumerate(trusted + probes, 1):
        role = "trusted" if replay_rank <= 3 else "probe"
        top5.append(
            enrich(
                candidate,
                replay_rank=replay_rank,
                role=role,
                ranking_sha256=sha256_file(args.ranking),
                scales=scales,
                uncertainty_penalty=uncertainty_penalty,
                score_deadband=score_deadband,
                reason=(
                    "fresh_calibrated_trusted_candidate"
                    if role == "trusted"
                    else "fresh_maximin_uncertain_probe"
                ),
            )
        )

    identifiers = [fingerprint_id(candidate) for candidate in top5]
    if len(set(identifiers)) != 5 or any(item in excluded for item in identifiers):
        raise ValueError("selected Top-5 is not fresh and unique")
    if any(candidate.get("boundary_plateau") is True for candidate in top5):
        raise ValueError("corner candidate selected")

    result = {
        "selection_contract": SELECTION_CONTRACT,
        "diagnostic_batch_contract": "job_round9_diagnostic_only_top5_v1",
        "calibration_contract": prior.get("calibration_contract"),
        "resource_calibration": calibration,
        "score_deadband": score_deadband,
        "score_deadband_method": prior.get("score_deadband_method"),
        "score_deadband_calibration": prior.get("score_deadband_calibration"),
        "calibration_source_selection_sha256": sha256_file(args.round8_selection),
        "source_candidate_count": len(raw_candidates),
        "novel_unique_candidate_count": len(fresh),
        "excluded_prior_fingerprint_count": len(excluded),
        "trusted_count": 3,
        "probe_count": 2,
        "probe_updates_gradient": False,
        "probe_updates_incumbent": False,
        "diagnostic_updates_formal_state": False,
        "qps_used_for_inner_scoring": False,
        "top5": top5,
        "ranked_candidates": top5,
    }
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": "ready",
                "top5_ids": identifiers,
                "trusted_scores": [
                    candidate["calibrated_candidate_score"] for candidate in top5[:3]
                ],
            },
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
