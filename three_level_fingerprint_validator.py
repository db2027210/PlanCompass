"""Fail-closed validation of the formal three-level candidate fingerprint."""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Dict, Mapping, Sequence


RESOURCES = ("cpu", "memory", "io")
EXACT_PLAN_RE = re.compile(r"exact-structure-sha256:[0-9a-f]{64}")
RATIO_PLAN_RE = re.compile(r"ratio-plan-sha256:[0-9a-f]{64}")


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _finite_mapping(
    value: Any,
    names: Sequence[str],
    field: str,
) -> Dict[str, float]:
    if not isinstance(value, Mapping) or set(value) < set(names):
        raise ValueError(f"{field} is incomplete")
    result: Dict[str, float] = {}
    for name in names:
        try:
            result[name] = float(value[name])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field}.{name} is not numeric") from exc
        if not math.isfinite(result[name]):
            raise ValueError(f"{field}.{name} is not finite")
    return result


def validate_three_level_candidate(
    candidate: Mapping[str, Any],
    capability: Mapping[str, Any],
) -> Dict[str, Any]:
    """Recompute all three levels from persisted estimator evidence."""

    proof = candidate.get("fingerprint_validation")
    if not isinstance(proof, Mapping):
        raise ValueError("candidate lacks fingerprint_validation evidence")
    if proof.get("resource_estimation_mode") != "ratio_scale":
        raise ValueError("candidate did not use ratio-scale")
    if proof.get("cost_parameter_free_features") is not True:
        raise ValueError("candidate features are not cost-parameter-free")
    if proof.get("qps_used_for_inner_scoring") is not False:
        raise ValueError("candidate does not prove QPS was audit-only")

    query_features = proof.get("query_features")
    if not isinstance(query_features, list) or not query_features:
        raise ValueError("candidate query fingerprint evidence is empty")
    expected_count = proof.get("expected_plannable_query_count")
    if expected_count is not None and int(expected_count) != len(query_features):
        raise ValueError("candidate query fingerprint coverage is incomplete")
    plan_rows = []
    queryids = set()
    for raw in query_features:
        if not isinstance(raw, Mapping):
            raise ValueError("candidate query fingerprint is not an object")
        queryid = raw.get("queryid")
        fingerprint = raw.get("fingerprint")
        if queryid is None or not isinstance(fingerprint, str):
            raise ValueError("candidate query fingerprint is incomplete")
        if EXACT_PLAN_RE.fullmatch(fingerprint) is None:
            raise ValueError("candidate query fingerprint is not exact-structure-sha256")
        queryid_text = str(queryid)
        if queryid_text in queryids:
            raise ValueError("candidate query fingerprint contains duplicate queryid")
        queryids.add(queryid_text)
        plan_rows.append({"queryid": queryid, "fingerprint": fingerprint})

    expected_plan = "ratio-plan-sha256:" + _canonical_sha256(plan_rows)
    declared_plan = str(candidate.get("workload_plan_fingerprint") or "")
    if RATIO_PLAN_RE.fullmatch(declared_plan) is None:
        raise ValueError("candidate lacks ratio-plan-sha256")
    if expected_plan != declared_plan or proof.get("workload_plan_fingerprint") != expected_plan:
        raise ValueError("ratio-plan-sha256 does not match persisted metadata")

    feature_ratio = _finite_mapping(
        candidate.get("feature_ratio"),
        RESOURCES,
        "feature_ratio",
    )
    proof_ratio = _finite_mapping(
        proof.get("feature_ratio"),
        RESOURCES,
        "fingerprint_validation.feature_ratio",
    )
    baseline_real = _finite_mapping(
        proof.get("baseline_real_resource"),
        RESOURCES,
        "fingerprint_validation.baseline_real_resource",
    )
    predicted_resource = _finite_mapping(
        candidate.get("predicted_resource"),
        RESOURCES,
        "predicted_resource",
    )
    capability_values = _finite_mapping(capability, RESOURCES, "capability")
    for name in RESOURCES:
        if capability_values[name] <= 0.0:
            raise ValueError(f"capability.{name} must be positive")
        if not math.isclose(
            feature_ratio[name],
            proof_ratio[name],
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError(f"feature ratio evidence mismatch for {name}")
        expected_resource = baseline_real[name] * proof_ratio[name]
        if not math.isclose(
            predicted_resource[name],
            expected_resource,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise ValueError(
                "candidate resource is not baseline_real_resource * "
                f"cost-independent feature_ratio for {name}"
            )

    feature_level = {
        name: int(round(feature_ratio[name] / 0.005))
        for name in RESOURCES
    }
    utilization_level = {
        name: int(
            round(
                (predicted_resource[name] / capability_values[name]) / 0.005
            )
        )
        for name in RESOURCES
    }
    fingerprint = candidate.get("three_level_fingerprint")
    if not isinstance(fingerprint, Mapping):
        raise ValueError("candidate three-level fingerprint is absent")
    if fingerprint.get("contract") != "plan_feature_resource_three_level_v1":
        raise ValueError("candidate three-level fingerprint contract mismatch")
    if fingerprint.get("valid") is not True:
        raise ValueError("candidate three-level fingerprint is not validated")
    if fingerprint.get("parameter_fallback_used") is not False:
        raise ValueError("candidate three-level fingerprint used parameter fallback")
    if fingerprint.get("plan_level") != expected_plan:
        raise ValueError("candidate plan-level fingerprint mismatch")
    if fingerprint.get("feature_ratio_level") != feature_level:
        raise ValueError("candidate 0.5% feature-ratio quantization mismatch")
    if fingerprint.get("predicted_resource_level") != utilization_level:
        raise ValueError("candidate 0.5pp utilization quantization mismatch")
    fingerprint_payload = {
        "plan": expected_plan,
        "feature_ratio_q005": feature_level,
        "resource_utilization_q005": utilization_level,
    }
    expected_id = "three-level-sha256:" + _canonical_sha256(
        fingerprint_payload
    )
    if fingerprint.get("id") != expected_id:
        raise ValueError("candidate three-level fingerprint digest mismatch")
    return {
        "valid": True,
        "workload_plan_fingerprint": expected_plan,
        "query_count": len(plan_rows),
        "feature_ratio_level": feature_level,
        "predicted_resource_level": utilization_level,
        "id": expected_id,
    }


__all__ = ["validate_three_level_candidate"]
