"""Module."""


from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import asdict, dataclass, is_dataclass, replace
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


LOG_COST_PARAMETERS = frozenset(
    {
        "seq_page_cost",
        "random_page_cost",
        "cpu_tuple_cost",
        "cpu_index_tuple_cost",
        "cpu_operator_cost",
        "parallel_tuple_cost",
    }
)

RESOURCE_NAMES = ("cpu", "memory", "io")
DEFAULT_NON_TARGET_BOTTLENECK_UTILIZATION_PCT = 80.0
GLOBAL_REAL_REPLAY_TOP_K_MAX = 5


def acceptance_tier_for_miss_count(
    consecutive_missed_outer_rounds: Any,
    *,
    downgrade_after_rounds: Any = 2,
) -> str:
    """Return the current three-level outer threshold.

    Each accepted round resets the caller-owned miss counter to zero. Two
    consecutive misses downgrade strict -> secondary; another two downgrade
    secondary -> fallback.
    """

    try:
        misses = int(consecutive_missed_outer_rounds)
        patience = int(downgrade_after_rounds)
    except (TypeError, ValueError) as exc:
        raise ValueError("outer tier counters must be integers") from exc
    if misses < 0:
        raise ValueError("consecutive missed outer rounds cannot be negative")
    if patience <= 0:
        raise ValueError("acceptance tier downgrade patience must be positive")
    if misses < patience:
        return "strict"
    if misses < 2 * patience:
        return "secondary"
    return "fallback"


def outer_acceptance_required_tier() -> str:
    """Return the hard acceptance floor for the three-tier policy.

    Strict, secondary and fallback are alternatives evaluated in the same
    Top-K batch.  The controller prefers the highest achieved tier, but must
    not reject a safe QPS-up fallback merely because no stricter point exists.
    """

    return "fallback"


def cap_real_replay_top_k(
    requested_top_k: Any,
    *,
    available_count: Optional[int] = None,
) -> int:
    """Apply the campaign-wide real benchmark replay ceiling.

    A requested value of zero historically meant "all candidates".  Under
    the current contract it means all candidates up to the global ceiling.
    Read-only estimation and ranking are intentionally unaffected.
    """
    try:
        requested = int(requested_top_k)
    except (TypeError, ValueError) as exc:
        raise ValueError("real replay top-k must be an integer") from exc
    if requested < 0:
        raise ValueError("real replay top-k cannot be negative")
    effective = (
        GLOBAL_REAL_REPLAY_TOP_K_MAX
        if requested == 0
        else min(requested, GLOBAL_REAL_REPLAY_TOP_K_MAX)
    )
    if available_count is not None:
        available = int(available_count)
        if available < 0:
            raise ValueError("available candidate count cannot be negative")
        effective = min(effective, available)
    return effective


