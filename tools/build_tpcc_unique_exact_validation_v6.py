#!/usr/bin/env python3
"""Build a collision-free TPC-C validation Top-5 from exact clean evidence.

This builder deliberately ignores candidate-carried pseudo histories.  Every
trusted observation is reconstructed from a SHA-pinned clean ledger and is
matched by exact parameters and validated plan fingerprint.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

try:
    import build_tpcc_exact_plan_repeat_validation_v2 as repeat_v2
    from tpcc_score_consistent_representative import (
        score_consistent_representative,
    )
except ModuleNotFoundError:
    from tools import build_tpcc_exact_plan_repeat_validation_v2 as repeat_v2
    from tools.tpcc_score_consistent_representative import (
        score_consistent_representative,
    )


CAP = 5
RESOURCES = ("cpu", "io", "memory")
REPAIR_CONTRACT = "tpcc_postcalibration_unique_exact_evidence_v36"
SELECTION_CONTRACT = "tpcc_robust_exact_plan_regime_trusted3_probe2_v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _params(candidate: Mapping[str, Any]) -> Dict[str, float]:
    raw = candidate.get("params") or candidate.get("candidate_params")
    if not isinstance(raw, Mapping) or not raw:
        raise RuntimeError("candidate is missing exact parameters")
    return {name: float(raw[name]) for name in sorted(raw)}


def _point_key(params: Mapping[str, Any], plan: str) -> str:
    return _canonical_sha256({"params": dict(params), "plan": plan})


def _load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def _score(resource: Mapping[str, Any]) -> float:
    return float(
        repeat_v2.SCORE_CALCULATOR.calculate_utility_score(
            dict(resource), repeat_v2.CAPABILITY
        )
    )


def _quantize(values: Mapping[str, Any], quantum: float = 0.005) -> Dict[str, int]:
    return {
        name: int(round(float(values[name]) / quantum)) for name in RESOURCES
    }


def _make_fingerprint(
    candidate: Mapping[str, Any],
    resource: Mapping[str, Any],
    utilization: Mapping[str, Any],
) -> Dict[str, Any]:
    feature_level = _quantize(candidate["feature_ratio"])
    resource_level = _quantize(utilization)
    payload = {
        "plan": str(candidate["workload_plan_fingerprint"]),
        "feature_ratio_q005": feature_level,
        "resource_utilization_q005": resource_level,
    }
    return {
        "contract": "plan_feature_resource_three_level_v1",
        "id": "three-level-sha256:" + _canonical_sha256(payload),
        "valid": True,
        "validation_errors": [],
        "plan_level": str(candidate["workload_plan_fingerprint"]),
        "feature_ratio_level": feature_level,
        "predicted_resource_level": resource_level,
        "parameter_fallback_used": False,
        "level_quantum": 0.005,
        "predicted_resource_level_semantics": "resource_utilization",
    }


def _source_descriptors(
    selection: Mapping[str, Any],
    selection_path: Path,
    ledger_path: Path,
) -> List[Dict[str, Any]]:
    sources: List[Dict[str, Any]] = []
    for ordinal, raw in enumerate(selection.get("source_batches") or [], 1):
        source_selection = Path(str(raw.get("selection_path") or "")).resolve()
        source_ledger = Path(str(raw.get("ledger_path") or "")).resolve()
        expected_selection_sha = str(raw.get("selection_sha256") or "")
        expected_ledger_sha = str(raw.get("ledger_sha256") or "")
        if (
            not source_selection.is_file()
            or not source_ledger.is_file()
            or _sha256(source_selection) != expected_selection_sha
            or _sha256(source_ledger) != expected_ledger_sha
        ):
            raise RuntimeError(f"evidence source SHA mismatch: ordinal={ordinal}")
        sources.append(
            {
                "name": str(raw.get("name") or f"historical_{ordinal:02d}"),
                "selection_path": str(source_selection),
                "selection_sha256": expected_selection_sha,
                "ledger_path": str(source_ledger),
                "ledger_sha256": expected_ledger_sha,
            }
        )
    sources.append(
        {
            "name": "current_clean_probe_source",
            "selection_path": str(selection_path.resolve()),
            "selection_sha256": _sha256(selection_path.resolve()),
            "ledger_path": str(ledger_path.resolve()),
            "ledger_sha256": _sha256(ledger_path.resolve()),
        }
    )
    unique: List[Dict[str, Any]] = []
    seen = set()
    for source in sources:
        identity = (source["selection_sha256"], source["ledger_sha256"])
        if identity not in seen:
            seen.add(identity)
            unique.append(source)
    return unique


def _validate_clean_batch(
    selection: Mapping[str, Any], ledger: Mapping[str, Any], label: str
) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    candidates = selection.get("top5") or []
    successful = ledger.get("successful") or []
    technical = ledger.get("technical_failures") or []
    if (
        len(candidates) != CAP
        or int(ledger.get("real_workload_invocation_count", -1)) != CAP
        or len(successful) != CAP
        or technical
    ):
        raise RuntimeError(f"evidence batch is not clean 5/5/0: {label}")
    by_rank = {int(item["rank"]): item for item in candidates}
    by_observation_rank = {int(item["rank"]): item for item in successful}
    if set(by_rank) != set(range(1, CAP + 1)) or set(by_rank) != set(by_observation_rank):
        raise RuntimeError(f"evidence rank binding mismatch: {label}")
    result = []
    for rank in range(1, CAP + 1):
        candidate = by_rank[rank]
        observation = by_observation_rank[rank]
        protocol = observation.get("measurement_protocol") or {}
        candidate_params = _params(candidate)
        observation_params = _params(observation)
        candidate_plan = str(candidate.get("workload_plan_fingerprint") or "")
        observation_plan = str(observation.get("workload_plan_fingerprint") or "")
        if (
            protocol.get("stable") is not True
            or protocol.get("correctness_passed") is not True
            or protocol.get("technical_failure") is not False
            or candidate_params != observation_params
            or candidate_plan != observation_plan
            or not candidate_plan.startswith("ratio-plan-sha256:")
            or (candidate.get("three_level_fingerprint") or {}).get("id")
            != (observation.get("three_level_fingerprint") or {}).get("id")
        ):
            raise RuntimeError(f"exact clean evidence mismatch: {label}:rank={rank}")
        result.append((copy.deepcopy(candidate), copy.deepcopy(observation)))
    return result


def reconstruct_exact_pool(
    source_selection: Mapping[str, Any],
    *,
    source_selection_path: Path,
    source_ledger_path: Path,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Reconstruct exact-point histories exclusively from clean ledgers."""
    descriptors = _source_descriptors(
        source_selection, source_selection_path, source_ledger_path
    )
    grouped: Dict[str, Dict[str, Any]] = {}
    for source_ordinal, descriptor in enumerate(descriptors, 1):
        selection_path = Path(descriptor["selection_path"])
        ledger_path = Path(descriptor["ledger_path"])
        batch_selection = _load(selection_path)
        batch_ledger = _load(ledger_path)
        pairs = _validate_clean_batch(
            batch_selection, batch_ledger, descriptor["name"]
        )
        for candidate, observation in pairs:
            params = _params(candidate)
            plan = str(candidate["workload_plan_fingerprint"])
            key = _point_key(params, plan)
            point = grouped.setdefault(
                key,
                {
                    "point_key": key,
                    "params": params,
                    "plan": plan,
                    "template": candidate,
                    "observations": [],
                },
            )
            if point["params"] != params or point["plan"] != plan:
                raise RuntimeError("exact point key collision")
            point["template"] = candidate
            point["observations"].append(
                {
                    "source_ordinal": source_ordinal,
                    "source_name": descriptor["name"],
                    "selection_path": descriptor["selection_path"],
                    "selection_sha256": descriptor["selection_sha256"],
                    "ledger_path": descriptor["ledger_path"],
                    "ledger_sha256": descriptor["ledger_sha256"],
                    "rank": int(observation["rank"]),
                    "actual_score": float(observation["actual_score"]),
                    "actual_resource": {
                        name: float(observation["actual_resource"][name])
                        for name in RESOURCES
                    },
                }
            )

    pool: List[Dict[str, Any]] = []
    for key in sorted(grouped):
        point = grouped[key]
        observations = sorted(
            point["observations"],
            key=lambda row: (row["source_ordinal"], row["rank"]),
        )
        scores = [row["actual_score"] for row in observations]
        resources = [row["actual_resource"] for row in observations]
        representative, robust_score, representative_audit = (
            score_consistent_representative(scores, resources, _score)
        )
        utilization = {
            name: representative[name] / repeat_v2.CAPABILITY[name]
            for name in RESOURCES
        }
        candidate = copy.deepcopy(point["template"])
        candidate.update(
            {
                "params": copy.deepcopy(point["params"]),
                "candidate_score": robust_score,
                "candidate_score_recomputed": robust_score,
                "predicted_score": robust_score,
                "final_score": robust_score,
                "plan_regime_empirical_score": robust_score,
                "robust_acceptance_score": robust_score,
                "formal_lcb_score": min(scores),
                "predicted_score_interval": {
                    "lower": min(scores),
                    "center": robust_score,
                    "upper": max(scores),
                    "source": "exact_clean_ledger_median_v36",
                },
                "predicted_resource": representative,
                "uncalibrated_predicted_resource": copy.deepcopy(representative),
                "final_resource": copy.deepcopy(representative),
                "utilization": utilization,
                "independent_actual_scores": scores,
                "independent_actual_resources": resources,
                "prediction_source": "exact_clean_ledger_score_consistent_median_v36",
                "repeat_prediction_source": "exact_clean_ledger_score_consistent_median_v36",
                "diagnostic_only": True,
                "incumbent_update_eligible": False,
                "used_for_gradient": False,
                "used_for_inner_incumbent_update": False,
                "formal_improvement_eligible": False,
                "boundary_corner": False,
                "exact_evidence_count": len(observations),
                "exact_point_key": key,
                "exact_evidence": observations,
                "score_consistent_representative_audit": representative_audit,
            }
        )
        confidence = copy.deepcopy(candidate.get("confidence") or {})
        confidence.update(
            {
                "exact_plan_regime": 1.0,
                "measurement_count": len(observations),
                "independent_repeat_complete": float(len(observations) >= 2),
                "score_lower": min(scores),
                "score_median": robust_score,
                "score_upper": max(scores),
                "actual_score_span": max(scores) - min(scores),
                "independent_actual_scores": scores,
                "exact_clean_ledger_evidence_only": True,
            }
        )
        candidate["confidence"] = confidence
        calibration = copy.deepcopy(candidate.get("plan_regime_calibration") or {})
        calibration.update(
            {
                "regime": {"plan": point["plan"]},
                "actual_resource": copy.deepcopy(representative),
                "actual_score": robust_score,
                "resource_consistent_score": robust_score,
                "observation_count": len(observations),
                "prediction_source": "exact_clean_ledger_score_consistent_median_v36",
                "trusted_eligible": False,
            }
        )
        candidate["plan_regime_calibration"] = calibration
        old_fingerprint = candidate.get("three_level_fingerprint") or {}
        candidate["three_level_fingerprint"] = _make_fingerprint(
            candidate, representative, utilization
        )
        candidate["three_level_fingerprint"].update(
            {
                "source_fingerprint_id": old_fingerprint.get("id"),
                "repeat_prediction_source": (
                    "exact_clean_ledger_score_consistent_median_v36"
                ),
            }
        )
        candidate["source_provenance"] = {
            "contract": "exact_clean_ledger_point_evidence_v36",
            "exact_point_key": key,
            "parameter_sha256": _canonical_sha256(point["params"]),
            "workload_plan_fingerprint": point["plan"],
            "observation_count": len(observations),
        }
        pool.append(candidate)
    return pool, descriptors


