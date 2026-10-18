"""Cross-round full-plan replay pruning for MySQL Algorithm C.

QPS is post-replay evidence only.  It never enters resource prediction,
gradients, or the inner candidate_score.  One rejection can only soft-demote a
matching complete plan.  Hard pruning requires QPS decline at two distinct
parameter points in two distinct outer rounds and no accepted counterexample.
"""

from __future__ import annotations

import copy
import json
import math
from typing import Any, Mapping, Sequence


CONTRACT_VERSION = "mysql_algorithm_c_full_plan_replay_pruning_v2"
SOFT_SINGLE_REJECT_PENALTY = 1


def _params_key(params: Mapping[str, Any]) -> str:
    return json.dumps(
        {str(key): float(value) for key, value in sorted(params.items())},
        sort_keys=True, separators=(",", ":"),
    )


def _plan_key(row: Mapping[str, Any]) -> str | None:
    expected = row.get("candidate_physical_plan_sha256")
    if isinstance(expected, str) and len(expected) == 64:
        return expected
    fingerprint = row.get("three_level_fingerprint") or {}
    direct = fingerprint.get("l1_plan_tree_sha256")
    if isinstance(direct, str) and len(direct) == 64:
        return direct
    resource_model = row.get("resource_model") or {}
    candidate = resource_model.get("candidate_plan_sha256")
    return candidate if isinstance(candidate, str) and len(candidate) == 64 else None


def record_outcomes(
    history: Sequence[Mapping[str, Any]], *, scene: str, outer_round: int,
    incumbent_qps: float, evaluations: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Append complete-plan outcomes without treating tier insufficiency as learnable."""
    if outer_round < 1 or not math.isfinite(incumbent_qps) or incumbent_qps <= 0:
        raise ValueError("valid outer round and incumbent QPS are required")
    result = [copy.deepcopy(dict(row)) for row in history]
    for evaluation in evaluations:
        decision = evaluation.get("outer_decision") or {}
        plan = _plan_key(evaluation)
        params = evaluation.get("params") or {}
        qps = float(evaluation.get("db_qps") or 0.0)
        if (
            plan is None or not isinstance(params, Mapping)
            or not math.isfinite(qps) or qps <= 0
            or evaluation.get("hard_gate_passed") is not True
            or evaluation.get("coverage_complete") is not True
        ):
            continue
        accepted = decision.get("accepted") is True
        qps_delta_fraction = qps / incumbent_qps - 1.0
        # A candidate that only misses the current acceptance tier may pass a
        # later fallback tier.  It is intentionally not negative evidence.
        reason = str(decision.get("reason") or "")
        required_rank = int(decision.get("required_acceptance_tier_rank") or 0)
        satisfied_rank = int(decision.get("satisfied_acceptance_tier_rank") or 0)
        tier_insufficient = (
            decision.get("qps_strictly_increased") is True
            and required_rank > 0
            and 0 < satisfied_rank < required_rank
        )
        learnable_decline = (not accepted and qps_delta_fraction < 0.0 and not tier_insufficient)
        result.append({
            "contract": CONTRACT_VERSION,
            "scene": scene,
            "outer_round": outer_round,
            "full_plan_sha256": plan,
            "parameter_point_key": _params_key(params),
            "accepted": accepted,
            "qps": qps,
            "incumbent_qps": incumbent_qps,
            "qps_delta_fraction": qps_delta_fraction,
            "qps_declined": qps_delta_fraction < 0.0,
            "qps_nondecline_counterexample": qps_delta_fraction >= 0.0,
            "decision_reason": reason,
            "required_acceptance_tier_rank": required_rank,
            "satisfied_acceptance_tier_rank": satisfied_rank,
            "tier_insufficient_not_learnable": tier_insufficient,
            "learnable_qps_decline": learnable_decline,
            "acceptance_tier_independent_qps_evidence": learnable_decline,
        })
    return result


def classify_plan(
    plan_sha256: str, history: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    rows = [dict(row) for row in history if row.get("full_plan_sha256") == plan_sha256]
    accepted = [row for row in rows if row.get("accepted") is True]
    declines = [row for row in rows if row.get("learnable_qps_decline") is True]
    nondecline = [row for row in rows if row.get("qps_nondecline_counterexample") is True]
    distinct_rounds = {int(row["outer_round"]) for row in declines}
    distinct_points = {str(row["parameter_point_key"]) for row in declines}
    hard_prune = (
        not accepted and not nondecline
        and len(distinct_rounds) >= 2 and len(distinct_points) >= 2
    )
    soft_penalty = SOFT_SINGLE_REJECT_PENALTY if declines and not hard_prune else 0
    return {
        "contract": CONTRACT_VERSION,
        "full_plan_sha256": plan_sha256,
        "accepted_counterexamples": len(accepted),
        "learnable_qps_declines": len(declines),
        "qps_nondecline_counterexamples": len(nondecline),
        "distinct_decline_rounds": len(distinct_rounds),
        "distinct_decline_parameter_points": len(distinct_points),
        "hard_prune": hard_prune,
        "soft_ranking_penalty": soft_penalty,
        "hard_prune_prohibited_by_accepted_counterexample": bool(accepted),
        "hard_prune_prohibited_by_qps_nondecline_counterexample": bool(nondecline),
        "qps_role": "post_replay_pruning_evidence_only",
    }


def rank_and_prune_candidates(
    candidates: Sequence[Mapping[str, Any]], history: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return replay ordering without modifying candidate_score or model payloads."""
    kept: list[tuple[int, int, dict[str, Any]]] = []
    pruned: list[dict[str, Any]] = []
    audit: list[dict[str, Any]] = []
    for original_rank, raw in enumerate(candidates, 1):
        row = copy.deepcopy(dict(raw))
        score_before = row.get("candidate_score")
        plan = _plan_key(row)
        classification = (
            classify_plan(plan, history) if plan is not None else {
                "contract": CONTRACT_VERSION, "full_plan_sha256": None,
                "hard_prune": False, "soft_ranking_penalty": 0,
                "reason": "complete_plan_identity_absent_fail_open",
                "qps_role": "post_replay_pruning_evidence_only",
            }
        )
        if row.get("candidate_score") != score_before:
            raise RuntimeError("candidate_score mutation is prohibited")
        audit.append({**classification, "original_rank": original_rank})
        if classification["hard_prune"]:
            row["plan_replay_pruning_v2"] = classification
            pruned.append(row)
        else:
            penalty = int(classification["soft_ranking_penalty"])
            row["plan_replay_pruning_v2"] = classification
            kept.append((penalty, original_rank, row))
    kept.sort(key=lambda item: (item[0], item[1]))
    ordered = []
    for replay_rank, (_penalty, original_rank, row) in enumerate(kept, 1):
        row["resource_model_rank"] = original_rank
        row["replay_rank_after_plan_history"] = replay_rank
        ordered.append(row)
    return ordered, pruned, audit
