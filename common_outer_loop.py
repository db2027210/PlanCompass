"""Module."""


from __future__ import annotations

import copy
import math
from dataclasses import asdict, dataclass, is_dataclass, replace
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from resource_utility import ResourceUtilityCalculator


LOG_COST_PARAMETERS = frozenset(
    {
        "io_block_read_cost",
        "memory_block_read_cost",
        "row_evaluate_cost",
        "key_compare_cost",
        "disk_temptable_create_cost",
        "disk_temptable_row_cost",
        "memory_temptable_create_cost",
        "memory_temptable_row_cost",
    }
)

RESOURCE_NAMES = ("cpu", "memory", "io")
DEFAULT_NON_TARGET_BOTTLENECK_UTILIZATION = 0.8
ACCEPTANCE_TIER_RANK = {"fallback_qps": 1, "qps_target": 2, "strict_three_level": 3}
DEFAULT_ACCEPTANCE_TIER = "strict_three_level"
ACCEPTANCE_TIER_DOWNGRADE_ORDER = {
    "strict_three_level": "qps_target",
    "qps_target": "fallback_qps",
}
DEFAULT_ACCEPTANCE_TIER_DOWNGRADE_AFTER_ROUNDS = 2
GLOBAL_REAL_REPLAY_TOP_K_MAX = 5


def cap_real_replay_top_k(
    requested_top_k: Any,
    *,
    available_count: Optional[int] = None,
) -> int:
    """Apply the shared formal ceiling of five real candidate replays."""
    try:
        requested = int(requested_top_k)
    except (TypeError, ValueError) as error:
        raise ValueError("real replay top-k must be an integer") from error
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
    required_acceptance_tier: str
    required_acceptance_tier_rank: int
    satisfied_acceptance_tier: Optional[str]
    satisfied_acceptance_tier_rank: int
    qps_strictly_increased: bool
    target_bottleneck_strictly_decreased: Optional[bool]
    all_non_target_resources_strictly_increased: Optional[bool]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OuterShrinkProgress:
    """Immutable progress for the ten accepted-shrink outer-loop target."""

    ranges: Dict[str, Any]
    accepted_shrink_count: int
    rejected_count: int
    target_accepted_shrinks: int
    complete: bool
    range_changes: Dict[str, Dict[str, Any]]


@dataclass(frozen=True)
class AcceptanceTierProgress:
    """Persistent three-tier outer-acceptance state after one outer round."""

    acceptance_tier: str
    consecutive_failed_rounds: int
    downgraded: bool
    downgraded_from: Optional[str]
    downgraded_to: Optional[str]
    downgrade_after_rounds: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def advance_acceptance_tier(
    *,
    acceptance_tier: str,
    consecutive_failed_rounds: int,
    accepted: bool,
    downgrade_after_rounds: int = DEFAULT_ACCEPTANCE_TIER_DOWNGRADE_AFTER_ROUNDS,
) -> AcceptanceTierProgress:
    """Reset on acceptance; otherwise downgrade after exactly N misses.

    The fallback tier never degrades further.  Its miss counter remains
    bounded at ``downgrade_after_rounds`` so persisted campaign state is
    deterministic and compact.
    """
    tier = str(acceptance_tier).strip().lower()
    if tier not in ACCEPTANCE_TIER_RANK:
        raise ValueError(f"invalid acceptance_tier: {acceptance_tier!r}")
    if downgrade_after_rounds <= 0:
        raise ValueError("downgrade_after_rounds must be positive")
    if consecutive_failed_rounds < 0:
        raise ValueError("consecutive_failed_rounds must be non-negative")
    if (
        tier in ACCEPTANCE_TIER_DOWNGRADE_ORDER
        and consecutive_failed_rounds >= downgrade_after_rounds
    ):
        raise ValueError(
            "degradable tier cannot persist at or above downgrade threshold"
        )
    if accepted:
        return AcceptanceTierProgress(
            acceptance_tier=tier,
            consecutive_failed_rounds=0,
            downgraded=False,
            downgraded_from=None,
            downgraded_to=None,
            downgrade_after_rounds=downgrade_after_rounds,
        )
    failed = consecutive_failed_rounds + 1
    next_tier = ACCEPTANCE_TIER_DOWNGRADE_ORDER.get(tier)
    if next_tier is not None and failed >= downgrade_after_rounds:
        return AcceptanceTierProgress(
            acceptance_tier=next_tier,
            consecutive_failed_rounds=0,
            downgraded=True,
            downgraded_from=tier,
            downgraded_to=next_tier,
            downgrade_after_rounds=downgrade_after_rounds,
        )
    return AcceptanceTierProgress(
        acceptance_tier=tier,
        consecutive_failed_rounds=min(failed, downgrade_after_rounds),
        downgraded=False,
        downgraded_from=None,
        downgraded_to=None,
        downgrade_after_rounds=downgrade_after_rounds,
    )


