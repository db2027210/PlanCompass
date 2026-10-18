#!/usr/bin/env python3
"""PostgreSQL-aligned search outer-loop semantics for MySQL algorithms.

The policy is database-neutral where possible, but consumes the MySQL formal
measurement shape (``db_qps``, ``p95_ms``, ``p99_ms``, ``resource_means``).
It mirrors the current PostgreSQL lane controller rather than the older MySQL
three-tier downgrade state machine:

* fallback is the permanent hard acceptance floor;
* strict/secondary/fallback classify and order same-batch accepted points;
* Top-5 is an upper bound and duplicate full plans are never padding;
* accepted points shrink only declared parameters that actually moved;
* the tenth accepted shrink is terminal; no champion repeatability phase runs.

The last rule is the explicit MySQL experiment override.  It intentionally
removes the current PostgreSQL controller's post-search stability phase while
leaving its search-round state machine unchanged.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from typing import Any, Mapping, Sequence


ACCEPTED_TARGET = 10
MAX_SEARCH_ROUNDS = 100
TOP_K_LIMIT = 5
REPLAY_BATCH_SIZES = (2, 2, 1)
REQUIRED_ACCEPTANCE_TIER = "fallback"
NON_TARGET_BOTTLENECK_THRESHOLD = 0.80
MIN_DB_QPS_GAIN_RATIO = 0.001
MIN_TARGET_DROP_RATIO = 0.001
TIER_RANK = {"strict": 3, "secondary": 2, "fallback": 1}
RESOURCE_NAMES = ("cpu", "memory", "io")


class TechnicalRoundFailure(RuntimeError):
    """No valid real replay exists although physical candidates were attempted."""


def policy_contract() -> dict[str, Any]:
    return {
        "schema_version": "mysql_algorithm_b_pg_outer_policy_v31",
        "status": "IMPLEMENTED_NOT_FORMALLY_FROZEN",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "accepted_target": ACCEPTED_TARGET,
        "maximum_search_rounds": MAX_SEARCH_ROUNDS,
        "top_k_is_upper_bound": True,
        "top_k_limit": TOP_K_LIMIT,
        "partial_top_k_allowed": True,
        "duplicate_plan_padding_allowed": False,
        "baseline_padding_allowed": False,
        "candidate_replay_batches": [list(range(1, 3)), [3, 4], [5]],
        "candidate_replay_batch_sizes": list(REPLAY_BATCH_SIZES),
        "required_acceptance_tier": REQUIRED_ACCEPTANCE_TIER,
        "qps_reference_mode": "canonical_baseline",
        "qps_threshold_mode": "static_floor",
        "minimum_db_qps_gain_ratio": MIN_DB_QPS_GAIN_RATIO,
        "minimum_target_drop_ratio": MIN_TARGET_DROP_RATIO,
        "same_batch_selection": (
            "tier_then_target_drop_then_qps_gain_then_min_non_target_io_up"
        ),
        "acceptance_tier_downgrade": False,
        "accepted_range_shrink": "declared_active_subset_intersect_moved_subset",
        "rejected_ranges_unchanged": True,
        "technical_only_round_semantic_state_unchanged": True,
        "cached_valid_replay_redecision_before_new_physical_work": True,
        "fallback_plan_change_expansion": True,
        "full_range_plan_basin_escape": True,
        "normalized_width": "0.5**accepted",
        "terminal_condition": "accepted_count_reaches_10",
        "post_search_stability_confirmation": False,
        "post_target_physical_replays": 0,
    }


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def parameter_signature(params: Mapping[str, Any]) -> str:
    return json.dumps(
        {str(name): float(value) for name, value in sorted(params.items())},
        sort_keys=True,
        separators=(",", ":"),
    )


def full_plan_fingerprint(candidate: Mapping[str, Any]) -> str:
    direct = candidate.get("candidate_physical_plan_sha256")
    if isinstance(direct, str) and direct:
        return direct
    fingerprint = candidate.get("three_level_fingerprint") or {}
    direct = fingerprint.get("l1_plan_tree_sha256")
    if isinstance(direct, str) and direct:
        return direct
    model = candidate.get("resource_model") or {}
    direct = model.get("candidate_plan_sha256")
    if isinstance(direct, str) and direct:
        return direct
    return "missing:" + hashlib.sha256(
        parameter_signature(_mapping(
            candidate.get("optimized_params"), "candidate params",
        )).encode("utf-8")
    ).hexdigest()


def admit_partial_topk(
    rankings: Sequence[Mapping[str, Any]],
    *,
    incumbent_params: Mapping[str, Any],
    previously_accepted_signatures: set[str] | None = None,
    hard_pruned_plan_fingerprints: set[str] | None = None,
    limit: int = TOP_K_LIMIT,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Admit at most five new plan representatives without padding."""
    if limit != TOP_K_LIMIT:
        raise ValueError(f"PG-aligned Top-K limit must remain {TOP_K_LIMIT}")
    incumbent_key = parameter_signature(incumbent_params)
    accepted = set(previously_accepted_signatures or set())
    pruned = set(hard_pruned_plan_fingerprints or set())
    seen_params: set[str] = set()
    seen_plans: set[str] = set()
    selected: list[dict[str, Any]] = []
    excluded = {
        "incumbent": 0,
        "previously_accepted": 0,
        "duplicate_parameter_point": 0,
        "duplicate_full_plan": 0,
        "hard_pruned_plan": 0,
    }
    for raw in rankings:
        row = copy.deepcopy(dict(raw))
        params = _mapping(row.get("optimized_params"), "candidate params")
        params_key = parameter_signature(params)
        plan = full_plan_fingerprint(row)
        if params_key == incumbent_key:
            excluded["incumbent"] += 1
            continue
        if params_key in accepted:
            excluded["previously_accepted"] += 1
            continue
        if params_key in seen_params:
            excluded["duplicate_parameter_point"] += 1
            continue
        if plan in pruned:
            excluded["hard_pruned_plan"] += 1
            continue
        if plan in seen_plans:
            excluded["duplicate_full_plan"] += 1
            continue
        seen_params.add(params_key)
        seen_plans.add(plan)
        row["pg_outer_admission_rank"] = len(selected) + 1
        selected.append(row)
        if len(selected) >= limit:
            break
    return selected, {
        "top_k_limit": limit,
        "admitted_count": len(selected),
        "partial_top_k": len(selected) < limit,
        "top_k_is_upper_bound": True,
        "duplicate_plan_padding_allowed": False,
        "baseline_padding_allowed": False,
        "excluded": excluded,
    }


