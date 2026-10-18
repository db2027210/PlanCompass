"""Adaptive, baseline-independent outer search control for Algorithm C."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Literal


AdaptiveAction = Literal[
    "initial_global_coverage",
    "gradient_search",
    "gradient_step_expansion",
    "first_miss_expansion",
    "shrink_incumbent",
    "frontier_diversification",
    "frontier_numeric_refinement",
    "outer_near_gate_confirmation",
    "retained_region_revisit",
    "uncertainty_expansion",
    "finish_search",
    "operational_safety_stop_invalid",
]


@dataclass(frozen=True)
class AdaptiveSearchPolicy:
    """Scene-agnostic thresholds expressed in normalized C-internal units."""

    worker_count: int = 2
    minimum_initial_coverage_waves: int = 2
    convergence_patience_waves: int = 3
    minimum_normalized_resource_gain: float = 0.01
    convergence_uncertainty: float = 0.05
    expansion_uncertainty: float = 0.15
    duplicate_fraction_for_convergence: float = 0.80
    maximum_batch_size: int = 5
    maximum_consecutive_empty_admissions: int = 3
    minimum_numeric_expansion_windows: int = 6
    maximum_support_expansion_distance_ratio: float = 2.5
    outer_confirmation_min_noise_fraction: float = 0.30
    maximum_outer_confirmations_without_finalist: int = 2
    outer_confirmation_repetitions_per_worker: int = 2
    champion_stability_patience_waves: int = 3
    rejected_batches_before_expansion: int = 1
    rejected_batches_before_shrink: int = 2

    def __post_init__(self) -> None:
        if self.worker_count < 1:
            raise ValueError("worker_count must be positive")
        if (self.rejected_batches_before_expansion, self.rejected_batches_before_shrink) != (1, 2):
            raise ValueError("expand after the first miss and shrink after the second valid miss")
        if self.minimum_initial_coverage_waves < 1 or self.convergence_patience_waves < 1:
            raise ValueError("wave counts must be positive")
        if self.maximum_batch_size < self.worker_count:
            raise ValueError("maximum_batch_size must fit one worker wave")
        if self.maximum_consecutive_empty_admissions < 1:
            raise ValueError("empty-admission guard must be positive")
        if self.minimum_numeric_expansion_windows < 1:
            raise ValueError("numeric expansion window count must be positive")
        if not (
            math.isfinite(self.outer_confirmation_min_noise_fraction)
            and 0.0 < self.outer_confirmation_min_noise_fraction <= 1.0
        ):
            raise ValueError("outer confirmation noise fraction must be in (0, 1]")
        if self.maximum_outer_confirmations_without_finalist < 1:
            raise ValueError("outer confirmation limit must be positive")
        if self.outer_confirmation_repetitions_per_worker < 2:
            raise ValueError("outer confirmation requires at least two repetitions per worker")
        if self.champion_stability_patience_waves < 1:
            raise ValueError("champion stability patience must be positive")
        confirmation_replays = (
            self.worker_count * self.outer_confirmation_repetitions_per_worker - 1
        )
        if confirmation_replays > self.maximum_batch_size:
            raise ValueError("outer confirmation batch exceeds maximum_batch_size")
        if (
            not math.isfinite(self.maximum_support_expansion_distance_ratio)
            or self.maximum_support_expansion_distance_ratio < 1.0
        ):
            raise ValueError("support expansion distance ratio must be finite and at least one")
        fractions = (
            self.minimum_normalized_resource_gain,
            self.convergence_uncertainty,
            self.expansion_uncertainty,
            self.duplicate_fraction_for_convergence,
        )
        if not all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in fractions):
            raise ValueError("normalized thresholds must be in [0, 1]")
        if self.convergence_uncertainty >= self.expansion_uncertainty:
            raise ValueError("convergence uncertainty must be below expansion uncertainty")


@dataclass(frozen=True)
class AdaptiveSearchEvidence:
    """Evidence available after a complete physical worker wave."""

    completed_waves: int
    attempted_search_replays: int
    valid_search_replays: int
    active_wall_s: float
    consecutive_no_progress_waves: int
    novel_plan_fingerprints: int
    duplicate_plan_fraction: float
    normalized_resource_gain: float
    maximum_model_uncertainty: float
    pending_region_count: int
    best_pending_region_optimistic_gain: float
    outer_qps_accepted: bool
    technical_failure_count: int = 0
    consecutive_empty_admissions: int = 0
    latest_valid_search_replays: int = 0
    has_feasible_finalist: bool = False
    pending_outer_confirmation_count: int = 0
    completed_outer_confirmation_count: int = 0
    consecutive_outer_non_accept_waves: int = 0
    consecutive_rejected_batches: int = 0
    recovery_stage: int = 0
    stage_rejected_batches: int = 0
    consecutive_no_progress_batches: int = 0
    consecutive_empty_numeric_windows: int = 0
    pending_numeric_candidate_count: int = 0
    normalized_interval_width: float = 1.0
    normalized_interval_threshold: float = 0.01

    def __post_init__(self) -> None:
        integer_values = (
            self.completed_waves,
            self.attempted_search_replays,
            self.valid_search_replays,
            self.consecutive_no_progress_waves,
            self.novel_plan_fingerprints,
            self.pending_region_count,
            self.technical_failure_count,
            self.consecutive_empty_admissions,
            self.latest_valid_search_replays,
            self.pending_outer_confirmation_count,
            self.completed_outer_confirmation_count,
            self.consecutive_outer_non_accept_waves,
            self.consecutive_rejected_batches,
            self.recovery_stage,
            self.stage_rejected_batches,
            self.consecutive_no_progress_batches,
            self.consecutive_empty_numeric_windows,
            self.pending_numeric_candidate_count,
        )
        if any(not isinstance(value, int) or value < 0 for value in integer_values):
            raise ValueError("adaptive counters must be non-negative integers")
        numeric_values = (
            self.active_wall_s,
            self.duplicate_plan_fraction,
            self.normalized_resource_gain,
            self.maximum_model_uncertainty,
            self.best_pending_region_optimistic_gain,
            self.normalized_interval_width,
            self.normalized_interval_threshold,
        )
        if not all(math.isfinite(value) and value >= 0.0 for value in numeric_values):
            raise ValueError("adaptive evidence must be finite and non-negative")
        if self.duplicate_plan_fraction > 1.0:
            raise ValueError("duplicate_plan_fraction must be in [0, 1]")
        if not 0.0 < self.normalized_interval_threshold <= 1.0:
            raise ValueError("interval threshold must be in (0, 1]")


@dataclass(frozen=True)
class AdaptiveSearchDecision:
    action: AdaptiveAction
    requested_candidates: int
    converged: bool
    valid_for_quality_or_efficiency_claim: bool
    reason: str
    qps_used_for_inner_scoring: bool = False
    qps_used_for_outer_interval_control: bool = True
    baseline_runtime_or_candidate_inputs_used: bool = False


def adaptive_search_decision(
    policy: AdaptiveSearchPolicy,
    evidence: AdaptiveSearchEvidence,
) -> AdaptiveSearchDecision:
    """Expand after one valid miss, bisect after two, stop at actual precision.

    Plan diversity selects probes, never postpones a due bisection. Replay
    counts and elapsed time are accounting only. Empty pools advance the
    finite candidate-generator ladder without fabricating physical misses.
    """
    def choose(action, reason, count=None, converged=False, valid=True):
        return AdaptiveSearchDecision(
            action=action,
            requested_candidates=(policy.maximum_batch_size if count is None else count),
            converged=converged,
            valid_for_quality_or_efficiency_claim=valid,
            reason=reason,
        )

    if evidence.technical_failure_count:
        return choose("operational_safety_stop_invalid",
                      "technical_search_failure_requires_repair", 0, valid=False)
    if evidence.normalized_interval_width <= evidence.normalized_interval_threshold:
        return choose("finish_search", "actual_interval_precision_reached", 0, converged=True)
    if evidence.consecutive_rejected_batches >= policy.rejected_batches_before_shrink:
        return choose("shrink_incumbent", "two_valid_misses_bisect_current_incumbent", 0)
    if evidence.consecutive_empty_numeric_windows >= policy.minimum_numeric_expansion_windows:
        return choose("operational_safety_stop_invalid",
                      "numeric_windows_exhausted_without_physical_evidence", 0, valid=False)
    if evidence.consecutive_empty_admissions:
        if evidence.recovery_stage == 0:
            return choose("first_miss_expansion", "empty_gradient_pool_expand_candidate_generators")
        return choose("frontier_numeric_refinement", "advance_unseen_numeric_window_after_empty_pool")
    if evidence.consecutive_rejected_batches >= policy.rejected_batches_before_expansion:
        return choose("first_miss_expansion", "first_valid_miss_expand_plan_and_region_diversity")
    return choose("gradient_search", "ordinary_resource_gradient_search")


def adaptive_policy_audit(policy: AdaptiveSearchPolicy) -> dict:
    return {
        "contract": "algorithm_c_interval_control_v7",
        "policy": asdict(policy),
        "normal_stop_uses_fixed_replay_cap": False,
        "normal_stop_uses_fixed_wall_cap": False,
        "fixed_operational_replay_or_wall_guard_enabled": False,
        "interval_shrink_progress_checked": True,
        "physical_duplicate_evidence_required_for_frontier_exhaustion": False,
        "empty_admission_is_not_convergence_evidence": True,
        "empty_admission_recovery_changes_candidate_generator": True,
        "numeric_refinement_requires_prior_distinct_plan_coverage": True,
        "numeric_refinement_uses_incumbent_centered_progressive_low_discrepancy_windows": True,
        "numeric_refinement_exhausts_configured_radius_ladder_before_empty_stop": True,
        "same_plan_new_parameter_identity_is_not_plan_level_hard_pruned": True,
        "outer_feasibility_boundary_refinement_enabled": True,
        "measured_outer_bracket_attempted_before_due_shrink": True,
        "maximum_outer_bracket_attempts_per_interval": 1,
        "outer_bracket_accepted_safe_endpoints_retained": True,
        "outer_boundary_endpoints_use_c_current_run_evidence_only": True,
        "outer_boundary_pairs_resource_feasible_and_positive_qps_infeasible_endpoints": True,
        "feasible_champion_stops_after_outer_stability": False,
        "rejection_counter_unit": "complete_technically_valid_candidate_batch",
        "empty_or_technical_batch_counts_as_rejection": False,
        "gradient_boundary_recovery_before_coverage": True,
        "pending_regions_or_numeric_candidates_veto_convergence": False,
        "normal_stop_requires_completed_recovery_and_physical_stagnation": False,
        "normal_stop_uses_actual_maximum_normalized_interval_width": True,
        "first_valid_miss_expands_plan_and_region_diversity": True,
        "two_valid_misses_bisect_current_incumbent": True,
        "legacy_wave_patience_fields_used_for_stopping": False,
        "champion_stability_uses_external_baseline_time": False,
        "feasible_finalist_required_for_convergence": False,
        "baseline_retained_if_no_improvement": True,
        "noise_confirmation_enabled": False,
        "near_gate_confirmation_uses_c_current_run_outer_evidence_only": True,
        "near_gate_confirmation_replays_count_as_formal_search": True,
        "static_model_uncertainty_can_veto_physical_frontier_exhaustion": False,
        "qps_used_for_inner_scoring": False,
        "qps_used_for_outer_interval_control": True,
        "external_baseline_runtime_inputs": [],
        "external_baseline_candidate_inputs": [],
        "scene_specific_branching": False,
    }