def advance_outer_shrink_progress(
    *,
    ranges: Mapping[str, Any],
    accepted_shrink_count: int,
    rejected_count: int,
    target_accepted_shrinks: int,
    decision: OuterAcceptanceDecision,
    candidate_params: Mapping[str, Any],
    active_params: Sequence[str],
) -> OuterShrinkProgress:
    """Shrink all active ranges exactly once per accepted real candidate.

    Rejections are counted for audit but never consume the accepted-shrink
    target and never change a range.
    """
    if target_accepted_shrinks <= 0:
        raise ValueError("target_accepted_shrinks must be positive")
    if accepted_shrink_count < 0 or rejected_count < 0:
        raise ValueError("outer counters must be non-negative")
    if accepted_shrink_count > target_accepted_shrinks:
        raise ValueError("accepted_shrink_count cannot exceed target")
    if accepted_shrink_count == target_accepted_shrinks:
        return OuterShrinkProgress(
            ranges=copy.deepcopy(dict(ranges)),
            accepted_shrink_count=accepted_shrink_count,
            rejected_count=rejected_count,
            target_accepted_shrinks=target_accepted_shrinks,
            complete=True,
            range_changes={},
        )
    if decision.accepted:
        updated, changes = binary_shrink_parameter_ranges(
            ranges,
            candidate_params,
            active_params,
        )
        accepted = accepted_shrink_count + 1
        rejected = rejected_count
    else:
        updated = copy.deepcopy(dict(ranges))
        changes = {}
        accepted = accepted_shrink_count
        rejected = rejected_count + 1
    return OuterShrinkProgress(
        ranges=updated,
        accepted_shrink_count=accepted,
        rejected_count=rejected,
        target_accepted_shrinks=target_accepted_shrinks,
        complete=accepted >= target_accepted_shrinks,
        range_changes=changes,
    )


@dataclass(frozen=True)
class OptimisticReplayPruningDecision:
    """OptimisticReplayPruningDecision."""

    pruned: bool
    reason: str
    bounds_usable: bool
    predicted_resources: Optional[Dict[str, float]]
    asymmetric_resource_errors: Optional[Dict[str, Dict[str, float]]]
    error_bound_provenance: Optional[Dict[str, Any]]
    utilization_intervals: Optional[Dict[str, Dict[str, float]]]
    per_resource_score_upper: Optional[Dict[str, float]]
    per_resource_argmax: Optional[Dict[str, Dict[str, Any]]]
    utility_bound_proof: Optional[Dict[str, Any]]
    score_upper: Optional[float]
    best_utility: Optional[float]
    score_upper_cannot_beat_best: Optional[bool]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _finite_or_none(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def sanitize_optimistic_pruning_payload(value: Any) -> Any:
    """Remove legacy QPS-bound values while retaining a schema-only audit."""
    if not isinstance(value, Mapping):
        return copy.deepcopy(value)
    violating = _find_qps_field_paths(value)
    if violating:
        return {
            "schema_violation": "legacy_qps_bound_key_present",
            "violating_keys": violating,
        }
    return copy.deepcopy(dict(value))


def _find_qps_field_paths(value: Any, prefix: str = "") -> List[str]:
    """Return every nested pruning-field path whose key mentions QPS."""
    paths: List[str] = []
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key)
            path = f"{prefix}.{key}" if prefix else key
            if "qps" in key.lower():
                paths.append(path)
            paths.extend(_find_qps_field_paths(item, path))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            path = f"{prefix}[{index}]" if prefix else f"[{index}]"
            paths.extend(_find_qps_field_paths(item, path))
    return sorted(set(paths))