def replay_batches(
    candidates: Sequence[Mapping[str, Any]],
) -> list[list[dict[str, Any]]]:
    """Return the PG [2,2,1] physical-wave shape for a partial Top-K."""
    if len(candidates) > TOP_K_LIMIT:
        raise ValueError("candidate count exceeds PG Top-K upper bound")
    rows = [copy.deepcopy(dict(row)) for row in candidates]
    batches: list[list[dict[str, Any]]] = []
    cursor = 0
    for size in REPLAY_BATCH_SIZES:
        batch = rows[cursor:cursor + size]
        cursor += size
        if batch:
            batches.append(batch)
    return batches


def physical_valid(row: Mapping[str, Any]) -> bool:
    return bool(
        row.get("technical_failure") is not True
        and row.get("hard_gate_passed") is True
        and row.get("coverage_complete") is True
        and isinstance(row.get("resource_means"), Mapping)
        and float(row.get("db_qps") or 0.0) > 0.0
    )


def outer_semantic_decision(
    candidate: Mapping[str, Any],
    reference: Mapping[str, Any],
    *,
    canonical_baseline: Mapping[str, Any] | None = None,
    target: str,
) -> dict[str, Any]:
    """Classify one real replay using the current PG tier semantics."""
    if target not in RESOURCE_NAMES:
        raise ValueError(f"invalid target resource: {target}")
    candidate_resources = _mapping(
        candidate.get("resource_means"), "candidate resource means",
    )
    reference_resources = _mapping(
        reference.get("resource_means"), "reference resource means",
    )
    candidate_qps = float(candidate.get("db_qps") or 0.0)
    reference_qps = float(reference.get("db_qps") or 0.0)
    baseline = canonical_baseline or reference
    baseline_qps = float(baseline.get("db_qps") or 0.0)
    qps_gain = (
        candidate_qps / baseline_qps - 1.0
        if baseline_qps > 0 else -math.inf
    )
    reference_target = float(reference_resources[target])
    target_drop = (
        (reference_target - float(candidate_resources[target])) / reference_target
        if reference_target > 0 else -math.inf
    )
    non_targets = [name for name in RESOURCE_NAMES if name != target]
    non_target_up = [
        name for name in non_targets
        if float(candidate_resources[name]) > float(reference_resources[name])
    ]
    non_target_io_up = (
        max(
            0.0,
            float(candidate_resources["io"])
            - float(reference_resources["io"]),
        )
        if target != "io" else 0.0
    )
    candidate_bottlenecks = [
        name for name in non_targets
        if float(candidate_resources[name]) >= NON_TARGET_BOTTLENECK_THRESHOLD
    ]
    hard_gate = bool(
        physical_valid(candidate)
        and not candidate_bottlenecks
    )
    qps_up = qps_gain > MIN_DB_QPS_GAIN_RATIO
    target_down = target_drop > MIN_TARGET_DROP_RATIO
    if qps_up and target_down and non_target_up:
        tier = "strict"
    elif qps_up and target_down:
        tier = "secondary"
    elif qps_up:
        tier = "fallback"
    else:
        tier = None
    accepted = bool(hard_gate and tier is not None)
    return {
        "accepted": accepted,
        "reason": (
            f"accepted_{tier}" if accepted else
            "hard_gate_failed" if not hard_gate else
            "db_qps_not_strictly_higher"
        ),
        "required_acceptance_tier": REQUIRED_ACCEPTANCE_TIER,
        "acceptance_tier": tier,
        "acceptance_tier_rank": TIER_RANK.get(tier or "", 0),
        "qps_gate_gain": qps_gain,
        "target_drop_vs_reference": target_drop,
        "target_resource": target,
        "non_target_up_resources": non_target_up,
        "non_target_io_up_vs_reference": non_target_io_up,
        "candidate_non_target_bottlenecks": candidate_bottlenecks,
        "incumbent_non_target_bottlenecks": [],
        "qps_reference_mode": "canonical_baseline",
        "qps_threshold_mode": "static_floor",
        "minimum_db_qps_gain_ratio": MIN_DB_QPS_GAIN_RATIO,
        "minimum_target_drop_ratio": MIN_TARGET_DROP_RATIO,
        "qps_reference_db_qps": baseline_qps,
        "last_accepted_db_qps": reference_qps,
        "hard_gate_passed": hard_gate,
        "accepted_shrink": accepted,
        "accepted_escape": False,
    }


