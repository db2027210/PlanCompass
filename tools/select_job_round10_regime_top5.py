#!/usr/bin/env python3
"""Build a fresh JOB Top-5 using exact validated query-transition regimes."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple


RESOURCES = ("cpu", "io", "memory")
RESOURCE_BASELINE = {"cpu": 3.0, "io": 256.0, "memory": 2.91015625}
SELECTION_CONTRACT = "job_round8_robust_calibrated_trusted_probe_top5_v1"
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


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def finite(value: Any, label: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} is not finite")
    return result


def fingerprint_id(candidate: Mapping[str, Any]) -> str:
    value = (candidate.get("three_level_fingerprint") or {}).get("id")
    if not value:
        raise ValueError("candidate has no three-level fingerprint")
    return str(value)


def signature(candidate: Mapping[str, Any]) -> Mapping[str, Any]:
    value = (
        (candidate.get("final_estimation_metadata") or {})
        .get("plan_signature_calibration") or {}
    ).get("signature")
    if not isinstance(value, Mapping):
        raise ValueError(f"candidate {fingerprint_id(candidate)} has no plan signature")
    return value


def regime_key(candidate: Mapping[str, Any]) -> Tuple[str, ...]:
    transitions = signature(candidate).get("transitions") or []
    return tuple(sorted(str(item["queryid"]) for item in transitions))


def signature_vector(candidate: Mapping[str, Any], queryids: Sequence[str]) -> List[float]:
    transitions = {
        str(item["queryid"]): item.get("normalized_weight_by_resource") or {}
        for item in (signature(candidate).get("transitions") or [])
    }
    return [
        finite((transitions.get(queryid) or {}).get(resource, 0.0), "signature weight")
        for queryid in queryids
        for resource in RESOURCES
    ]


def euclidean(left: Sequence[float], right: Sequence[float]) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right)))


def ensure_utilization(candidate: Dict[str, Any]) -> None:
    if "utilization" in candidate:
        return
    components = candidate.get("gradient_components")
    if not isinstance(components, Mapping):
        raise ValueError("candidate lacks utilization and gradient_components")
    candidate["utilization"] = {
        parameter: {
            resource: finite(values[resource]["utilization"], "utilization")
            for resource in RESOURCES
        }
        for parameter, values in components.items()
    }
    candidate["utilization_source"] = "gradient_components_per_active_parameter"


def validate_raw(candidate: Dict[str, Any]) -> None:
    fingerprint_id(candidate)
    ensure_utilization(candidate)
    if candidate.get("boundary_plateau") is True:
        raise ValueError("corner candidate is forbidden")
    missing = [field for field in REQUIRED_DIAGNOSTICS if field not in candidate]
    if missing:
        raise ValueError(f"candidate lacks diagnostics: {missing}")


def fingerprint_vector(candidate: Mapping[str, Any]) -> List[float]:
    fingerprint = candidate["three_level_fingerprint"]
    feature = fingerprint["feature_ratio_level"]
    resource = fingerprint["predicted_resource_level"]
    values = [finite(feature[name], "feature") for name in RESOURCES]
    values.extend(finite(resource[name], "resource") for name in RESOURCES)
    params = candidate["candidate_params"]
    values.extend(math.log1p(finite(params[name], "param")) for name in sorted(params))
    return values


def normalize(vectors: Sequence[Sequence[float]]) -> List[List[float]]:
    low = [min(column) for column in zip(*vectors)]
    high = [max(column) for column in zip(*vectors)]
    return [
        [
            0.0 if high[index] - low[index] <= 1e-12
            else (value - low[index]) / (high[index] - low[index])
            for index, value in enumerate(vector)
        ]
        for vector in vectors
    ]


def enrich(
    raw: Mapping[str, Any],
    *,
    replay_rank: int,
    role: str,
    ranking_sha: str,
    calibrated_score: float,
    calibrated_resource: Mapping[str, float],
    selection_reason: str,
    validated_regime: Tuple[str, ...] | None,
) -> Dict[str, Any]:
    candidate = copy.deepcopy(dict(raw))
    ensure_utilization(candidate)
    original_rank = int(candidate.get("predicted_rank", candidate.get("rank", 0)))
    candidate.update({
        "rank": replay_rank,
        "predicted_rank": original_rank,
        "raw_candidate_score": finite(candidate["candidate_score"], "candidate_score"),
        "calibrated_candidate_score": calibrated_score,
        "selection_score": calibrated_score,
        "calibrated_predicted_resource": dict(calibrated_resource),
        "calibrated_utilization": {
            resource: finite(calibrated_resource[resource], resource)
            / RESOURCE_BASELINE[resource]
            for resource in RESOURCES
        },
        "selection_role": role,
        "selection_reason": selection_reason,
        "validated_plan_regime": list(validated_regime) if validated_regime else None,
        "validated_plan_regime_exact_match": validated_regime is not None,
        "diagnostic_fields_complete": True,
        "missing_diagnostic_fields": [],
        "diagnostic_only": True,
        "accepted_update_applied": False,
        "gradient_update_applied": False,
        "incumbent_update_applied": False,
        "range_update_applied": False,
        "qps_used_for_inner_scoring": False,
        "candidate_pool_binding": {
            "candidate_id": fingerprint_id(candidate),
            "candidate_rank": original_rank,
            "source_contract": "ranking_analysis.json.ranked_candidates",
            "source_path": "authority/ranking_analysis.json",
            "source_sha256": ranking_sha,
        },
    })
    if role == "probe":
        candidate["used_for_gradient"] = False
        candidate["used_for_inner_incumbent_update"] = False
        candidate["incumbent_update_eligible"] = False
    return candidate


def observed_regimes(
    selection: Mapping[str, Any],
    summary: Mapping[str, Any],
) -> Dict[Tuple[str, ...], Dict[str, Any]]:
    by_rank = {int(item["rank"]): item for item in selection.get("top5") or []}
    rows: Dict[Tuple[str, ...], List[Mapping[str, Any]]] = {}
    for result in summary.get("successful") or []:
        rank = int(result["rank"])
        candidate = by_rank.get(rank)
        if candidate is None or result.get("technical_failure") is not False:
            raise ValueError("round9 result cannot be bound to selection")
        rows.setdefault(regime_key(candidate), []).append(result)
    if len(rows) < 3:
        raise ValueError("fewer than three validated plan regimes")
    output: Dict[Tuple[str, ...], Dict[str, Any]] = {}
    for key, values in rows.items():
        output[key] = {
            "mean_score": sum(finite(item["actual_score"], "actual_score") for item in values)
            / len(values),
            "mean_resource": {
                resource: sum(
                    finite(item["actual_resource"][resource], f"actual_resource.{resource}")
                    for item in values
                ) / len(values)
                for resource in RESOURCES
            },
            "anchors": [by_rank[int(item["rank"])] for item in values],
            "ranks": [int(item["rank"]) for item in values],
        }
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ranking", required=True, type=Path)
    parser.add_argument("--round7-exclusion", required=True, type=Path)
    parser.add_argument("--round9-selection", required=True, type=Path)
    parser.add_argument("--round9-summary", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    ranking = load(args.ranking)
    round9_selection = load(args.round9_selection)
    round9_summary = load(args.round9_summary)
    round7 = load(args.round7_exclusion)
    excluded = {
        str(value)
        for value in round7.get("round7_replayed_three_level_fingerprints") or []
    }
    excluded.update(
        fingerprint_id(candidate) for candidate in round9_selection.get("top5") or []
    )
    if len(excluded) != 10:
        raise ValueError("expected ten distinct prior replay fingerprints")

    raw_candidates = ranking.get("ranked_candidates") or []
    fresh: List[Dict[str, Any]] = []
    seen = set()
    for raw in raw_candidates:
        candidate = copy.deepcopy(dict(raw))
        validate_raw(candidate)
        identifier = fingerprint_id(candidate)
        if identifier in excluded or identifier in seen:
            continue
        seen.add(identifier)
        fresh.append(candidate)
    if len(fresh) < 5:
        raise ValueError("fewer than five fresh candidates")

    regimes = observed_regimes(round9_selection, round9_summary)
    regime_candidates: Dict[Tuple[str, ...], List[Dict[str, Any]]] = {}
    for candidate in fresh:
        regime_candidates.setdefault(regime_key(candidate), []).append(candidate)
    eligible_regimes = [
        key for key in regimes
        if regime_candidates.get(key)
    ]
    eligible_regimes.sort(key=lambda key: regimes[key]["mean_score"], reverse=True)
    if len(eligible_regimes) < 3:
        raise ValueError("fewer than three validated regimes have fresh candidates")

    ranking_sha = sha256(args.ranking)
    trusted: List[Dict[str, Any]] = []
    for replay_rank, key in enumerate(eligible_regimes[:3], 1):
        evidence = regimes[key]
        anchors = evidence["anchors"]
        queryids = list(key)
        anchor_vectors = [signature_vector(anchor, queryids) for anchor in anchors]
        chosen = min(
            regime_candidates[key],
            key=lambda candidate: (
                min(
                    euclidean(signature_vector(candidate, queryids), anchor_vector)
                    for anchor_vector in anchor_vectors
                ),
                -finite(candidate["candidate_score"], "candidate_score"),
                int(candidate.get("predicted_rank", candidate.get("rank", 0))),
            ),
        )
        trusted.append(enrich(
            chosen,
            replay_rank=replay_rank,
            role="trusted",
            ranking_sha=ranking_sha,
            calibrated_score=evidence["mean_score"],
            calibrated_resource=evidence["mean_resource"],
            selection_reason="fresh_exact_validated_query_transition_regime",
            validated_regime=key,
        ))

    trusted_ids = {fingerprint_id(candidate) for candidate in trusted}
    probe_pool = [
        candidate for candidate in fresh
        if fingerprint_id(candidate) not in trusted_ids
        and regime_key(candidate) not in regimes
    ]
    if len(probe_pool) < 2:
        raise ValueError("fewer than two unvalidated-regime probes")
    combined = fresh + trusted
    vectors = normalize([fingerprint_vector(candidate) for candidate in combined])
    vector_by_id = {
        fingerprint_id(candidate): vector
        for candidate, vector in zip(combined, vectors)
    }
    selected: List[Mapping[str, Any]] = list(trusted)
    probes: List[Dict[str, Any]] = []
    while len(probes) < 2:
        remaining = [
            candidate for candidate in probe_pool
            if fingerprint_id(candidate)
            not in {fingerprint_id(item) for item in selected}
        ]
        chosen = max(
            remaining,
            key=lambda candidate: (
                min(
                    euclidean(
                        vector_by_id[fingerprint_id(candidate)],
                        vector_by_id[fingerprint_id(item)],
                    )
                    for item in selected
                ),
                finite(candidate["candidate_score"], "candidate_score"),
                -int(candidate.get("predicted_rank", candidate.get("rank", 0))),
            ),
        )
        raw_resource = {
            resource: finite(chosen["predicted_resource"][resource], resource)
            for resource in RESOURCES
        }
        probe = enrich(
            chosen,
            replay_rank=4 + len(probes),
            role="probe",
            ranking_sha=ranking_sha,
            calibrated_score=finite(chosen["candidate_score"], "candidate_score"),
            calibrated_resource=raw_resource,
            selection_reason="fresh_maximin_unvalidated_query_transition_regime",
            validated_regime=None,
        )
        probes.append(probe)
        selected.append(probe)

    top5 = trusted + probes
    identifiers = [fingerprint_id(candidate) for candidate in top5]
    if len(set(identifiers)) != 5 or any(identifier in excluded for identifier in identifiers):
        raise ValueError("Top-5 is not fresh and unique")
    result = {
        "selection_contract": SELECTION_CONTRACT,
        "diagnostic_batch_contract": "job_round10_exact_query_transition_regime_v1",
        "calibration_contract": "job_exact_validated_query_transition_regime_v1",
        "calibration_source_selection_sha256": sha256(args.round9_selection),
        "calibration_source_summary_sha256": sha256(args.round9_summary),
        "source_ranking_sha256": ranking_sha,
        "source_candidate_count": len(raw_candidates),
        "novel_unique_candidate_count": len(fresh),
        "excluded_prior_fingerprint_count": len(excluded),
        "trusted_count": 3,
        "probe_count": 2,
        "probe_updates_gradient": False,
        "probe_updates_incumbent": False,
        "qps_used_for_inner_scoring": False,
        "qps_role": "outer_audit_only",
        "maximum_physical_workload_calls": 5,
        "serial_real_replay": True,
        "technical_failure_consumes_physical_call_cap": True,
        "technical_failure_counts_as_valid_replay": False,
        "diagnostic_only": True,
        "rejected_updates_state": False,
        "validated_regime_evidence": {
            ",".join(key): {
                "round9_ranks": regimes[key]["ranks"],
                "mean_actual_score": regimes[key]["mean_score"],
                "mean_actual_resource": regimes[key]["mean_resource"],
            }
            for key in regimes
        },
        "top5": top5,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "output": str(args.output),
        "top5": [
            {
                "rank": candidate["rank"],
                "role": candidate["selection_role"],
                "fingerprint": fingerprint_id(candidate),
                "score": candidate["calibrated_candidate_score"],
                "regime": candidate["validated_plan_regime"],
            }
            for candidate in top5
        ],
    }, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