_ERROR_BOUND_BINDING_KEYS = {
    "artifact_sha256", "freeze_id", "benchmark_id", "scenario_id",
    "resource_envelope_sha256", "domain_id",
}


def _valid_sha256(value: Any) -> bool:
    text = str(value or "").lower()
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text)


def _validate_error_bound_provenance(
    provenance: Any,
    expected_bindings: Any,
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """Verify a pinned immutable joint-error artifact against trusted context."""
    if not isinstance(provenance, Mapping) or not isinstance(expected_bindings, Mapping):
        return False, "missing_error_bound_provenance_keep_for_real_replay", None
    expected = copy.deepcopy(dict(expected_bindings))
    bindings = provenance.get("bindings")
    if (
        provenance.get("schema_version") != "resource_error_bound_provenance_v1"
        or provenance.get("immutable") is not True
        or provenance.get("simultaneous_coverage_guaranteed") is not True
        or list(provenance.get("resources") or []) != ["cpu", "memory", "io"]
        or not isinstance(bindings, Mapping)
        or set(expected) != _ERROR_BOUND_BINDING_KEYS
        or set(bindings) != _ERROR_BOUND_BINDING_KEYS - {"artifact_sha256"}
        or not _valid_sha256(provenance.get("artifact_sha256"))
        or not _valid_sha256(expected.get("artifact_sha256"))
        or not _valid_sha256(bindings.get("resource_envelope_sha256"))
        or not _valid_sha256(expected.get("resource_envelope_sha256"))
        or any(
            not str(bindings.get(name) or "").strip()
            for name in _ERROR_BOUND_BINDING_KEYS - {
                "artifact_sha256", "resource_envelope_sha256"
            }
        )
    ):
        return False, "invalid_error_bound_provenance_keep_for_real_replay", None
    actual = {"artifact_sha256": str(provenance["artifact_sha256"]).lower()}
    actual.update({name: str(bindings[name]) for name in bindings})
    normalized_expected = {name: str(value) for name, value in expected.items()}
    normalized_expected["artifact_sha256"] = normalized_expected["artifact_sha256"].lower()
    normalized_expected["resource_envelope_sha256"] = normalized_expected[
        "resource_envelope_sha256"
    ].lower()
    actual["resource_envelope_sha256"] = actual["resource_envelope_sha256"].lower()
    if actual != normalized_expected:
        return False, "error_bound_provenance_binding_mismatch_keep_for_real_replay", None
    return True, "verified", copy.deepcopy(dict(provenance))


def sanitize_optimistic_pruning_metadata_tree(value: Any) -> Any:
    """Recursively sanitize only pruning payloads, preserving canonical QPS metrics."""
    if isinstance(value, Mapping):
        result: Dict[str, Any] = {}
        for key, item in value.items():
            if str(key) == "optimistic_replay_pruning_input":
                result[str(key)] = sanitize_optimistic_pruning_payload(item)
            else:
                result[str(key)] = sanitize_optimistic_pruning_metadata_tree(item)
        return result
    if isinstance(value, list):
        return [sanitize_optimistic_pruning_metadata_tree(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_optimistic_pruning_metadata_tree(item) for item in value]
    return copy.deepcopy(value)


def decide_optimistic_replay_pruning(
    *,
    predicted_resources: Any,
    asymmetric_resource_errors: Any,
    capabilities: Any,
    utility_config: Optional[Mapping[str, Any]],
    best_utility: Any,
    calibration_ready: bool,
    calibration_scope_matches: bool,
    simultaneous_coverage_guaranteed: bool,
    error_bound_provenance: Any = None,
    expected_error_bound_bindings: Any = None,
) -> OptimisticReplayPruningDecision:
    """Maximize the configured Utility over the calibrated CPU×Memory×IO box."""

    common: Dict[str, Any] = {
        "predicted_resources": None,
        "asymmetric_resource_errors": None,
        "error_bound_provenance": None,
        "utilization_intervals": None,
        "per_resource_score_upper": None,
        "per_resource_argmax": None,
        "utility_bound_proof": None,
        "score_upper": None,
        "best_utility": _finite_or_none(best_utility),
        "score_upper_cannot_beat_best": None,
    }
    if _find_qps_field_paths({
        "predicted_resources": predicted_resources,
        "asymmetric_resource_errors": asymmetric_resource_errors,
        "error_bound_provenance": error_bound_provenance,
    }):
        return OptimisticReplayPruningDecision(
            False, "qps_error_field_present_keep_for_real_replay", False, **common
        )
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
    provenance_valid, provenance_reason, verified_provenance = (
        _validate_error_bound_provenance(
            error_bound_provenance, expected_error_bound_bindings
        )
    )
    if not provenance_valid:
        return OptimisticReplayPruningDecision(
            False, provenance_reason, False, **common
        )
    common["error_bound_provenance"] = verified_provenance
    required_resources = {"cpu", "memory", "io"}
    if not all(
        isinstance(item, Mapping)
        for item in (predicted_resources, asymmetric_resource_errors, capabilities)
    ) or common["best_utility"] is None or any(
        set(item) != required_resources
        for item in (predicted_resources, asymmetric_resource_errors, capabilities)
    ):
        return OptimisticReplayPruningDecision(
            False, "resource_schema_must_exactly_cover_cpu_memory_io", False, **common
        )

    resources: Dict[str, float] = {}
    errors: Dict[str, Dict[str, float]] = {}
    intervals: Dict[str, Dict[str, float]] = {}
    for name in ("cpu", "memory", "io"):
        predicted = _finite_or_none(predicted_resources.get(name))
        capability = _finite_or_none(capabilities.get(name))
        error = asymmetric_resource_errors.get(name)
        if (
            not isinstance(error, Mapping)
            or error.get("valid") is not True
            or error.get("scope_matches") is not True
        ):
            return OptimisticReplayPruningDecision(
                False, "resource_error_not_valid_or_scope_mismatch", False, **common
            )
        lower_error = (
            _finite_or_none(error.get("lower", error.get("lower_error")))
            if isinstance(error, Mapping) else None
        )
        upper_error = (
            _finite_or_none(error.get("upper", error.get("upper_error")))
            if isinstance(error, Mapping) else None
        )
        if (
            predicted is None or predicted < 0.0
            or capability is None or capability <= 0.0
            or lower_error is None or lower_error < 0.0
            or upper_error is None or upper_error < 0.0
            or predicted - lower_error < 0.0
        ):
            return OptimisticReplayPruningDecision(
                False, "missing_or_invalid_resource_error_keep_for_real_replay", False,
                **common,
            )
        resources[name] = predicted
        errors[name] = {"lower": lower_error, "upper": upper_error}
        intervals[name] = {
            "lower": (predicted - lower_error) / capability,
            "upper": (predicted + upper_error) / capability,
        }
    common["predicted_resources"] = resources
    common["asymmetric_resource_errors"] = errors
    common["utilization_intervals"] = intervals

    cfg = dict(utility_config or {})
    try:
        calculator = ResourceUtilityCalculator(
            reward_type=str(cfg.get("reward_type", "log")),
            penalty_type=str(cfg.get("penalty_type", "power")),
            use_continuous=bool(cfg.get("use_continuous", False)),
            continuous_type=str(cfg.get("continuous_type", "gaussian")),
            overload_threshold=float(cfg.get("overload_threshold", 0.8)),
            score_mode=str(cfg.get("score_mode", "band_target")),
            resource_weights=cfg.get("resource_weights"),
            resource_reward_weights=cfg.get("resource_reward_weights"),
            resource_penalty_weights=cfg.get("resource_penalty_weights"),
            resource_target_ranges=cfg.get("resource_target_ranges"),
        )
        per_resource_upper: Dict[str, float] = {}
        per_resource_argmax: Dict[str, Dict[str, Any]] = {}
        for name, interval in intervals.items():
            certified = calculator.certified_resource_score_upper(
                name, interval["lower"], interval["upper"]
            )
            per_resource_upper[name] = float(certified["score_upper"])
            per_resource_argmax[name] = {
                "utilization": certified["argmax_utilization"],
                "kind": certified["argmax_kind"],
                "proof": certified["proof"],
            }
        score_upper = sum(per_resource_upper.values())
    except (KeyError, TypeError, ValueError, OverflowError):
        return OptimisticReplayPruningDecision(
            False, "invalid_utility_configuration_keep_for_real_replay", False, **common
        )
    if not math.isfinite(score_upper):
        return OptimisticReplayPruningDecision(
            False, "invalid_utility_upper_bound_keep_for_real_replay", False, **common
        )
    common["per_resource_score_upper"] = per_resource_upper
    common["per_resource_argmax"] = per_resource_argmax
    common["utility_bound_proof"] = {
        "method": "separable_cpu_memory_io_box_true_max_v2",
        "joint_box_max_is_sum_of_certified_per_resource_bounds": True,
        "uncertifiable_discontinuity_fails_open": True,
    }
    common["score_upper"] = score_upper
    common["score_upper_cannot_beat_best"] = score_upper <= common["best_utility"]
    if score_upper <= common["best_utility"]:
        return OptimisticReplayPruningDecision(
            True, "utility_score_upper_not_above_current_best", True, **common
        )
    return OptimisticReplayPruningDecision(
        False, "utility_improvement_possible_in_joint_resource_box", True, **common
    )


def prune_ranked_candidates_for_replay(
    candidates: Sequence[Mapping[str, Any]],
    *,
    capabilities: Mapping[str, Any],
    utility_config: Optional[Mapping[str, Any]],
    best_utility: Any,
    expected_error_bound_bindings: Optional[Mapping[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """prune ranked candidates for replay."""


    kept: List[Dict[str, Any]] = []
    pruned: List[Dict[str, Any]] = []
    checks: List[Dict[str, Any]] = []
    for position, raw_candidate in enumerate(candidates, start=1):
        candidate = dict(raw_candidate)
        payload = candidate.get("optimistic_replay_pruning_input")
        audit_payload: Optional[Dict[str, Any]] = None
        if not isinstance(payload, Mapping):
            decision = OptimisticReplayPruningDecision(
                pruned=False,
                reason="missing_pruning_input_keep_for_real_replay",
                bounds_usable=False,
                predicted_resources=None,
                asymmetric_resource_errors=None,
                error_bound_provenance=None,
                utilization_intervals=None,
                per_resource_score_upper=None,
                per_resource_argmax=None,
                utility_bound_proof=None,
                score_upper=None,
                best_utility=_finite_or_none(best_utility),
                score_upper_cannot_beat_best=None,
            )
        else:
            legacy_qps_keys = _find_qps_field_paths(payload)
            if legacy_qps_keys:
                audit_payload = sanitize_optimistic_pruning_payload(payload)
                candidate["optimistic_replay_pruning_input"] = copy.deepcopy(
                    audit_payload
                )
                decision = OptimisticReplayPruningDecision(
                    pruned=False,
                    reason="legacy_qps_bound_schema_violation_keep_for_real_replay",
                    bounds_usable=False,
                    predicted_resources=None,
                    asymmetric_resource_errors=None,
                    error_bound_provenance=None,
                    utilization_intervals=None,
                    per_resource_score_upper=None,
                    per_resource_argmax=None,
                    utility_bound_proof=None,
                    score_upper=None,
                    best_utility=_finite_or_none(best_utility),
                    score_upper_cannot_beat_best=None,
                )
            else:
                audit_payload = copy.deepcopy(dict(payload))
                decision = decide_optimistic_replay_pruning(
                    predicted_resources=payload.get("predicted_resources"),
                    asymmetric_resource_errors=payload.get(
                        "asymmetric_resource_errors"
                    ),
                    capabilities=capabilities,
                    utility_config=utility_config,
                    best_utility=best_utility,
                    calibration_ready=payload.get("calibration_ready") is True,
                    calibration_scope_matches=(
                        payload.get("calibration_scope_matches") is True
                    ),
                    simultaneous_coverage_guaranteed=(
                        payload.get("simultaneous_coverage_guaranteed") is True
                    ),
                    error_bound_provenance=payload.get("error_bound_provenance"),
                    expected_error_bound_bindings=expected_error_bound_bindings,
                )
        decision_record = {
            "candidate_position": position,
            "candidate_rank": candidate.get("rank"),
            "pruning_input": (
                audit_payload if isinstance(payload, Mapping) else None
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
    acceptance_tier: str = DEFAULT_ACCEPTANCE_TIER,
) -> OuterAcceptanceDecision:
    """decide outer acceptance."""

    incumbent_qps = _finite_or_none(incumbent_db_qps)
    candidate_qps = _finite_or_none(candidate_db_qps)
    incumbent_util = _finite_or_none(incumbent_target_utilization)
    candidate_util = _finite_or_none(candidate_target_utilization)
    target = str(target_resource).strip().lower()
    required_tier = str(acceptance_tier).strip().lower()
    if required_tier not in ACCEPTANCE_TIER_RANK:
        required_tier = "invalid"
    threshold = _finite_or_none(non_target_bottleneck_threshold)
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
    qps_up = (
        incumbent_qps is not None
        and candidate_qps is not None
        and candidate_qps > incumbent_qps
    )
    target_down = (
        candidate_util < incumbent_util
        if incumbent_util is not None and candidate_util is not None
        else None
    )
    non_targets_up = (
        all(candidate_non_targets[name] > incumbent_non_targets[name] for name in non_targets)
        if incumbent_non_targets is not None and candidate_non_targets is not None
        else None
    )
    satisfied_tier: Optional[str] = None
    if qps_up:
        satisfied_tier = "fallback_qps"
        if target_down is True:
            satisfied_tier = "qps_target"
            if non_targets_up is True:
                satisfied_tier = "strict_three_level"
    satisfied_rank = ACCEPTANCE_TIER_RANK.get(satisfied_tier or "", 0)
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
        "required_acceptance_tier": required_tier,
        "required_acceptance_tier_rank": ACCEPTANCE_TIER_RANK.get(required_tier, 0),
        "satisfied_acceptance_tier": satisfied_tier,
        "satisfied_acceptance_tier_rank": satisfied_rank,
        "qps_strictly_increased": qps_up,
        "target_bottleneck_strictly_decreased": target_down,
        "all_non_target_resources_strictly_increased": non_targets_up,
    }
    if not hard_gate_passed:
        return OuterAcceptanceDecision(False, "hard_gate_failed", **common)
    if target not in RESOURCE_NAMES:
        return OuterAcceptanceDecision(False, "invalid_target_resource", **common)
    if required_tier not in ACCEPTANCE_TIER_RANK:
        return OuterAcceptanceDecision(False, "invalid_acceptance_tier", **common)
    if threshold is None or threshold <= 0.0:
        return OuterAcceptanceDecision(
            False,
            "invalid_non_target_bottleneck_threshold",
            **common,
        )
    if incumbent_qps is None or candidate_qps is None:
        return OuterAcceptanceDecision(False, "missing_or_nonfinite_db_qps", **common)
    if required_tier != "fallback_qps" and (incumbent_util is None or candidate_util is None):
        return OuterAcceptanceDecision(
            False,
            "missing_or_nonfinite_target_utilization",
            **common,
        )
    if required_tier == "strict_three_level" and (
        incumbent_non_targets is None or candidate_non_targets is None
    ):
        return OuterAcceptanceDecision(
            False,
            "missing_or_nonfinite_non_target_utilization",
            **common,
        )
    if not qps_up:
        return OuterAcceptanceDecision(False, "db_qps_not_strictly_higher", **common)
    if satisfied_rank < ACCEPTANCE_TIER_RANK[required_tier]:
        if required_tier == "strict_three_level" and target_down is True:
            reason = "non_target_resources_not_all_strictly_higher"
        elif required_tier in {"strict_three_level", "qps_target"}:
            reason = "target_bottleneck_not_strictly_lower"
        else:
            reason = "required_acceptance_tier_not_satisfied"
        return OuterAcceptanceDecision(
            False, reason, **common,
        )
    return OuterAcceptanceDecision(
        True,
        f"accepted_{satisfied_tier}_required_{required_tier}",
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
        DEFAULT_NON_TARGET_BOTTLENECK_UTILIZATION
    ),
    acceptance_tier: str = DEFAULT_ACCEPTANCE_TIER,
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
            acceptance_tier=acceptance_tier,
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
        acceptance_tier=acceptance_tier,
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