def _distance(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    ranges: Mapping[str, Any],
) -> float:
    left_params = _params(left)
    right_params = _params(right)
    terms = []
    for name in sorted(left_params):
        spec = ranges.get(name) or {}
        span = float(spec.get("max_value", left_params[name])) - float(
            spec.get("min_value", left_params[name])
        )
        denominator = span if span > 0.0 else max(abs(left_params[name]), 1.0)
        terms.append(((left_params[name] - right_params[name]) / denominator) ** 2)
    for name in RESOURCES:
        terms.append(
            (float(left["utilization"][name]) - float(right["utilization"][name]))
            ** 2
        )
    plan_penalty = (
        1.0
        if left["workload_plan_fingerprint"] != right["workload_plan_fingerprint"]
        else 0.0
    )
    return math.sqrt(sum(terms) / max(len(terms), 1)) + plan_penalty


def _maximin(
    candidates: Iterable[Dict[str, Any]],
    count: int,
    ranges: Mapping[str, Any],
    *,
    anchors: Sequence[Dict[str, Any]] = (),
) -> List[Dict[str, Any]]:
    remaining = sorted(
        candidates,
        key=lambda item: (
            -float(item["candidate_score"]),
            str(item["exact_point_key"]),
        ),
    )
    selected: List[Dict[str, Any]] = []
    used_fingerprints = {
        str(item["three_level_fingerprint"]["id"]) for item in anchors
    }
    while len(selected) < count:
        eligible = [
            item
            for item in remaining
            if str(item["three_level_fingerprint"]["id"])
            not in used_fingerprints
        ]
        if not eligible:
            raise RuntimeError("not enough unique post-calibration fingerprints")
        references = [*anchors, *selected]
        if references:
            chosen = max(
                eligible,
                key=lambda item: (
                    min(_distance(item, other, ranges) for other in references),
                    float(item["candidate_score"]),
                    str(item["exact_point_key"]),
                ),
            )
        else:
            chosen = eligible[0]
        selected.append(chosen)
        used_fingerprints.add(str(chosen["three_level_fingerprint"]["id"]))
        remaining = [
            item for item in remaining if item["exact_point_key"] != chosen["exact_point_key"]
        ]
    return selected


