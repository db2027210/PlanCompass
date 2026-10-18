"""Plan pruning with a fail-closed technical-instability branch.

The v2 QPS rules are unchanged.  A separate hard-safety branch excludes a
complete physical plan only when a checkpoint contains two independently
bound formal-incumbent baseline incidents proving an internal database hang
with zero optimizer credit.  An earlier successful replay does not make a
server-crashing incumbent safe.
"""

from __future__ import annotations

import copy
from typing import Any, Mapping, Sequence

import mysql_algorithm_c_plan_replay_pruning as v2


CONTRACT_VERSION = "mysql_algorithm_c_full_plan_replay_pruning_v3"


def classify_plan(
    plan_sha256: str, history: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    classification = v2.classify_plan(plan_sha256, history)
    incidents = [
        dict(row) for row in history
        if row.get("full_plan_sha256") == plan_sha256
        and row.get("technical_instability_proven") is True
        and row.get("formal_state_credit_increment") == 0
        and isinstance(row.get("technical_incident_id"), str)
    ]
    independent = {str(row["technical_incident_id"]) for row in incidents}
    technical_hard_prune = len(independent) >= 2
    if technical_hard_prune:
        classification.update({
            "contract": CONTRACT_VERSION,
            "hard_prune": True,
            "soft_ranking_penalty": 0,
            "hard_prune_reason": "repeated_formal_incumbent_internal_hang",
            "technical_instability_incidents": len(independent),
            "technical_incident_ids": sorted(independent),
            "accepted_counterexample_overridden_for_technical_safety": True,
            "qps_role": "post_replay_pruning_evidence_only",
        })
    else:
        classification.update({
            "contract": CONTRACT_VERSION,
            "technical_instability_incidents": len(independent),
            "technical_incident_ids": sorted(independent),
            "accepted_counterexample_overridden_for_technical_safety": False,
        })
    return classification


def rank_and_prune_candidates(
    candidates: Sequence[Mapping[str, Any]], history: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    kept: list[tuple[int, int, dict[str, Any]]] = []
    pruned: list[dict[str, Any]] = []
    audit: list[dict[str, Any]] = []
    for original_rank, raw in enumerate(candidates, 1):
        row = copy.deepcopy(dict(raw))
        score_before = row.get("candidate_score")
        plan = v2._plan_key(row)
        classification = (
            classify_plan(plan, history) if plan is not None else {
                "contract": CONTRACT_VERSION, "full_plan_sha256": None,
                "hard_prune": False, "soft_ranking_penalty": 0,
                "reason": "complete_plan_identity_absent_fail_open",
                "qps_role": "post_replay_pruning_evidence_only",
                "technical_instability_incidents": 0,
            }
        )
        if row.get("candidate_score") != score_before:
            raise RuntimeError("candidate_score mutation is prohibited")
        audit.append({**classification, "original_rank": original_rank})
        if classification["hard_prune"]:
            row["plan_replay_pruning_v3"] = classification
            pruned.append(row)
        else:
            penalty = int(classification["soft_ranking_penalty"])
            row["plan_replay_pruning_v3"] = classification
            kept.append((penalty, original_rank, row))
    kept.sort(key=lambda item: (item[0], item[1]))
    ordered = []
    for replay_rank, (_penalty, original_rank, row) in enumerate(kept, 1):
        row["resource_model_rank"] = original_rank
        row["replay_rank_after_plan_history"] = replay_rank
        ordered.append(row)
    return ordered, pruned, audit
