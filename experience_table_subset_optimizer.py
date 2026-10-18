"""Module."""


from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime
import copy
import itertools
import json
import math
import os
from pathlib import Path
import re
import time
import traceback
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from gradient_descent_optimizer import (
    OptimizationResult,
    ParameterRange,
    optimize_with_gradient_descent,
)
from common_outer_loop import sanitize_optimistic_pruning_payload


ResourceEstimator = Callable[[Any, Dict[str, float]], Dict[str, float]]
ResourceEstimatorFactory = Callable[[Tuple[str, ...], str], ResourceEstimator]


FORMAL_OPTIMIZER_OPTION_KEYS = frozenset(
    {
        "eta",
        "max_iter",
        "patience",
        "gamma",
        "min_eta",
        "min_param_delta_rel_pct",
        "max_param_delta_rel_pct",
        "boundary_aware_eta_enabled",
        "boundary_eta_growth_factor",
        "plateau_exploration_enabled",
        "plateau_score_epsilon",
        "direction_override_log_vector",
        "active_bottleneck_underuse_scale",
        "post_bottleneck_target_util",
        "post_bottleneck_non_bottleneck_pressure_scale",
        "target_bottleneck_resources",
        "target_bottleneck_utilization_override",
        "reward_type",
        "penalty_type",
        "use_continuous",
        "continuous_type",
        "overload_threshold",
        "score_mode",
        "resource_weights",
        "resource_reward_weights",
        "resource_penalty_weights",
        "resource_target_ranges",
        "original_gradient_only",
        "verbose",
        "identify_sys_func",
    }
)


@dataclass
class AllSubsetOptimizationResult:
    """AllSubsetOptimizationResult."""

    best_result: OptimizationResult
    candidate_rankings: List[Dict[str, Any]]
    all_candidate_rankings: List[Dict[str, Any]]
    audit_records: List[Dict[str, Any]]
    subset_count: int
    successful_subset_count: int
    failed_subset_count: int
    unique_candidate_count: int
    round_index: Any
    log_dir: str
    inner_algorithm: str = "experience_table_analytic_sgd_v1"
    total_inner_iterations: int = 0

    @property
    def rankings(self) -> List[Dict[str, Any]]:
        """rankings."""

        return self.candidate_rankings

    @property
    def subset_audit(self) -> List[Dict[str, Any]]:
        """subset audit."""

        return self.audit_records

    def top_candidates(self, limit: int = 20) -> List[Dict[str, Any]]:
        """top candidates."""

        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError("limit ")
        return copy.deepcopy(self.candidate_rankings[:limit])


class AllSubsetOptimizationError(RuntimeError):
    """AllSubsetOptimizationError."""

    def __init__(self, message: str, audit_records: List[Dict[str, Any]]) -> None:
        super().__init__(message)
        self.audit_records = audit_records
        self.subset_audit = audit_records
        self.candidate_rankings: List[Dict[str, Any]] = []


class IncompleteSubsetOptimizationError(RuntimeError):
    """IncompleteSubsetOptimizationError."""

    def __init__(
        self,
        message: str,
        audit_records: List[Dict[str, Any]],
        log_dir: str,
    ) -> None:
        super().__init__(message)
        self.audit_records = audit_records
        self.subset_audit = audit_records
        self.candidate_rankings: List[Dict[str, Any]] = []
        self.all_candidate_rankings: List[Dict[str, Any]] = []
        self.subset_count = len(audit_records)
        self.successful_subset_count = sum(
            1 for record in audit_records if record.get("status") == "success"
        )
        self.failed_subset_count = self.subset_count - self.successful_subset_count
        self.log_dir = log_dir


@dataclass
class _SuccessfulRun:
    canonical_order: int
    subset_id: str
    active_subset: Tuple[str, ...]
    log_path: str
    result: OptimizationResult


