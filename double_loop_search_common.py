"""Module."""


from __future__ import annotations

import copy
import json
import math
import os
import threading
import time
from dataclasses import asdict, dataclass, field, is_dataclass, replace
from typing import Any, Callable, Dict, List, Literal, Mapping, Optional, Sequence, Tuple

from common_outer_loop import (
    binary_shrink_parameter_ranges,
    cap_real_replay_top_k,
    decide_outer_acceptance,
)

Params = Dict[str, float]
Resource = Dict[str, float]
Evaluator = Callable[[Any, Params], Any]
ScoreFunc = Callable[[Resource, Any], float]
WorkloadPruner = Callable[[Any, Sequence[str]], Sequence[str]]
RealCandidateEligibilityFunc = Callable[[Resource, Any, Resource, Any], bool]
OuterCandidateAcceptanceFunc = Callable[[Resource, Any, Resource, Any], Any]
ResourceBoxUpperBoundFunc = Callable[[Resource, Resource], Any]
ReplayContinuationRequiredFunc = Callable[[], Any]


class _InnerIterationJsonlWriter:
    """Thread-safe, flush-on-every-row writer for plotting inner-loop progress."""

    def __init__(self, path: str) -> None:
        self.path = os.path.abspath(path)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._handle = open(self.path, "w", encoding="utf-8")
        self._lock = threading.Lock()

    def write(self, record: Dict[str, Any]) -> None:
        row = dict(record)
        row.setdefault("timestamp", time.strftime("%Y-%m-%d %H:%M:%S"))
        with self._lock:
            self._handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            self._handle.flush()

    def close(self) -> None:
        with self._lock:
            self._handle.close()


@dataclass(frozen=True)
class ParameterRange:
    """ParameterRange."""


    min_value: float
    max_value: float
    step: Optional[float] = None
    discrete_count: int = 15
    priority: int = 1
    values: Optional[Tuple[float, ...]] = None
    transform: Literal["linear", "log"] = "linear"

    def __post_init__(self) -> None:
        if self.max_value < self.min_value:
            raise ValueError("max_value  min_value")
        if self.discrete_count < 2:
            raise ValueError("discrete_count  2")
        if self.step is not None and self.step <= 0.0:
            raise ValueError("step  0")
        if self.transform not in {"linear", "log"}:
            raise ValueError("transform  linear  log")
        if self.transform == "log" and self.min_value <= 0.0:
            raise ValueError("log transform  min_value  0")
        if self.values is not None:
            values = tuple(sorted({float(value) for value in self.values}))
            if len(values) < 2:
                raise ValueError("values ")
            object.__setattr__(self, "values", values)

    @property
    def width(self) -> float:
        return self.max_value - self.min_value


@dataclass
class OuterLoopConfig:
    """OuterLoopConfig."""

    max_rounds: int = 6
    # Per-round real replay budget. Zero still means "all", but the shared
    # campaign contract caps every algorithm at five real candidate replays.
    real_validation_top_k: int = 5
    real_validation_top_fraction: float = 0.25
    topk_controller: Literal["fixed", "decision_tree"] = "decision_tree"
    adaptive_topk_min_k: int = 5
    adaptive_topk_batch_size: int = 1
    adaptive_topk_inversion_threshold: float = 0.10
    adaptive_topk_score_tie_margin: float = 0.01
    selection_policy: Literal["estimated_top1", "real_score_topk"] = "real_score_topk"
    # 0 disables rejection-patience early stop.  A rejected inner best always
    # returns control to at least the next complete small-loop search.
    no_improvement_patience: int = 0
    score_tolerance: float = 1e-8
    epsilon: float = 1e-4

    target_resource: Optional[Literal["cpu", "memory", "io"]] = None

    def __post_init__(self) -> None:
        self.real_validation_top_k = cap_real_replay_top_k(
            self.real_validation_top_k
        )
        self.adaptive_topk_min_k = cap_real_replay_top_k(
            self.adaptive_topk_min_k
        )


