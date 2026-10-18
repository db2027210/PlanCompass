"""Fail-closed three-level fingerprints for MySQL candidate replay evidence.

L1 is the physical plan structure. L2 is measured execution state. L3 is a
scenario-specific, per-query or per-transaction resource vector. QPS is
deliberately forbidden because it belongs only to outer replay acceptance.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


SCHEMA_VERSION = "mysql_candidate_three_level_fingerprint_v1"
L1_VERSION = "mysql_plan_structure_l1_v2"
L2_VERSION = "mysql_execution_state_l2_v1"
L3_VERSION = "mysql_scenario_resource_l3_v1"

L2_FIELDS = (
    "workers",
    "hash_batches",
    "sort_mode",
    "spill_mode",
    "spill_bytes",
    "bitmap_lossy_pages",
    "intermediate_rows",
)

L3_SCENARIO_FIELDS = {
    "job": (
        "memory_peak_bytes",
        "dynamic_execution_memory_bytes",
        "spill_bytes",
    ),
    "tpch": (
        "memory_peak_bytes",
        "dynamic_execution_memory_bytes",
        "spill_bytes",
    ),
    "hybench_xp": (
        "shared_read_blocks",
        "shared_write_blocks",
        "local_read_blocks",
        "local_write_blocks",
        "temp_read_blocks",
        "temp_write_blocks",
        "io_wait_ms",
    ),
    "tpcc": (
        "cpu_time_ms",
        "lock_wait_ms",
        "io_read_bytes",
        "io_write_bytes",
        "io_wait_ms",
        "latency_p50_ms",
        "latency_p95_ms",
        "latency_p99_ms",
    ),
}

L3_SCENARIO_OPTIONAL_FIELDS = {
    "job": ("cpu_time_ms",),
    "tpch": ("cpu_time_ms",),
    "hybench_xp": ("cpu_time_ms", "memory_peak_bytes"),
    "tpcc": ("memory_peak_bytes",),
}

DEFAULT_TOLERANCE = {
    "l2_relative": 0.05,
    "l2_absolute": 1.0,
    "l3_relative": 0.10,
    "l3_absolute": 1e-9,
}


def normalize_scenario(value: Any) -> str:
    scenario = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "job_memory": "job",
        "tpc_h": "tpch",
        "tpch_memory": "tpch",
        "xp": "hybench_xp",
        "hybench_xp_io": "hybench_xp",
        "tpc_c": "tpcc",
        "tpcc_cpu": "tpcc",
    }
    return aliases.get(scenario, scenario)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _parameter_signature(params: Mapping[str, Any]) -> List[List[str]]:
    signature: List[List[str]] = []
    for name in sorted(params):
        value = params[name]
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            encoded = f"{type(value).__module__}.{type(value).__qualname__}:{value!r}"
        else:
            if not math.isfinite(numeric):
                raise ValueError(f"parameter {name!r} is not finite")
            encoded = numeric.hex()
        signature.append([str(name), encoded])
    return signature


def _contains_qps_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized == "qps" or normalized.endswith("_qps"):
                return True
            if _contains_qps_key(item):
                return True
    elif isinstance(value, list):
        return any(_contains_qps_key(item) for item in value)
    return False


def _finite_number(value: Any, *, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not math.isfinite(number) or number < 0.0:
        raise ValueError(f"{label} must be finite and non-negative")
    return number


def _l1_from_bundle(bundle: Any) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    if not isinstance(bundle, Mapping):
        return None, ["missing_l1_plan_bundle"]
    structural = bundle.get("strict_canonical_physical_bundle")
    declared = str(bundle.get("strict_cluster_id_sha256") or "")
    if bundle.get("strict_cluster_usable") is not True or not isinstance(structural, Mapping):
        return None, ["l1_plan_bundle_not_strict_usable"]
    computed = _sha256(structural)
    if declared != computed:
        return None, ["l1_plan_bundle_hash_mismatch"]
    return {
        "version": L1_VERSION,
        "sha256": computed,
        "structure": copy.deepcopy(dict(structural)),
    }, []


def _normalize_l2(evidence: Any) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    if not isinstance(evidence, Mapping) or not evidence:
        return None, ["missing_l2_execution_state_by_query"]
    if _contains_qps_key(evidence):
        return None, ["qps_forbidden_in_l2"]
    normalized: Dict[str, Dict[str, Any]] = {}
    reasons: List[str] = []
    numeric_fields = {
        "workers", "hash_batches", "spill_bytes", "bitmap_lossy_pages",
        "intermediate_rows",
    }
    for query_id in sorted(str(name) for name in evidence):
        row = evidence.get(query_id)
        if not isinstance(row, Mapping):
            reasons.append(f"invalid_l2_query:{query_id}")
            continue
        projected: Dict[str, Any] = {}
        missing = [field for field in L2_FIELDS if field not in row]
        if missing:
            reasons.extend(f"missing_l2_metric:{query_id}:{field}" for field in missing)
            continue
        for field in L2_FIELDS:
            if field in numeric_fields:
                try:
                    projected[field] = _finite_number(
                        row[field], label=f"l2.{query_id}.{field}"
                    )
                except ValueError:
                    reasons.append(f"invalid_l2_metric:{query_id}:{field}")
            else:
                value = str(row[field]).strip().lower()
                if value:
                    projected[field] = value
        if not projected:
            reasons.append(f"empty_l2_query:{query_id}")
        else:
            normalized[query_id] = projected
    if reasons or not normalized:
        return None, sorted(set(reasons or ["empty_l2_execution_state_by_query"]))
    body = {"version": L2_VERSION, "queries": normalized}
    body["sha256"] = _sha256(body)
    return body, []


def _normalize_l3(
    scenario: str, evidence: Any,
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    fields = L3_SCENARIO_FIELDS.get(scenario)
    if fields is None:
        return None, [f"unsupported_l3_scenario:{scenario}"]
    if not isinstance(evidence, Mapping) or not evidence:
        return None, ["missing_l3_resource_vectors"]
    if _contains_qps_key(evidence):
        return None, ["qps_forbidden_in_l3"]
    normalized: Dict[str, Dict[str, float]] = {}
    reasons: List[str] = []
    for unit_id in sorted(str(name) for name in evidence):
        row = evidence.get(unit_id)
        if not isinstance(row, Mapping):
            reasons.append(f"invalid_l3_unit:{unit_id}")
            continue
        missing = [field for field in fields if field not in row]
        if missing:
            reasons.extend(f"missing_l3_metric:{unit_id}:{field}" for field in missing)
            continue
        projected: Dict[str, float] = {}
        optional_fields = L3_SCENARIO_OPTIONAL_FIELDS.get(scenario, ())
        for field in (*fields, *optional_fields):
            if field not in row:
                continue
            try:
                projected[field] = _finite_number(
                    row[field], label=f"l3.{unit_id}.{field}"
                )
            except ValueError:
                reasons.append(f"invalid_l3_metric:{unit_id}:{field}")
        if all(field in projected for field in fields):
            normalized[unit_id] = projected
    if reasons or not normalized:
        return None, sorted(set(reasons or ["empty_l3_resource_vectors"]))
    body = {
        "version": L3_VERSION,
        "scenario": scenario,
        "unit_kind": "transaction_type" if scenario == "tpcc" else "query",
        "primary_metrics": list(fields),
        "vectors": normalized,
        "qps_included": False,
    }
    body["sha256"] = _sha256(body)
    return body, []


def build_three_level_fingerprint(
    *,
    scenario: Any,
    params: Mapping[str, Any],
    plan_bundle: Any,
    execution_state_by_query: Any,
    resource_vectors: Any,
) -> Dict[str, Any]:
    normalized_scenario = normalize_scenario(scenario)
    parameter_signature = _parameter_signature(params)
    l1, l1_reasons = _l1_from_bundle(plan_bundle)
    l2, l2_reasons = _normalize_l2(execution_state_by_query)
    l3, l3_reasons = _normalize_l3(normalized_scenario, resource_vectors)
    reasons = [*l1_reasons, *l2_reasons, *l3_reasons]
    usable = not reasons
    identity = {
        "parameter_signature": parameter_signature,
        "l1_sha256": (l1 or {}).get("sha256"),
        "l2_sha256": (l2 or {}).get("sha256"),
        "l3_sha256": (l3 or {}).get("sha256"),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "scenario": normalized_scenario,
        "parameter_signature": parameter_signature,
        "l1": l1,
        "l2": l2,
        "l3": l3,
        "true_duplicate_usable": usable,
        "true_duplicate_ineligible_reasons": sorted(set(reasons)),
        "exact_identity_sha256": _sha256(identity) if usable else None,
        "qps_role": "outer_acceptance_only",
        "qps_in_fingerprint": False,
    }


def _same_shape(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    if set(left) != set(right):
        return False
    for key in left:
        left_value = left[key]
        right_value = right[key]
        if isinstance(left_value, Mapping) and isinstance(right_value, Mapping):
            if not _same_shape(left_value, right_value):
                return False
        elif isinstance(left_value, Mapping) or isinstance(right_value, Mapping):
            return False
    return True


def _approximately_equal(
    left: Any, right: Any, *, relative: float, absolute: float,
) -> bool:
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        if not _same_shape(left, right):
            return False
        return all(
            _approximately_equal(
                left[key], right[key], relative=relative, absolute=absolute,
            )
            for key in left
        )
    if isinstance(left, (int, float)) and not isinstance(left, bool):
        if not isinstance(right, (int, float)) or isinstance(right, bool):
            return False
        return math.isclose(float(left), float(right), rel_tol=relative, abs_tol=absolute)
    return left == right


def _l2_approximately_equal(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    *,
    relative: float,
    absolute: float,
) -> bool:
    if set(left) != set(right):
        return False
    exact_fields = {"workers", "hash_batches", "sort_mode", "spill_mode"}
    approximate_fields = {"spill_bytes", "bitmap_lossy_pages", "intermediate_rows"}
    for query_id in left:
        left_row = left[query_id]
        right_row = right[query_id]
        if not isinstance(left_row, Mapping) or not isinstance(right_row, Mapping):
            return False
        if set(left_row) != set(right_row):
            return False
        if any(left_row.get(field) != right_row.get(field) for field in exact_fields):
            return False
        if any(
            not _approximately_equal(
                left_row.get(field),
                right_row.get(field),
                relative=relative,
                absolute=absolute,
            )
            for field in approximate_fields
        ):
            return False
    return True


def fingerprints_are_true_duplicates(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    *,
    tolerance: Optional[Mapping[str, Any]] = None,
) -> Tuple[bool, List[str]]:
    cfg = dict(DEFAULT_TOLERANCE)
    cfg.update(dict(tolerance or {}))
    reasons: List[str] = []
    if left.get("true_duplicate_usable") is not True:
        reasons.append("left_fingerprint_ineligible")
    if right.get("true_duplicate_usable") is not True:
        reasons.append("right_fingerprint_ineligible")
    if reasons:
        return False, reasons
    if left.get("parameter_signature") != right.get("parameter_signature"):
        reasons.append("parameter_signature_differs")
    if (left.get("l1") or {}).get("sha256") != (right.get("l1") or {}).get("sha256"):
        reasons.append("l1_structure_differs")
    left_l2 = ((left.get("l2") or {}).get("queries") or {})
    right_l2 = ((right.get("l2") or {}).get("queries") or {})
    if not _l2_approximately_equal(
        left_l2,
        right_l2,
        relative=float(cfg["l2_relative"]),
        absolute=float(cfg["l2_absolute"]),
    ):
        reasons.append("l2_execution_state_differs")
    left_l3 = ((left.get("l3") or {}).get("vectors") or {})
    right_l3 = ((right.get("l3") or {}).get("vectors") or {})
    if not _approximately_equal(
        left_l3,
        right_l3,
        relative=float(cfg["l3_relative"]),
        absolute=float(cfg["l3_absolute"]),
    ):
        reasons.append("l3_resource_behavior_differs")
    return not reasons, reasons


def candidate_fingerprint_inputs(candidate: Mapping[str, Any]) -> Dict[str, Any]:
    validation = candidate.get("candidate_final_validation")
    validation = validation if isinstance(validation, Mapping) else {}
    evidence = validation.get("three_level_fingerprint_evidence")
    evidence = evidence if isinstance(evidence, Mapping) else {}
    bundle = candidate.get("canonical_plan_bundle")
    if not isinstance(bundle, Mapping):
        metadata = candidate.get("final_estimation_metadata")
        if isinstance(metadata, Mapping):
            bundle = metadata.get("canonical_plan_bundle") or metadata.get("plan_bundle")
    if not isinstance(bundle, Mapping):
        plan_capture = validation.get("plan_capture")
        if isinstance(plan_capture, Mapping):
            bundle = plan_capture.get("canonical_plan_bundle")
    return {
        "params": candidate.get("candidate_optimized_params")
        or candidate.get("optimized_params")
        or {},
        "plan_bundle": bundle,
        "execution_state_by_query": evidence.get("execution_state_by_query"),
        "resource_vectors": evidence.get("resource_vectors"),
    }


def build_candidate_fingerprint(
    candidate: Mapping[str, Any], scenario: Any,
) -> Dict[str, Any]:
    inputs = candidate_fingerprint_inputs(candidate)
    return build_three_level_fingerprint(scenario=scenario, **inputs)


__all__ = [
    "DEFAULT_TOLERANCE",
    "L2_FIELDS",
    "L3_SCENARIO_FIELDS",
    "L3_SCENARIO_OPTIONAL_FIELDS",
    "SCHEMA_VERSION",
    "build_candidate_fingerprint",
    "build_three_level_fingerprint",
    "candidate_fingerprint_inputs",
    "fingerprints_are_true_duplicates",
    "normalize_scenario",
]
