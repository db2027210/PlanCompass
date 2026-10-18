"""Module."""


from __future__ import annotations

import copy
import itertools
import json
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field, is_dataclass, replace
from typing import Any, Callable, Dict, List, Literal, Mapping, Optional, Sequence, Tuple

from logger_utils import OptimizationLogger


Params = Dict[str, float]
Resource = Dict[str, float]
Evaluator = Callable[[Any, Params], Any]
ScoreFunc = Callable[[Resource, Any], float]
WorkloadPruner = Callable[[Any, Sequence[str]], Sequence[str]]
RealCandidateEligibilityFunc = Callable[[Resource, Any, Resource, Any], bool]
OuterCandidateAcceptanceFunc = Callable[[Resource, Any, Resource, Any], Any]
ResourceBoxUpperBoundFunc = Callable[[Resource, Resource], Any]
InnerIterationCallback = Callable[[Dict[str, Any]], None]
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

    def __post_init__(self) -> None:
        if self.max_value < self.min_value:
            raise ValueError("max_value  min_value")
        if self.discrete_count < 2:
            raise ValueError("discrete_count  2")
        if self.step is not None and self.step <= 0.0:
            raise ValueError("step  0")
        if self.values is not None:
            values = tuple(sorted({float(value) for value in self.values}))
            if len(values) < 2:
                raise ValueError("values ")
            object.__setattr__(self, "values", values)

    @property
    def width(self) -> float:
        return self.max_value - self.min_value


@dataclass
class InnerLoopConfig:
    """InnerLoopConfig."""

    max_workers: int = 1
    max_epochs: int = 20
    learning_rate: float = 0.15
    learning_rate_decay: float = 0.85
    finite_difference_ratio: float = 0.03
    gradient_clip: float = 1.0
    max_step_backtracks: int = 4
    parameter_tolerance: float = 1e-4
    score_tolerance: float = 1e-8
    convergence_patience: int = 2


@dataclass
class OuterLoopConfig:
    """OuterLoopConfig."""

    max_rounds: int = 6
    # Optimizer candidate replay budget; independent of the fixed Top15 fresh
    # calibration replays enforced before optimizer construction.
    real_validation_top_k: int = 30
    real_validation_top_fraction: float = 0.25
    topk_controller: Literal["fixed", "decision_tree"] = "decision_tree"
    adaptive_topk_min_k: int = 15
    adaptive_topk_batch_size: int = 1
    adaptive_topk_inversion_threshold: float = 0.10
    adaptive_topk_score_tie_margin: float = 0.01
    selection_policy: Literal["estimated_top1", "real_score_topk"] = "real_score_topk"
    # 0 disables rejection-patience early stop.  A rejected inner best always
    # returns control to at least the next complete small-loop search.
    no_improvement_patience: int = 0
    score_tolerance: float = 1e-8
    epsilon: float = 1e-4


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
    # ``score`` drives the existing inner gradient search. ``ranking_score`` is
    # the model's pure predicted score used only for convergence-point Top-K.
    score: float
    ranking_score: float
    raw: Any