@dataclass
class ConvergedPoint:
    """ConvergedPoint."""

    subset_id: str
    params: Params
    estimated_resource: Resource
    estimated_score: float
    active_subset: Tuple[str, ...]
    fixed_params: Params
    start_params: Params
    current_bounds: Dict[str, ParameterRange]
    source_subsets: List[Tuple[str, ...]]
    iterations: int
    converged: bool
    improving_steps: int
    estimated_db_qps: Optional[float]
    estimated_cpu: Optional[float]
    estimated_memory: Optional[float]
    estimated_io: Optional[float]
    iteration_trace: List[Dict[str, Any]]
    failure_reason: str = ""
    # This key is consulted only after the primary resource Score compares
    # equal.  The coordinate-grid optimizer uses it to prefer candidates with
    # stronger measured support instead of inheriting thread completion order.
    ranking_tiebreak: Tuple[float, ...] = field(default_factory=tuple)
    ranking_tiebreak_details: Dict[str, Any] = field(default_factory=dict)
    # Compact full-workload plan identity captured by Algorithm B's estimator.
    # The complete EXPLAIN payload is intentionally not retained for every
    # grid point; selected Top-K points are re-probed for their full audit.
    plan_fingerprint: str = ""

    @property
    def epochs(self) -> int:
        """epochs."""
        return self.iterations


@dataclass
class RealValidation:
    """RealValidation."""

    params: Params
    resource: Resource
    estimated_resource: Resource
    score: float
    estimated_score: float
    estimated_rank: int
    active_subset: Tuple[str, ...]
    source_subsets: List[Tuple[str, ...]]
    successful: bool
    failure_reason: str = ""
    raw_result: Any = None
    outer_accepted: bool = False
    outer_acceptance_reason: str = "not_evaluated"
    outer_acceptance_details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OuterRound:
    """OuterRound."""

    round_index: int
    common_start_params: Params
    accepted_anchor_after: Params
    ranges_before: Dict[str, ParameterRange]
    ranges_after: Dict[str, ParameterRange]
    subset_count: int
    subset_runs: List[ConvergedPoint]
    converged_points: List[ConvergedPoint]
    validated_points: List[RealValidation]
    best_real_score: float
    best_params: Params
    binary_shrink_subset: Tuple[str, ...]
    improved: bool
    small_loop_best: Optional[RealValidation] = None
    outer_accepted: bool = False
    outer_acceptance_reason: str = "no_small_loop_best"
    outer_acceptance_details: Dict[str, Any] = field(default_factory=dict)
    adaptive_topk_decisions: List[Dict[str, Any]] = field(default_factory=list)
    resource_bound_pruning_decisions: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class DoubleLoopResult:
    """DoubleLoopResult."""

    optimized_params: Params
    initial_params: Params
    initial_real_resource: Resource
    final_real_resource: Resource
    initial_real_score: float
    final_real_score: float
    successful: bool
    active_params: List[str]
    pruned_params: List[str]
    rounds: List[OuterRound]
    real_validation_count: int
    stop_reason: str
    log_path: Optional[str] = None
    inner_iteration_log_path: Optional[str] = None


@dataclass
class _Evaluation:
    resource: Resource
    # ``score`` is the shared physical resource score. ``ranking_score`` is the
    # model's pure predicted score used only for candidate Top-K.
    score: float
    ranking_score: float
    raw: Any


