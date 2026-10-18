"""Strict MySQL physical-plan bundles and replay-cluster selection.

The strict fingerprint is deliberately narrower than the saved EXPLAIN: costs,
cardinality estimates and row counts remain in ``raw_explain`` for audit, but
cannot affect a plan-cluster id.  An approximate fingerprint is also emitted
for diagnostics and is never accepted as a clustering key.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from typing import Any, Dict, List, Mapping, Optional, Sequence

from mysql_three_level_fingerprint import (
    build_candidate_fingerprint,
    fingerprints_are_true_duplicates,
)


SCHEMA_VERSION = "mysql_strict_physical_plan_bundle_v2"
STRICT_FINGERPRINT_VERSION = "mysql_plan_structure_l1_v2"
APPROXIMATE_FINGERPRINT_VERSION = "mysql_approximate_plan_audit_v1"
FORMAL_REPLAY_CAPS = {
    "hybench_ap": 5,
    "ap": 5,
    "tpcc": 5,
    "job": 5,
    "tpch": 5,
    "hybench_xp": 5,
    "xp": 5,
}

# Numeric optimizer estimates are intentionally absent.  Wrapper/node keys are
# retained so ordered subtrees and join order survive the projection.
_STRICT_SCALAR_KEYS = frozenset(
    {
        "select_id",
        "table_name",
        "access_type",
        "key",
        "used_key_parts",
        "ref",
        "join_type",
        "join_algorithm",
        "node_type",
        "operator",
        "operation",
        "scan_type",
        "using_index",
        "using_index_for_group_by",
        "using_join_buffer",
        "using_temporary_table",
        "using_filesort",
        "first_match",
        "dependent",
        "cacheable",
        "materialized",
        "parallel",
        "parallel_aware",
        "using_parallel",
        "message",
    }
)
_STRUCTURAL_WRAPPERS = frozenset(
    {
        "query_block",
        "table",
        "nested_loop",
        "hash_join",
        "merge_join",
        "grouping_operation",
        "ordering_operation",
        "duplicates_removal",
        "buffer_result",
        "materialized_from_subquery",
        "attached_subqueries",
        "optimized_away_subqueries",
        "union_result",
        "query_specifications",
        "windowing",
        "windows",
    }
)
_COST_OR_ROWS_KEYS = frozenset(
    {
        "cost_info",
        "query_cost",
        "read_cost",
        "eval_cost",
        "prefix_cost",
        "sort_cost",
        "data_read_per_join",
        "rows_examined_per_scan",
        "rows_produced_per_join",
        "rows",
        "estimated_rows",
        "filtered",
    }
)


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


def strict_physical_projection(value: Any) -> Any:
    """Project Node/Join/table/index/scan/parallel structure, preserving order."""

    if isinstance(value, list):
        return [strict_physical_projection(item) for item in value]
    if not isinstance(value, Mapping):
        return copy.deepcopy(value)
    projected: Dict[str, Any] = {}
    for raw_key, item in value.items():
        key = str(raw_key)
        if key in _COST_OR_ROWS_KEYS:
            continue
        if key in _STRICT_SCALAR_KEYS:
            projected[key] = strict_physical_projection(item)
            continue
        if key in _STRUCTURAL_WRAPPERS:
            projected[key] = strict_physical_projection(item)
            continue
        # Unknown containers may be future physical node wrappers.  Retain the
        # wrapper only when a descendant contains a known physical attribute;
        # scalar predicates and numeric estimates are excluded.
        if isinstance(item, (Mapping, list)):
            child = strict_physical_projection(item)
            if _contains_physical_signal(child):
                projected[key] = child
    return projected


def _contains_physical_signal(value: Any) -> bool:
    if isinstance(value, list):
        return any(_contains_physical_signal(item) for item in value)
    if not isinstance(value, Mapping):
        return False
    if any(key in _STRICT_SCALAR_KEYS or key in _STRUCTURAL_WRAPPERS for key in value):
        return True
    return any(_contains_physical_signal(item) for item in value.values())


def _approximate_projection(value: Any) -> Any:
    if isinstance(value, list):
        return [_approximate_projection(item) for item in value]
    if not isinstance(value, Mapping):
        return copy.deepcopy(value)
    result: Dict[str, Any] = {}
    for key, item in value.items():
        if key in _COST_OR_ROWS_KEYS:
            continue
        if key in {"table_name", "access_type", "key", "nested_loop", "hash_join", "merge_join"}:
            result[str(key)] = _approximate_projection(item)
        elif isinstance(item, (Mapping, list)):
            child = _approximate_projection(item)
            if child not in ({}, []):
                result[str(key)] = child
    return result


def build_canonical_plan_bundle(
    query_captures: Sequence[Mapping[str, Any]],
    *,
    registry_query_ids: Sequence[str],
    registry_sha256: str = "",
    registry_authority_verified: bool = False,
) -> Dict[str, Any]:
    """Build a complete bundle or mark it ineligible for strict clustering.

    Each capture requires ``query_id``, ``binding_id``, ``plan_mode`` and the
    full ``raw_explain`` object.  Static SQL uses one ``static`` binding and
    ``custom`` mode.  Dynamic SQL must declare the same non-empty
    ``required_binding_ids`` and ``required_plan_modes`` on every capture.
    MySQL dynamic SQL may explicitly declare that generic-plan semantics are
    unavailable.  In that case every representative custom plan is still
    required and preserved, but the bundle is ineligible for strict clustering.
    """

    registry = [str(value) for value in registry_query_ids]
    reasons: List[str] = []
    if registry_authority_verified is not True:
        reasons.append("registry_authority_not_verified")
    registry_digest = str(registry_sha256 or "").lower()
    if len(registry_digest) != 64 or any(character not in "0123456789abcdef" for character in registry_digest):
        reasons.append("missing_or_invalid_registry_sha256")
    if not registry or len(set(registry)) != len(registry):
        reasons.append("invalid_or_duplicate_registry_query_ids")
    grouped: Dict[str, List[Dict[str, Any]]] = {query_id: [] for query_id in registry}
    for index, raw_capture in enumerate(query_captures):
        if not isinstance(raw_capture, Mapping):
            reasons.append(f"capture_{index}_not_mapping")
            continue
        query_id = str(raw_capture.get("query_id") or "")
        binding_id = str(raw_capture.get("binding_id") or "")
        plan_mode = str(raw_capture.get("plan_mode") or "")
        raw_plan = raw_capture.get("raw_explain")
        if query_id not in grouped:
            reasons.append(f"unregistered_query:{query_id}")
            continue
        sql_sha = str(raw_capture.get("query_sql_sha256") or "")
        query_order = raw_capture.get("query_order")
        query_weight = raw_capture.get("query_weight")
        if (
            not binding_id
            or plan_mode not in {"generic", "custom"}
            or not isinstance(raw_plan, Mapping)
            or len(sql_sha) != 64
            or any(character not in "0123456789abcdef" for character in sql_sha.lower())
            or isinstance(query_order, bool)
            or not isinstance(query_order, int)
            or query_order < 1
            or isinstance(query_weight, bool)
            or not isinstance(query_weight, (int, float))
            or not math.isfinite(float(query_weight))
            or float(query_weight) <= 0.0
        ):
            reasons.append(f"invalid_capture:{query_id}:{binding_id}:{plan_mode}")
            continue
        structural = strict_physical_projection(raw_plan)
        if not _contains_physical_signal(structural):
            reasons.append(f"missing_physical_fingerprint:{query_id}:{binding_id}:{plan_mode}")
            continue
        grouped[query_id].append(
            {
                "query_id": query_id,
                "query_sql_sha256": sql_sha.lower(),
                "query_order": query_order,
                "query_weight": float(query_weight),
                "dynamic": raw_capture.get("dynamic") is True,
                "generic_plan_semantics_available": raw_capture.get(
                    "generic_plan_semantics_available"
                ),
                "binding_id": binding_id,
                "binding_values": copy.deepcopy(raw_capture.get("binding_values")),
                "plan_mode": plan_mode,
                "raw_explain": copy.deepcopy(dict(raw_plan)),
                "raw_explain_sha256": _sha256(raw_plan),
                "strict_physical_projection": structural,
                "strict_fingerprint_sha256": _sha256(structural),
                "approximate_fingerprint_sha256": _sha256(_approximate_projection(raw_plan)),
                "required_binding_ids": [str(v) for v in raw_capture.get("required_binding_ids") or []],
                "required_plan_modes": [str(v) for v in raw_capture.get("required_plan_modes") or []],
            }
        )

    ordered: List[Dict[str, Any]] = []
    strict_cluster_rows: List[Dict[str, Any]] = []
    for query_id in registry:
        captures = grouped.get(query_id) or []
        if not captures:
            reasons.append(f"missing_query_capture:{query_id}")
            continue
        dynamic_flags = {row["dynamic"] for row in captures}
        if len(dynamic_flags) != 1:
            reasons.append(f"inconsistent_dynamic_flag:{query_id}")
            continue
        dynamic = next(iter(dynamic_flags))
        if len({row["query_sql_sha256"] for row in captures}) != 1:
            reasons.append(f"inconsistent_query_sql_sha256:{query_id}")
        if len({row["query_order"] for row in captures}) != 1:
            reasons.append(f"inconsistent_query_order:{query_id}")
        if len({float(row["query_weight"]).hex() for row in captures}) != 1:
            reasons.append(f"inconsistent_query_weight:{query_id}")
        if captures[0]["query_order"] != registry.index(query_id) + 1:
            reasons.append(f"query_order_mismatch:{query_id}")
        if dynamic:
            binding_declarations = {tuple(row["required_binding_ids"]) for row in captures}
            mode_declarations = {tuple(row["required_plan_modes"]) for row in captures}
            if len(binding_declarations) != 1 or len(mode_declarations) != 1:
                reasons.append(f"inconsistent_dynamic_coverage_declaration:{query_id}")
            required_bindings = set(next(iter(binding_declarations), ()))
            required_modes = set(next(iter(mode_declarations), ()))
            if not required_bindings or required_modes != {"generic", "custom"}:
                reasons.append(f"dynamic_generic_custom_coverage_not_declared:{query_id}")
            semantics_declarations = {
                row["generic_plan_semantics_available"] for row in captures
            }
            if not semantics_declarations.issubset({True, False}) or len(semantics_declarations) != 1:
                reasons.append(f"inconsistent_generic_plan_semantics_declaration:{query_id}")
                effective_modes = required_modes
            elif semantics_declarations == {False}:
                reasons.append(f"generic_plan_semantics_unavailable:{query_id}")
                effective_modes = {"custom"}
            else:
                effective_modes = required_modes
        else:
            required_bindings, required_modes = {"static"}, {"custom"}
            effective_modes = required_modes
        observed = {(row["binding_id"], row["plan_mode"]) for row in captures}
        expected = {(binding, mode) for binding in required_bindings for mode in effective_modes}
        if observed != expected:
            reasons.append(f"incomplete_binding_or_plan_mode_coverage:{query_id}")
        if len(observed) != len(captures):
            reasons.append(f"duplicate_binding_plan_mode_capture:{query_id}")
        if dynamic and any(row["binding_values"] is None for row in captures):
            reasons.append(f"missing_dynamic_binding_values:{query_id}")
        captures.sort(key=lambda row: (row["binding_id"], row["plan_mode"]))
        ordered.extend(captures)
        strict_cluster_rows.append(
            {
                "query_id": query_id,
                "query_sql_sha256": captures[0]["query_sql_sha256"],
                "query_order": captures[0]["query_order"],
                "query_weight_hex": float(captures[0]["query_weight"]).hex(),
                "variants": [
                    {
                        "binding_id": row["binding_id"],
                        "plan_mode": row["plan_mode"],
                        "strict_fingerprint_sha256": row["strict_fingerprint_sha256"],
                    }
                    for row in captures
                ],
            }
        )

    strict_canonical_physical_bundle = {
        "fingerprint_version": STRICT_FINGERPRINT_VERSION,
        "registry_sha256": registry_digest,
        "registry_authority_verified": registry_authority_verified is True,
        "queries": strict_cluster_rows,
    }
    cluster_id = _sha256(strict_canonical_physical_bundle) if not reasons else None
    raw_explain_audit_bundle = [
        {
            "query_id": row["query_id"],
            "binding_id": row["binding_id"],
            "plan_mode": row["plan_mode"],
            "raw_explain": copy.deepcopy(row["raw_explain"]),
        }
        for row in ordered
    ]
    body: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "strict_fingerprint_version": STRICT_FINGERPRINT_VERSION,
        "approximate_fingerprint_version": APPROXIMATE_FINGERPRINT_VERSION,
        "registry_query_ids": registry,
        "registry_sha256": registry_digest,
        "registry_authority_verified": registry_authority_verified is True,
        "query_captures": ordered,
        "strict_cluster_usable": not reasons,
        "strict_cluster_ineligible_reasons": sorted(set(reasons)),
        "strict_cluster_id_sha256": cluster_id,
        "strict_canonical_physical_bundle": strict_canonical_physical_bundle,
        "strict_canonical_physical_bundle_sha256": cluster_id,
        "plan_similarity_cluster_id": cluster_id,
        "raw_explain_audit_bundle": raw_explain_audit_bundle,
        "raw_explain_bundle_sha256": _sha256(raw_explain_audit_bundle),
        "approximate_fingerprints_audit_only": True,
        "cost_and_rows_excluded_from_cluster_id": True,
        "full_explain_preserved": True,
    }
    body["canonical_plan_bundle_sha256"] = _sha256(body)
    return body


def validate_canonical_plan_bundle(bundle: Any) -> Dict[str, Any]:
    """Rebuild a bundle from full EXPLAIN and verify all canonical hashes."""

    reasons: List[str] = []
    if not isinstance(bundle, Mapping):
        return {"valid": False, "reasons": ["bundle_not_mapping"]}
    if bundle.get("schema_version") != SCHEMA_VERSION:
        reasons.append("schema_version_mismatch")
    try:
        rebuilt = build_canonical_plan_bundle(
            list(bundle.get("query_captures") or []),
            registry_query_ids=list(bundle.get("registry_query_ids") or []),
            registry_sha256=str(bundle.get("registry_sha256") or ""),
            registry_authority_verified=bundle.get("registry_authority_verified") is True,
        )
    except (TypeError, ValueError, OverflowError):
        return {"valid": False, "reasons": ["bundle_rebuild_failed"]}
    if rebuilt.get("canonical_plan_bundle_sha256") != bundle.get("canonical_plan_bundle_sha256"):
        reasons.append("canonical_plan_bundle_sha256_mismatch")
    if rebuilt.get("strict_cluster_id_sha256") != bundle.get("strict_cluster_id_sha256"):
        reasons.append("strict_cluster_id_sha256_mismatch")
    if bundle.get("plan_similarity_cluster_id") != bundle.get("strict_canonical_physical_bundle_sha256"):
        reasons.append("plan_similarity_cluster_id_mismatch")
    if rebuilt.get("plan_similarity_cluster_id") != bundle.get("plan_similarity_cluster_id"):
        reasons.append("rebuilt_plan_similarity_cluster_id_mismatch")
    if rebuilt.get("raw_explain_bundle_sha256") != bundle.get("raw_explain_bundle_sha256"):
        reasons.append("raw_explain_bundle_sha256_mismatch")
    if rebuilt.get("strict_cluster_usable") is not bundle.get("strict_cluster_usable"):
        reasons.append("strict_cluster_usable_mismatch")
    return {"valid": not reasons, "reasons": reasons, "rebuilt": rebuilt}


def _finite_score(candidate: Mapping[str, Any]) -> float:
    try:
        score = float(candidate.get("final_score"))
    except (TypeError, ValueError):
        return -math.inf
    return score if math.isfinite(score) else -math.inf


def cluster_ranked_candidates(
    candidates: Sequence[Mapping[str, Any]],
    *,
    scenario: str = "",
    fingerprint_config: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Build L1 plan clusters and fail-closed true-duplicate diagnostics.

    L1 remains useful for observing plan-boundary crossings. It is not a
    duplicate key. A true duplicate additionally requires an exact parameter
    signature and approximately equal L2/L3 fingerprints.
    """

    clusters: Dict[str, List[Dict[str, Any]]] = {}
    order: List[str] = []
    for position, raw in enumerate(candidates, 1):
        candidate = copy.deepcopy(dict(raw))
        candidate["three_level_fingerprint"] = build_candidate_fingerprint(
            candidate,
            scenario or candidate.get("scenario") or candidate.get("benchmark") or "",
        )
        bundle = candidate.get("canonical_plan_bundle")
        if not isinstance(bundle, Mapping):
            metadata = candidate.get("final_estimation_metadata")
            if isinstance(metadata, Mapping):
                bundle = metadata.get("canonical_plan_bundle")
        strict_id: Optional[str] = None
        verification = validate_canonical_plan_bundle(bundle)
        if verification["valid"] and isinstance(bundle, Mapping) and bundle.get("strict_cluster_usable") is True:
            strict_id = str(bundle.get("strict_cluster_id_sha256") or "") or None
        # Missing/incomplete fingerprints are independent singleton clusters.
        cluster_key = f"strict:{strict_id}" if strict_id else f"missing:{position}"
        if cluster_key not in clusters:
            clusters[cluster_key] = []
            order.append(cluster_key)
        candidate["strict_plan_cluster_key"] = cluster_key
        candidate["canonical_plan_bundle_validation"] = {
            "valid": verification["valid"],
            "reasons": list(verification["reasons"]),
        }
        clusters[cluster_key].append(candidate)

    representatives: List[Dict[str, Any]] = []
    audit: List[Dict[str, Any]] = []
    for cluster_key in order:
        members = clusters[cluster_key]
        ranked = sorted(
            enumerate(members),
            key=lambda item: (-_finite_score(item[1]), item[0]),
        )
        representative = copy.deepcopy(ranked[0][1])
        representative["strict_plan_cluster_member_subset_ids"] = [
            member.get("subset_id") for member in members
        ]
        representative["strict_plan_cluster_size"] = len(members)
        representatives.append(representative)
        audit.append(
            {
                "cluster_key": cluster_key,
                "strict_fingerprint_present": cluster_key.startswith("strict:"),
                "strict_cluster_id_sha256": (
                    cluster_key[len("strict:"):]
                    if cluster_key.startswith("strict:") else None
                ),
                "member_subset_ids": [member.get("subset_id") for member in members],
                "representative_subset_id": representative.get("subset_id"),
                "representative_final_score": representative.get("final_score"),
                "representative_canonical_plan_bundle_sha256": (
                    (
                        representative.get("canonical_plan_bundle")
                        if isinstance(representative.get("canonical_plan_bundle"), Mapping)
                        else ((representative.get("final_estimation_metadata") or {}).get("canonical_plan_bundle") or {})
                        if isinstance(representative.get("final_estimation_metadata"), Mapping)
                        else {}
                    ).get("canonical_plan_bundle_sha256")
                ),
                "representative_bundle_validation": copy.deepcopy(
                    representative.get("canonical_plan_bundle_validation") or {}
                ),
                "representative_rule": "highest_estimated_final_score_then_original_rank",
            }
        )
    representatives.sort(
        key=lambda candidate: (
            -_finite_score(candidate),
            int(candidate.get("canonical_subset_order") or 2**63 - 1),
        )
    )
    ordered_candidates = [
        candidate
        for cluster_key in order
        for candidate in clusters[cluster_key]
    ]
    true_duplicate_groups: List[Dict[str, Any]] = []
    for position, candidate in enumerate(ordered_candidates, 1):
        fingerprint = candidate["three_level_fingerprint"]
        matched: Optional[Dict[str, Any]] = None
        if fingerprint.get("true_duplicate_usable") is True:
            for group in true_duplicate_groups:
                representative_fingerprint = group["representative_fingerprint"]
                same, _reasons = fingerprints_are_true_duplicates(
                    representative_fingerprint,
                    fingerprint,
                    tolerance=(fingerprint_config or {}).get("tolerance"),
                )
                if same:
                    matched = group
                    break
        if matched is None:
            true_duplicate_groups.append(
                {
                    "group_key": (
                        "true_duplicate:"
                        + str(fingerprint.get("exact_identity_sha256"))
                        if fingerprint.get("true_duplicate_usable") is True
                        else f"ineligible_singleton:{position}"
                    ),
                    "true_duplicate_usable": fingerprint.get("true_duplicate_usable") is True,
                    "ineligible_reasons": copy.deepcopy(
                        fingerprint.get("true_duplicate_ineligible_reasons") or []
                    ),
                    "representative_subset_id": candidate.get("subset_id"),
                    "member_subset_ids": [candidate.get("subset_id")],
                    "representative_fingerprint": copy.deepcopy(fingerprint),
                }
            )
        else:
            matched["member_subset_ids"].append(candidate.get("subset_id"))
    public_duplicate_groups = []
    for group in true_duplicate_groups:
        public = copy.deepcopy(group)
        public.pop("representative_fingerprint", None)
        public["size"] = len(public["member_subset_ids"])
        public_duplicate_groups.append(public)
    return {
        "contract": "l1_plan_clusters_plus_l2_l3_true_duplicates_v1",
        "input_candidate_count": len(candidates),
        "strict_plan_cluster_count": len(representatives),
        "representatives": representatives,
        "clusters": audit,
        "missing_fingerprint_candidates_are_independent_clusters": True,
        "approximate_fingerprints_used_for_clustering": False,
        "l1_plan_clusters_are_duplicate_keys": False,
        "true_duplicate_contract": (
            "exact_parameter_signature_and_equal_l1_and_approximate_l2_l3_v1"
        ),
        "true_duplicate_groups": public_duplicate_groups,
        "true_duplicate_group_count": len(public_duplicate_groups),
        "true_duplicate_eligible_candidate_count": sum(
            1
            for candidate in ordered_candidates
            if candidate["three_level_fingerprint"].get("true_duplicate_usable") is True
        ),
        "missing_l2_or_l3_candidates_are_independent": True,
        "qps_used_by_fingerprint": False,
        "fingerprint_config": copy.deepcopy(dict(fingerprint_config or {})),
    }


def formal_replay_cap(benchmark: str) -> int:
    normalized = str(benchmark or "").strip().lower().replace("-", "_")
    normalized = {"tpc_h": "tpch", "tpc_c": "tpcc"}.get(normalized, normalized)
    if normalized not in FORMAL_REPLAY_CAPS:
        raise ValueError(f"unknown benchmark replay cap: {benchmark!r}")
    return FORMAL_REPLAY_CAPS[normalized]


def replay_stage_boundaries(cluster_count: int, benchmark: str) -> List[int]:
    """Return cumulative 5 -> 10 -> K boundaries for strict representatives."""

    if isinstance(cluster_count, bool) or not isinstance(cluster_count, int) or cluster_count < 0:
        raise ValueError("cluster_count must be a non-negative integer")
    maximum = min(cluster_count, formal_replay_cap(benchmark))
    if maximum == 0:
        return []
    if maximum < 10:
        # The executor may still checkpoint after five, but must replay all
        # available clusters before selecting/rejecting the round.
        return [min(5, maximum), maximum] if maximum > 5 else [maximum]
    result = [5, 10]
    if maximum > 10:
        result.append(maximum)
    return result