class DoubleLoopSGDOptimizer:
    """DoubleLoopSGDOptimizer."""

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
        inner_config: Optional[InnerLoopConfig] = None,
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
        # Keep the old callback as a compatibility alias.  The callback now runs
        # exactly once per round, after real-score selection has produced p*.
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
        if (
            self.preflight_quality_gate is not None
            and not bool(self.preflight_quality_gate.get("passed"))
        ):
            raise RuntimeError(
                "/Score "
            )
        self.inner_config = inner_config or InnerLoopConfig()
        self.outer_config = outer_config or OuterLoopConfig()
        self.verbose = verbose
        self._validate_config()

    def _validate_config(self) -> None:
        inner = self.inner_config
        outer = self.outer_config
        if inner.max_workers <= 0:
            raise ValueError("max_workers  0")
        if inner.max_epochs <= 0 or inner.learning_rate <= 0.0:
            raise ValueError("max_epochs  learning_rate  0")
        if inner.gradient_clip <= 0.0 or inner.max_step_backtracks < 0:
            raise ValueError("gradient_clip  0max_step_backtracks  0")
        if not 0.0 < inner.learning_rate_decay <= 1.0:
            raise ValueError("learning_rate_decay  (0, 1]")
        if not 0.0 < inner.finite_difference_ratio <= 0.5:
            raise ValueError("finite_difference_ratio  (0, 0.5]")
        if outer.max_rounds <= 0 or outer.no_improvement_patience < 0:
            raise ValueError("max_rounds  0no_improvement_patience  0")
        if outer.real_validation_top_k < 0:
            raise ValueError("real_validation_top_k  00 ")
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
        baseline: _Evaluation,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        if self.outer_candidate_acceptance_func is None:
            accepted = evaluation.score > baseline.score + self.outer_config.score_tolerance
            return accepted, (
                "accepted_real_score_above_baseline"
                if accepted
                else "real_score_not_above_baseline"
            ), {
                "accepted": accepted,
                "candidate_score": evaluation.score,
                "baseline_score": baseline.score,
            }
        raw_decision = self.outer_candidate_acceptance_func(
            evaluation.resource,
            evaluation.raw,
            baseline.resource,
            baseline.raw,
        )
        return self._normalize_outer_decision(raw_decision)

    @staticmethod
    def _parameter_subsets(active_params: Sequence[str]) -> List[Tuple[str, ...]]:
        """ parameter subsets."""
        active = tuple(active_params)
        return [
            subset
            for size in range(1, len(active) + 1)
            for subset in itertools.combinations(active, size)
        ]

    def _subset_gradient_descent(
        self,
        sql_set: Any,
        incumbent: Params,
        active_subset: Tuple[str, ...],
        ranges: Dict[str, ParameterRange],
        *,
        round_index: int = 0,
        subset_id: str = "",
        iteration_callback: Optional[InnerIterationCallback] = None,
    ) -> ConvergedPoint:
        """ subset gradient descent."""
        subset_id = subset_id or self._subset_id(active_subset)
        inner = self.inner_config
        estimate_cache: Dict[Tuple[Tuple[str, float], ...], _Evaluation] = {}

        def evaluate(params: Params) -> _Evaluation:
            signature = self._signature(params)
            if signature not in estimate_cache:
                estimate_cache[signature] = self._estimate(sql_set, params)
            return estimate_cache[signature]

        start_params = copy.deepcopy(incumbent)
        current = copy.deepcopy(start_params)
        current_eval = evaluate(current)
        iteration_trace: List[Dict[str, Any]] = []
        improving_steps = 0
        stable_epochs = 0
        converged = False
        epoch = 0

        for epoch in range(1, inner.max_epochs + 1):
            epoch_score_before = current_eval.score
            epoch_ranking_score_before = current_eval.ranking_score
            params_before = copy.deepcopy(current)
            learning_rate = inner.learning_rate * (inner.learning_rate_decay ** (epoch - 1))
            normalized_steps: Dict[str, float] = {}
            gradient_records: Dict[str, Dict[str, Any]] = {}


            for name in active_subset:
                param_range = ranges[name]
                if param_range.width <= 0.0:
                    continue
                z = (current[name] - param_range.min_value) / param_range.width
                z_low = max(0.0, z - inner.finite_difference_ratio)
                z_high = min(1.0, z + inner.finite_difference_ratio)
                if z_high <= z_low:
                    continue

                lower = copy.deepcopy(current)
                upper = copy.deepcopy(current)
                lower[name] = param_range.min_value + z_low * param_range.width
                upper[name] = param_range.min_value + z_high * param_range.width
                lower_score = evaluate(lower).score
                upper_score = evaluate(upper).score
                lower_ranking_score = evaluate(lower).ranking_score
                upper_ranking_score = evaluate(upper).ranking_score
                gradient = (upper_score - lower_score) / (z_high - z_low)
                clipped_gradient = max(-inner.gradient_clip, min(inner.gradient_clip, gradient))
                normalized_steps[name] = learning_rate * clipped_gradient
                gradient_records[name] = {
                    "lower_value": lower[name],
                    "upper_value": upper[name],
                    "lower_score": lower_score,
                    "upper_score": upper_score,
                    "lower_predicted_score": lower_ranking_score,
                    "upper_predicted_score": upper_ranking_score,
                    "normalized_gradient": gradient,
                    "clipped_gradient": clipped_gradient,
                    "normalized_step": normalized_steps[name],
                }

            max_normalized_delta = 0.0
            accepted = False
            backtrack_scale = 1.0
            attempted_backtracks = 0
            accepted_backtrack_scale: Optional[float] = None
            for backtrack_index in range(inner.max_step_backtracks + 1):
                attempted_backtracks = backtrack_index + 1
                candidate = copy.deepcopy(current)
                candidate_delta = 0.0
                for name, normalized_step in normalized_steps.items():
                    param_range = ranges[name]
                    z = (current[name] - param_range.min_value) / param_range.width
                    z_new = max(0.0, min(1.0, z + normalized_step * backtrack_scale))
                    candidate[name] = param_range.min_value + z_new * param_range.width
                    candidate_delta = max(candidate_delta, abs(z_new - z))
                if candidate_delta <= inner.parameter_tolerance:
                    break
                candidate_eval = evaluate(candidate)
                if candidate_eval.score > current_eval.score + inner.score_tolerance:
                    current = candidate
                    current_eval = candidate_eval
                    improving_steps += 1
                    max_normalized_delta = candidate_delta
                    accepted = True
                    accepted_backtrack_scale = backtrack_scale
                    break
                backtrack_scale *= 0.5

            score_delta = current_eval.score - epoch_score_before
            if (
                not accepted
                or not normalized_steps
                or max_normalized_delta <= inner.parameter_tolerance
                or score_delta <= inner.score_tolerance
            ):
                stable_epochs += 1
            else:
                stable_epochs = 0
            trace_record = {
                    "event": "double_loop_inner_iteration",
                    "outer_round": round_index,
                    "subset_id": subset_id,
                    "iteration": epoch,
                    "active_subset": list(active_subset),
                    "fixed_params": {
                        name: float(value)
                        for name, value in start_params.items()
                        if name not in active_subset
                    },
                    "start_params": start_params,
                    "current_bounds": {
                        name: [bound.min_value, bound.max_value]
                        for name, bound in ranges.items()
                    },
                    "params_before": params_before,
                    "score_before": epoch_score_before,
                    "predicted_score_before": epoch_ranking_score_before,
                    "learning_rate": learning_rate,
                    "gradients": gradient_records,
                    "attempted_backtracks": attempted_backtracks,
                    "accepted_backtrack_scale": accepted_backtrack_scale,
                    "accepted": accepted,
                    "params_after": copy.deepcopy(current),
                    "score_after": current_eval.score,
                    "predicted_score_after": current_eval.ranking_score,
                    "score_delta": score_delta,
                    "max_normalized_delta": max_normalized_delta,
                    "stable_iterations": stable_epochs,
                }
            iteration_trace.append(trace_record)
            if iteration_callback is not None:
                iteration_callback(trace_record)
            if stable_epochs >= inner.convergence_patience:
                converged = True
                break

        return ConvergedPoint(
            subset_id=subset_id,
            params=copy.deepcopy(current),
            estimated_resource=copy.deepcopy(current_eval.resource),
            # Top-K ranking uses only the shared predicted-resource formula.
            # Parameter-space acquisition is confined to gradient exploration.
            estimated_score=current_eval.ranking_score,
            active_subset=active_subset,
            fixed_params={
                name: float(value)
                for name, value in start_params.items()
                if name not in active_subset
            },
            start_params=start_params,
            current_bounds=dict(ranges),
            source_subsets=[active_subset],
            iterations=epoch,
            converged=converged,
            improving_steps=improving_steps,
            estimated_db_qps=current_eval.resource.get("db_qps"),
            estimated_cpu=current_eval.resource.get("cpu"),
            estimated_memory=current_eval.resource.get("memory"),
            estimated_io=current_eval.resource.get("io"),
            iteration_trace=iteration_trace,
        )

    @staticmethod
    def _subset_id(active_subset: Sequence[str]) -> str:
        return "subset:" + "+".join(active_subset)

    def _failed_subset_point(
        self,
        incumbent: Params,
        active_subset: Tuple[str, ...],
        ranges: Dict[str, ParameterRange],
        exc: BaseException,
        *,
        subset_id: str,
    ) -> ConvergedPoint:
        """ failed subset point."""
        return ConvergedPoint(
            subset_id=subset_id,
            params=copy.deepcopy(incumbent),
            estimated_resource={},
            estimated_score=float("-inf"),
            active_subset=active_subset,
            fixed_params={
                name: float(value)
                for name, value in incumbent.items()
                if name not in active_subset
            },
            start_params=copy.deepcopy(incumbent),
            current_bounds=dict(ranges),
            source_subsets=[active_subset],
            iterations=0,
            converged=False,
            improving_steps=0,
            estimated_db_qps=None,
            estimated_cpu=None,
            estimated_memory=None,
            estimated_io=None,
            iteration_trace=[],
            failure_reason=f"{type(exc).__name__}: {exc}",
        )

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
        explicit_limit = (
            candidate_count
            if outer.real_validation_top_k == 0
            else min(candidate_count, outer.real_validation_top_k)
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
        result = dict(ranges)
        for name in active_params:
            current = ranges[name]
            if current.width <= self.outer_config.epsilon:
                continue
            midpoint = (current.min_value + current.max_value) / 2.0
            if float(anchor[name]) <= midpoint:
                result[name] = replace(current, max_value=midpoint)
            else:
                result[name] = replace(current, min_value=midpoint)
        return result

    def _ranges_converged(
        self,
        ranges: Dict[str, ParameterRange],
        active_params: Sequence[str],
    ) -> bool:
        return all(ranges[name].width <= self.outer_config.epsilon for name in active_params)

    def optimize(
        self,
        p_real: Params,
        param_ranges: Dict[str, ParameterRange],
        sql_set: Any,
        *,
        candidate_params: Optional[Sequence[str]] = None,
        initial_real_resource: Optional[Resource] = None,
        log_path: Optional[str] = None,
        inner_iteration_log_path: Optional[str] = None,
    ) -> DoubleLoopResult:
        """optimize."""


        logger = OptimizationLogger(log_path, self.verbose)
        inner_trace_writer = (
            _InnerIterationJsonlWriter(inner_iteration_log_path)
            if inner_iteration_log_path
            else None
        )
        try:
            initial_params = {name: float(value) for name, value in p_real.items()}
            ranges = dict(param_ranges)
            candidates = list(candidate_params or ranges.keys())
            missing = [name for name in candidates if name not in ranges or name not in initial_params]
            if missing:
                raise ValueError(f": {missing}")
            invalid_ranges = [
                name
                for name in candidates
                if ranges[name].max_value < ranges[name].min_value
                or not ranges[name].min_value <= initial_params[name] <= ranges[name].max_value
            ]
            if invalid_ranges:
                raise ValueError(f": {invalid_ranges}")
            active = list(self.workload_pruner(sql_set, candidates)) if self.workload_pruner else candidates
            active = list(dict.fromkeys(str(name) for name in active))
            invalid_active = [name for name in active if name not in candidates]
            if invalid_active:
                raise ValueError(f"workload_pruner : {invalid_active}")
            if not active:
                raise ValueError("")
            pruned = [name for name in candidates if name not in active]
            logger.log(f"Active parameters after workload pruning: {active}")

            if initial_real_resource is None:
                initial_real_eval = self._real_validate(sql_set, initial_params)
            else:
                initial_real_eval = self._parse_evaluation(
                    initial_real_resource,
                    self.real_score_func,
                )
            best_params = copy.deepcopy(initial_params)
            best_real_eval = initial_real_eval
            best_is_successful = False
            real_validation_count = 0 if initial_real_resource is not None else 1
            rounds: List[OuterRound] = []
            consecutive_rejected_rounds = 0
            stop_reason = "max_rounds"

            # Round 0 starts from PostgreSQL defaults.  Later rounds start from
            # the most recently accepted inner best.  A rejection leaves this
            # anchor and every bound unchanged.
            for round_index in range(self.outer_config.max_rounds):
                ranges_before = dict(ranges)
                common_start_params = copy.deepcopy(best_params)
                subsets = self._parameter_subsets(active)
                indexed_subsets = [
                    (subset_index, subset, f"r{round_index:02d}_s{subset_index:04d}")
                    for subset_index, subset in enumerate(subsets, start=1)
                ]
                if self.inner_config.max_workers == 1:
                    points = []
                    for _subset_index, subset, subset_id in indexed_subsets:
                        try:
                            point = self._subset_gradient_descent(
                                sql_set,
                                best_params,
                                subset,
                                ranges,
                                round_index=round_index,
                                subset_id=subset_id,
                                iteration_callback=(
                                    inner_trace_writer.write if inner_trace_writer else None
                                ),
                            )
                        except Exception as exc:
                            point = self._failed_subset_point(
                                best_params,
                                subset,
                                ranges,
                                exc,
                                subset_id=subset_id,
                            )
                        points.append(point)
                else:
                    points_by_subset: Dict[Tuple[str, ...], ConvergedPoint] = {}
                    worker_count = min(self.inner_config.max_workers, len(subsets))
                    with ThreadPoolExecutor(max_workers=worker_count) as executor:
                        future_map = {
                            executor.submit(
                                self._subset_gradient_descent,
                                sql_set,
                                best_params,
                                subset,
                                ranges,
                                round_index=round_index,
                                subset_id=subset_id,
                                iteration_callback=(
                                    inner_trace_writer.write if inner_trace_writer else None
                                ),
                            ): (subset, subset_id)
                            for _subset_index, subset, subset_id in indexed_subsets
                        }
                        for future in as_completed(future_map):
                            subset, subset_id = future_map[future]
                            try:
                                points_by_subset[subset] = future.result()
                            except Exception as exc:
                                points_by_subset[subset] = self._failed_subset_point(
                                    best_params,
                                    subset,
                                    ranges,
                                    exc,
                                    subset_id=subset_id,
                                )
                    points = [points_by_subset[subset] for subset in subsets]

                ranked = self._deduplicate_and_rank(points)
                validation_limit = self._validation_limit(len(ranked))
                validated: List[RealValidation] = []
                adaptive_topk_decisions: List[Dict[str, Any]] = []
                resource_bound_pruning_decisions: List[Dict[str, Any]] = []
                round_real_evaluations: Dict[
                    Tuple[Tuple[str, float], ...], _Evaluation
                ] = {}
                rank_by_signature = {
                    self._signature(point.params): rank for rank, point in enumerate(ranked, start=1)
                }
                candidate_cursor = 0
                while (
                    candidate_cursor < len(ranked)
                    and len(validated) < validation_limit
                ):
                    point = ranked[candidate_cursor]
                    candidate_cursor += 1
                    estimated_rank = rank_by_signature[self._signature(point.params)]
                    signature = self._signature(point.params)
                    resource_prune = self._resource_bound_pruning_decision(
                        point,
                        validated,
                        estimated_rank=estimated_rank,
                    )
                    resource_prune["round"] = round_index
                    resource_bound_pruning_decisions.append(
                        copy.deepcopy(resource_prune)
                    )
                    logger.log_struct(resource_prune)
                    if resource_prune["action"] == "prune_without_real_replay":
                        continue

                    real_validation_count += 1
                    try:
                        round_real_evaluations[signature] = self._real_validate(
                            sql_set, point.params
                        )
                    except Exception as exc:
                        source_subset = min(
                            point.source_subsets,
                            key=lambda item: (len(item), item),
                        )
                        validated.append(
                            RealValidation(
                                params=copy.deepcopy(point.params),
                                resource={},
                                estimated_resource=copy.deepcopy(
                                    point.estimated_resource
                                ),
                                score=float("-inf"),
                                estimated_score=point.estimated_score,
                                estimated_rank=estimated_rank,
                                active_subset=source_subset,
                                source_subsets=list(point.source_subsets),
                                successful=False,
                                failure_reason=f"{type(exc).__name__}: {exc}",
                                raw_result=None,
                            )
                        )
                    else:
                        real_eval = round_real_evaluations[signature]
                        source_subset = min(
                            point.source_subsets,
                            key=lambda item: (len(item), item),
                        )
                        validated.append(
                            RealValidation(
                                params=copy.deepcopy(point.params),
                                resource=copy.deepcopy(real_eval.resource),
                                estimated_resource=copy.deepcopy(
                                    point.estimated_resource
                                ),
                                score=real_eval.score,
                                estimated_score=point.estimated_score,
                                estimated_rank=estimated_rank,
                                active_subset=source_subset,
                                source_subsets=list(point.source_subsets),
                                # The shared real resource score selects p*.  The
                                # outer semantic gate runs only after p* exists.
                                successful=False,
                                raw_result=real_eval.raw,
                            )
                        )

                    batch_size = self.outer_config.adaptive_topk_batch_size
                    at_batch_boundary = len(validated) % batch_size == 0
                    at_max_k = len(validated) >= validation_limit
                    if not at_batch_boundary and not at_max_k:
                        continue
                    topk_decision = self._adaptive_topk_decision(
                        validated,
                        ranked,
                        validation_limit,
                        remaining_ranked=ranked[candidate_cursor:],
                    )
                    topk_decision["round"] = round_index
                    adaptive_topk_decisions.append(copy.deepcopy(topk_decision))
                    logger.log_struct(topk_decision)
                    if topk_decision["action"] == "stop":
                        break

                if (
                    candidate_cursor >= len(ranked)
                    and len(validated) < validation_limit
                    and (
                        not adaptive_topk_decisions
                        or adaptive_topk_decisions[-1].get("action") != "stop"
                    )
                ):
                    exhaustion_decision = {
                        "event": "adaptive_topk_decision",
                        "round": round_index,
                        "controller": self.outer_config.topk_controller,
                        "action": "stop",
                        "reason": "no_unpruned_candidate_remains",
                        "replayed_k": len(validated),
                        "max_k": validation_limit,
                        "resource_bound_pruned_candidate_count": sum(
                            1
                            for decision in resource_bound_pruning_decisions
                            if decision.get("action")
                            == "prune_without_real_replay"
                        ),
                    }
                    adaptive_topk_decisions.append(exhaustion_decision)
                    logger.log_struct(exhaustion_decision)

                # Small-loop p* is the real-score argmax over every usable Top-K
                # replay.  Only after p* exists does the outer loop inspect QPS
                # and resource directions and decide whether ranges may shrink.
                usable_validated = [
                    item
                    for item in validated
                    if not item.failure_reason and math.isfinite(item.score)
                ]
                round_p_star = max(
                    usable_validated,
                    key=lambda item: item.score,
                    default=None,
                )
                outer_accepted = False
                outer_acceptance_reason = "no_usable_small_loop_best"
                outer_acceptance_details: Dict[str, Any] = {}
                if round_p_star is not None:
                    round_eval = round_real_evaluations[
                        self._signature(round_p_star.params)
                    ]
                    (
                        outer_accepted,
                        outer_acceptance_reason,
                        outer_acceptance_details,
                    ) = self._outer_decide(round_eval, initial_real_eval)
                    round_p_star.successful = outer_accepted
                    round_p_star.outer_accepted = outer_accepted
                    round_p_star.outer_acceptance_reason = outer_acceptance_reason
                    round_p_star.outer_acceptance_details = copy.deepcopy(
                        outer_acceptance_details
                    )
                for item in validated:
                    if item is round_p_star:
                        continue
                    item.outer_acceptance_reason = "not_small_loop_real_score_best"

                # Acceptance, not cross-round score improvement, controls state
                # advancement and interval shrinkage.
                improved = outer_accepted
                if outer_accepted:
                    assert round_p_star is not None
                    best_params = copy.deepcopy(round_p_star.params)
                    best_real_eval = round_real_evaluations[
                        self._signature(best_params)
                    ]
                    best_is_successful = True
                    consecutive_rejected_rounds = 0
                else:
                    consecutive_rejected_rounds += 1

                # Only the deterministic source subset S* that produced the
                # accepted convergence point has search evidence in this run.
                # All other bounds remain unchanged.  A rejection changes none.
                shrink_subset = (
                    round_p_star.active_subset
                    if outer_accepted and round_p_star is not None
                    else tuple()
                )
                ranges = self._binary_shrink(
                    ranges,
                    round_p_star.params if round_p_star is not None else best_params,
                    shrink_subset,
                )
                outer_round = OuterRound(
                    round_index=round_index,
                    common_start_params=common_start_params,
                    accepted_anchor_after=copy.deepcopy(best_params),
                    ranges_before=ranges_before,
                    ranges_after=dict(ranges),
                    subset_count=len(subsets),
                    subset_runs=points,
                    converged_points=ranked,
                    validated_points=validated,
                    # These remain the outer-loop accepted incumbent.  The
                    # rejected small-loop p* is recorded separately below.
                    best_real_score=best_real_eval.score,
                    best_params=copy.deepcopy(best_params),
                    binary_shrink_subset=shrink_subset,
                    improved=improved,
                    small_loop_best=round_p_star,
                    outer_accepted=outer_accepted,
                    outer_acceptance_reason=outer_acceptance_reason,
                    outer_acceptance_details=copy.deepcopy(outer_acceptance_details),
                    adaptive_topk_decisions=adaptive_topk_decisions,
                    resource_bound_pruning_decisions=resource_bound_pruning_decisions,
                )
                rounds.append(outer_round)
                logger.log_struct(
                    {
                        "event": "double_loop_round",
                        "round": round_index,
                        "subset_count": len(subsets),
                        "expected_subset_count": (2 ** len(active)) - 1,
                        "inner_max_workers": self.inner_config.max_workers,
                        "selection_policy": self.outer_config.selection_policy,
                        "topk_controller": self.outer_config.topk_controller,
                        "real_validation_max_k": validation_limit,
                        "selected_real_k": len(validated),
                        "adaptive_topk_decisions": adaptive_topk_decisions,
                        "resource_bound_pruning_decisions": (
                            resource_bound_pruning_decisions
                        ),
                        "resource_bound_pruned_candidate_count": sum(
                            1
                            for decision in resource_bound_pruning_decisions
                            if decision.get("action") == "prune_without_real_replay"
                        ),
                        "converged_point_count": len(ranked),
                        "real_validation_count": len(validated),
                        "ranked_candidate_count": len(ranked),
                        "cross_round_real_replay_cache_used": False,
                        "round_best_score": (
                            round_p_star.score if round_p_star is not None else None
                        ),
                        "round_p_star": (
                            {
                                "params": copy.deepcopy(round_p_star.params),
                                "real_score": round_p_star.score,
                                "estimated_score": round_p_star.estimated_score,
                                "estimated_rank": round_p_star.estimated_rank,
                                "active_subset": list(round_p_star.active_subset),
                                "source_subsets": [
                                    list(subset) for subset in round_p_star.source_subsets
                                ],
                                "outer_accepted": outer_accepted,
                                "outer_acceptance_reason": outer_acceptance_reason,
                                "outer_acceptance_details": copy.deepcopy(
                                    outer_acceptance_details
                                ),
                            }
                            if round_p_star is not None
                            else None
                        ),
                        "accepted_anchor_score": best_real_eval.score,
                        "outer_state_advanced": improved,
                        "outer_accepted": outer_accepted,
                        "outer_acceptance_reason": outer_acceptance_reason,
                        "outer_acceptance_details": copy.deepcopy(
                            outer_acceptance_details
                        ),
                        "binary_shrink_subset": list(shrink_subset),
                        "outer_accepted_best_count": 1 if outer_accepted else 0,
                        "common_start_params": common_start_params,
                        "accepted_anchor_after": copy.deepcopy(best_params),
                        "subset_runs": [
                            {
                                "subset_id": point.subset_id,
                                "active_subset": list(point.active_subset),
                                "fixed_params": point.fixed_params,
                                "start_params": point.start_params,
                                "current_bounds": {
                                    name: [bound.min_value, bound.max_value]
                                    for name, bound in point.current_bounds.items()
                                },
                                "iterations": point.iterations,
                                "converged": point.converged,
                                "final_params": point.params,
                                "estimated_db_qps": point.estimated_db_qps,
                                "estimated_cpu": point.estimated_cpu,
                                "estimated_memory": point.estimated_memory,
                                "estimated_io": point.estimated_io,
                                "estimated_objective": (
                                    point.estimated_score if math.isfinite(point.estimated_score) else None
                                ),
                                "iteration_trace": point.iteration_trace,
                                "failure_reason": point.failure_reason,
                                "source_subsets": [list(subset) for subset in point.source_subsets],
                            }
                            for point in points
                        ],
                        "deduplicated_converged_points": [
                            {
                                "final_params": point.params,
                                "source_subsets": [list(subset) for subset in point.source_subsets],
                                "estimated_db_qps": point.estimated_db_qps,
                                "estimated_cpu": point.estimated_cpu,
                                "estimated_memory": point.estimated_memory,
                                "estimated_io": point.estimated_io,
                                "estimated_objective": (
                                    point.estimated_score if math.isfinite(point.estimated_score) else None
                                ),
                            }
                            for point in ranked
                        ],
                        "ranges_after": {
                            name: [value.min_value, value.max_value]
                            for name, value in ranges.items()
                            if name in active
                        },
                    }
                )
                logger.log(
                    f"[Round {round_index}] subsets={len(subsets)}, unique={len(ranked)}, "
                    f"real={len(validated)}, best={best_real_eval.score:.6f}"
                )

                if self._ranges_converged(ranges, active):
                    stop_reason = "epsilon_converged"
                    break
                if (
                    self.outer_config.no_improvement_patience > 0
                    and consecutive_rejected_rounds
                    >= self.outer_config.no_improvement_patience
                ):
                    stop_reason = "outer_rejection_patience"
                    break

            return DoubleLoopResult(
                optimized_params=best_params,
                initial_params=initial_params,
                initial_real_resource=copy.deepcopy(initial_real_eval.resource),
                final_real_resource=copy.deepcopy(best_real_eval.resource),
                initial_real_score=initial_real_eval.score,
                final_real_score=best_real_eval.score,
                successful=best_is_successful,
                active_params=active,
                pruned_params=pruned,
                rounds=rounds,
                real_validation_count=real_validation_count,
                stop_reason=stop_reason,
                log_path=logger.get_log_path(),
                inner_iteration_log_path=(
                    inner_trace_writer.path if inner_trace_writer else None
                ),
            )
        finally:
            if inner_trace_writer is not None:
                inner_trace_writer.close()
            logger.close()


def optimize_with_double_loop_sgd(
    resource_estimation_func: Evaluator,
    real_validation_func: Evaluator,
    capability: Dict[str, float],
    p_real: Params,
    param_ranges: Dict[str, ParameterRange],
    sql_set: Any,
    **kwargs: Any,
) -> DoubleLoopResult:
    """optimize with double loop sgd."""
    optimize_keys = {
        "candidate_params",
        "initial_real_resource",
        "log_path",
        "inner_iteration_log_path",
    }
    optimize_kwargs = {key: kwargs.pop(key) for key in list(kwargs) if key in optimize_keys}
    optimizer = DoubleLoopSGDOptimizer(
        resource_estimation_func,
        real_validation_func,
        capability,
        **kwargs,
    )
    return optimizer.optimize(p_real, param_ranges, sql_set, **optimize_kwargs)