class DoubleLoopSearchBase:
    """DoubleLoopSearchBase."""

    def __init__(
        self,
        resource_estimation_func: Evaluator,
        real_validation_func: Evaluator,
        capability: Dict[str, float],
        *,
        workload_pruner: Optional[WorkloadPruner] = None,
        estimated_score_func: Optional[ScoreFunc] = None,
        real_score_func: Optional[ScoreFunc] = None,
        resource_box_upper_bound_func: Optional[ResourceBoxUpperBoundFunc] = None,
        outer_candidate_acceptance_func: Optional[OuterCandidateAcceptanceFunc] = None,
        real_candidate_eligibility_func: Optional[RealCandidateEligibilityFunc] = None,
        initial_resource_error_bounds: Optional[Mapping[str, Mapping[str, float]]] = None,
        preflight_quality_gate: Optional[Mapping[str, Any]] = None,
        replay_continuation_required_func: Optional[ReplayContinuationRequiredFunc] = None,
        outer_config: Optional[OuterLoopConfig] = None,
        verbose: bool = True,
    ) -> None:
        self.resource_estimation_func = resource_estimation_func
        self.real_validation_func = real_validation_func
        self.capability = {name: float(value) for name, value in capability.items()}
        self.workload_pruner = workload_pruner
        self.estimated_score_func = estimated_score_func
        self.real_score_func = real_score_func
        self.resource_box_upper_bound_func = resource_box_upper_bound_func
        if outer_candidate_acceptance_func is not None and real_candidate_eligibility_func is not None:
            raise ValueError(
                "outer_candidate_acceptance_func  real_candidate_eligibility_func "
            )
        # Keep the old callback as a compatibility alias.  In the unified outer
        # loop its boolean result is a hard gate; QPS/target directions are
        # always enforced here and cannot be bypassed by the callback.
        self.outer_candidate_acceptance_func = (
            outer_candidate_acceptance_func or real_candidate_eligibility_func
        )
        self.initial_resource_error_bounds = {
            str(name): {
                "lower_delta": float((details or {}).get("lower_delta", 0.0)),
                "upper_delta": float((details or {}).get("upper_delta", 0.0)),
                "sample_count": max(0, int((details or {}).get("sample_count", 0))),
            }
            for name, details in (initial_resource_error_bounds or {}).items()
            if isinstance(details, Mapping)
        }
        self.preflight_quality_gate = copy.deepcopy(
            dict(preflight_quality_gate) if preflight_quality_gate is not None else None
        )
        self.replay_continuation_required_func = replay_continuation_required_func
        self.preflight_quality_gate_soft_failure_reason: Optional[str] = None
        if self.preflight_quality_gate is not None:
            passed = bool(self.preflight_quality_gate.get("passed"))
            if not passed:
                self.preflight_quality_gate_soft_failure_reason = str(
                    self.preflight_quality_gate.get("reason")
                    or "resource model preflight did not pass quality gate"
                )
        self.outer_config = outer_config or OuterLoopConfig()
        self.verbose = verbose
        self._validate_config()

    def _validate_config(self) -> None:
        outer = self.outer_config
        if outer.max_rounds <= 0 or outer.no_improvement_patience < 0:
            raise ValueError("max_rounds  0no_improvement_patience  0")
        if outer.real_validation_top_k <= 0:
            raise ValueError("real_validation_top_k must be positive after global capping")
        if outer.topk_controller not in {"fixed", "decision_tree"}:
            raise ValueError("topk_controller  fixed  decision_tree")
        if outer.adaptive_topk_min_k <= 0 or outer.adaptive_topk_batch_size <= 0:
            raise ValueError("adaptive_topk_min_k  adaptive_topk_batch_size  0")
        if not 0.0 <= outer.adaptive_topk_inversion_threshold <= 1.0:
            raise ValueError("adaptive_topk_inversion_threshold  [0, 1]")
        if (
            not math.isfinite(outer.adaptive_topk_score_tie_margin)
            or outer.adaptive_topk_score_tie_margin < 0.0
        ):
            raise ValueError("adaptive_topk_score_tie_margin ")
        if outer.selection_policy not in {"estimated_top1", "real_score_topk"}:
            raise ValueError("selection_policy  estimated_top1  real_score_topk")
        if not 0.0 < outer.real_validation_top_fraction <= 1.0:
            raise ValueError("real_validation_top_fraction  (0, 1]")
        if outer.target_resource is not None:
            capability = self.capability.get(outer.target_resource)
            if capability is None or not math.isfinite(capability) or capability <= 0.0:
                raise ValueError("target_resource  capability ")

    @staticmethod
    def _clip(value: float, param_range: ParameterRange) -> float:
        return max(param_range.min_value, min(param_range.max_value, float(value)))

    @staticmethod
    def _signature(params: Params) -> Tuple[Tuple[str, float], ...]:
        return tuple((name, round(float(value), 12)) for name, value in sorted(params.items()))

    def _default_score(self, resource: Resource, _raw: Any) -> float:
        _ = resource
        raise ValueError(
            " payload  score fresh score_func"
        )

    def _parse_evaluation(self, raw: Any, score_func: Optional[ScoreFunc]) -> _Evaluation:
        payload = raw
        if isinstance(raw, tuple) and raw:
            payload = raw[0]
        if not isinstance(payload, dict):
            raise TypeError(" (, )")

        explicit_score: Optional[float] = None
        if payload.get("score") is not None:
            explicit_score = float(payload["score"])
        resource_payload: Any = payload
        for key in ("resource", "resources", "resource_avg", "resource_normalized"):
            if isinstance(payload.get(key), dict):
                resource_payload = payload[key]
                break
        resource = {
            str(name): float(value)
            for name, value in resource_payload.items()
            if isinstance(value, (int, float)) and str(name) != "score"
        }
        if isinstance(payload.get("db_qps"), (int, float)):
            resource["db_qps"] = float(payload["db_qps"])
        scorer = score_func or self._default_score
        score = explicit_score if explicit_score is not None else float(scorer(resource, raw))
        if not math.isfinite(score):
            raise ValueError(f" {score}")
        ranking_score = float(payload.get("estimated_score", score))
        if not math.isfinite(ranking_score):
            raise ValueError(f" {ranking_score}")
        return _Evaluation(
            resource=resource,
            score=score,
            ranking_score=ranking_score,
            raw=raw,
        )

    def _estimate(self, sql_set: Any, params: Params) -> _Evaluation:
        raw = self.resource_estimation_func(sql_set, copy.deepcopy(params))
        return self._parse_evaluation(raw, self.estimated_score_func)

    def _real_validate(self, sql_set: Any, params: Params) -> _Evaluation:
        raw = self.real_validation_func(sql_set, copy.deepcopy(params))
        return self._parse_evaluation(raw, self.real_score_func)

    @staticmethod
    def _normalize_outer_decision(raw: Any) -> Tuple[bool, str, Dict[str, Any]]:
        """Normalize bool/dict/dataclass callbacks into one auditable decision."""
        if isinstance(raw, bool):
            return raw, "accepted" if raw else "rejected", {"accepted": raw}
        if is_dataclass(raw):
            payload = asdict(raw)
        elif isinstance(raw, Mapping):
            payload = dict(raw)
        else:
            raise TypeError(
                "outer candidate acceptance callback must return bool, mapping, or dataclass"
            )
        accepted_value = payload.get("accepted", payload.get("successful"))
        if not isinstance(accepted_value, bool):
            raise ValueError("outer candidate acceptance decision lacks boolean accepted/successful")
        reason = str(payload.get("reason") or ("accepted" if accepted_value else "rejected"))
        return accepted_value, reason, payload

    def _outer_decide(
        self,
        evaluation: _Evaluation,
        incumbent: _Evaluation,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """Apply the common fail-closed outer acceptance rule.

        A candidate advances state iff its validation hard gate passes, its DB
        QPS is strictly higher than the current incumbent, and the current
        target bottleneck utilization is strictly lower.  Scores only select
        which validated candidate becomes p*; they never accept p*.
        """
        hard_gate_passed = True
        hard_gate_reason = "hard_gate_not_configured"
        hard_gate_details: Dict[str, Any] = {"accepted": True}
        if self.outer_candidate_acceptance_func is not None:
            raw_decision = self.outer_candidate_acceptance_func(
                evaluation.resource,
                evaluation.raw,
                incumbent.resource,
                incumbent.raw,
            )
            hard_gate_passed, hard_gate_reason, hard_gate_details = (
                self._normalize_outer_decision(raw_decision)
            )

        target_resource = self._target_resource_for(incumbent)
        candidate_qps = self._finite_resource_value(evaluation.resource, "db_qps")
        incumbent_qps = self._finite_resource_value(incumbent.resource, "db_qps")
        capability = (
            self._finite_resource_value(self.capability, target_resource)
            if target_resource is not None
            else None
        )
        candidate_target = (
            self._finite_resource_value(evaluation.resource, target_resource)
            if target_resource is not None
            else None
        )
        incumbent_target = (
            self._finite_resource_value(incumbent.resource, target_resource)
            if target_resource is not None
            else None
        )
        candidate_utilization = (
            candidate_target / capability
            if candidate_target is not None and capability is not None and capability > 0.0
            else None
        )
        incumbent_utilization = (
            incumbent_target / capability
            if incumbent_target is not None and capability is not None and capability > 0.0
            else None
        )
        candidate_resource_utilization = {}
        incumbent_resource_utilization = {}
        for resource_name in ("cpu", "memory", "io"):
            resource_capability = self._finite_resource_value(
                self.capability, resource_name
            )
            candidate_resource_value = self._finite_resource_value(
                evaluation.resource, resource_name
            )
            incumbent_resource_value = self._finite_resource_value(
                incumbent.resource, resource_name
            )
            candidate_resource_utilization[resource_name] = (
                candidate_resource_value / resource_capability
                if candidate_resource_value is not None
                and resource_capability is not None
                and resource_capability > 0.0
                else None
            )
            incumbent_resource_utilization[resource_name] = (
                incumbent_resource_value / resource_capability
                if incumbent_resource_value is not None
                and resource_capability is not None
                and resource_capability > 0.0
                else None
            )
        common_decision = decide_outer_acceptance(
            incumbent_db_qps=incumbent_qps,
            candidate_db_qps=candidate_qps,
            incumbent_target_utilization=incumbent_utilization,
            candidate_target_utilization=candidate_utilization,
            target_resource=target_resource,
            incumbent_resource_utilization=incumbent_resource_utilization,
            candidate_resource_utilization=candidate_resource_utilization,
            non_target_bottleneck_threshold=0.8,
            hard_gate_passed=hard_gate_passed,
        )
        common_details = common_decision.to_dict()
        common_details.update({
            "hard_gate_passed": hard_gate_passed,
            "hard_gate_reason": hard_gate_reason,
            "hard_gate_details": copy.deepcopy(hard_gate_details),
            "target_resource": target_resource,
            "candidate_target_resource": candidate_target,
            "incumbent_target_resource": incumbent_target,
        })
        common_details.update(
            {
                "candidate_score": evaluation.score,
                "incumbent_score": incumbent.score,
            }
        )
        return common_decision.accepted, common_decision.reason, common_details

    @staticmethod
    def _finite_resource_value(resource: Mapping[str, Any], name: Optional[str]) -> Optional[float]:
        if name is None:
            return None
        value = resource.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        normalized = float(value)
        return normalized if math.isfinite(normalized) else None

    def _target_resource_for(self, incumbent: _Evaluation) -> Optional[str]:
        configured = self.outer_config.target_resource
        if configured is not None:
            return configured
        candidates: List[Tuple[float, str]] = []
        for name in ("cpu", "memory", "io"):
            value = self._finite_resource_value(incumbent.resource, name)
            capability = self._finite_resource_value(self.capability, name)
            if value is None or capability is None or capability <= 0.0:
                continue
            candidates.append((value / capability, name))
        return max(candidates)[1] if candidates else None

    def _deduplicate_and_rank(self, points: Sequence[ConvergedPoint]) -> List[ConvergedPoint]:
        best_by_signature: Dict[Tuple[Tuple[str, float], ...], ConvergedPoint] = {}
        for point in points:
            if point.failure_reason:
                continue
            signature = self._signature(point.params)
            previous = best_by_signature.get(signature)
            if previous is None:
                best_by_signature[signature] = point
                continue
            merged_subsets = list(previous.source_subsets)
            for subset in point.source_subsets:
                if subset not in merged_subsets:
                    merged_subsets.append(subset)
            if point.estimated_score > previous.estimated_score:
                point.source_subsets = merged_subsets
                best_by_signature[signature] = point
            else:
                previous.source_subsets = merged_subsets
        return sorted(
            best_by_signature.values(),
            key=lambda point: point.estimated_score,
            reverse=True,
        )

    def _validation_limit(self, candidate_count: int) -> int:
        outer = self.outer_config
        if outer.selection_policy == "estimated_top1":
            return min(candidate_count, 1)
        explicit_limit = cap_real_replay_top_k(
            outer.real_validation_top_k,
            available_count=candidate_count,
        )
        # The deterministic decision tree owns K.  Its only hard ceiling is the
        # unique candidate count unless an explicit non-zero safety budget was
        # requested; a legacy fraction must not silently make that ceiling smaller.
        if outer.topk_controller == "decision_tree":
            return explicit_limit
        fraction_limit = max(1, int(math.ceil(candidate_count * outer.real_validation_top_fraction)))
        return min(explicit_limit, fraction_limit)

    def _resource_bound_pruning_decision(
        self,
        point: ConvergedPoint,
        validated: Sequence[RealValidation],
        *,
        estimated_rank: int,
    ) -> Dict[str, Any]:
        """Prove whether a candidate cannot beat the current real best.

        Error bounds are learned only from this round's/current-run replayed
        points.  For each resource, signed residual extrema form
        ``predicted + [min_delta,max_delta]``.  The caller-provided shared score
        computes the exact optimistic score inside that physical resource box.
        """
        base: Dict[str, Any] = {
            "event": "candidate_replay_resource_bound_pruning",
            "estimated_rank": estimated_rank,
            "estimated_score": point.estimated_score,
            "estimated_resource": copy.deepcopy(point.estimated_resource),
            "controls": "real_replay_admission_only",
            "modifies_candidate_score": False,
            "participates_in_candidate_ranking": False,
        }
        if self.resource_box_upper_bound_func is None:
            return {
                **base,
                "action": "keep_for_real_replay",
                "reason": "resource_box_upper_bound_callback_not_configured",
            }
        usable = [
            item
            for item in validated
            if not item.failure_reason
            and math.isfinite(item.score)
            and all(
                isinstance(item.resource.get(name), (int, float))
                and isinstance(item.estimated_resource.get(name), (int, float))
                and math.isfinite(float(item.resource[name]))
                and math.isfinite(float(item.estimated_resource[name]))
                for name in ("cpu", "memory", "io")
            )
        ]
        calibration_sample_count = min(
            [
                int(self.initial_resource_error_bounds.get(name, {}).get("sample_count", 0))
                for name in ("cpu", "memory", "io")
            ]
            or [0]
        )
        total_error_evidence = calibration_sample_count + len(usable)
        if not usable:
            return {
                **base,
                "action": "keep_for_real_replay",
                "reason": "current_round_real_best_not_available",
                "preflight_error_sample_count": calibration_sample_count,
            }
        if total_error_evidence < self.outer_config.adaptive_topk_min_k:
            return {
                **base,
                "action": "keep_for_real_replay",
                "reason": "minimum_resource_error_evidence_not_reached",
                "usable_error_sample_count": len(usable),
                "preflight_error_sample_count": calibration_sample_count,
                "total_error_evidence_count": total_error_evidence,
                "required_error_sample_count": self.outer_config.adaptive_topk_min_k,
            }
        if not all(
            isinstance(point.estimated_resource.get(name), (int, float))
            and math.isfinite(float(point.estimated_resource[name]))
            for name in ("cpu", "memory", "io")
        ):
            return {
                **base,
                "action": "keep_for_real_replay",
                "reason": "candidate_predicted_resource_missing",
                "usable_error_sample_count": len(usable),
            }

        residual_bounds: Dict[str, Dict[str, float]] = {}
        lower_resource: Resource = {}
        upper_resource: Resource = {}
        for name in ("cpu", "memory", "io"):
            residuals = [
                float(item.resource[name]) - float(item.estimated_resource[name])
                for item in usable
            ]
            initial_bound = self.initial_resource_error_bounds.get(name, {})
            lower_delta = min(
                [0.0, float(initial_bound.get("lower_delta", 0.0)), *residuals]
            )
            upper_delta = max(
                [0.0, float(initial_bound.get("upper_delta", 0.0)), *residuals]
            )
            predicted = float(point.estimated_resource[name])
            lower_resource[name] = max(0.0, predicted + lower_delta)
            upper_resource[name] = max(
                lower_resource[name],
                predicted + upper_delta,
            )
            residual_bounds[name] = {
                "lower_delta": lower_delta,
                "upper_delta": upper_delta,
                "min_observed_residual": min(residuals),
                "max_observed_residual": max(residuals),
                "preflight_lower_delta": float(initial_bound.get("lower_delta", 0.0)),
                "preflight_upper_delta": float(initial_bound.get("upper_delta", 0.0)),
            }

        raw_bound = self.resource_box_upper_bound_func(
            copy.deepcopy(lower_resource),
            copy.deepcopy(upper_resource),
        )
        if isinstance(raw_bound, Mapping):
            optimistic_score = float(raw_bound.get("score"))
            bound_details: Any = copy.deepcopy(dict(raw_bound))
        else:
            optimistic_score = float(raw_bound)
            bound_details = {"score": optimistic_score}
        if not math.isfinite(optimistic_score):
            return {
                **base,
                "action": "keep_for_real_replay",
                "reason": "non_finite_optimistic_score",
                "usable_error_sample_count": len(usable),
                "resource_residual_bounds": residual_bounds,
                "candidate_resource_lower": lower_resource,
                "candidate_resource_upper": upper_resource,
            }
        real_best = max(usable, key=lambda item: item.score)
        provably_dominated = (
            optimistic_score
            <= real_best.score + self.outer_config.score_tolerance
        )
        return {
            **base,
            "action": (
                "prune_without_real_replay"
                if provably_dominated
                else "keep_for_real_replay"
            ),
            "reason": (
                "optimistic_resource_box_score_cannot_beat_real_best"
                if provably_dominated
                else "optimistic_resource_box_score_can_beat_real_best"
            ),
            "usable_error_sample_count": len(usable),
            "preflight_error_sample_count": calibration_sample_count,
            "total_error_evidence_count": total_error_evidence,
            "resource_residual_bounds": residual_bounds,
            "candidate_resource_lower": lower_resource,
            "candidate_resource_upper": upper_resource,
            "optimistic_score": optimistic_score,
            "optimistic_score_details": bound_details,
            "current_real_best_score": real_best.score,
            "current_real_best_estimated_rank": real_best.estimated_rank,
            "comparison_tolerance": self.outer_config.score_tolerance,
        }

    def _adaptive_topk_decision(
        self,
        validated: Sequence[RealValidation],
        ranked: Sequence[ConvergedPoint],
        max_k: int,
        *,
        remaining_ranked: Optional[Sequence[ConvergedPoint]] = None,
    ) -> Dict[str, Any]:
        """Deterministic decision tree that controls only the replay prefix K.

        The tree never predicts or changes a candidate score.  It expands K when
        evidence is insufficient, the current real best is on the newest boundary,
        the estimated/real ordering is unstable, or an unplayed candidate's
        error-adjusted upper bound can still beat the current real best.
        """
        replayed_k = len(validated)
        usable = [
            item
            for item in validated
            if not item.failure_reason and math.isfinite(item.score)
        ]
        min_k = min(max_k, self.outer_config.adaptive_topk_min_k)
        batch_size = self.outer_config.adaptive_topk_batch_size
        remaining_points = list(
            remaining_ranked
            if remaining_ranked is not None
            else ranked[replayed_k:]
        )
        replayed_evidence = [
            {
                "estimated_rank": item.estimated_rank,
                "estimated_score": item.estimated_score,
                "real_score": item.score if math.isfinite(item.score) else None,
                "usable": not item.failure_reason and math.isfinite(item.score),
                "failure_reason": item.failure_reason or None,
            }
            for item in validated
        ]
        base: Dict[str, Any] = {
            "event": "adaptive_topk_decision",
            "tree_type": "deterministic_rule_tree_not_trained_cart",
            "controls": "real_replay_count_k_only",
            "predicts_resources": False,
            "modifies_candidate_scores": False,
            "participates_in_candidate_ranking": False,
            "candidate_order": "estimated_score_descending",
            "controller": self.outer_config.topk_controller,
            "replayed_k": replayed_k,
            "min_k": min_k,
            "max_k": max_k,
            "max_k_source": "min(unique_ranked_candidate_count,--real-top-k)",
            "batch_size": batch_size,
            "usable_real_replay_count": len(usable),
            "failed_real_replay_count": replayed_k - len(usable),
            "remaining_real_replay_budget": max(0, max_k - replayed_k),
            "remaining_unpruned_candidate_count": len(remaining_points),
            "replayed_candidates": replayed_evidence,
            "score_formula": "sigmoid_linear_sweet_band_weighted_mean_v2",
            "inversion_rate_formula": (
                "count((estimated_i-estimated_j)*(real_i-real_j)<0)/"
                "pairs_where_abs_estimated_and_abs_real_exceed_tie_margin"
            ),
            "score_tie_margin": self.outer_config.adaptive_topk_score_tie_margin,
            "estimation_error_formula": "residual=real_score-estimated_score",
            "optimistic_bound_formula": "estimated_score+max_observed_signed_score_residual",
            "decision_order": [
                "max_k_reached",
                "online_resource_model_needs_more_evidence",
                "minimum_evidence_not_reached",
                "no_usable_real_replay",
                "real_best_on_newest_boundary",
                "estimated_real_ranking_unstable",
                "remaining_error_adjusted_upper_bound_can_win",
                "stop_stable",
            ],
        }
        if self.outer_config.topk_controller == "fixed":
            return {
                **base,
                "action": "stop" if replayed_k >= max_k else "expand_k",
                "reason": "fixed_topk_reached" if replayed_k >= max_k else "fixed_topk_budget",
            }
        if replayed_k >= max_k:
            return {**base, "action": "stop", "reason": "max_k_reached"}
        if self.replay_continuation_required_func is not None:
            raw_requirement = self.replay_continuation_required_func()
            if isinstance(raw_requirement, Mapping):
                continuation_required = bool(raw_requirement.get("required"))
                continuation_details = copy.deepcopy(dict(raw_requirement))
            else:
                continuation_required = bool(raw_requirement)
                continuation_details = {"required": continuation_required}
            if continuation_required:
                return {
                    **base,
                    "action": "expand_k",
                    "reason": "online_resource_model_needs_more_evidence",
                    "online_resource_model_gate": continuation_details,
                }
        if replayed_k < min_k:
            return {
                **base,
                "action": "expand_k",
                "reason": "minimum_evidence_not_reached",
            }
        if not usable:
            return {**base, "action": "expand_k", "reason": "no_usable_real_replay"}
        if len(usable) < min_k:
            return {
                **base,
                "action": "expand_k",
                "reason": "minimum_usable_evidence_not_reached",
            }

        real_best = max(usable, key=lambda item: item.score)
        newest_batch = list(usable[-batch_size:])
        best_on_newest_boundary = any(real_best is item for item in newest_batch)

        comparable_pairs = 0
        inversion_pairs = 0
        tie_margin = self.outer_config.adaptive_topk_score_tie_margin
        for left_index, left in enumerate(usable):
            for right in usable[left_index + 1 :]:
                estimated_delta = left.estimated_score - right.estimated_score
                real_delta = left.score - right.score
                if (
                    abs(estimated_delta) <= tie_margin
                    or abs(real_delta) <= tie_margin
                ):
                    continue
                comparable_pairs += 1
                if estimated_delta * real_delta < 0.0:
                    inversion_pairs += 1
        inversion_rate = (
            inversion_pairs / comparable_pairs if comparable_pairs else 0.0
        )
        ranking_unstable = (
            comparable_pairs > 0
            and inversion_rate > self.outer_config.adaptive_topk_inversion_threshold
        )

        score_residuals = [
            item.score - item.estimated_score for item in usable
        ]
        min_score_residual = min(score_residuals)
        max_score_residual = max(score_residuals)
        max_abs_score_error = max(abs(value) for value in score_residuals)
        global_rank_by_object = {
            id(item): rank for rank, item in enumerate(ranked, start=1)
        }
        remaining_evidence = [
            {
                "estimated_rank": global_rank_by_object.get(id(point)),
                "estimated_score": point.estimated_score,
                "optimistic_upper_bound": point.estimated_score + max_score_residual,
            }
            for point in remaining_points
            if math.isfinite(point.estimated_score)
        ]
        best_remaining = max(
            remaining_evidence,
            key=lambda item: item["optimistic_upper_bound"],
            default=None,
        )
        remaining_upper_bound = (
            float(best_remaining["optimistic_upper_bound"])
            if best_remaining is not None
            else float("-inf")
        )
        remaining_can_beat = (
            remaining_upper_bound > real_best.score + self.outer_config.score_tolerance
        )
        evidence = {
            "real_best_estimated_rank": real_best.estimated_rank,
            "real_best_score": real_best.score,
            "best_on_newest_boundary": best_on_newest_boundary,
            "comparable_pair_count": comparable_pairs,
            "inversion_pair_count": inversion_pairs,
            "pairwise_inversion_rate": inversion_rate,
            "inversion_threshold": self.outer_config.adaptive_topk_inversion_threshold,
            "score_tie_margin": tie_margin,
            "min_observed_signed_score_residual": min_score_residual,
            "max_observed_signed_score_residual": max_score_residual,
            "max_abs_estimation_error": max_abs_score_error,
            "remaining_candidates": remaining_evidence,
            "remaining_upper_bound_estimated_rank": (
                best_remaining["estimated_rank"] if best_remaining is not None else None
            ),
            "remaining_upper_bound": (
                remaining_upper_bound if math.isfinite(remaining_upper_bound) else None
            ),
            "remaining_can_beat_real_best": remaining_can_beat,
        }
        if best_on_newest_boundary:
            return {
                **base,
                **evidence,
                "action": "expand_k",
                "reason": "real_best_on_newest_boundary",
            }
        if ranking_unstable:
            return {
                **base,
                **evidence,
                "action": "expand_k",
                "reason": "estimated_real_ranking_unstable",
            }
        if remaining_can_beat:
            return {
                **base,
                **evidence,
                "action": "expand_k",
                "reason": "remaining_error_adjusted_upper_bound_can_win",
            }
        return {
            **base,
            **evidence,
            "action": "stop",
            "reason": "best_stable_and_remaining_upper_bound_cannot_win",
        }

    @staticmethod
    def _select_validation_candidates(
        ranked: Sequence[ConvergedPoint],
        limit: int,
    ) -> List[ConvergedPoint]:
        """Return the exact current-model Top-K after convergence-point dedup."""
        if limit <= 0 or not ranked:
            return []
        return list(ranked[:limit])

    def _binary_shrink(
        self,
        ranges: Dict[str, ParameterRange],
        anchor: Params,
        active_params: Sequence[str],
    ) -> Dict[str, ParameterRange]:
        shrinkable = [
            name
            for name in active_params
            if ranges[name].width > self.outer_config.epsilon
        ]
        result, _ = binary_shrink_parameter_ranges(ranges, anchor, shrinkable)
        return result

    def _ranges_converged(
        self,
        ranges: Dict[str, ParameterRange],
        active_params: Sequence[str],
    ) -> bool:
        return all(ranges[name].width <= self.outer_config.epsilon for name in active_params)
