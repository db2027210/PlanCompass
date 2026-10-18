"""Soft plan-physics ranking for the formal four-scene Algorithm C search.

The 151 replay audit showed that operator-count transitions have contradictory
resource outcomes.  This module therefore uses the full estimated plan context
to adjust a candidate's soft score, but deliberately never authorizes pruning.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from typing import Any, Mapping

from plan_resource_estimator import CacheProfile, MySQLPlanPhysics


RESOURCES = ("cpu", "memory", "io")
DEFAULT_PHYSICS_WEIGHT = 0.65
DEFAULT_DIRECTION_LOG_STEP = 0.05


@dataclass(frozen=True)
class PlanWorkVector:
    cpu_work: float
    memory_resident_bytes: float
    memory_demand_bytes: float
    sequential_pages: float
    random_pages: float
    effective_io_bytes: float
    estimated_output_rows: float


def _sha(value: Any) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _physical_plan(value: Any) -> Any:
    """Remove planner preference costs while retaining physical plan context."""
    if isinstance(value, list):
        return [_physical_plan(item) for item in value]
    if not isinstance(value, Mapping):
        return value
    result: dict[str, Any] = {}
    for key, item in value.items():
        if key == "cost_info":
            if isinstance(item, Mapping) and "data_read_per_join" in item:
                # This is MySQL's cardinality × row-width estimate and is used
                # as physical width, unlike read/eval/prefix/query costs.
                result[key] = {
                    "data_read_per_join": item["data_read_per_join"],
                }
            continue
        if key in {"query_cost", "read_cost", "eval_cost", "prefix_cost"}:
            continue
        result[str(key)] = _physical_plan(item)
    return result


def summarize_plan_work(
    plans: Mapping[str, Any],
    relation_stats: Mapping[str, Mapping[str, Any]],
    executor_parameters: Mapping[str, Any],
    *,
    concurrency: int,
    cache_profile: CacheProfile = CacheProfile(),
    page_size: int = 16 * 1024,
) -> tuple[PlanWorkVector, dict[str, dict[str, float]]]:
    if not plans:
        raise ValueError("at least one representative plan is required")
    physics = MySQLPlanPhysics(
        relation_stats, executor_parameters=executor_parameters, page_size=page_size,
    )
    per_query: dict[str, dict[str, float]] = {}
    cpu = 0.0
    sequential = 0.0
    random = 0.0
    resident_peak = 0.0
    demand_peak = 0.0
    output_rows = 0.0
    for query_id, plan in plans.items():
        work = physics.work(plan)
        per_query[str(query_id)] = {
            "cpu_work": work.cpu,
            "memory_resident_bytes": work.memory_bytes,
            "memory_demand_bytes": work.memory_demand_bytes,
            "sequential_pages": work.io.sequential_pages,
            "random_pages": work.io.random_pages,
            "estimated_output_rows": work.output_rows,
        }
        cpu += work.cpu
        sequential += work.io.sequential_pages
        random += work.io.random_pages
        resident_peak = max(resident_peak, work.memory_bytes)
        demand_peak = max(demand_peak, work.memory_demand_bytes)
        output_rows += work.output_rows
    sessions = max(1, int(concurrency))
    effective_io = (
        sequential * (1.0 - cache_profile.sequential_hit_rate)
        + random * (1.0 - cache_profile.random_hit_rate)
    ) * page_size
    return PlanWorkVector(
        cpu_work=cpu,
        memory_resident_bytes=resident_peak * sessions,
        memory_demand_bytes=demand_peak * sessions,
        sequential_pages=sequential,
        random_pages=random,
        effective_io_bytes=effective_io,
        estimated_output_rows=output_rows,
    ), per_query


def _ratio(candidate: float, baseline: float) -> float:
    if baseline <= 1e-12:
        return 1.0 if candidate <= 1e-12 else math.inf
    value = candidate / baseline
    return value if math.isfinite(value) and value > 0.0 else 1.0


def _direction_ratio(raw_ratio: float, log_step: float) -> float:
    if raw_ratio > 1.0 + 1e-12:
        return math.exp(log_step)
    if raw_ratio < 1.0 - 1e-12:
        return math.exp(-log_step)
    return 1.0


def enhance_resource_prediction(
    *,
    incumbent_resources: Mapping[str, float],
    experience_prediction: Mapping[str, float],
    baseline_plans: Mapping[str, Any],
    candidate_plans: Mapping[str, Any],
    relation_stats: Mapping[str, Mapping[str, Any]],
    executor_parameters: Mapping[str, Any],
    concurrency: int,
    cache_profile: CacheProfile = CacheProfile(),
    physics_weight: float = DEFAULT_PHYSICS_WEIGHT,
    direction_log_step: float = DEFAULT_DIRECTION_LOG_STEP,
) -> dict[str, Any]:
    """Blend experience-table priors with guarded full-plan physical work.

    Magnitudes from an uncalibrated plan transition are intentionally reduced
    to a direction-only step.  This encodes the contradictory replay evidence
    and prevents cardinality errors from dominating candidate ordering.
    """
    if not 0.0 <= physics_weight <= 1.0:
        raise ValueError("physics_weight must be within [0, 1]")
    if not 0.0 < direction_log_step <= 0.20:
        raise ValueError("direction_log_step must be within (0, 0.20]")
    for resource in RESOURCES:
        if resource not in incumbent_resources or resource not in experience_prediction:
            raise ValueError(f"missing resource: {resource}")
    baseline, baseline_queries = summarize_plan_work(
        baseline_plans, relation_stats, executor_parameters,
        concurrency=concurrency, cache_profile=cache_profile,
    )
    candidate, candidate_queries = summarize_plan_work(
        candidate_plans, relation_stats, executor_parameters,
        concurrency=concurrency, cache_profile=cache_profile,
    )
    raw_ratios = {
        "cpu": _ratio(candidate.cpu_work, baseline.cpu_work),
        "memory": _ratio(
            candidate.memory_demand_bytes, baseline.memory_demand_bytes,
        ),
        "io": _ratio(candidate.effective_io_bytes, baseline.effective_io_bytes),
    }
    guarded_ratios = {
        name: _direction_ratio(value, direction_log_step)
        for name, value in raw_ratios.items()
    }
    physics_prediction = {
        name: max(1e-9, float(incumbent_resources[name]) * guarded_ratios[name])
        for name in RESOURCES
    }
    prior_weight = 1.0 - physics_weight
    blended = {
        name: max(
            1e-9,
            physics_weight * physics_prediction[name]
            + prior_weight * float(experience_prediction[name]),
        )
        for name in RESOURCES
    }
    baseline_raw_sha = _sha(baseline_plans)
    candidate_raw_sha = _sha(candidate_plans)
    baseline_sha = _sha(_physical_plan(baseline_plans))
    candidate_sha = _sha(_physical_plan(candidate_plans))
    plan_changed = baseline_sha != candidate_sha
    extreme_ratio = any(value > 4.0 or value < 0.25 for value in raw_ratios.values())
    baseline_spill = baseline.memory_demand_bytes > baseline.memory_resident_bytes
    candidate_spill = candidate.memory_demand_bytes > candidate.memory_resident_bytes
    spill_transition = baseline_spill != candidate_spill
    confidence = 0.75 if not plan_changed else 0.55
    if extreme_ratio:
        confidence -= 0.20
    if spill_transition:
        confidence -= 0.15
    confidence = min(0.90, max(0.20, confidence))
    return {
        "predicted_resources": blended,
        "confidence": confidence,
        "hard_prune_allowed": False,
        "operator_count_rule_role": "soft_prior_only",
        "model": "mysql_algorithm_c_contextual_plan_physics_soft_v2",
        "plan_changed": plan_changed,
        "baseline_plan_sha256": baseline_sha,
        "candidate_plan_sha256": candidate_sha,
        "baseline_raw_explain_sha256": baseline_raw_sha,
        "candidate_raw_explain_sha256": candidate_raw_sha,
        "planner_cost_fields_excluded_from_physical_identity": True,
        "experience_prediction": {
            name: float(experience_prediction[name]) for name in RESOURCES
        },
        "physics_prediction": physics_prediction,
        "raw_plan_work_ratios": raw_ratios,
        "guarded_direction_ratios": guarded_ratios,
        "baseline_work": asdict(baseline),
        "candidate_work": asdict(candidate),
        "baseline_query_work": baseline_queries,
        "candidate_query_work": candidate_queries,
        "risk_flags": {
            "extreme_estimated_work_ratio": extreme_ratio,
            "estimated_spill_state_transition": spill_transition,
            "candidate_estimated_spill": candidate_spill,
            "cache_profile_is_frozen_prior_not_runtime_observation": True,
            "actual_rows_available_before_replay": False,
            "actual_loops_available_before_replay": False,
            "actual_workers_available_before_replay": False,
            "actual_operator_time_available_before_replay": False,
        },
        "features_used": {
            "complete_plan_tree": True,
            "estimated_cardinality": True,
            "estimated_row_width": True,
            "estimated_nested_loop_multiplier": True,
            "operator_substitution_context": True,
            "sort_hash_temp_memory_and_spill": True,
            "sequential_and_random_pages": True,
            "frozen_cache_profile": {
                "sequential_hit_rate": cache_profile.sequential_hit_rate,
                "random_hit_rate": cache_profile.random_hit_rate,
            },
            "workload_concurrency": max(1, int(concurrency)),
        },
    }


def confidence_adjusted_score(raw_score: float, confidence: float) -> float:
    if not 0.0 < confidence <= 1.0:
        raise ValueError("confidence must be within (0, 1]")
    value = float(raw_score)
    return value * confidence if value >= 0.0 else value / confidence