def _canonical_active_params(active_params: Sequence[str]) -> Tuple[str, ...]:
    names = tuple(str(name).strip() for name in active_params)
    if not names or any(not name for name in names):
        raise ValueError("active_params ")
    if len(set(names)) != len(names):
        raise ValueError("active_params ")


    return names


def _all_nonempty_subsets(active_params: Tuple[str, ...]) -> List[Tuple[str, ...]]:
    subsets = [
        subset
        for size in range(1, len(active_params) + 1)
        for subset in itertools.combinations(active_params, size)
    ]
    expected = (1 << len(active_params)) - 1
    if len(subsets) != expected:
        raise AssertionError(
            f": actual={len(subsets)}, expected={expected}"
        )
    return subsets


def _safe_name(value: Any) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("_.")
    return normalized or "value"


def _write_log_event(log_path: str, event: Mapping[str, Any]) -> None:
    record = dict(event)
    record.setdefault("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def _exact_parameter_signature(params: Mapping[str, Any]) -> Tuple[Tuple[str, str], ...]:
    """ exact parameter signature."""

    signature: List[Tuple[str, str]] = []
    for name in sorted(params):
        value = params[name]
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            encoded = f"{type(value).__module__}.{type(value).__qualname__}:{value!r}"
        else:
            encoded = numeric.hex()
        signature.append((str(name), encoded))
    return tuple(signature)


def _predicted_target_utilization(
    run: _SuccessfulRun,
    ranking_target_resource: Optional[str],
    capability: Mapping[str, Any],
) -> Optional[float]:
    """Return the predicted target utilization used only as a score tie-break."""

    if not ranking_target_resource:
        return None
    try:
        target_value = float(run.result.final_resource[ranking_target_resource])
        target_capability = float(capability[ranking_target_resource])
    except (KeyError, TypeError, ValueError):
        return None
    if (
        not math.isfinite(target_value)
        or not math.isfinite(target_capability)
        or target_capability <= 0.0
    ):
        return None
    utilization = target_value / target_capability
    return utilization if math.isfinite(utilization) else None


def _run_ranking_key(
    run: _SuccessfulRun,
    ranking_target_resource: Optional[str],
    capability: Mapping[str, Any],
) -> Tuple[float, float, int]:
    """Keep final score primary; prefer lower predicted target use on exact ties."""

    target_utilization = _predicted_target_utilization(
        run,
        ranking_target_resource,
        capability,
    )
    return (
        -float(run.result.final_score),
        target_utilization if target_utilization is not None else math.inf,
        run.canonical_order,
    )


def _public_ranking(
    run: _SuccessfulRun,
    ranking_target_resource: Optional[str] = None,
    capability: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    result = run.result
    ranking = {
        "subset_id": run.subset_id,
        "active_subset": list(run.active_subset),
        "canonical_subset_order": run.canonical_order,
        "optimized_params": copy.deepcopy(result.optimized_params),
        "initial_params": copy.deepcopy(result.initial_params),
        "initial_resource": copy.deepcopy(result.initial_resource),
        "final_resource": copy.deepcopy(result.final_resource),
        "initial_score": float(result.initial_score),
        "final_score": float(result.final_score),
        "score_improvement": float(result.score_improvement),
        "iterations": int(result.iterations),
        "log_path": run.log_path,
        # Preserve the exact raw-gradient events that produced this subset
        # winner.  Formal sealing validates these events again instead of
        # trusting the summary parameters/score alone.
        "gradient_events": [
            copy.deepcopy(record["gradient_debug"])
            for record in result.history
            if isinstance(record, Mapping)
            and isinstance(record.get("gradient_debug"), Mapping)
        ],
    }
    if ranking_target_resource:
        target_utilization = _predicted_target_utilization(
            run,
            ranking_target_resource,
            capability or {},
        )
        ranking["ranking_tiebreak"] = {
            "contract": "final_score_desc_then_predicted_target_utilization_asc_then_canonical_subset_order_v1",
            "target_resource": ranking_target_resource,
            "predicted_target_resource": copy.deepcopy(
                result.final_resource.get(ranking_target_resource)
            ),
            "predicted_target_utilization": target_utilization,
            "applies_only_when_final_score_equal": True,
        }
    final_metadata = getattr(result, "final_estimation_metadata", None)
    if isinstance(final_metadata, dict):
        sanitized_metadata = copy.deepcopy(final_metadata)
        pruning_input = final_metadata.get("optimistic_replay_pruning_input")
        if isinstance(pruning_input, dict):
            sanitized_pruning = sanitize_optimistic_pruning_payload(pruning_input)
            sanitized_metadata["optimistic_replay_pruning_input"] = copy.deepcopy(
                sanitized_pruning
            )
            ranking["optimistic_replay_pruning_input"] = copy.deepcopy(
                sanitized_pruning
            )
        ranking["final_estimation_metadata"] = sanitized_metadata
        # A formal candidate is an estimator result for exactly the parameters
        # that the raw-gradient inner loop returned.  Do not let a cached or
        # stale baseline payload be silently relabelled as a new candidate.
        metadata_params = sanitized_metadata.get("params")
        if not isinstance(metadata_params, Mapping):
            raise RuntimeError(
                "formal candidate lacks estimator parameter-identity evidence"
            )
        requested_signature = _exact_parameter_signature(result.optimized_params)
        metadata_signature = _exact_parameter_signature(metadata_params)
        if metadata_signature != requested_signature:
            raise RuntimeError(
                "formal candidate estimator parameter identity differs from "
                "optimized_params"
            )
        plan_bundle = (
            sanitized_metadata.get("canonical_plan_bundle")
            or sanitized_metadata.get("plan_bundle")
        )
        if not isinstance(plan_bundle, Mapping):
            raise RuntimeError("formal candidate lacks canonical plan bundle")
        ranking["canonical_plan_bundle"] = copy.deepcopy(dict(plan_bundle))
        ranking["candidate_estimation_identity"] = {
            "contract": "exact_candidate_parameters_and_canonical_plan_v1",
            "requested_parameter_signature": [list(item) for item in requested_signature],
            "metadata_parameter_signature": [list(item) for item in metadata_signature],
            "candidate_plan_work_sha256": sanitized_metadata.get("plan_work_sha256"),
            "baseline_plan_work_sha256": sanitized_metadata.get(
                "baseline_plan_work_sha256"
            ),
            "verified": True,
        }
    return ranking


def _attach_compatibility_metadata(
    result: OptimizationResult,
    summary: AllSubsetOptimizationResult,
    best_subset: Tuple[str, ...],
    best_subset_id: str,
) -> None:
    setattr(result, "candidate_rankings", copy.deepcopy(summary.candidate_rankings))
    setattr(result, "all_candidate_rankings", copy.deepcopy(summary.all_candidate_rankings))
    setattr(result, "subset_audit", copy.deepcopy(summary.audit_records))
    setattr(result, "audit_records", copy.deepcopy(summary.audit_records))
    setattr(result, "subset_count", summary.subset_count)
    setattr(result, "successful_subset_count", summary.successful_subset_count)
    setattr(result, "failed_subset_count", summary.failed_subset_count)
    setattr(result, "unique_candidate_count", summary.unique_candidate_count)
    setattr(result, "best_subset", best_subset)
    setattr(result, "best_subset_id", best_subset_id)
    setattr(result, "subset_log_dir", summary.log_dir)
    setattr(result, "inner_algorithm", summary.inner_algorithm)
    setattr(result, "total_inner_iterations", summary.total_inner_iterations)


def optimize_all_experience_table_subsets(
    resource_estimation_factory: ResourceEstimatorFactory,
    capability: Dict[str, float],
    p_real: Dict[str, float],
    sensitivity_table: Dict[str, Dict[str, float]],
    param_ranges: Dict[str, ParameterRange],
    sql_set: Any,
    active_params: Sequence[str],
    max_workers: int,
    round_index: Any,
    log_dir: str,
    optimizer_options: Optional[Mapping[str, Any]] = None,
    ranking_target_resource: Optional[str] = None,
    restored_subset_results: Optional[Mapping[str, Mapping[str, Any]]] = None,
    on_subset_completed: Optional[Callable[[str, Mapping[str, Any]], None]] = None,
) -> AllSubsetOptimizationResult:
    """optimize all experience table subsets."""


    if not callable(resource_estimation_factory):
        raise TypeError("resource_estimation_factory ")
    if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers <= 0:
        raise ValueError("max_workers ")

    canonical_params = _canonical_active_params(active_params)
    if ranking_target_resource is not None:
        ranking_target_resource = str(ranking_target_resource).strip()
        if not ranking_target_resource:
            ranking_target_resource = None
        elif ranking_target_resource not in capability:
            raise ValueError(
                "ranking_target_resource  capability : "
                f"{ranking_target_resource!r}"
            )
    missing_from_real = [name for name in canonical_params if name not in p_real]
    missing_ranges = [name for name in canonical_params if name not in param_ranges]
    if missing_from_real:
        raise ValueError(f"active_params  p_real : {missing_from_real}")
    if missing_ranges:
        raise ValueError(f"active_params : {missing_ranges}")

    options = dict(optimizer_options or {})
    unsupported = sorted(set(options) - FORMAL_OPTIMIZER_OPTION_KEYS)
    if unsupported:
        raise ValueError(f"optimizer_options informal sensitivity-prior option: {unsupported}")

    subsets = _all_nonempty_subsets(canonical_params)
    call_stamp = datetime.now()
    run_token = f"{time.time_ns()}_{os.getpid()}"
    run_dir = Path(log_dir) / (
        f"round_{_safe_name(round_index)}_{call_stamp.strftime('%Y%m%dT%H%M%S')}_{run_token}"
    )
    run_dir.mkdir(parents=True, exist_ok=False)
    filename_stamp = call_stamp.strftime("%Y-%m-%d_%H%M%S")

    audit_by_order: Dict[int, Dict[str, Any]] = {}
    successes: List[_SuccessfulRun] = []
    restored = dict(restored_subset_results or {})

    def run_subset(
        canonical_order: int,
        active_subset: Tuple[str, ...],
    ) -> Tuple[Optional[_SuccessfulRun], Dict[str, Any]]:
        subset_id = f"subset_{canonical_order:04d}__{'__'.join(active_subset)}"
        log_path = str(
            run_dir
            / f"{_safe_name(subset_id)}_{filename_stamp}.jsonl"
        )
        started_at = time.perf_counter()
        _write_log_event(
            log_path,
            {
                "event": "experience_table_subset_start",
                "round_index": round_index,
                "subset_id": subset_id,
                "canonical_subset_order": canonical_order,
                "active_subset": list(active_subset),
                "inner_algorithm": "experience_table_analytic_sgd_v1",
                "gradient_source": "frozen_experience_table_times_analytic_utility_derivative",
                "p_real": copy.deepcopy(p_real),
            },
        )
        try:
            estimator = resource_estimation_factory(active_subset, subset_id)
            if not callable(estimator):
                raise TypeError(
                    "resource_estimation_factory  estimator"
                )

            subset_p_real = copy.deepcopy(p_real)
            subset_result = optimize_with_gradient_descent(
                resource_estimation_func=estimator,
                capability=copy.deepcopy(capability),
                p_real=subset_p_real,
                p_candidate=copy.deepcopy(subset_p_real),
                sensitivity_table=copy.deepcopy(sensitivity_table),
                param_ranges=copy.deepcopy(param_ranges),
                sql_set=sql_set,
                active_params=list(active_subset),
                log_path=log_path,
                **copy.deepcopy(options),
            )
            final_score = float(subset_result.final_score)
            if not math.isfinite(final_score):
                raise ValueError(f" final_score : {final_score!r}")

            non_subset_changed = [
                name
                for name, value in p_real.items()
                if name not in active_subset
                and subset_result.optimized_params.get(name) != value
            ]
            if non_subset_changed:
                raise RuntimeError(
                    f" SGD : {non_subset_changed}"
                )

            duration_s = time.perf_counter() - started_at
            audit = {
                "round_index": round_index,
                "subset_id": subset_id,
                "canonical_subset_order": canonical_order,
                "active_subset": list(active_subset),
                "status": "success",
                "p_real": copy.deepcopy(p_real),
                "final_score": final_score,
                "optimized_params": copy.deepcopy(subset_result.optimized_params),
                "iterations": int(subset_result.iterations),
                "inner_algorithm": "experience_table_analytic_sgd_v1",
                "evaluated_sgd_iterations": int(subset_result.iterations),
                "log_path": log_path,
                "duration_s": duration_s,
            }
            _write_log_event(
                log_path,
                {"event": "experience_table_subset_complete", **audit},
            )
            return (
                _SuccessfulRun(
                    canonical_order=canonical_order,
                    subset_id=subset_id,
                    active_subset=active_subset,
                    log_path=log_path,
                    result=subset_result,
                ),
                audit,
            )
        except Exception as exc:
            duration_s = time.perf_counter() - started_at
            audit = {
                "round_index": round_index,
                "subset_id": subset_id,
                "canonical_subset_order": canonical_order,
                "active_subset": list(active_subset),
                "status": "failed",
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "traceback": traceback.format_exc(),
                "log_path": log_path,
                "duration_s": duration_s,
            }
            _write_log_event(
                log_path,
                {"event": "experience_table_subset_failed", **audit},
            )
            return None, audit

    def restore_subset(
        canonical_order: int, active_subset: Tuple[str, ...], payload: Mapping[str, Any],
    ) -> Tuple[_SuccessfulRun, Dict[str, Any]]:
        subset_id = f"subset_{canonical_order:04d}__{'__'.join(active_subset)}"
        if (
            payload.get("subset_id") != subset_id
            or payload.get("canonical_subset_order") != canonical_order
            or payload.get("active_subset") != list(active_subset)
        ):
            raise ValueError(f"restored subset identity mismatch: {subset_id}")
        result_payload = payload.get("optimization_result")
        audit = payload.get("audit")
        if not isinstance(result_payload, Mapping) or not isinstance(audit, Mapping):
            raise ValueError(f"restored subset result is incomplete: {subset_id}")
        result = OptimizationResult(**copy.deepcopy(dict(result_payload)))
        if _exact_parameter_signature(result.initial_params) != _exact_parameter_signature(p_real):
            raise ValueError(f"restored subset baseline mismatch: {subset_id}")
        return _SuccessfulRun(
            canonical_order=canonical_order, subset_id=subset_id,
            active_subset=active_subset, log_path=str(audit.get("log_path") or ""),
            result=result,
        ), copy.deepcopy(dict(audit))

    def persist_success(successful_run: _SuccessfulRun, audit: Mapping[str, Any]) -> None:
        if on_subset_completed is None:
            return
        on_subset_completed(successful_run.subset_id, {
            "subset_id": successful_run.subset_id,
            "canonical_subset_order": successful_run.canonical_order,
            "active_subset": list(successful_run.active_subset),
            "optimization_result": asdict(successful_run.result),
            "audit": copy.deepcopy(dict(audit)),
        })

    pending: List[Tuple[int, Tuple[str, ...]]] = []
    for canonical_order, subset in enumerate(subsets, start=1):
        subset_id = f"subset_{canonical_order:04d}__{'__'.join(subset)}"
        if subset_id in restored:
            successful_run, audit = restore_subset(
                canonical_order, subset, restored[subset_id]
            )
            audit_by_order[canonical_order] = audit
            successes.append(successful_run)
        else:
            pending.append((canonical_order, subset))

    def collect(
        canonical_order: int,
        outcome: Tuple[Optional[_SuccessfulRun], Dict[str, Any]],
    ) -> None:
        successful_run, audit = outcome
        audit_by_order[canonical_order] = audit
        if successful_run is not None:
            persist_success(successful_run, audit)
            successes.append(successful_run)

    if max_workers == 1:
        for canonical_order, subset in pending:
            collect(canonical_order, run_subset(canonical_order, subset))
    else:
        futures: Dict[
            Future[Tuple[Optional[_SuccessfulRun], Dict[str, Any]]], int,
        ] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for canonical_order, subset in pending:
                future = executor.submit(run_subset, canonical_order, subset)
                futures[future] = canonical_order
            for future in as_completed(futures):
                canonical_order = futures[future]
                try:
                    collect(canonical_order, future.result())
                except Exception as exc:


                    active_subset = subsets[canonical_order - 1]
                    subset_id = (
                        f"subset_{canonical_order:04d}__{'__'.join(active_subset)}"
                    )
                    audit = {
                        "round_index": round_index,
                        "subset_id": subset_id,
                        "canonical_subset_order": canonical_order,
                        "active_subset": list(active_subset),
                        "status": "failed",
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                        "traceback": traceback.format_exc(),
                        "log_path": "",
                        "duration_s": None,
                    }
                    audit_by_order[canonical_order] = audit

    audit_records = [audit_by_order[index] for index in range(1, len(subsets) + 1)]
    if not successes:
        raise AllSubsetOptimizationError(
            f" {len(subsets)}  SGD ",
            copy.deepcopy(audit_records),
        )
    if len(successes) != len(subsets):
        raise IncompleteSubsetOptimizationError(
            (
                " SGD  fail-closed: "
                f"success={len(successes)}, failed={len(subsets) - len(successes)}, "
                f"expected={len(subsets)}"
            ),
            copy.deepcopy(audit_records),
            str(run_dir),
        )

    successes.sort(
        key=lambda run: _run_ranking_key(
            run,
            ranking_target_resource,
            capability,
        )
    )
    all_candidate_rankings = []
    for rank, run in enumerate(successes, start=1):
        item = _public_ranking(run, ranking_target_resource, capability)
        item["pre_fingerprint_rank"] = rank
        all_candidate_rankings.append(item)

    candidate_rankings: List[Dict[str, Any]] = []
    for rank, run in enumerate(successes, start=1):
        item = _public_ranking(run, ranking_target_resource, capability)
        item["rank"] = rank
        item["duplicate_subset_ids"] = []
        item["duplicate_evidence_status"] = (
            "not_evaluated_until_parameter_l1_l2_l3_replay_evidence"
        )
        candidate_rankings.append(item)
        audit_by_order[run.canonical_order]["candidate_rank"] = rank

    # Candidate identity remains independent until replay provides L1/L2/L3 evidence.
    audit_records = [audit_by_order[index] for index in range(1, len(subsets) + 1)]
    best_run = successes[0]
    summary = AllSubsetOptimizationResult(
        best_result=best_run.result,
        candidate_rankings=copy.deepcopy(candidate_rankings),
        all_candidate_rankings=copy.deepcopy(all_candidate_rankings),
        audit_records=copy.deepcopy(audit_records),
        subset_count=len(subsets),
        successful_subset_count=len(successes),
        failed_subset_count=len(subsets) - len(successes),
        unique_candidate_count=len(successes),
        round_index=round_index,
        log_dir=str(run_dir),
        inner_algorithm="experience_table_analytic_sgd_v1",
        total_inner_iterations=sum(int(run.result.iterations) for run in successes),
    )
    _attach_compatibility_metadata(
        summary.best_result,
        summary,
        best_run.active_subset,
        best_run.subset_id,
    )
    return summary


__all__ = [
    "AllSubsetOptimizationError",
    "AllSubsetOptimizationResult",
    "FORMAL_OPTIMIZER_OPTION_KEYS",
    "IncompleteSubsetOptimizationError",
    "ResourceEstimatorFactory",
    "optimize_all_experience_table_subsets",
]
