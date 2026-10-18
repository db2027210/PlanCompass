"""Module."""


from __future__ import annotations

import copy
import itertools
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from logger_utils import OptimizationLogger
from double_loop_sgd_optimizer import (
    ConvergedPoint,
    DoubleLoopResult,
    DoubleLoopSGDOptimizer,
    OuterLoopConfig,
    OuterRound,
    ParameterRange,
    RealValidation,
    Resource,
    Params,
    _Evaluation,
    _InnerIterationJsonlWriter,
)


@dataclass
class GridInnerLoopConfig:
    """GridInnerLoopConfig."""

    max_workers: int = 8
    max_passes: int = 20
    no_improvement_patience: int = 2
    score_tolerance: float = 1e-8
    combination_probe_enabled: bool = True
    combination_min_size: int = 2
    combination_max_size: int = 4
    combination_values_per_param: int = 3
    combination_max_candidates: int = 0


class DoubleLoopGridOptimizer(DoubleLoopSGDOptimizer):
    """DoubleLoopGridOptimizer."""

    def __init__(
        self,
        resource_estimation_func: Any,
        real_validation_func: Any,
        capability: Dict[str, float],
        *,
        grid_config: Optional[GridInnerLoopConfig] = None,
        outer_round_evidence_func: Optional[
            Callable[[Any, int, Params, Any], Optional[Mapping[str, Any]]]
        ] = None,
        **kwargs: Any,
    ) -> None:


        kwargs.pop("inner_config", None)
        super().__init__(
            resource_estimation_func,
            real_validation_func,
            capability,
            **kwargs,
        )
        self.grid_config = grid_config or GridInnerLoopConfig()
        self.outer_round_evidence_func = outer_round_evidence_func
        self._validate_grid_config()

    def _validate_grid_config(self) -> None:
        cfg = self.grid_config
        if cfg.max_workers <= 0 or cfg.max_passes <= 0:
            raise ValueError("grid max_workers/max_passes  0")
        if cfg.no_improvement_patience <= 0:
            raise ValueError("grid no_improvement_patience  0")
        if cfg.score_tolerance < 0.0:
            raise ValueError("grid score_tolerance  0")
        if cfg.combination_min_size < 2:
            raise ValueError("combination_min_size  2")
        if cfg.combination_max_size < cfg.combination_min_size:
            raise ValueError("combination_max_size  combination_min_size")
        if not 2 <= cfg.combination_values_per_param <= 5:
            raise ValueError("combination_values_per_param  [2,5]")
        if cfg.combination_max_candidates < 0:
            raise ValueError("combination_max_candidates  0")

    @staticmethod
    def discretize(
        param_range: ParameterRange,
        *,
        include_value: Optional[float] = None,
    ) -> List[float]:
        """discretize."""
        low = float(param_range.min_value)
        high = float(param_range.max_value)
        if param_range.values:
            values = [
                float(value)
                for value in param_range.values
                if low - 1e-12 <= float(value) <= high + 1e-12
            ]
        elif param_range.step is not None:
            values = []
            current = low
            max_points = max(2, min(10000, int(param_range.discrete_count) * 20))
            while current <= high + param_range.step * 1e-9 and len(values) < max_points:
                values.append(current)
                current += param_range.step
            if values and values[-1] < high - param_range.step * 1e-6:
                values.append(high)
        elif math.isclose(low, high, rel_tol=0.0, abs_tol=1e-15):
            values = [low]
        else:
            count = max(2, int(param_range.discrete_count))
            step = (high - low) / (count - 1)
            values = [low + index * step for index in range(count)]
        if include_value is not None and low <= float(include_value) <= high:
            values.append(float(include_value))
        return sorted({round(float(value), 15) for value in values})

    @staticmethod
    def _changed_dimensions(start: Params, params: Params) -> Tuple[str, ...]:
        return tuple(
            name
            for name in start
            if not math.isclose(
                float(start[name]),
                float(params[name]),
                rel_tol=0.0,
                abs_tol=1e-12,
            )
        )

    def _point_from_evaluation(
        self,
        *,
        params: Params,
        evaluation: _Evaluation,
        common_start: Params,
        ranges: Dict[str, ParameterRange],
        source_subset: Tuple[str, ...],
        point_id: str,
        pass_index: int,
    ) -> ConvergedPoint:
        changed = self._changed_dimensions(common_start, params)
        audit_subset = changed or source_subset
        return ConvergedPoint(
            subset_id=point_id,
            params=copy.deepcopy(params),
            estimated_resource=copy.deepcopy(evaluation.resource),
            # Grid selection and Top-K both use the pure shared resource Score.
            # An estimator acquisition/search score is deliberately ignored.
            estimated_score=float(evaluation.ranking_score),
            active_subset=audit_subset,
            fixed_params={
                name: float(value)
                for name, value in params.items()
                if name not in audit_subset
            },
            start_params=copy.deepcopy(common_start),
            current_bounds=dict(ranges),
            source_subsets=[audit_subset],
            iterations=pass_index + 1,
            converged=True,
            improving_steps=0,
            estimated_db_qps=evaluation.resource.get("db_qps"),
            estimated_cpu=evaluation.resource.get("cpu"),
            estimated_memory=evaluation.resource.get("memory"),
            estimated_io=evaluation.resource.get("io"),
            iteration_trace=[],
        )

    def _evaluate_grid_specs(
        self,
        sql_set: Any,
        specs: Sequence[Tuple[Params, Tuple[str, ...], str, int]],
        common_start: Params,
        ranges: Dict[str, ParameterRange],
        cache: Dict[Tuple[Tuple[str, float], ...], ConvergedPoint],
        *,
        round_index: int,
        iteration_writer: Optional[_InnerIterationJsonlWriter],
    ) -> List[ConvergedPoint]:
        pending: Dict[
            Tuple[Tuple[str, float], ...],
            Tuple[Params, Tuple[str, ...], str, int],
        ] = {}
        ordered_signatures: List[Tuple[Tuple[str, float], ...]] = []
        for spec in specs:
            signature = self._signature(spec[0])
            ordered_signatures.append(signature)
            if signature in cache:
                source_subset = spec[1]
                if source_subset not in cache[signature].source_subsets:
                    cache[signature].source_subsets.append(source_subset)
                continue
            pending.setdefault(signature, spec)

        def evaluate_one(
            signature: Tuple[Tuple[str, float], ...],
            spec: Tuple[Params, Tuple[str, ...], str, int],
        ) -> Tuple[Tuple[Tuple[str, float], ...], ConvergedPoint]:
            params, source_subset, point_id, pass_index = spec
            try:
                evaluation = self._estimate(sql_set, params)
                point = self._point_from_evaluation(
                    params=params,
                    evaluation=evaluation,
                    common_start=common_start,
                    ranges=ranges,
                    source_subset=source_subset,
                    point_id=point_id,
                    pass_index=pass_index,
                )
            except Exception as exc:
                point = ConvergedPoint(
                    subset_id=point_id,
                    params=copy.deepcopy(params),
                    estimated_resource={},
                    estimated_score=float("-inf"),
                    active_subset=source_subset,
                    fixed_params={
                        name: float(value)
                        for name, value in params.items()
                        if name not in source_subset
                    },
                    start_params=copy.deepcopy(common_start),
                    current_bounds=dict(ranges),
                    source_subsets=[source_subset],
                    iterations=pass_index + 1,
                    converged=False,
                    improving_steps=0,
                    estimated_db_qps=None,
                    estimated_cpu=None,
                    estimated_memory=None,
                    estimated_io=None,
                    iteration_trace=[],
                    failure_reason=f"{type(exc).__name__}: {exc}",
                )
            return signature, point

        if pending:
            workers = min(self.grid_config.max_workers, len(pending))
            if workers <= 1:
                completed = [
                    evaluate_one(signature, spec)
                    for signature, spec in pending.items()
                ]
            else:
                completed = []
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = {
                        executor.submit(evaluate_one, signature, spec): signature
                        for signature, spec in pending.items()
                    }
                    for future in as_completed(futures):
                        completed.append(future.result())
            for signature, point in completed:
                cache[signature] = point
                if iteration_writer is not None:
                    iteration_writer.write(
                        {
                            "event": "double_loop_grid_evaluation",
                            "round": round_index,
                            "point_id": point.subset_id,
                            "pass": max(0, point.iterations - 1),
                            "source_dimensions": list(point.active_subset),
                            "params": copy.deepcopy(point.params),
                            "estimated_resource": copy.deepcopy(point.estimated_resource),
                            "estimated_resource_score": (
                                point.estimated_score
                                if math.isfinite(point.estimated_score)
                                else None
                            ),
                            "failure_reason": point.failure_reason or None,
                            "qps_used_in_score": False,
                        }
                    )
        return [cache[signature] for signature in ordered_signatures]

    def _representative_values(
        self,
        param_range: ParameterRange,
        current: float,
    ) -> List[float]:
        values = self.discretize(param_range, include_value=current)
        requested = self.grid_config.combination_values_per_param
        if len(values) <= requested:
            return values
        indices = {
            round(index * (len(values) - 1) / (requested - 1))
            for index in range(requested)
        }
        nearest = min(range(len(values)), key=lambda index: abs(values[index] - current))
        indices.add(nearest)
        selected = [values[index] for index in sorted(indices)]
        if len(selected) > requested:
            selected = sorted(selected, key=lambda value: (abs(value - current), value))[:requested]
            selected.sort()
        return selected

    def _grid_small_loop(
        self,
        sql_set: Any,
        common_start: Params,
        active: Sequence[str],
        ranges: Dict[str, ParameterRange],
        *,
        round_index: int,
        iteration_writer: Optional[_InnerIterationJsonlWriter],
    ) -> Tuple[List[ConvergedPoint], Dict[str, Any]]:
        cache: Dict[Tuple[Tuple[str, float], ...], ConvergedPoint] = {}
        ordered_active = sorted(active, key=lambda name: (ranges[name].priority, active.index(name)))
        anchor_spec = [
            (copy.deepcopy(common_start), tuple(), f"r{round_index:02d}_anchor", 0)
        ]
        anchor = self._evaluate_grid_specs(
            sql_set,
            anchor_spec,
            common_start,
            ranges,
            cache,
            round_index=round_index,
            iteration_writer=iteration_writer,
        )[0]
        if anchor.failure_reason:
            raise RuntimeError(f"grid anchor estimation failed: {anchor.failure_reason}")
        incumbent_params = copy.deepcopy(common_start)
        incumbent_score = float(anchor.estimated_score)
        no_improvement_passes = 0
        executed_passes = 0

        for pass_index in range(self.grid_config.max_passes):
            executed_passes = pass_index + 1
            pass_improved = False
            for param_index, name in enumerate(ordered_active, start=1):
                values = self.discretize(
                    ranges[name],
                    include_value=incumbent_params[name],
                )
                specs: List[Tuple[Params, Tuple[str, ...], str, int]] = []
                for value_index, value in enumerate(values, start=1):
                    params = copy.deepcopy(incumbent_params)
                    params[name] = value
                    specs.append(
                        (
                            params,
                            (name,),
                            f"r{round_index:02d}_p{pass_index:03d}_d{param_index:02d}_v{value_index:04d}",
                            pass_index,
                        )
                    )
                points = self._evaluate_grid_specs(
                    sql_set,
                    specs,
                    common_start,
                    ranges,
                    cache,
                    round_index=round_index,
                    iteration_writer=iteration_writer,
                )
                usable = [point for point in points if not point.failure_reason]
                coordinate_best = max(
                    usable,
                    key=lambda point: (
                        point.estimated_score,
                        -abs(point.params[name] - incumbent_params[name]),
                    ),
                    default=None,
                )
                if (
                    coordinate_best is not None
                    and coordinate_best.estimated_score
                    > incumbent_score + self.grid_config.score_tolerance
                ):
                    incumbent_params = copy.deepcopy(coordinate_best.params)
                    incumbent_score = float(coordinate_best.estimated_score)
                    pass_improved = True
            if pass_improved:
                no_improvement_passes = 0
            else:
                no_improvement_passes += 1
            if no_improvement_passes >= self.grid_config.no_improvement_patience:
                break

        combination_candidate_count = 0
        if self.grid_config.combination_probe_enabled and len(ordered_active) >= 2:
            specs = []
            max_size = min(len(ordered_active), self.grid_config.combination_max_size)
            for size in range(self.grid_config.combination_min_size, max_size + 1):
                for subset in itertools.combinations(ordered_active, size):
                    choices = [
                        self._representative_values(ranges[name], incumbent_params[name])
                        for name in subset
                    ]
                    for values in itertools.product(*choices):
                        params = copy.deepcopy(incumbent_params)
                        for name, value in zip(subset, values):
                            params[name] = value
                        specs.append(
                            (
                                params,
                                tuple(subset),
                                f"r{round_index:02d}_combo_{size}_{len(specs) + 1:06d}",
                                executed_passes,
                            )
                        )
                        cap = self.grid_config.combination_max_candidates
                        if cap > 0 and len(specs) >= cap:
                            break
                    if self.grid_config.combination_max_candidates > 0 and len(specs) >= self.grid_config.combination_max_candidates:
                        break
                if self.grid_config.combination_max_candidates > 0 and len(specs) >= self.grid_config.combination_max_candidates:
                    break
            combination_candidate_count = len(specs)
            self._evaluate_grid_specs(
                sql_set,
                specs,
                common_start,
                ranges,
                cache,
                round_index=round_index,
                iteration_writer=iteration_writer,
            )

        points = list(cache.values())
        return points, {
            "algorithm": "binary_plan_coordinate_grid_plus_resource_estimation",
            "coordinate_order": ordered_active,
            "grid_pass_count": executed_passes,
            "grid_candidate_count": len(points),
            "combination_probe_enabled": self.grid_config.combination_probe_enabled,
            "combination_candidate_count_before_dedup": combination_candidate_count,
            "combination_max_candidates": self.grid_config.combination_max_candidates,
            "combination_cap_applied": (
                self.grid_config.combination_max_candidates > 0
                and combination_candidate_count >= self.grid_config.combination_max_candidates
            ),
            "estimated_ranking_score": "sigmoid_linear_sweet_band_weighted_mean_v2",
            "qps_used_in_score": False,
        }

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
        logger = OptimizationLogger(log_path, self.verbose)
        inner_writer = (
            _InnerIterationJsonlWriter(inner_iteration_log_path)
            if inner_iteration_log_path
            else None
        )
        try:
            initial_params = {name: float(value) for name, value in p_real.items()}
            ranges = dict(param_ranges)
            candidates = list(candidate_params or ranges.keys())
            missing = [
                name
                for name in candidates
                if name not in ranges or name not in initial_params
            ]
            if missing:
                raise ValueError(f": {missing}")
            invalid = [
                name
                for name in candidates
                if not ranges[name].min_value
                <= initial_params[name]
                <= ranges[name].max_value
            ]
            if invalid:
                raise ValueError(f": {invalid}")
            active = (
                list(self.workload_pruner(sql_set, candidates))
                if self.workload_pruner
                else list(candidates)
            )
            active = list(dict.fromkeys(str(name) for name in active))
            if not active:
                raise ValueError("")
            invalid_active = [name for name in active if name not in candidates]
            if invalid_active:
                raise ValueError(f"workload_pruner : {invalid_active}")
            pruned = [name for name in candidates if name not in active]

            if initial_real_resource is None:
                initial_real_eval = self._real_validate(sql_set, initial_params)
            else:
                initial_real_eval = self._parse_evaluation(
                    initial_real_resource,
                    self.real_score_func,
                )
            logger.log_struct(
                {
                    "event": "optimizer_preflight_quality_gate",
                    "required": self.preflight_quality_gate is not None,
                    "summary": copy.deepcopy(self.preflight_quality_gate),
                    "passed": (
                        bool(self.preflight_quality_gate.get("passed"))
                        if self.preflight_quality_gate is not None
                        else None
                    ),
                }
            )

            best_params = copy.deepcopy(initial_params)
            best_real_eval = initial_real_eval
            best_is_successful = False
            real_validation_count = 0 if initial_real_resource is not None else 1
            rounds: List[OuterRound] = []
            rejected_rounds = 0
            stop_reason = "max_rounds"

            for round_index in range(self.outer_config.max_rounds):
                ranges_before = dict(ranges)
                common_start = copy.deepcopy(best_params)
                points, grid_meta = self._grid_small_loop(
                    sql_set,
                    common_start,
                    active,
                    ranges,
                    round_index=round_index,
                    iteration_writer=inner_writer,
                )
                ranked = self._deduplicate_and_rank(points)
                validation_limit = self._validation_limit(len(ranked))
                validated: List[RealValidation] = []
                adaptive_decisions: List[Dict[str, Any]] = []
                pruning_decisions: List[Dict[str, Any]] = []
                real_evaluations: Dict[Tuple[Tuple[str, float], ...], _Evaluation] = {}
                rank_by_signature = {
                    self._signature(point.params): rank
                    for rank, point in enumerate(ranked, start=1)
                }
                cursor = 0
                while cursor < len(ranked) and len(validated) < validation_limit:
                    point = ranked[cursor]
                    cursor += 1
                    signature = self._signature(point.params)
                    estimated_rank = rank_by_signature[signature]
                    prune = self._resource_bound_pruning_decision(
                        point,
                        validated,
                        estimated_rank=estimated_rank,
                    )
                    prune["round"] = round_index
                    pruning_decisions.append(copy.deepcopy(prune))
                    logger.log_struct(prune)
                    if prune["action"] == "prune_without_real_replay":
                        continue
                    real_validation_count += 1
                    source_subset = min(
                        point.source_subsets,
                        key=lambda item: (len(item), item),
                        default=tuple(),
                    )
                    try:
                        real_eval = self._real_validate(sql_set, point.params)
                        real_evaluations[signature] = real_eval
                        validated.append(
                            RealValidation(
                                params=copy.deepcopy(point.params),
                                resource=copy.deepcopy(real_eval.resource),
                                estimated_resource=copy.deepcopy(point.estimated_resource),
                                score=real_eval.score,
                                estimated_score=point.estimated_score,
                                estimated_rank=estimated_rank,
                                active_subset=source_subset,
                                source_subsets=list(point.source_subsets),
                                successful=False,
                                raw_result=real_eval.raw,
                            )
                        )
                    except Exception as exc:
                        validated.append(
                            RealValidation(
                                params=copy.deepcopy(point.params),
                                resource={},
                                estimated_resource=copy.deepcopy(point.estimated_resource),
                                score=float("-inf"),
                                estimated_score=point.estimated_score,
                                estimated_rank=estimated_rank,
                                active_subset=source_subset,
                                source_subsets=list(point.source_subsets),
                                successful=False,
                                failure_reason=f"{type(exc).__name__}: {exc}",
                            )
                        )

                    at_boundary = (
                        len(validated) % self.outer_config.adaptive_topk_batch_size == 0
                    )
                    if not at_boundary and len(validated) < validation_limit:
                        continue
                    decision = self._adaptive_topk_decision(
                        validated,
                        ranked,
                        validation_limit,
                        remaining_ranked=ranked[cursor:],
                    )
                    decision["round"] = round_index
                    adaptive_decisions.append(copy.deepcopy(decision))
                    logger.log_struct(decision)
                    if decision["action"] == "stop":
                        break

                usable = [
                    item
                    for item in validated
                    if not item.failure_reason and math.isfinite(item.score)
                ]
                p_star = max(usable, key=lambda item: item.score, default=None)
                outer_accepted = False
                acceptance_reason = "no_usable_small_loop_best"
                acceptance_details: Dict[str, Any] = {}
                if p_star is not None:
                    real_eval = real_evaluations[self._signature(p_star.params)]
                    outer_accepted, acceptance_reason, acceptance_details = self._outer_decide(
                        real_eval,
                        initial_real_eval,
                    )
                    p_star.successful = outer_accepted
                    p_star.outer_accepted = outer_accepted
                    p_star.outer_acceptance_reason = acceptance_reason
                    p_star.outer_acceptance_details = copy.deepcopy(acceptance_details)
                    if self.outer_round_evidence_func is not None:
                        try:
                            evidence = self.outer_round_evidence_func(
                                sql_set,
                                round_index,
                                copy.deepcopy(p_star.params),
                                real_eval.raw,
                            )
                            if isinstance(evidence, Mapping):
                                if isinstance(real_eval.raw, dict):
                                    real_eval.raw.update(copy.deepcopy(dict(evidence)))
                                if isinstance(p_star.raw_result, dict):
                                    p_star.raw_result.update(copy.deepcopy(dict(evidence)))
                                logger.log_struct(
                                    {
                                        "event": "outer_round_evidence_capture",
                                        "round": round_index,
                                        "status": "ok",
                                        "params": copy.deepcopy(p_star.params),
                                        "evidence_keys": sorted(str(key) for key in evidence),
                                        "participates_in_ranking": False,
                                        "participates_in_acceptance": False,
                                    }
                                )
                        except Exception as exc:
                            logger.log_struct(
                                {
                                    "event": "outer_round_evidence_capture",
                                    "round": round_index,
                                    "status": "failed",
                                    "params": copy.deepcopy(p_star.params),
                                    "error": f"{type(exc).__name__}: {exc}",
                                    "participates_in_ranking": False,
                                    "participates_in_acceptance": False,
                                }
                            )
                for item in validated:
                    if item is not p_star:
                        item.outer_acceptance_reason = "not_small_loop_real_score_best"

                if outer_accepted:
                    assert p_star is not None
                    best_params = copy.deepcopy(p_star.params)
                    best_real_eval = real_evaluations[self._signature(best_params)]
                    best_is_successful = True
                    rejected_rounds = 0


                    shrink_dimensions = tuple(active)
                    ranges = self._binary_shrink(ranges, p_star.params, shrink_dimensions)
                else:
                    rejected_rounds += 1
                    shrink_dimensions = tuple()

                outer_round = OuterRound(
                    round_index=round_index,
                    common_start_params=common_start,
                    accepted_anchor_after=copy.deepcopy(best_params),
                    ranges_before=ranges_before,
                    ranges_after=dict(ranges),
                    subset_count=len(points),
                    subset_runs=points,
                    converged_points=ranked,
                    validated_points=validated,
                    best_real_score=best_real_eval.score,
                    best_params=copy.deepcopy(best_params),
                    binary_shrink_subset=shrink_dimensions,
                    improved=outer_accepted,
                    small_loop_best=p_star,
                    outer_accepted=outer_accepted,
                    outer_acceptance_reason=acceptance_reason,
                    outer_acceptance_details=copy.deepcopy(acceptance_details),
                    adaptive_topk_decisions=adaptive_decisions,
                    resource_bound_pruning_decisions=pruning_decisions,
                )
                rounds.append(outer_round)
                logger.log_struct(
                    {
                        "event": "double_loop_grid_round",
                        "round": round_index,
                        **grid_meta,
                        "active_params": list(active),
                        "estimated_candidate_count": len(points),
                        "unique_ranked_candidate_count": len(ranked),
                        "real_validation_limit": validation_limit,
                        "actual_real_validation_count": len(validated),
                        "resource_bound_pruned_candidate_count": sum(
                            decision.get("action") == "prune_without_real_replay"
                            for decision in pruning_decisions
                        ),
                        "p_star": (
                            {
                                "params": copy.deepcopy(p_star.params),
                                "estimated_rank": p_star.estimated_rank,
                                "estimated_score": p_star.estimated_score,
                                "real_score": p_star.score,
                            }
                            if p_star is not None
                            else None
                        ),
                        "outer_accepted": outer_accepted,
                        "outer_acceptance_reason": acceptance_reason,
                        "outer_acceptance_details": copy.deepcopy(acceptance_details),
                        "shrink_dimensions": list(shrink_dimensions),
                        "ranges_after": {
                            name: [ranges[name].min_value, ranges[name].max_value]
                            for name in active
                        },
                    }
                )
                logger.log(
                    f"[Grid Round {round_index}] estimated={len(points)}, "
                    f"unique={len(ranked)}, real={len(validated)}, "
                    f"accepted={outer_accepted}"
                )

                if self._ranges_converged(ranges, active):
                    stop_reason = "epsilon_converged"
                    break
                if (
                    self.outer_config.no_improvement_patience > 0
                    and rejected_rounds >= self.outer_config.no_improvement_patience
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
                inner_iteration_log_path=(inner_writer.path if inner_writer else None),
            )
        finally:
            if inner_writer is not None:
                inner_writer.close()
            logger.close()


def optimize_with_double_loop_grid(
    resource_estimation_func: Any,
    real_validation_func: Any,
    capability: Dict[str, float],
    p_real: Params,
    param_ranges: Dict[str, ParameterRange],
    sql_set: Any,
    **kwargs: Any,
) -> DoubleLoopResult:
    optimize_keys = {
        "candidate_params",
        "initial_real_resource",
        "log_path",
        "inner_iteration_log_path",
    }
    optimize_kwargs = {
        key: kwargs.pop(key) for key in list(kwargs) if key in optimize_keys
    }
    optimizer = DoubleLoopGridOptimizer(
        resource_estimation_func,
        real_validation_func,
        capability,
        **kwargs,
    )
    return optimizer.optimize(p_real, param_ranges, sql_set, **optimize_kwargs)
