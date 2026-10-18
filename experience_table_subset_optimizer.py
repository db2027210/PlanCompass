"""Module."""


from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
import copy
import hashlib
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


ResourceEstimator = Callable[[Any, Dict[str, float]], Dict[str, float]]
ResourceEstimatorFactory = Callable[[Tuple[str, ...], str], ResourceEstimator]
EXACT_PLAN_FINGERPRINT_CONTRACT = "full_plannable_workload_exact_physical_structure_v1"


FORMAL_OPTIMIZER_OPTION_KEYS = frozenset(
    {
        "eta",
        "max_iter",
        "patience",
        "gamma",
        "min_eta",
        "min_param_delta_rel_pct",
        "max_param_delta_rel_pct",
        "relative_step_probe_pct",
        "boundary_aware_eta_enabled",
        "boundary_eta_growth_factor",
        "plateau_exploration_enabled",
        "plateau_score_epsilon",
        "full_subset_boundary_corner_scan_enabled",
        "directional_scoring_contract_required",
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


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validated_public_plan_fingerprints(
    metadata: Mapping[str, Any],
) -> Dict[str, Any]:
    """Return exact plan fields only when the full-workload proof is intact."""

    if metadata.get("plan_fingerprint_contract") != EXACT_PLAN_FINGERPRINT_CONTRACT:
        return {}
    coverage = metadata.get("plan_fingerprint_coverage")
    fingerprints = metadata.get("plan_fingerprints")
    if not isinstance(coverage, Mapping) or not isinstance(fingerprints, Mapping):
        return {}
    try:
        expected_count = int(coverage.get("plannable_query_count"))
        explained_count = int(coverage.get("explain_result_count"))
        successful_count = int(coverage.get("successful_explain_count"))
        failed_count = int(coverage.get("failed_explain_count"))
        dynamic_count = int(coverage.get("dynamic_binding_query_count"))
        duplicate_count = int(coverage.get("duplicate_query_identity_count"))
        fingerprint_count = int(coverage.get("fingerprint_count"))
    except (TypeError, ValueError):
        return {}
    if not (
        coverage.get("contract") == EXACT_PLAN_FINGERPRINT_CONTRACT
        and coverage.get("complete") is True
        and coverage.get("dynamic_binding_evidence_complete") is True
        and expected_count > 0
        and explained_count == expected_count
        and successful_count == expected_count
        and failed_count == 0
        and dynamic_count == 0
        and duplicate_count == 0
        and fingerprint_count == expected_count
        and len(fingerprints) == expected_count
    ):
        return {}
    normalized_fingerprints: Dict[str, str] = {}
    for query_identity, fingerprint in fingerprints.items():
        if not isinstance(query_identity, str) or not re.fullmatch(
            r"sql-sha256:[0-9a-f]{64}",
            query_identity,
        ):
            return {}
        if not isinstance(fingerprint, str) or not re.fullmatch(
            r"exact-physical-sha256:[0-9a-f]{64}",
            fingerprint,
        ):
            return {}
        normalized_fingerprints[query_identity] = fingerprint
    expected_workload_fingerprint = (
        "workload-exact-physical-sha256:"
        + _canonical_sha256(normalized_fingerprints)
    )
    for key in (
        "plan_fingerprint",
        "workload_plan_fingerprint",
        "plan_similarity_cluster_id",
    ):
        if metadata.get(key) != expected_workload_fingerprint:
            return {}
    return {
        "plan_fingerprint_contract": EXACT_PLAN_FINGERPRINT_CONTRACT,
        "plan_fingerprint_coverage": copy.deepcopy(dict(coverage)),
        "plan_fingerprints": copy.deepcopy(normalized_fingerprints),
        "plan_fingerprint": expected_workload_fingerprint,
        "workload_plan_fingerprint": expected_workload_fingerprint,
        "plan_similarity_cluster_id": expected_workload_fingerprint,
    }


def _quantized_finite_mapping(
    raw: Any,
    *,
    quantum: float,
) -> Dict[str, int]:
    if not isinstance(raw, Mapping):
        return {}
    normalized: Dict[str, int] = {}
    for name, value in raw.items():
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(numeric):
            continue
        normalized[str(name)] = int(round(numeric / quantum))
    return normalized


def _validated_ratio_plan_fingerprint(metadata: Mapping[str, Any]) -> Optional[str]:
    """Validate the ratio-scale estimator's cost-independent plan fingerprint."""

    if (
        metadata.get("resource_estimation_mode") != "ratio_scale"
        or metadata.get("cost_parameter_free_features") is not True
        or metadata.get("qps_used_for_inner_scoring") is not False
    ):
        return None
    declared = metadata.get("workload_plan_fingerprint")
    if not isinstance(declared, str) or not re.fullmatch(
        r"ratio-plan-sha256:[0-9a-f]{64}", declared
    ):
        return None
    query_features = metadata.get("query_features")
    if not isinstance(query_features, list) or not query_features:
        current_features = metadata.get("current_features")
        if isinstance(current_features, Mapping):
            query_features = current_features.get("query_features")
    if not isinstance(query_features, list) or not query_features:
        return None
    fingerprints: List[Dict[str, Any]] = []
    for item in query_features:
        if not isinstance(item, Mapping):
            return None
        queryid = item.get("queryid")
        fingerprint = item.get("plan_fingerprint")
        if queryid is None or not isinstance(fingerprint, str) or not re.fullmatch(
            r"exact-structure-sha256:[0-9a-f]{64}", fingerprint
        ):
            return None
        fingerprints.append({"queryid": queryid, "fingerprint": fingerprint})
    expected = "ratio-plan-sha256:" + _canonical_sha256(fingerprints)
    return declared if declared == expected else None


def _three_level_fingerprint(
    run: _SuccessfulRun,
    capability: Mapping[str, Any],
) -> Dict[str, Any]:
    metadata = getattr(run.result, "final_estimation_metadata", None)
    metadata = metadata if isinstance(metadata, Mapping) else {}
    exact_plan = _validated_public_plan_fingerprints(metadata)
    plan_id = exact_plan.get("workload_plan_fingerprint") or (
        _validated_ratio_plan_fingerprint(metadata)
    )
    feature_ratio = _quantized_finite_mapping(
        metadata.get("feature_ratios"),
        quantum=0.005,
    )
    utilization: Dict[str, float] = {}
    for resource_name, resource_value in run.result.final_resource.items():
        try:
            cap = float(capability[resource_name])
            value = float(resource_value)
        except (KeyError, TypeError, ValueError):
            continue
        if cap > 0.0 and math.isfinite(cap) and math.isfinite(value):
            utilization[str(resource_name)] = value / cap
    resource_ratio = _quantized_finite_mapping(
        utilization,
        quantum=0.005,
    )
    validation_errors: List[str] = []
    if not plan_id:
        validation_errors.append("validated_plan_fingerprint_missing")
    if not feature_ratio:
        validation_errors.append("feature_ratio_missing")
    expected_resources = {
        str(name)
        for name, value in capability.items()
        if isinstance(value, (int, float))
        and math.isfinite(float(value))
        and float(value) > 0.0
    }
    if not resource_ratio or not expected_resources.issubset(resource_ratio):
        validation_errors.append("predicted_utilization_incomplete")
    resource_formula_audit: Dict[str, Dict[str, float]] = {}
    if metadata.get("resource_estimation_mode") == "ratio_scale":
        if metadata.get("qps_used_for_inner_scoring") is not False:
            validation_errors.append("qps_used_for_inner_scoring_not_false")
        baseline_real = metadata.get("baseline_real")
        effective_feature_ratio = metadata.get("feature_ratios")
        if not isinstance(baseline_real, Mapping) or not all(
            name in baseline_real
            and isinstance(baseline_real[name], (int, float))
            and math.isfinite(float(baseline_real[name]))
            for name in expected_resources
        ):
            validation_errors.append("baseline_real_incomplete")
        if not isinstance(effective_feature_ratio, Mapping) or not all(
            name in effective_feature_ratio
            and isinstance(effective_feature_ratio[name], (int, float))
            and math.isfinite(float(effective_feature_ratio[name]))
            for name in expected_resources
        ):
            validation_errors.append("effective_feature_ratio_incomplete")
        if (
            isinstance(baseline_real, Mapping)
            and isinstance(effective_feature_ratio, Mapping)
            and "baseline_real_incomplete" not in validation_errors
            and "effective_feature_ratio_incomplete" not in validation_errors
        ):
            for resource_name in sorted(expected_resources):
                observed = float(run.result.final_resource[resource_name])
                expected = (
                    float(baseline_real[resource_name])
                    * float(effective_feature_ratio[resource_name])
                )
                absolute_error = abs(observed - expected)
                allowed_error = max(
                    1e-12,
                    1e-12 * max(abs(observed), abs(expected)),
                )
                resource_formula_audit[resource_name] = {
                    "baseline_real": float(baseline_real[resource_name]),
                    "feature_ratio": float(
                        effective_feature_ratio[resource_name]
                    ),
                    "expected_resource": expected,
                    "observed_resource": observed,
                    "absolute_error": absolute_error,
                    "allowed_error": allowed_error,
                }
                if absolute_error > allowed_error:
                    validation_errors.append(
                        "predicted_resource_formula_mismatch:"
                        f"{resource_name}"
                    )
    payload = {
        "plan": plan_id,
        "feature_ratio_q005": feature_ratio,
        "resource_utilization_q005": resource_ratio,
    }
    return {
        "contract": "plan_feature_resource_three_level_v1",
        "id": (
            "three-level-sha256:" + _canonical_sha256(payload)
            if not validation_errors
            else None
        ),
        "valid": not validation_errors,
        "validation_errors": validation_errors,
        "plan_level": plan_id,
        "feature_ratio_level": feature_ratio,
        "predicted_resource_level": resource_ratio,
        "parameter_fallback_used": False,
        "resource_formula_audit": resource_formula_audit,
    }


def attach_three_level_fingerprint(
    ranking: Mapping[str, Any],
    capability: Mapping[str, Any],
) -> Dict[str, Any]:
    """Attach the canonical fingerprint to an externally evaluated ranking.

    Plan-basin escape candidates are evaluated after the ordinary subset
    optimizer has created its public rankings.  They therefore need the same
    fingerprint constructor applied explicitly before formal replay filtering.
    """

    enriched = copy.deepcopy(dict(ranking))

    class _RankingResult:
        pass

    result = _RankingResult()
    result.final_estimation_metadata = copy.deepcopy(
        enriched.get("final_estimation_metadata") or {}
    )
    result.final_resource = copy.deepcopy(enriched.get("final_resource") or {})
    run = _SuccessfulRun(
        canonical_order=int(enriched.get("canonical_subset_order") or 0),
        subset_id=str(enriched.get("subset_id") or "plan_basin_escape"),
        active_subset=tuple(enriched.get("active_subset") or ()),
        log_path=str(enriched.get("log_path") or ""),
        result=result,
    )
    fingerprint = _three_level_fingerprint(run, capability)
    if fingerprint.get("valid") is not True:
        raise RuntimeError(
            "plan-basin escape candidate lacks a valid three-level "
            f"fingerprint: {fingerprint.get('validation_errors')}"
        )
    enriched["three_level_fingerprint"] = fingerprint

    metadata = enriched.get("final_estimation_metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    feature_ratio = metadata.get("feature_ratios")
    predicted_resource = enriched.get("final_resource")
    baseline_params = metadata.get("baseline_params")
    optimized_params = enriched.get("optimized_params")
    if not isinstance(feature_ratio, Mapping):
        raise RuntimeError("plan-basin escape candidate lacks feature ratios")
    if not isinstance(predicted_resource, Mapping):
        raise RuntimeError("plan-basin escape candidate lacks predicted resources")
    if not isinstance(baseline_params, Mapping) or not isinstance(
        optimized_params, Mapping
    ):
        raise RuntimeError("plan-basin escape candidate lacks parameter audit inputs")

    def finite_float(value: Any) -> Optional[float]:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if math.isfinite(parsed) else None

    utilization: Dict[str, float] = {}
    for resource_name in ("cpu", "memory", "io"):
        predicted = finite_float(predicted_resource.get(resource_name))
        capacity = finite_float(capability.get(resource_name))
        if predicted is None or capacity is None or capacity <= 0.0:
            raise RuntimeError(
                "plan-basin escape candidate lacks finite resource/capability "
                f"for {resource_name}"
            )
        utilization[resource_name] = predicted / capacity

    delta_param: Dict[str, float] = {}
    direction_log_vector: Dict[str, float] = {}
    for raw_name, raw_before in baseline_params.items():
        name = str(raw_name)
        before = finite_float(raw_before)
        after = finite_float(optimized_params.get(name))
        if before is None or after is None:
            raise RuntimeError(
                f"plan-basin escape candidate lacks finite parameter {name}"
            )
        delta_param[name] = after - before
        if abs(after - before) <= 1e-15:
            direction_log_vector[name] = 0.0
        elif before > 0.0 and after > 0.0:
            direction_log_vector[name] = math.log(after / before)
        else:
            direction_log_vector[name] = (
                (after - before) / max(abs(before), 1.0)
            )
    eta = math.sqrt(
        sum(value * value for value in direction_log_vector.values())
    )

    enriched.update(
        {
            "feature_ratio": {
                str(name): float(value)
                for name, value in feature_ratio.items()
            },
            "predicted_resource": {
                str(name): float(value)
                for name, value in predicted_resource.items()
            },
            "utilization": utilization,
            "dScore/dU": {
                "available": False,
                "reason": "plan_basin_escape_non_gradient_candidate",
            },
            "T": {
                "available": False,
                "reason": "plan_basin_escape_non_gradient_candidate",
            },
            "confidence": {
                "value": 0.0,
                "classification": "uncertain_plan_basin_probe",
            },
            "g_j": direction_log_vector,
            "eta": eta,
            "eta_semantics": "normalized_log_linear_distance_from_incumbent",
            "delta_param": delta_param,
            "candidate_score": float(enriched["final_score"]),
        }
    )
    return enriched


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
    }
    trajectory_iteration = getattr(result, "trajectory_iteration", None)
    if trajectory_iteration is not None:
        ranking["trajectory_iteration"] = int(trajectory_iteration)
    trajectory_index = getattr(result, "trajectory_index", None)
    if trajectory_index is not None:
        ranking["trajectory_index"] = int(trajectory_index)
    for field_name in (
        "requested_relative_step_pct",
        "candidate_origin",
        "probe_kind",
        "used_for_gradient",
        "used_for_inner_incumbent_update",
        "eta",
        "eta_semantics",
        "eta_applied_to_candidate",
        "delta_param",
        "dScore/dU",
        "T",
        "confidence",
        "gradient_components",
        "g_j",
        "directional_scoring_contract",
        "boundary_corner_index",
        "boundary_corner_mask",
        "boundary_corner_sides",
        "parameter_noop",
        "initial_score_plateau",
        "incumbent_score_plateau",
        "boundary_plateau",
        "offline_plateau",
        "incumbent_update_eligible",
    ):
        if hasattr(result, field_name):
            ranking[field_name] = copy.deepcopy(getattr(result, field_name))
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
        ranking["final_estimation_metadata"] = copy.deepcopy(final_metadata)
        pruning_input = final_metadata.get("optimistic_replay_pruning_input")
        if isinstance(pruning_input, dict):
            ranking["optimistic_replay_pruning_input"] = copy.deepcopy(
                pruning_input
            )
        public_plan_fields = _validated_public_plan_fingerprints(final_metadata)
        if public_plan_fields:
            ranking.update(public_plan_fields)
        elif "plan_fingerprint_coverage" in final_metadata:
            ranking["plan_fingerprint_missing_policy"] = (
                "fail_open_unique_cluster"
            )
            ranking["plan_fingerprint_coverage"] = copy.deepcopy(
                final_metadata.get("plan_fingerprint_coverage")
            )

    # Formal Algorithm C Top-5 evidence consumes these names directly.  Keep
    # the original result fields for compatibility, but also publish the
    # explicit audit aliases so evidence readers never have to infer them from
    # nested estimator metadata.
    feature_ratio = (
        final_metadata.get("feature_ratios")
        if isinstance(final_metadata, Mapping)
        else {}
    )
    predicted_resource = copy.deepcopy(result.final_resource)
    utilization: Dict[str, float] = {}
    for resource_name, resource_value in predicted_resource.items():
        try:
            capacity_value = float((capability or {})[resource_name])
            predicted_value = float(resource_value)
        except (KeyError, TypeError, ValueError):
            continue
        if (
            capacity_value > 0.0
            and math.isfinite(capacity_value)
            and math.isfinite(predicted_value)
        ):
            utilization[str(resource_name)] = predicted_value / capacity_value
    ranking.update(
        {
            "feature_ratio": copy.deepcopy(dict(feature_ratio or {})),
            "predicted_resource": predicted_resource,
            "utilization": utilization,
            "candidate_score": float(result.final_score),
        }
    )
    three_level = _three_level_fingerprint(run, capability or {})
    ranking["three_level_fingerprint"] = copy.deepcopy(three_level)
    ranking["candidate_plan_signature"] = three_level.get("id")
    return ranking


def _expand_candidate_trajectory(
    runs: Sequence[_SuccessfulRun],
    capability: Mapping[str, Any],
) -> List[_SuccessfulRun]:
    """Materialize every evaluated gradient probe as a rankable candidate."""

    def stats_avg(stats: Any, name: str) -> Optional[float]:
        if not isinstance(stats, Mapping):
            return None
        value = stats.get(name)
        if not isinstance(value, Mapping) or value.get("avg") is None:
            return None
        try:
            parsed = float(value["avg"])
        except (TypeError, ValueError):
            return None
        return parsed if math.isfinite(parsed) else None

    expanded: List[_SuccessfulRun] = []
    for run in runs:
        trajectory = getattr(run.result, "candidate_trajectory", None)
        if trajectory is None:
            expanded.append(run)
            continue
        if not isinstance(trajectory, list):
            raise RuntimeError(
                f"candidate trajectory is empty or invalid: {run.subset_id}"
            )
        if not trajectory:
            if getattr(run.result, "candidate_trajectory_complete", False):
                raise RuntimeError(
                    f"candidate trajectory is empty or invalid: {run.subset_id}"
                )
            expanded.append(run)
            continue
        trajectory_indices = [
            int(point.get("trajectory_index", point.get("iteration", -1)))
            if isinstance(point, Mapping)
            else -1
            for point in trajectory
        ]
        if trajectory_indices != list(range(1, len(trajectory) + 1)):
            raise RuntimeError(
                "candidate trajectory_index must be unique and contiguous: "
                f"{run.subset_id}"
            )
        for point in trajectory:
            if not isinstance(point, Mapping):
                raise RuntimeError(
                    f"candidate trajectory item is invalid: {run.subset_id}"
                )
            try:
                iteration = int(point["iteration"])
                trajectory_index = int(
                    point.get("trajectory_index", iteration)
                )
                score = float(point["score"])
                params = copy.deepcopy(dict(point["params"]))
                resource = copy.deepcopy(dict(point["resource"]))
                resource_stats = copy.deepcopy(
                    dict(point.get("resource_stats") or {})
                )
                metadata = copy.deepcopy(dict(point["estimation_metadata"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError(
                    f"candidate trajectory item is incomplete: {run.subset_id}"
                ) from exc
            if (
                iteration <= 0
                or trajectory_index <= 0
                or not math.isfinite(score)
            ):
                raise RuntimeError(
                    f"candidate trajectory item is non-finite: {run.subset_id}"
                )
            result = copy.deepcopy(run.result)
            result.optimized_params = params
            result.final_resource = resource
            result.final_score = score
            result.score_improvement = score - float(result.initial_score)
            result.score_improvement_pct = (
                result.score_improvement / float(result.initial_score) * 100.0
                if float(result.initial_score) != 0.0
                else None
            )
            result.final_utilization = {
                str(name): float(value) / float(capability[name])
                for name, value in resource.items()
                if (
                    name in capability
                    and math.isfinite(float(capability[name]))
                    and float(capability[name]) > 0.0
                )
            }
            result.final_qps = stats_avg(resource_stats, "qps")
            result.final_query_count = stats_avg(
                resource_stats,
                "query_count",
            )
            result.final_query_time = stats_avg(
                resource_stats,
                "query_time",
            )
            result.qps_improvement = (
                result.final_qps - result.initial_qps
                if result.final_qps is not None
                and result.initial_qps is not None
                else None
            )
            result.qps_improvement_pct = (
                result.qps_improvement / result.initial_qps * 100.0
                if result.qps_improvement is not None
                and result.initial_qps not in (None, 0.0)
                else None
            )
            result.degradation_pct = (
                (
                    result.final_query_time / result.initial_query_time
                    - 1.0
                )
                * 100.0
                if result.final_query_time is not None
                and result.initial_query_time not in (None, 0.0)
                else None
            )
            result.degraded = (
                result.degradation_pct > 10.0
                if result.degradation_pct is not None
                else None
            )
            result.final_estimation_metadata = metadata
            result.candidate_trajectory = []
            setattr(result, "trajectory_iteration", iteration)
            setattr(result, "trajectory_index", trajectory_index)
            setattr(
                result,
                "requested_relative_step_pct",
                copy.deepcopy(point.get("requested_relative_step_pct")),
            )
            setattr(
                result,
                "candidate_origin",
                str(point.get("candidate_origin") or "gradient_probe"),
            )
            for field_name in (
                "probe_kind",
                "used_for_gradient",
                "used_for_inner_incumbent_update",
                "eta",
                "eta_semantics",
                "eta_applied_to_candidate",
                "delta_param",
                "dScore/dU",
                "T",
                "confidence",
                "gradient_components",
                "g_j",
                "directional_scoring_contract",
                "boundary_corner_index",
                "boundary_corner_mask",
                "boundary_corner_sides",
            ):
                if field_name in point:
                    setattr(
                        result,
                        field_name,
                        copy.deepcopy(point.get(field_name)),
                    )
            setattr(
                result,
                "parameter_noop",
                bool(point.get("parameter_noop")),
            )
            setattr(
                result,
                "initial_score_plateau",
                bool(point.get("initial_score_plateau")),
            )
            setattr(
                result,
                "incumbent_score_plateau",
                bool(point.get("incumbent_score_plateau")),
            )
            setattr(
                result,
                "boundary_plateau",
                bool(point.get("boundary_plateau")),
            )
            setattr(
                result,
                "offline_plateau",
                bool(
                    point.get(
                        "offline_plateau",
                        bool(point.get("parameter_noop"))
                        or bool(point.get("initial_score_plateau"))
                        or bool(point.get("incumbent_score_plateau"))
                        or bool(point.get("boundary_plateau")),
                    )
                ),
            )
            setattr(
                result,
                "incumbent_update_eligible",
                bool(
                    point.get(
                        "incumbent_update_eligible",
                        not (
                            bool(point.get("parameter_noop"))
                            or bool(point.get("initial_score_plateau"))
                            or bool(point.get("incumbent_score_plateau"))
                            or bool(point.get("boundary_plateau"))
                        ),
                    )
                ),
            )
            expanded.append(_SuccessfulRun(
                canonical_order=run.canonical_order,
                subset_id=(
                    f"{run.subset_id}__trajectory_{trajectory_index:04d}"
                ),
                active_subset=run.active_subset,
                log_path=run.log_path,
                result=result,
            ))
    return expanded


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
    full_subset_boundary_corner_scan_enabled = options.pop(
        "full_subset_boundary_corner_scan_enabled",
        False,
    )
    if not isinstance(full_subset_boundary_corner_scan_enabled, bool):
        raise ValueError(
            "full_subset_boundary_corner_scan_enabled "
        )

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

    def is_transient_connection_failure(audit: Mapping[str, Any]) -> bool:
        """Return whether a failed subset may be retried without changing semantics."""
        error_text = " ".join(
            str(audit.get(key) or "")
            for key in ("error_type", "error_message")
        ).lower()
        transient_markers = (
            "timeout expired",
            "connection timed out",
            "could not connect to server",
            "server closed the connection unexpectedly",
            "connection reset by peer",
        )
        return any(marker in error_text for marker in transient_markers)

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
        boundary_corner_scan_enabled = bool(
            full_subset_boundary_corner_scan_enabled
            and active_subset == canonical_params
        )
        _write_log_event(
            log_path,
            {
                "event": "experience_table_subset_start",
                "round_index": round_index,
                "subset_id": subset_id,
                "canonical_subset_order": canonical_order,
                "active_subset": list(active_subset),
                "p_real": copy.deepcopy(p_real),
                "parameter_boundary_corner_scan_enabled": (
                    boundary_corner_scan_enabled
                ),
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
                parameter_boundary_corner_scan_enabled=(
                    boundary_corner_scan_enabled
                ),
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
                    f": {non_subset_changed}"
                )

            duration_s = time.perf_counter() - started_at
            audit = {
                "round_index": round_index,
                "subset_id": subset_id,
                "canonical_subset_order": canonical_order,
                "active_subset": list(active_subset),
                "status": "success",
                "final_score": final_score,
                "optimized_params": copy.deepcopy(subset_result.optimized_params),
                "iterations": int(subset_result.iterations),
                "parameter_boundary_corner_scan_enabled": (
                    boundary_corner_scan_enabled
                ),
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
                "parameter_boundary_corner_scan_enabled": (
                    boundary_corner_scan_enabled
                ),
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

    futures: Dict[
        Future[Tuple[Optional[_SuccessfulRun], Dict[str, Any]]],
        int,
    ] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for canonical_order, subset in enumerate(subsets, start=1):
            future = executor.submit(run_subset, canonical_order, subset)
            futures[future] = canonical_order
        for future in as_completed(futures):
            canonical_order = futures[future]
            try:
                successful_run, audit = future.result()
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
                successful_run = None
            audit_by_order[canonical_order] = audit
            if successful_run is not None:
                successes.append(successful_run)

    # A subset run contains many plan probes.  The normal parallel pass can
    # briefly create nested connection bursts (subset workers x probe workers),
    # so a PostgreSQL connect timeout is a technical scheduling failure rather
    # than evidence about the candidate.  Retry only those failed subsets,
    # sequentially, while retaining fail-closed behavior for every other error.
    # Successful subsets are never recomputed and the canonical subset set is
    # unchanged.
    transient_retry_orders = [
        canonical_order
        for canonical_order in range(1, len(subsets) + 1)
        if audit_by_order[canonical_order].get("status") == "failed"
        and is_transient_connection_failure(audit_by_order[canonical_order])
    ]
    for canonical_order in transient_retry_orders:
        initial_audit = copy.deepcopy(audit_by_order[canonical_order])
        active_subset = subsets[canonical_order - 1]
        retry_log_path = str(initial_audit.get("log_path") or "")
        if retry_log_path:
            _write_log_event(
                retry_log_path,
                {
                    "event": "experience_table_subset_transient_retry_start",
                    "round_index": round_index,
                    "subset_id": initial_audit.get("subset_id"),
                    "canonical_subset_order": canonical_order,
                    "active_subset": list(active_subset),
                    "retry_mode": "sequential_failed_subset_only",
                    "initial_error_type": initial_audit.get("error_type"),
                    "initial_error_message": initial_audit.get("error_message"),
                },
            )
        successful_run, retry_audit = run_subset(canonical_order, active_subset)
        retry_audit["transient_retry"] = {
            "attempted": True,
            "attempt_count": 1,
            "mode": "sequential_failed_subset_only",
            "initial_error_type": initial_audit.get("error_type"),
            "initial_error_message": initial_audit.get("error_message"),
            "recovered": successful_run is not None,
        }
        audit_by_order[canonical_order] = retry_audit
        if successful_run is not None:
            successes.append(successful_run)
            _write_log_event(
                successful_run.log_path,
                {
                    "event": "experience_table_subset_transient_retry_recovered",
                    "round_index": round_index,
                    "subset_id": successful_run.subset_id,
                    "canonical_subset_order": canonical_order,
                    "active_subset": list(active_subset),
                    "retry_mode": "sequential_failed_subset_only",
                },
            )

    audit_records = [audit_by_order[index] for index in range(1, len(subsets) + 1)]
    if not successes:
        raise AllSubsetOptimizationError(
            f" {len(subsets)} ",
            copy.deepcopy(audit_records),
        )
    if len(successes) != len(subsets):
        raise IncompleteSubsetOptimizationError(
            (
                " fail-closed: "
                f"success={len(successes)}, failed={len(subsets) - len(successes)}, "
                f"expected={len(subsets)}"
            ),
            copy.deepcopy(audit_records),
            str(run_dir),
        )

    candidate_runs = _expand_candidate_trajectory(successes, capability)
    candidate_runs.sort(
        key=lambda run: _run_ranking_key(
            run,
            ranking_target_resource,
            capability,
        )
    )
    all_candidate_rankings = []
    for rank, run in enumerate(candidate_runs, start=1):
        item = _public_ranking(run, ranking_target_resource, capability)
        item["pre_dedup_rank"] = rank
        all_candidate_rankings.append(item)

    unique_runs: List[_SuccessfulRun] = []
    representative_by_signature: Dict[Tuple[Tuple[str, str], ...], _SuccessfulRun] = {}
    duplicate_ids: Dict[str, List[str]] = {}
    for run in candidate_runs:
        signature = _exact_parameter_signature(run.result.optimized_params)
        representative = representative_by_signature.get(signature)
        if representative is None:
            representative_by_signature[signature] = run
            unique_runs.append(run)
            duplicate_ids[run.subset_id] = []
        else:
            duplicate_ids[representative.subset_id].append(run.subset_id)
            audit_by_order[run.canonical_order]["deduplicated_into_subset_id"] = (
                representative.subset_id
            )

    fingerprint_validated_runs: List[_SuccessfulRun] = []
    behavior_unique_runs: List[_SuccessfulRun] = []
    runs_by_plan_fingerprint: Dict[str, List[_SuccessfulRun]] = {}
    representative_by_plan_fingerprint: Dict[str, _SuccessfulRun] = {}
    for run in unique_runs:
        fingerprint = _three_level_fingerprint(run, capability)
        fingerprint_id = fingerprint.get("id")
        plan_fingerprint = str(fingerprint.get("plan_level") or "").strip()
        if not fingerprint_id or not plan_fingerprint:
            audit_by_order[run.canonical_order][
                "three_level_fingerprint"
            ] = copy.deepcopy(fingerprint)
            audit_by_order[run.canonical_order]["fingerprint_rejected"] = True
            continue
        fingerprint_id = str(fingerprint_id)
        fingerprint_validated_runs.append(run)
        runs_by_plan_fingerprint.setdefault(plan_fingerprint, []).append(run)
        audit_by_order[run.canonical_order]["three_level_fingerprint"] = copy.deepcopy(
            fingerprint
        )
        representative = representative_by_plan_fingerprint.get(plan_fingerprint)
        if representative is None:
            representative_by_plan_fingerprint[plan_fingerprint] = run
            behavior_unique_runs.append(run)
        else:
            audit_by_order[run.canonical_order][
                "deduplicated_into_behavior_subset_id"
            ] = representative.subset_id
            audit_by_order[run.canonical_order]["formal_top5_eligible"] = False
            audit_by_order[run.canonical_order][
                "formal_top5_exclusion_reason"
            ] = "duplicate_validated_full_physical_plan_cluster"

    if not fingerprint_validated_runs:
        raise IncompleteSubsetOptimizationError(
            " validated full physical plan + feature ratio + "
            "predicted utilization ",
            copy.deepcopy(audit_records),
            str(run_dir),
        )

    candidate_rankings: List[Dict[str, Any]] = []
    for rank, run in enumerate(behavior_unique_runs, start=1):
        item = _public_ranking(run, ranking_target_resource, capability)
        item["rank"] = rank
        item["duplicate_subset_ids"] = duplicate_ids[run.subset_id]
        plan_fingerprint = str(
            (item.get("three_level_fingerprint") or {}).get("plan_level") or ""
        )
        item["same_fingerprint_distinct_parameter_subset_ids"] = [
            other.subset_id
            for other in runs_by_plan_fingerprint.get(plan_fingerprint, [])
            if other.subset_id != run.subset_id
        ]
        item["base_topk_dedupe_key"] = "validated_full_physical_plan_fingerprint"
        item["same_plan_different_behavior_retained"] = False
        item["same_behavior_different_parameters_retained"] = False
        item["behavior_cluster_required_for_formal_top5"] = True
        candidate_rankings.append(item)
        audit_by_order[run.canonical_order]["candidate_rank"] = rank
        audit_by_order[run.canonical_order]["formal_top5_eligible"] = True


    audit_records = [audit_by_order[index] for index in range(1, len(subsets) + 1)]
    best_run = behavior_unique_runs[0]
    summary = AllSubsetOptimizationResult(
        best_result=best_run.result,
        candidate_rankings=copy.deepcopy(candidate_rankings),
        all_candidate_rankings=copy.deepcopy(all_candidate_rankings),
        audit_records=copy.deepcopy(audit_records),
        subset_count=len(subsets),
        successful_subset_count=len(successes),
        failed_subset_count=len(subsets) - len(successes),
        unique_candidate_count=len(behavior_unique_runs),
        round_index=round_index,
        log_dir=str(run_dir),
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