@dataclass(frozen=True)
class OuterAcceptanceDecision:
    """OuterAcceptanceDecision."""

    accepted: bool
    reason: str
    hard_gate_passed: bool
    incumbent_db_qps: Optional[float]
    candidate_db_qps: Optional[float]
    incumbent_target_utilization: Optional[float]
    candidate_target_utilization: Optional[float]
    target_resource: Optional[str]
    non_target_bottleneck_threshold: Optional[float]
    incumbent_non_target_utilization: Optional[Dict[str, float]]
    candidate_non_target_utilization: Optional[Dict[str, float]]
    non_target_bottleneck_resources: Optional[List[str]]
    incumbent_non_target_bottlenecks: Optional[List[str]]
    candidate_non_target_bottlenecks: Optional[List[str]]
    minimum_db_qps_improvement_ratio: Optional[float]
    observed_db_qps_improvement_ratio: Optional[float]
    acceptance_tier: Optional[str] = None
    required_acceptance_tier: Optional[str] = None
    preferred_acceptance_tier: Optional[str] = None
    acceptance_selection_policy: Optional[str] = None
    qps_up: Optional[bool] = None
    target_down: Optional[bool] = None
    non_target_up_resources: Optional[List[str]] = None
    accepted_shrink: bool = False
    accepted_escape: bool = False
    formal_state_mutation_allowed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OptimisticReplayPruningDecision:
    """OptimisticReplayPruningDecision."""

    pruned: bool
    reason: str
    bounds_usable: bool
    incumbent_db_qps: Optional[float]
    incumbent_target_utilization: Optional[float]
    predicted_db_qps: Optional[float]
    db_qps_upper_error: Optional[float]
    optimistic_db_qps_upper: Optional[float]
    predicted_target_utilization: Optional[float]
    target_utilization_lower_error: Optional[float]
    optimistic_target_utilization_lower: Optional[float]
    predicted_score: Optional[float]
    score_upper_error: Optional[float]
    optimistic_score_upper: Optional[float]
    best_score: Optional[float]
    scalar_score_cannot_beat_best: Optional[bool]
    resource_intervals: Optional[Dict[str, Dict[str, float]]] = None
    utilization_intervals: Optional[Dict[str, Dict[str, float]]] = None
    utility_upper_bound_details: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _finite_or_none(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def decide_optimistic_replay_pruning(
    *,
    incumbent_db_qps: Any,
    incumbent_target_utilization: Any,
    predicted_db_qps: Any,
    db_qps_upper_error: Any,
    predicted_target_utilization: Any,
    target_utilization_lower_error: Any,
    calibration_ready: bool,
    calibration_scope_matches: bool,
    simultaneous_coverage_guaranteed: bool,
    predicted_score: Any = None,
    score_upper_error: Any = None,
    best_score: Any = None,
    predicted_resource: Any = None,
    resource_error_bounds: Any = None,
    capability: Any = None,
    utility_calculator: Any = None,
    required_resources: Sequence[str] = ("cpu", "memory", "io"),
) -> OptimisticReplayPruningDecision:
    """decide optimistic replay pruning."""


    incumbent_qps = _finite_or_none(incumbent_db_qps)
    incumbent_util = _finite_or_none(incumbent_target_utilization)
    predicted_qps = _finite_or_none(predicted_db_qps)
    qps_error = _finite_or_none(db_qps_upper_error)
    predicted_util = _finite_or_none(predicted_target_utilization)
    util_error = _finite_or_none(target_utilization_lower_error)
    predicted_score_value = _finite_or_none(predicted_score)
    score_error = _finite_or_none(score_upper_error)
    best_score_value = _finite_or_none(best_score)

    optimistic_score_upper: Optional[float] = None
    scalar_score_cannot_beat_best: Optional[bool] = None
    common = {
        "incumbent_db_qps": incumbent_qps,
        "incumbent_target_utilization": incumbent_util,
        "predicted_db_qps": predicted_qps,
        "db_qps_upper_error": qps_error,
        "optimistic_db_qps_upper": (
            predicted_qps + qps_error
            if predicted_qps is not None and qps_error is not None and qps_error >= 0.0
            else None
        ),
        "predicted_target_utilization": predicted_util,
        "target_utilization_lower_error": util_error,
        "optimistic_target_utilization_lower": (
            max(0.0, predicted_util - util_error)
            if predicted_util is not None and util_error is not None and util_error >= 0.0
            else None
        ),
        "predicted_score": predicted_score_value,
        "score_upper_error": score_error,
        "optimistic_score_upper": optimistic_score_upper,
        "best_score": best_score_value,
        "scalar_score_cannot_beat_best": scalar_score_cannot_beat_best,
        "resource_intervals": None,
        "utilization_intervals": None,
        "utility_upper_bound_details": None,
    }
    if not calibration_ready:
        return OptimisticReplayPruningDecision(
            False, "calibration_not_ready_keep_for_real_replay", False, **common
        )
    if not calibration_scope_matches:
        return OptimisticReplayPruningDecision(
            False, "calibration_scope_mismatch_keep_for_real_replay", False, **common
        )
    if not simultaneous_coverage_guaranteed:
        return OptimisticReplayPruningDecision(
            False, "no_simultaneous_coverage_keep_for_real_replay", False, **common
        )
    if (
        not isinstance(predicted_resource, Mapping)
        or not isinstance(resource_error_bounds, Mapping)
        or not isinstance(capability, Mapping)
        or utility_calculator is None
        or not callable(
            getattr(utility_calculator, "maximize_utility_over_utilization_box", None)
        )
        or best_score_value is None
    ):
        return OptimisticReplayPruningDecision(
            False,
            "missing_resource_box_or_utility_keep_for_real_replay",
            False,
            **common,
        )

    resource_intervals: Dict[str, Dict[str, float]] = {}
    utilization_intervals: Dict[str, Tuple[float, float]] = {}
    for raw_resource in required_resources:
        resource = str(raw_resource).strip().lower()
        predicted_value = _finite_or_none(predicted_resource.get(resource))
        capability_value = _finite_or_none(capability.get(resource))
        raw_bound = resource_error_bounds.get(resource)
        if isinstance(raw_bound, Mapping):
            lower_error = _finite_or_none(
                raw_bound.get("error_lower", raw_bound.get("lower_error"))
            )
            upper_error = _finite_or_none(
                raw_bound.get("error_upper", raw_bound.get("upper_error"))
            )
            if lower_error is None or upper_error is None:
                symmetric = _finite_or_none(
                    raw_bound.get("absolute_error", raw_bound.get("value"))
                )
                if lower_error is None:
                    lower_error = symmetric
                if upper_error is None:
                    upper_error = symmetric
        else:
            lower_error = _finite_or_none(raw_bound)
            upper_error = lower_error
        if (
            predicted_value is None
            or capability_value is None
            or capability_value <= 0.0
            or lower_error is None
            or upper_error is None
            or lower_error < 0.0
            or upper_error < 0.0
        ):
            return OptimisticReplayPruningDecision(
                False,
                f"invalid_{resource}_resource_bound_keep_for_real_replay",
                False,
                **common,
            )
        resource_lower = max(0.0, predicted_value - lower_error)
        resource_upper = max(resource_lower, predicted_value + upper_error)
        utilization_lower = resource_lower / capability_value
        utilization_upper = resource_upper / capability_value
        resource_intervals[resource] = {
            "predicted": predicted_value,
            "error_lower": lower_error,
            "error_upper": upper_error,
            "actual_lower": resource_lower,
            "actual_upper": resource_upper,
            "capability": capability_value,
        }
        utilization_intervals[resource] = (
            utilization_lower,
            utilization_upper,
        )

    try:
        optimistic_score_upper, upper_details = (
            utility_calculator.maximize_utility_over_utilization_box(
                utilization_intervals
            )
        )
        optimistic_score_upper = _finite_or_none(optimistic_score_upper)
    except (TypeError, ValueError, ArithmeticError):
        return OptimisticReplayPruningDecision(
            False,
            "utility_box_maximization_failed_keep_for_real_replay",
            False,
            **common,
        )
    if optimistic_score_upper is None:
        return OptimisticReplayPruningDecision(
            False,
            "nonfinite_utility_upper_keep_for_real_replay",
            False,
            **common,
        )
    scalar_score_cannot_beat_best = optimistic_score_upper <= best_score_value
    common.update(
        {
            "optimistic_score_upper": optimistic_score_upper,
            "scalar_score_cannot_beat_best": scalar_score_cannot_beat_best,
            "resource_intervals": resource_intervals,
            "utilization_intervals": {
                name: {"lower": bounds[0], "upper": bounds[1]}
                for name, bounds in utilization_intervals.items()
            },
            "utility_upper_bound_details": copy.deepcopy(upper_details),
        }
    )
    if scalar_score_cannot_beat_best:
        return OptimisticReplayPruningDecision(
            True,
            "utility_upper_not_strictly_above_current_best",
            True,
            **common,
        )
    return OptimisticReplayPruningDecision(
        False,
        "utility_upper_can_beat_current_best",
        True,
        **common,
    )


def prune_ranked_candidates_for_replay(
    candidates: Sequence[Mapping[str, Any]],
    *,
    incumbent_db_qps: Any,
    incumbent_target_utilization: Any,
    capability: Any = None,
    utility_calculator: Any = None,
    best_score: Any = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """prune ranked candidates for replay."""


    kept: List[Dict[str, Any]] = []
    pruned: List[Dict[str, Any]] = []
    checks: List[Dict[str, Any]] = []
    for position, raw_candidate in enumerate(candidates, start=1):
        candidate = dict(raw_candidate)
        payload = candidate.get("optimistic_replay_pruning_input")
        if not isinstance(payload, Mapping):
            decision = OptimisticReplayPruningDecision(
                pruned=False,
                reason="missing_pruning_input_keep_for_real_replay",
                bounds_usable=False,
                incumbent_db_qps=_finite_or_none(incumbent_db_qps),
                incumbent_target_utilization=_finite_or_none(
                    incumbent_target_utilization
                ),
                predicted_db_qps=None,
                db_qps_upper_error=None,
                optimistic_db_qps_upper=None,
                predicted_target_utilization=None,
                target_utilization_lower_error=None,
                optimistic_target_utilization_lower=None,
                predicted_score=None,
                score_upper_error=None,
                optimistic_score_upper=None,
                best_score=None,
                scalar_score_cannot_beat_best=None,
            )
        else:
            decision = decide_optimistic_replay_pruning(
                incumbent_db_qps=incumbent_db_qps,
                incumbent_target_utilization=incumbent_target_utilization,
                predicted_db_qps=payload.get("predicted_db_qps"),
                db_qps_upper_error=payload.get("db_qps_upper_error"),
                predicted_target_utilization=payload.get(
                    "predicted_target_utilization"
                ),
                target_utilization_lower_error=payload.get(
                    "target_utilization_lower_error"
                ),
                calibration_ready=payload.get("calibration_ready") is True,
                calibration_scope_matches=(
                    payload.get("calibration_scope_matches") is True
                ),
                simultaneous_coverage_guaranteed=(
                    payload.get("simultaneous_coverage_guaranteed") is True
                ),
                predicted_score=payload.get("predicted_score"),
                score_upper_error=payload.get("score_upper_error"),
                best_score=(best_score if best_score is not None else payload.get("best_score")),
                predicted_resource=payload.get("predicted_resource"),
                resource_error_bounds=payload.get("resource_error_bounds"),
                capability=(capability if capability is not None else payload.get("capability")),
                utility_calculator=utility_calculator,
            )
        decision_record = {
            "candidate_position": position,
            "candidate_rank": candidate.get("rank"),
            "pruning_input": (
                copy.deepcopy(dict(payload))
                if isinstance(payload, Mapping)
                else None
            ),
            "decision": decision.to_dict(),
        }
        candidate["optimistic_replay_pruning_decision"] = decision.to_dict()
        checks.append(decision_record)
        if decision.pruned:
            pruned.append(candidate)
        else:
            kept.append(candidate)
    return kept, pruned, checks


def _stable_plan_cluster_token(value: Any) -> Optional[str]:
    """ stable plan cluster token."""

    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    try:
        normalized = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _candidate_plan_cluster(candidate: Mapping[str, Any], position: int) -> Tuple[str, str]:
    """ candidate plan cluster."""

    for key in (
        "plan_similarity_cluster_id",
        "workload_plan_cluster_id",
        "plan_cluster_id",
        "workload_plan_fingerprint",
        "workload_plan_signature",
        "plan_fingerprint",
        "plan_signature",
        "plan_fingerprints",
    ):
        token = _stable_plan_cluster_token(candidate.get(key))
        if token is not None:
            return f"{key}:{token}", key
    rank = candidate.get("rank")
    return f"missing_plan_unique:{position}:{rank}", "missing_plan_fail_open_unique"


def _candidate_representative_priority(
    candidate: Mapping[str, Any],
    position: int,
) -> Tuple[float, float, float, int]:
    """ candidate representative priority."""

    direction_prediction = candidate.get("real_replay_direction_prediction")
    direction_prediction = (
        direction_prediction if isinstance(direction_prediction, Mapping) else {}
    )
    direction_score = _finite_or_none(
        direction_prediction.get("ranking_score")
    )

    pruning = candidate.get("optimistic_replay_pruning_decision")
    pruning = pruning if isinstance(pruning, Mapping) else {}
    pruning_input = candidate.get("optimistic_replay_pruning_input")
    pruning_input = pruning_input if isinstance(pruning_input, Mapping) else {}
    optimistic = _finite_or_none(
        pruning.get(
            "optimistic_score_upper",
            pruning_input.get("optimistic_score_upper"),
        )
    )
    if optimistic is None:
        predicted = _finite_or_none(pruning_input.get("predicted_score"))
        error = _finite_or_none(pruning_input.get("score_upper_error"))
        if predicted is not None and error is not None and error >= 0.0:
            optimistic = predicted + error
    score = None
    for key in ("final_score", "predicted_score", "score", "estimated_score"):
        score = _finite_or_none(candidate.get(key))
        if score is not None:
            break
    return (
        direction_score if direction_score is not None else float("-inf"),
        optimistic if optimistic is not None else float("-inf"),
        score if score is not None else float("-inf"),
        -position,
    )


def select_plan_diverse_representatives(
    candidates: Sequence[Mapping[str, Any]],
    *,
    suppress_similar: bool = False,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """select plan diverse representatives."""


    clusters: Dict[str, List[Tuple[int, Dict[str, Any], str]]] = {}
    for position, raw_candidate in enumerate(candidates, start=1):
        candidate = dict(raw_candidate)
        cluster_id, cluster_source = _candidate_plan_cluster(candidate, position)
        candidate["replay_plan_cluster_id"] = cluster_id
        candidate["replay_plan_cluster_source"] = cluster_source
        clusters.setdefault(cluster_id, []).append(
            (position, candidate, cluster_source)
        )

    if not suppress_similar:
        representatives = []
        cluster_audit = []
        for cluster_id, members in clusters.items():
            member_ranks = [member[1].get("rank") for member in members]
            for _, candidate, _ in members:
                candidate["replay_plan_cluster_representative"] = True
            cluster_audit.append(
                {
                    "cluster_id": cluster_id,
                    "cluster_source": members[0][2],
                    "member_count": len(members),
                    "member_ranks": member_ranks,
                    "representative_rank": member_ranks[0],
                    "representative_ranks": member_ranks,
                    "suppressed_ranks": [],
                }
            )
        for position, raw_candidate in enumerate(candidates, start=1):
            cluster_id, cluster_source = _candidate_plan_cluster(
                raw_candidate,
                position,
            )
            candidate = dict(raw_candidate)
            candidate["replay_plan_cluster_id"] = cluster_id
            candidate["replay_plan_cluster_source"] = cluster_source
            candidate["replay_plan_cluster_representative"] = True
            representatives.append(candidate)
        return representatives, [], {
            "input_candidate_count": len(candidates),
            "plan_cluster_count": len(clusters),
            "representative_count": len(representatives),
            "suppressed_similar_candidate_count": 0,
            "selection_policy": "plan_cluster_audit_only_no_suppression",
            "missing_plan_policy": "fail_open_unique_cluster",
            "plan_shape_pruning_enabled": False,
            "clusters": cluster_audit,
        }

    representatives_with_position: List[Tuple[int, Dict[str, Any]]] = []
    suppressed: List[Dict[str, Any]] = []
    cluster_audit: List[Dict[str, Any]] = []
    for cluster_id, members in clusters.items():
        representative_position, representative, cluster_source = max(
            members,
            key=lambda item: _candidate_representative_priority(item[1], item[0]),
        )
        representative_rank = representative.get("rank")
        representative["replay_plan_cluster_representative"] = True
        representatives_with_position.append(
            (representative_position, representative)
        )
        suppressed_ranks: List[Any] = []
        for position, candidate, _ in members:
            if position == representative_position:
                continue
            candidate["replay_plan_cluster_representative"] = False
            candidate["replay_plan_cluster_representative_rank"] = representative_rank
            candidate["real_replay_suppressed_reason"] = (
                "similar_plan_cluster_nonrepresentative"
            )
            suppressed.append(candidate)
            suppressed_ranks.append(candidate.get("rank"))
        cluster_audit.append(
            {
                "cluster_id": cluster_id,
                "cluster_source": cluster_source,
                "member_count": len(members),
                "member_ranks": [member[1].get("rank") for member in members],
                "representative_rank": representative_rank,
                "suppressed_ranks": suppressed_ranks,
            }
        )


    representatives_with_position.sort(key=lambda item: item[0])
    representatives = [candidate for _, candidate in representatives_with_position]
    return representatives, suppressed, {
        "input_candidate_count": len(candidates),
        "plan_cluster_count": len(clusters),
        "representative_count": len(representatives),
        "suppressed_similar_candidate_count": len(suppressed),
        "selection_policy": (
            "one_per_audited_plan_cluster_by_validated_joint_direction_"
            "then_optimistic_upper_then_score"
        ),
        "missing_plan_policy": "fail_open_unique_cluster",
        "plan_shape_pruning_enabled": True,
        "clusters": cluster_audit,
    }


def decide_outer_acceptance(
    *,
    incumbent_db_qps: Any,
    candidate_db_qps: Any,
    incumbent_target_utilization: Any,
    candidate_target_utilization: Any,
    target_resource: Any,
    incumbent_resource_utilization: Any,
    candidate_resource_utilization: Any,
    non_target_bottleneck_threshold: Any,
    hard_gate_passed: bool,
    minimum_db_qps_improvement_ratio: Any = 0.0,
    required_acceptance_tier: str = "strict",
) -> OuterAcceptanceDecision:
    """decide outer acceptance."""


    incumbent_qps = _finite_or_none(incumbent_db_qps)
    candidate_qps = _finite_or_none(candidate_db_qps)
    incumbent_util = _finite_or_none(incumbent_target_utilization)
    candidate_util = _finite_or_none(candidate_target_utilization)
    target = str(target_resource).strip().lower()
    preferred_tier = str(required_acceptance_tier).strip().lower()
    required_tier = outer_acceptance_required_tier()
    threshold = _finite_or_none(non_target_bottleneck_threshold)
    minimum_qps_ratio = _finite_or_none(minimum_db_qps_improvement_ratio)
    observed_qps_ratio = (
        candidate_qps / incumbent_qps - 1.0
        if (
            incumbent_qps is not None
            and incumbent_qps > 0.0
            and candidate_qps is not None
        )
        else None
    )
    non_targets = [name for name in RESOURCE_NAMES if name != target]

    def _normalize_non_targets(values: Any) -> Optional[Dict[str, float]]:
        if not isinstance(values, Mapping):
            return None
        normalized: Dict[str, float] = {}
        for resource in non_targets:
            value = _finite_or_none(values.get(resource))
            if value is None:
                return None
            normalized[resource] = value
        return normalized

    incumbent_non_targets = _normalize_non_targets(incumbent_resource_utilization)
    candidate_non_targets = _normalize_non_targets(candidate_resource_utilization)
    incumbent_bottlenecks = (
        sorted(
            resource
            for resource, value in incumbent_non_targets.items()
            if threshold is not None and value >= threshold
        )
        if incumbent_non_targets is not None
        else None
    )
    candidate_bottlenecks = (
        sorted(
            resource
            for resource, value in candidate_non_targets.items()
            if threshold is not None and value >= threshold
        )
        if candidate_non_targets is not None
        else None
    )
    qps_up = bool(
        observed_qps_ratio is not None
        and (
            observed_qps_ratio > 0.0
            if minimum_qps_ratio is not None and minimum_qps_ratio <= 0.0
            else (
                minimum_qps_ratio is not None
                and observed_qps_ratio + 1e-12 >= minimum_qps_ratio
            )
        )
    )
    target_down = bool(
        incumbent_util is not None
        and candidate_util is not None
        and candidate_util < incumbent_util
    )
    non_target_up_resources = (
        sorted(
            resource
            for resource in non_targets
            if candidate_non_targets is not None
            and incumbent_non_targets is not None
            and candidate_non_targets[resource] > incumbent_non_targets[resource]
        )
        if candidate_non_targets is not None and incumbent_non_targets is not None
        else None
    )
    common = {
        "hard_gate_passed": bool(hard_gate_passed),
        "incumbent_db_qps": incumbent_qps,
        "candidate_db_qps": candidate_qps,
        "incumbent_target_utilization": incumbent_util,
        "candidate_target_utilization": candidate_util,
        "target_resource": target if target in RESOURCE_NAMES else None,
        "non_target_bottleneck_threshold": threshold,
        "incumbent_non_target_utilization": incumbent_non_targets,
        "candidate_non_target_utilization": candidate_non_targets,
        "non_target_bottleneck_resources": candidate_bottlenecks,
        "incumbent_non_target_bottlenecks": incumbent_bottlenecks,
        "candidate_non_target_bottlenecks": candidate_bottlenecks,
        "minimum_db_qps_improvement_ratio": minimum_qps_ratio,
        "observed_db_qps_improvement_ratio": observed_qps_ratio,
        "acceptance_tier": None,
        "required_acceptance_tier": required_tier,
        "preferred_acceptance_tier": preferred_tier,
        "acceptance_selection_policy": "same_batch_strict_then_secondary_then_fallback",
        "qps_up": qps_up,
        "target_down": target_down,
        "non_target_up_resources": non_target_up_resources,
        "accepted_shrink": False,
        "accepted_escape": False,
        "formal_state_mutation_allowed": False,
    }
    if not hard_gate_passed:
        return OuterAcceptanceDecision(False, "hard_gate_failed", **common)
    if preferred_tier not in {"strict", "secondary", "fallback"}:
        return OuterAcceptanceDecision(
            False, "invalid_required_acceptance_tier", **common
        )
    if target not in RESOURCE_NAMES:
        return OuterAcceptanceDecision(False, "invalid_target_resource", **common)
    if threshold is None or threshold <= 0.0:
        return OuterAcceptanceDecision(
            False,
            "invalid_non_target_bottleneck_threshold",
            **common,
        )
    if minimum_qps_ratio is None or minimum_qps_ratio < 0.0:
        return OuterAcceptanceDecision(
            False,
            "invalid_minimum_db_qps_improvement_ratio",
            **common,
        )
    if incumbent_qps is None or candidate_qps is None:
        return OuterAcceptanceDecision(False, "missing_or_nonfinite_db_qps", **common)
    if incumbent_util is None or candidate_util is None:
        return OuterAcceptanceDecision(
            False,
            "missing_or_nonfinite_target_utilization",
            **common,
        )
    if incumbent_non_targets is None or candidate_non_targets is None:
        return OuterAcceptanceDecision(
            False,
            "missing_or_nonfinite_non_target_utilization",
            **common,
        )
    if incumbent_bottlenecks:
        return OuterAcceptanceDecision(
            False,
            "incumbent_non_target_resource_is_bottleneck",
            **common,
        )
    if candidate_bottlenecks:
        return OuterAcceptanceDecision(
            False,
            "non_target_resource_became_bottleneck",
            **common,
        )
    if observed_qps_ratio is None:
        return OuterAcceptanceDecision(
            False,
            "missing_or_nonfinite_db_qps",
            **common,
        )
    if minimum_qps_ratio <= 0.0 and observed_qps_ratio <= 0.0:
        return OuterAcceptanceDecision(
            False,
            "db_qps_not_strictly_higher",
            **common,
        )
    if observed_qps_ratio + 1e-12 < minimum_qps_ratio:
        return OuterAcceptanceDecision(
            False,
            "db_qps_improvement_below_minimum",
            **common,
        )
    if target_down and non_target_up_resources:
        achieved_tier = "strict"
        reason = "accepted_strict_qps_up_target_down_non_target_up"
    elif target_down:
        achieved_tier = "secondary"
        reason = "accepted_secondary_qps_up_target_down"
    else:
        achieved_tier = "fallback"
        reason = "accepted_fallback_qps_up"
    common["acceptance_tier"] = achieved_tier
    common["accepted_shrink"] = True
    common["formal_state_mutation_allowed"] = True
    return OuterAcceptanceDecision(
        True,
        reason,
        **common,
    )


def decide_validation_acceptance(
    incumbent: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    target_resource: str,
    hard_gate_passed: bool,
    db_qps_key: str = "db_qps",
    utilization_key: str = "utilization_pct",
    non_target_bottleneck_threshold: float = (
        DEFAULT_NON_TARGET_BOTTLENECK_UTILIZATION_PCT
    ),
    minimum_db_qps_improvement_ratio: float = 0.0,
    required_acceptance_tier: str = "strict",
) -> OuterAcceptanceDecision:
    """decide validation acceptance."""

    target = str(target_resource).strip().lower()
    if target not in {"cpu", "memory", "io"}:
        return decide_outer_acceptance(
            incumbent_db_qps=incumbent.get(db_qps_key),
            candidate_db_qps=candidate.get(db_qps_key),
            incumbent_target_utilization=None,
            candidate_target_utilization=None,
            target_resource=target,
            incumbent_resource_utilization=incumbent.get(utilization_key),
            candidate_resource_utilization=candidate.get(utilization_key),
            non_target_bottleneck_threshold=non_target_bottleneck_threshold,
            hard_gate_passed=False,
            minimum_db_qps_improvement_ratio=(
                minimum_db_qps_improvement_ratio
            ),
            required_acceptance_tier=required_acceptance_tier,
        )
    incumbent_util = incumbent.get(utilization_key) or {}
    candidate_util = candidate.get(utilization_key) or {}
    return decide_outer_acceptance(
        incumbent_db_qps=incumbent.get(db_qps_key),
        candidate_db_qps=candidate.get(db_qps_key),
        incumbent_target_utilization=(
            incumbent_util.get(target) if isinstance(incumbent_util, Mapping) else None
        ),
        candidate_target_utilization=(
            candidate_util.get(target) if isinstance(candidate_util, Mapping) else None
        ),
        target_resource=target,
        incumbent_resource_utilization=incumbent_util,
        candidate_resource_utilization=candidate_util,
        non_target_bottleneck_threshold=non_target_bottleneck_threshold,
        hard_gate_passed=hard_gate_passed,
        minimum_db_qps_improvement_ratio=minimum_db_qps_improvement_ratio,
        required_acceptance_tier=required_acceptance_tier,
    )


def resolve_parameter_transform(
    name: str,
    explicit_transform: Optional[str] = None,
) -> str:
    """resolve parameter transform."""

    transform = str(explicit_transform or "").strip().lower()
    if not transform:
        transform = "log" if name in LOG_COST_PARAMETERS else "linear"
    if transform not in {"linear", "log"}:
        raise ValueError(f": {name}={transform!r}")
    return transform


def transformed_midpoint(low: float, high: float, transform: str) -> float:
    """transformed midpoint."""

    low_value = float(low)
    high_value = float(high)
    if not math.isfinite(low_value) or not math.isfinite(high_value):
        raise ValueError("")
    if high_value < low_value:
        raise ValueError("")
    normalized_transform = resolve_parameter_transform("", transform)
    if normalized_transform == "log":
        if low_value <= 0.0:
            raise ValueError("log  0")
        return math.sqrt(low_value * high_value)
    return low_value + (high_value - low_value) / 2.0


def _replace_range(param_range: Any, **changes: Any) -> Any:
    if not is_dataclass(param_range):
        raise TypeError(" dataclass ")
    fields = getattr(param_range, "__dataclass_fields__", {})
    filtered = {name: value for name, value in changes.items() if name in fields}
    return replace(param_range, **filtered)


def binary_shrink_parameter_ranges(
    ranges: Mapping[str, Any],
    accepted_anchor: Mapping[str, Any],
    active_params: Sequence[str],
) -> Tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]:
    """binary shrink parameter ranges."""


    updated = dict(ranges)
    details: Dict[str, Dict[str, Any]] = {}
    for name in active_params:
        if name not in ranges or name not in accepted_anchor:
            continue
        current = ranges[name]
        low = float(current.min_value)
        high = float(current.max_value)
        transform = resolve_parameter_transform(
            name,
            getattr(current, "transform", None),
        )
        midpoint = transformed_midpoint(low, high, transform)
        anchor = float(accepted_anchor[name])
        if not math.isfinite(anchor):
            raise ValueError(f": {name}={anchor!r}")
        anchor = min(high, max(low, anchor))
        if anchor <= midpoint:
            selected_half = "left"
            new_low, new_high = low, midpoint
        else:
            selected_half = "right"
            new_low, new_high = midpoint, high
        changes: Dict[str, Any] = {
            "min_value": new_low,
            "max_value": new_high,
            "transform": transform,
        }
        updated[name] = _replace_range(current, **changes)
        details[name] = {
            "old_min": low,
            "old_max": high,
            "midpoint": midpoint,
            "transform": transform,
            "anchor": anchor,
            "selected_half": selected_half,
            "new_min": new_low,
            "new_max": new_high,
        }
    return updated, details