def build(
    source_selection: Dict[str, Any],
    *,
    source_selection_path: Path,
    source_ledger_path: Path,
) -> Dict[str, Any]:
    pool, descriptors = reconstruct_exact_pool(
        source_selection,
        source_selection_path=source_selection_path,
        source_ledger_path=source_ledger_path,
    )
    ranges = source_selection.get("candidate_param_ranges") or {}
    trusted_pool = [item for item in pool if int(item["exact_evidence_count"]) >= 2]
    trusted = _maximin(trusted_pool, 3, ranges)
    trusted_keys = {item["exact_point_key"] for item in trusted}

    current_ledger_sha = _sha256(source_ledger_path.resolve())
    unknown = [
        item
        for item in pool
        if item["exact_point_key"] not in trusted_keys
        and int(item["exact_evidence_count"]) < 2
        and any(
            evidence["ledger_sha256"] == current_ledger_sha
            for evidence in item["exact_evidence"]
        )
    ]
    probe_pool = unknown + [
        item
        for item in pool
        if item["exact_point_key"] not in trusted_keys
        and item["exact_point_key"]
        not in {candidate["exact_point_key"] for candidate in unknown}
    ]
    probes = _maximin(probe_pool, 2, ranges, anchors=trusted)

    trusted = sorted(
        trusted,
        key=lambda item: (-float(item["candidate_score"]), item["exact_point_key"]),
    )
    probes = sorted(
        probes,
        key=lambda item: (-float(item["candidate_score"]), item["exact_point_key"]),
    )
    selected = trusted + probes
    for rank, candidate in enumerate(selected, 1):
        role = "trusted" if rank <= 3 else "uncertain_probe"
        evidence_count = int(candidate["exact_evidence_count"])
        if role == "trusted" and evidence_count < 2:
            raise RuntimeError("trusted candidate lacks two exact clean observations")
        candidate["rank"] = rank
        candidate["selection_role"] = role
        candidate["trusted"] = role == "trusted"
        candidate["uncertain_probe"] = role != "trusted"
        candidate["exact_plan_regime_validated"] = role == "trusted"
        candidate["plan_regime_calibration"]["trusted_eligible"] = role == "trusted"
        candidate["probe_kind"] = None if role == "trusted" else "v36_maximin_uncertain_probe"
        candidate["probe_reason"] = (
            None
            if role == "trusted"
            else "held_out_or_unknown_exact_point_after_postcalibration_dedup"
        )

    ids = [item["three_level_fingerprint"]["id"] for item in selected]
    if len(set(ids)) != CAP:
        raise RuntimeError("v36 post-calibration selection is not fingerprint-unique")
    if [item["selection_role"] for item in selected] != [
        "trusted",
        "trusted",
        "trusted",
        "uncertain_probe",
        "uncertain_probe",
    ]:
        raise RuntimeError("v36 role contract mismatch")

    output = copy.deepcopy(source_selection)
    output.update(
        {
            "schema_version": "tpcc_unique_exact_validation_v36",
            "selection_contract": SELECTION_CONTRACT,
            "diagnostic_only": True,
            "diagnostic_role_evaluation_only": True,
            "diagnostic_acquisition_reason": "postcalibration_fingerprint_collapse_repair_v36",
            "maximum_real_workload_invocations": CAP,
            "qps_role": "outer_audit_only_not_inner_score",
            "formal_expected_improvement_required": False,
            "state_mutation_contract": {
                "accepted_update": False,
                "gradient_update": False,
                "incumbent_update": False,
                "range_update": False,
            },
            "trusted_selection_strategy": "exact_clean_evidence_deterministic_maximin_v36",
            "exact_plan_repeat_prediction_source": "exact_clean_ledger_score_consistent_median_v36",
            "independent_repeat_required": True,
            "source_batches": descriptors,
            "trusted_source_ranks": [item["exact_point_key"] for item in trusted],
            "probe_source_ranks": [item["exact_point_key"] for item in probes],
            "top5": selected,
            "v36_repair_contract": {
                "contract": REPAIR_CONTRACT,
                "source_selection_path": str(source_selection_path.resolve()),
                "source_selection_sha256": _sha256(source_selection_path.resolve()),
                "source_ledger_path": str(source_ledger_path.resolve()),
                "source_ledger_sha256": current_ledger_sha,
                "evidence_sources": descriptors,
                "evidence_basis": "exact_parameter_plus_validated_plan_clean_ledgers_only",
                "minimum_trusted_exact_clean_observations": 2,
                "unknown_exact_points_must_be_probes": True,
                "postcalibration_fingerprint_unique": True,
                "postcalibration_fingerprint_ids": ids,
                "deduplication_stage": "after_score_consistent_resource_recalibration",
                "supplement_strategy": "deterministic_maximin_parameter_utilization_plan_v36",
                "candidate_pool_exact_point_count": len(pool),
                "trusted_pool_exact_point_count": len(trusted_pool),
                "selected_exact_point_keys": [item["exact_point_key"] for item in selected],
                "roles": [item["selection_role"] for item in selected],
                "maximum_real_workload_invocations": CAP,
                "qps_role": "outer_audit_only_not_inner_score",
                "state_neutral": True,
                "physical_workload_calls_added": 0,
            },
        }
    )
    output.pop("validation_repeat_contract", None)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--minimum-score-span", type=float, default=0.0)
    args = parser.parse_args()
    if args.minimum_score_span < 0.0:
        raise RuntimeError("minimum score span must be non-negative")
    selection_path = args.selection.resolve()
    ledger_path = args.ledger.resolve()
    result = build(
        _load(selection_path),
        source_selection_path=selection_path,
        source_ledger_path=ledger_path,
    )
    repeat_v2.import_base().atomic_json(args.output.resolve(), result)
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "roles": [item["selection_role"] for item in result["top5"]],
                "fingerprints": [
                    item["three_level_fingerprint"]["id"] for item in result["top5"]
                ],
                "exact_evidence_counts": [
                    item["exact_evidence_count"] for item in result["top5"]
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