def accepted_selection_key(evaluation: Mapping[str, Any]) -> tuple[Any, ...]:
    decision = _mapping(evaluation.get("decision"), "outer decision")
    target = str(decision.get("target_resource") or "")
    io_up = float(decision.get("non_target_io_up_vs_reference") or 0.0)
    if target != "io" and "non_target_io_up_vs_reference" not in decision:
        candidate = _mapping(evaluation.get("row"), "candidate row")
        reference = _mapping(evaluation.get("reference"), "reference row")
        io_up = max(
            0.0,
            float(candidate["resource_means"]["io"])
            - float(reference["resource_means"]["io"]),
        )
    ranking = evaluation.get("row", {}).get("candidate_ranking") or {}
    return (
        int(decision.get("acceptance_tier_rank") or 0),
        float(decision.get("target_drop_vs_reference") or 0.0),
        float(decision.get("qps_gate_gain") or -math.inf),
        -io_up,
        -int(ranking.get("rank") or ranking.get("pg_outer_admission_rank") or 0),
    )


def evaluate_batch(
    rows: Sequence[Mapping[str, Any]],
    reference: Mapping[str, Any],
    *,
    canonical_baseline: Mapping[str, Any] | None = None,
    target: str,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    evaluations: list[dict[str, Any]] = []
    for raw in rows:
        row = copy.deepcopy(dict(raw))
        if not physical_valid(row):
            continue
        decision = outer_semantic_decision(
            row,
            reference,
            canonical_baseline=canonical_baseline,
            target=target,
        )
        evaluations.append({
            "row": row,
            "reference": copy.deepcopy(dict(reference)),
            "decision": decision,
        })
    accepted = [item for item in evaluations if item["decision"]["accepted"]]
    accepted.sort(key=accepted_selection_key, reverse=True)
    return (accepted[0] if accepted else None), evaluations


def moved_shrink_subset(
    *,
    incumbent_params: Mapping[str, Any],
    candidate_ranking: Mapping[str, Any],
    active_params: Sequence[str],
) -> list[str]:
    params = _mapping(candidate_ranking.get("optimized_params"), "candidate params")
    declared = {
        str(name) for name in candidate_ranking.get("active_subset") or []
        if str(name) in active_params
    }
    moved = {
        name for name in active_params
        if not math.isclose(
            float(params[name]),
            float(incumbent_params[name]),
            rel_tol=1e-12,
            abs_tol=1e-15,
        )
    }
    result = [name for name in active_params if name in declared and name in moved]
    if not result:
        raise RuntimeError("accepted candidate has no moved declared parameter")
    return result


__all__ = [
    "TechnicalRoundFailure",
    "accepted_selection_key",
    "admit_partial_topk",
    "evaluate_batch",
    "full_plan_fingerprint",
    "moved_shrink_subset",
    "outer_semantic_decision",
    "parameter_signature",
    "physical_valid",
    "policy_contract",
    "replay_batches",
]
