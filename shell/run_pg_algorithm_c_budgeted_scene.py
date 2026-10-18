#!/usr/bin/env python3
"""Run a budgeted C pilot with a feasible archive and revisitable regions.

This entry point deliberately uses a separate trial contract. It preserves the
existing resource estimator, baseline, real replay and hard semantic gates.
Budget exhaustion is not reported as convergence or a performance victory.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from algorithm_c_budget_policy import (  # noqa: E402
    ACTIVE_TIME_CONTRACT, CONTRACT, OUTER_CONFIRMATION_CONTRACT, Budget,
    actual_widths, admit_feasible_evidence, balanced_outer_qps_confirmation,
    best_feasible, box_identity, complement_boxes, evidence_identity,
    resume_active_time_accounting,
)
from algorithm_c_adaptive_search import (  # noqa: E402
    AdaptiveSearchEvidence,
    AdaptiveSearchPolicy,
    adaptive_policy_audit,
    adaptive_search_decision,
)
from algorithm_c_execution_role import resolve_execution_role  # noqa: E402
from shell import run_pg_algorithm_c_scene as base  # noqa: E402


OUTER_BOUNDARY_REFINEMENT_CONTRACT = (
    "algorithm_c_outer_feasibility_boundary_refinement_v2"
)
PROGRESSIVE_NUMERIC_REFINEMENT_CONTRACT = (
    "algorithm_c_progressive_centered_low_discrepancy_numeric_refinement_v2"
)
INNER_RESOURCE_ANCHOR_CONTRACT = (
    "algorithm_c_historical_baseline_resource_anchor_v1"
)

PROGRESSIVE_NUMERIC_REFINEMENT_RADII = (
    0.05,
    0.10,
    0.20,
    0.40,
    0.70,
    1.00,
)
RESOURCE_MODEL_C_ONLY = "c_only"
RESOURCE_MODEL_PAPER_ABC = "paper_abc"


def external_baseline_information_paths(value: Any, path: str = "$") -> List[str]:
    """Locate explicit Algorithm A/B inputs before formal C loads any model."""
    tokens = ("algorithm_a", "algorithm_b", "pg_algorithm_a_", "pg_algorithm_b_")
    violations: List[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if any(token in str(key).lower() for token in tokens):
                violations.append(child_path)
            violations.extend(external_baseline_information_paths(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            violations.extend(external_baseline_information_paths(child, f"{path}[{index}]"))
    elif isinstance(value, str) and any(token in value.lower() for token in tokens):
        violations.append(path)
    return violations


def validate_paper_abc_runtime_model(model: Mapping[str, Any]) -> None:
    """Accept ABC resource training only behind the paper model's C holdout firewall."""

    expected_sources = {"algorithm_a", "algorithm_b", "algorithm_c"}
    training_boundary = model.get("training_boundary")
    if not isinstance(training_boundary, Mapping):
        raise RuntimeError("paper ABC model has no training boundary")
    if set(training_boundary.get("allowed_training_source_algorithms") or []) != (
        expected_sources
    ):
        raise RuntimeError("paper ABC model has unexpected training sources")
    forbidden_training_flags = (
        "accept_reject_labels_used_for_resource_model",
        "historical_parameter_identity_used_as_model_feature",
        "historical_plan_fingerprint_used_as_model_feature",
        "qps_present_in_resource_training_rows",
        "qps_used_as_model_feature_or_target",
        "source_algorithm_used_as_model_feature",
    )
    if any(training_boundary.get(field) is not False for field in forbidden_training_flags):
        raise RuntimeError("paper ABC model violates the resource-only training boundary")
    if model.get("qps_used_for_inner_scoring") is not False:
        raise RuntimeError("paper ABC model uses QPS for inner scoring")
    holdout = model.get("holdout")
    if not isinstance(holdout, Mapping):
        raise RuntimeError("paper ABC model has no holdout audit")
    if holdout.get("holdout_source_algorithm") != "algorithm_c":
        raise RuntimeError("paper ABC model holdout is not Algorithm C")
    if holdout.get("independent_from_fit") is not True:
        raise RuntimeError("paper ABC model holdout is not independent from fit")
    if holdout.get("qps_used_for_selection_prediction") is not False:
        raise RuntimeError("paper ABC model holdout selection uses QPS")
    firewall = model.get("runtime_candidate_prior_firewall")
    if not isinstance(firewall, Mapping):
        raise RuntimeError("paper ABC model has no runtime candidate firewall")
    forbidden_runtime_flags = (
        "qps_used_as_model_feature_or_target",
        "qps_used_for_candidate_ranking",
        "qps_used_for_candidate_score",
        "qps_used_for_gradient",
        "source_algorithm_used_as_model_feature",
        "training_source_accept_reject_used_as_runtime_candidates",
        "training_source_parameters_used_as_runtime_candidates",
        "training_source_plan_fingerprints_used_as_runtime_candidates",
        "training_source_rankings_used_as_runtime_candidates",
    )
    if any(firewall.get(field) is not False for field in forbidden_runtime_flags):
        raise RuntimeError("paper ABC model violates the runtime candidate firewall")


def paper_abc_runtime_config_violations(config: Mapping[str, Any]) -> List[str]:
    """Ignore only audited offline ABC declarations when scanning runtime inputs."""

    sanitized = copy.deepcopy(config)
    algorithm_cfg = sanitized.get("algorithm_c")
    if not isinstance(algorithm_cfg, dict):
        return ["$.algorithm_c"]
    audit = algorithm_cfg.pop("offline_resource_training_audit", None)
    if not isinstance(audit, Mapping):
        return ["$.algorithm_c.offline_resource_training_audit"]
    if audit.get("contract") != "algorithm_c_abc_resource_training_c_holdout_audit_v1":
        return ["$.algorithm_c.offline_resource_training_audit.contract"]
    if set(audit.get("training_sources") or []) != {
        "algorithm_a", "algorithm_b", "algorithm_c"
    }:
        return ["$.algorithm_c.offline_resource_training_audit.training_sources"]
    if audit.get("qps_used") is not False:
        return ["$.algorithm_c.offline_resource_training_audit.qps_used"]
    workload = algorithm_cfg.get("workload_contract")
    if isinstance(workload, dict):
        workload.pop("training_runtime_boundary", None)
    return external_baseline_information_paths(sanitized)


def interpolate_parameter_vectors(
    left: Mapping[str, float],
    right: Mapping[str, float],
    fraction: float,
) -> Dict[str, float]:
    """Interpolate PostgreSQL costs in each parameter's native search space."""
    if not math.isfinite(fraction) or not 0.0 < fraction < 1.0:
        raise ValueError("interpolation fraction must be in (0, 1)")
    result: Dict[str, float] = {}
    for name in base.ACTIVE_PARAMS:
        before = float(left[name])
        after = float(right[name])
        if base.resolve_parameter_transform(name) == "log":
            if before <= 0.0 or after <= 0.0:
                raise ValueError(f"log-scale parameter must be positive: {name}")
            value = math.exp(
                math.log(before)
                + fraction * (math.log(after) - math.log(before))
            )
        else:
            value = before + fraction * (after - before)
        result[name] = float(value)
    return result


def normalized_parameter_distance(
    left: Mapping[str, float],
    right: Mapping[str, float],
    ranges: Mapping[str, base.ParameterRange],
) -> float:
    """Return RMS distance after applying the configured parameter transforms."""
    squared: List[float] = []
    for name in base.ACTIVE_PARAMS:
        limits = ranges[name]
        low = float(limits.min_value)
        high = float(limits.max_value)
        before = float(left[name])
        after = float(right[name])
        if base.resolve_parameter_transform(name) == "log":
            low, high = math.log(low), math.log(high)
            before, after = math.log(before), math.log(after)
        width = max(high - low, 1e-12)
        squared.append(((after - before) / width) ** 2)
    return math.sqrt(sum(squared) / max(len(squared), 1))


def radical_inverse(index: int, base_value: int) -> float:
    """Deterministic radical inverse used for progressive low-discrepancy probes."""
    if index < 1 or base_value < 2:
        raise ValueError("radical inverse requires positive index and base >= 2")
    value = 0.0
    factor = 1.0 / base_value
    remaining = index
    while remaining:
        value += (remaining % base_value) * factor
        remaining //= base_value
        factor /= base_value
    return value


class BudgetedLaneController(base.LaneController):
    def __init__(self, cfg_path: Path, output_dir: Path, target: str) -> None:
        initialization_started = time.time()
        raw_config = json.loads(cfg_path.read_text(encoding="utf-8"))
        raw_algorithm_cfg = raw_config.get("algorithm_c") or {}
        raw_policy = raw_algorithm_cfg.get("budgeted_trial") or {}
        raw_role = resolve_execution_role(raw_policy)
        if raw_role.interrupted_resume_allowed is False:
            runtime_model_policy = raw_algorithm_cfg.get(
                "runtime_resource_model_policy"
            ) or {}
            resource_model_mode = runtime_model_policy.get(
                "resource_model_mode", RESOURCE_MODEL_C_ONLY
            )
            if resource_model_mode == RESOURCE_MODEL_PAPER_ABC:
                violations = paper_abc_runtime_config_violations(raw_config)
            else:
                violations = external_baseline_information_paths(raw_config)
            if violations:
                raise RuntimeError(
                    "formal Algorithm C external-baseline input firewall failed: "
                    f"{violations[:20]}"
                )
            if "budget" in raw_policy or not isinstance(raw_policy.get("adaptive_search"), Mapping):
                raise RuntimeError("formal Algorithm C requires adaptive search without a fixed budget")
            model_path = Path(str(raw_algorithm_cfg.get("experience_table_path") or ""))
            model = json.loads(model_path.read_text(encoding="utf-8"))
            if resource_model_mode == RESOURCE_MODEL_PAPER_ABC:
                validate_paper_abc_runtime_model(model)
            else:
                model_violations = external_baseline_information_paths(model)
                if model_violations:
                    raise RuntimeError(
                        "formal Algorithm C model external-baseline firewall failed: "
                        f"{model_violations[:20]}"
                    )
                fit = model.get("fit") if isinstance(model.get("fit"), Mapping) else {}
                source_audit = (
                    fit.get("source_audit")
                    if isinstance(fit.get("source_audit"), Mapping)
                    else {}
                )
                source_counts = source_audit.get("source_counts")
                if not isinstance(source_counts, Mapping) or not source_counts or any(
                    "algorithm_c" not in str(source).lower() for source in source_counts
                ):
                    raise RuntimeError(
                        "formal Algorithm C requires auditable C-only resource-model training"
                    )
        prior_state_path = output_dir / "algorithm_c_state.json"
        prior_log_path = output_dir / "controller.log"
        prior_state: Dict[str, Any] = {}
        prior_activity_epoch_s = initialization_started
        if prior_state_path.is_file():
            prior_state = json.loads(prior_state_path.read_text(encoding="utf-8"))
            timestamps = [prior_state_path.stat().st_mtime]
            if prior_log_path.is_file():
                timestamps.append(prior_log_path.stat().st_mtime)
            prior_activity_epoch_s = max(timestamps)
        prior_trial = prior_state.get("budgeted_trial")
        super().__init__(cfg_path, output_dir, target)
        policy = self.algorithm_cfg.get("budgeted_trial") or {}
        if policy.get("contract") != CONTRACT:
            raise ValueError("explicit budgeted trial contract required")
        self.policy = policy
        anchor = policy.get("inner_resource_anchor")
        self.inner_resource_anchor: Optional[Dict[str, Any]] = None
        if isinstance(anchor, Mapping) and anchor.get("enabled") is True:
            if anchor.get("contract") != INNER_RESOURCE_ANCHOR_CONTRACT:
                raise ValueError("unexpected inner resource anchor contract")
            if anchor.get("qps_used") is not False:
                raise ValueError("inner resource anchor must be QPS-blind")
            if anchor.get("physical_validity_uses_actual_resources") is not True:
                raise ValueError(
                    "inner resource anchor cannot replace physical validation resources"
                )
            utilization_pct = anchor.get("utilization_pct")
            if not isinstance(utilization_pct, Mapping):
                raise ValueError("inner resource anchor utilization is required")
            normalized_utilization: Dict[str, float] = {}
            reference_resources: Dict[str, float] = {}
            for resource in ("cpu", "memory", "io"):
                value = float(utilization_pct[resource])
                if not math.isfinite(value) or not 0.0 < value <= 100.0:
                    raise ValueError(
                        f"invalid inner resource anchor utilization: {resource}={value}"
                    )
                normalized_utilization[resource] = value
                reference_resources[resource] = (
                    float(self.capacity[resource]) * value / 100.0
                )
            self.inner_resource_anchor = {
                **copy.deepcopy(dict(anchor)),
                "utilization_pct": normalized_utilization,
                "reference_resources": reference_resources,
            }
        self.execution_role = resolve_execution_role(policy)
        adaptive_config = policy.get("adaptive_search")
        self.adaptive_policy = (
            AdaptiveSearchPolicy(**adaptive_config)
            if isinstance(adaptive_config, Mapping)
            else None
        )
        self.budget = (
            None
            if self.adaptive_policy is not None
            else Budget(**policy["budget"])
        )
        if self.execution_role.interrupted_resume_allowed is False and prior_trial is not None:
            raise RuntimeError("formal Algorithm C cannot resume an interrupted controller state")
        if self.state.get("formal_started") and "budgeted_trial" not in self.state:
            raise RuntimeError("cannot reinterpret an old campaign as a fresh budgeted trial")
        self.trial = self.state.setdefault("budgeted_trial", {
            "contract": CONTRACT, "started_at_epoch_s": initialization_started,
            "search_replays": 0, "planning_s": 0.0, "replay_batch_wall_s": 0.0,
            "search_valid_replays": 0, "search_technical_failures": 0,
            "baseline_wall_s": 0.0, "region_queue": [], "visited_regions": [],
            "active_region_mode": "gradient", "stagnation": 0,
            "feasible_archive": {}, "accounted_labels": [],
            "completed_search_waves": 0,
            "seen_search_plan_fingerprints": [],
            "latest_adaptive_evidence": {},
            "latest_adaptive_decision": {},
            "consecutive_empty_admissions": 0,
            "latest_valid_search_replays": 0,
            "pending_outer_confirmation": None,
            "outer_confirmation_exhausted_identities": [],
            "completed_outer_confirmation_count": 0,
            "consecutive_outer_non_accept_waves": 0,
            "consecutive_rejected_batches": 0,
            "recovery_stage": 0,
            "stage_rejected_batches": 0,
            "consecutive_no_progress_batches": 0,
            "consecutive_empty_numeric_windows": 0,
            "numeric_pending_rankings": [],
            "outer_boundary_observations": [],
            "outer_boundary_generated_identities": [],
            "outer_boundary_refinement_attempts_since_shrink": 0,
            "progressive_numeric_refinement_window": 0,
            # The ordinary coverage design uses center + two axis probes per
            # parameter before Halton indices. Start after that initial pool.
            "progressive_numeric_refinement_cursor": max(
                1,
                int(self.policy.get("region_plan_probes", 32))
                - 2 * len(base.ACTIVE_PARAMS),
            ),
        })
        if isinstance(prior_trial, Mapping):
            self.trial.update(resume_active_time_accounting(
                prior_trial,
                observed_activity_epoch_s=prior_activity_epoch_s,
                resume_epoch_s=initialization_started,
            ))
            # An early recovery build used the generic ``contract`` key for
            # this sub-contract. Keep the enclosing trial identity canonical.
            self.trial["contract"] = CONTRACT
            self.log(
                "algorithm_c_active_time_session_resumed",
                active_time_contract=ACTIVE_TIME_CONTRACT,
                completed_active_wall_s=self.trial["completed_active_wall_s"],
                host_outage_excluded_s=self.trial["host_outage_excluded_s"],
                wall_budget_uses_active_controller_time=True,
            )
        else:
            self.trial.update({
                "contract": CONTRACT,
                "active_time_contract": ACTIVE_TIME_CONTRACT,
                "completed_active_wall_s": 0.0,
                "host_outage_excluded_s": 0.0,
                "active_session_started_at_epoch_s": initialization_started,
                "active_time_sessions": [],
                "wall_budget_uses_active_controller_time": True,
            })
        self.trial["inner_resource_anchor"] = copy.deepcopy(
            self.inner_resource_anchor
        )
        self.save_state()
        if self.inner_resource_anchor is not None:
            self.log(
                "algorithm_c_inner_resource_anchor_bound",
                anchor=copy.deepcopy(self.inner_resource_anchor),
                application=self.inner_resource_anchor["application"],
                qps_used_for_inner_scoring=False,
                old_candidate_parameters_imported=False,
                old_accept_reject_labels_imported=False,
                physical_validity_uses_actual_resources=True,
            )

    def inner_scoring_reference_resources(
        self,
        measured_incumbent_resources: Mapping[str, float],
    ) -> Dict[str, float]:
        """Apply the sealed old-C utilization prior only to inner scoring."""

        anchor = getattr(self, "inner_resource_anchor", None)
        if anchor is None:
            return super().inner_scoring_reference_resources(
                measured_incumbent_resources
            )
        return copy.deepcopy(anchor["reference_resources"])

    def wall_s(self) -> float:
        completed = float(self.trial.get("completed_active_wall_s") or 0.0)
        session_start = float(
            self.trial.get("active_session_started_at_epoch_s")
            or self.trial["started_at_epoch_s"]
        )
        return completed + max(0.0, time.time() - session_start)

    def budget_stop(self) -> Optional[str]:
        if self.budget is None:
            return None
        return self.budget.stop_reason(used=self.trial["search_replays"], wall_s=self.wall_s(),
                                       planning_s=self.trial["planning_s"])

    def box(self) -> Dict[str, tuple]:
        return {k: (v.min_value, v.max_value) for k, v in self.ranges.items()}

    def interval_progress_width(self, effective_shrink_count: int = 0) -> float:
        """Measure the widest actual interval, including explicitly reopened space."""
        widths = actual_widths(self.box(), dict(base.DEFAULT_RANGES))
        if not widths or any(not math.isfinite(value) or value < 0.0 for value in widths.values()):
            raise RuntimeError("invalid actual interval widths")
        self.trial["actual_parameter_widths"] = widths
        return max(widths.values())

    def reset_interval_misses(self) -> None:
        for name in (
            "stagnation", "recovery_stage", "stage_rejected_batches", "consecutive_rejected_batches",
            "consecutive_no_progress_batches", "consecutive_empty_admissions",
            "consecutive_empty_numeric_windows", "consecutive_outer_non_accept_waves",
        ):
            self.trial[name] = 0
        self.trial["numeric_pending_rankings"] = []
        self.trial["active_region_mode"] = "gradient"
        self.state["consecutive_missed_outer_rounds"] = 0
        self.state["consecutive_no_improvement_rounds"] = 0

    def check_interval_shrink(self, before: Mapping[str, tuple], *, require_all: bool = False) -> None:
        """Reject a no-op or widening masquerading as binary contraction."""
        after = self.box()
        if any(after[name][0] < low or after[name][1] > high
               for name, (low, high) in before.items()):
            raise RuntimeError("interval_shrink_expanded_bounds")
        previous = actual_widths(before, dict(base.DEFAULT_RANGES))
        current = actual_widths(after, dict(base.DEFAULT_RANGES))
        if not any(current[name] < previous[name] for name in previous):
            raise RuntimeError("interval_shrink_made_no_progress")
        if require_all and any(
            not math.isclose(current[name], previous[name] * 0.5, rel_tol=1e-10, abs_tol=1e-14)
            for name in previous if previous[name] > 0.0
        ):
            raise RuntimeError("two_miss_shrink_did_not_bisect_all_intervals")
        if base.parameter_vector_range_audit(self.state.get("incumbent_params"), self.ranges).get("passed") is not True:
            raise RuntimeError("interval_shrink_excluded_incumbent")
        self.state["normalized_width"] = self.interval_progress_width()
        self.log("algorithm_c_actual_interval_shrink_checked", previous_widths=previous,
                 actual_widths=current, normalized_width=self.state["normalized_width"],
                 normalized_width_threshold=self.normalized_width_threshold,
                 progress_verified=True)

    def shrink_after_two_misses(self) -> None:
        before = self.box()
        if base.parameter_vector_range_audit(self.state.get("incumbent_params"), self.ranges).get("passed") is not True:
            raise RuntimeError("incumbent_outside_interval_before_shrink")
        self.refine_ranges_around_current_incumbent(
            round_index=int(self.state["round_index"]),
            reason="two_complete_valid_outer_misses",
        )
        self.check_interval_shrink(before, require_all=True)
        self.trial["outer_boundary_refinement_attempts_since_shrink"] = 0
        self.reset_interval_misses()
        self.save_state()

    def begin_first_miss_expansion(self, round_index: int) -> None:
        """Reopen at most one C-only region for this miss, keeping its parent box."""
        if self.trial.get("expansion_parent_box") is not None:
            return
        before = self.box()
        self.trial["expansion_parent_box"] = copy.deepcopy(before)
        queue = self.trial.get("region_queue") or []
        region = None
        if queue:
            region = max(queue, key=lambda item: (
                float(item.get("optimistic_resource_gain") or 0.0),
                -int(item.get("created_round") or 0),
            ))
            queue.remove(region)
            # The hull always contains the current incumbent and is bounded
            # by the original parameter domain. Reopening is not a shrink.
            expanded = {}
            for name, (low, high) in before.items():
                rlow, rhigh = region["box"][name]
                original_low, original_high = base.DEFAULT_RANGES[name]
                expanded[name] = base.ParameterRange(
                    max(original_low, min(low, rlow)), min(original_high, max(high, rhigh)),
                )
            self.ranges = expanded
            self.trial.setdefault("visited_regions", []).append(region["id"])
        self.trial["active_region_mode"] = "first_miss_expansion"
        self.state["normalized_width"] = self.interval_progress_width()
        self.log("algorithm_c_first_miss_expansion", round_index=round_index,
                 parent_box=before, expanded_box=self.box(), region=region,
                 actual_parameter_widths=self.trial["actual_parameter_widths"],
                 miss_counter_unchanged=True, qps_used_for_region_priority=False)
        self.save_state()

    def restore_rejected_expansion(self) -> None:
        parent = self.trial.pop("expansion_parent_box", None)
        if parent is not None:
            self.ranges = {name: base.ParameterRange(*limits) for name, limits in parent.items()}
            self.state["normalized_width"] = self.interval_progress_width()
            self.log("algorithm_c_rejected_expansion_parent_restored", restored_box=self.box(),
                     incumbent_unchanged=True, miss_counter_unchanged=True)

    def generate_first_miss_rankings(self, round_index: int) -> List[Dict[str, Any]]:
        self.begin_first_miss_expansion(round_index)
        if base.plan_portfolio_policy(getattr(self, "algorithm_cfg", {})):
            # One staged portfolio already includes both local and global
            # probes; do not run it twice through the legacy rescue branches.
            return self.generate_rankings(round_index, ranking_scope="first_miss_portfolio")
        gradient = self.generate_recovered_gradient_rankings(round_index)
        coverage = self.generate_global_plan_coverage_rankings(round_index, allow_numeric_refinement=True)
        # Admission still checks ranges, exact replay identities and validity.
        # Preserve the resource model's ordering within each generator.
        result = []
        identities = set()
        for row in gradient + coverage:
            identity = evidence_identity(row.get("optimized_params") or {})
            if identity not in identities:
                identities.add(identity)
                result.append(row)
        return result

    def select_outer_admitted_candidates(self, candidates, *, round_index, batch_kind, limit=base.BASE_TOP_K):
        if base.plan_portfolio_policy(getattr(self, "algorithm_cfg", {})):
            return super().select_outer_admitted_candidates(
                candidates, round_index=round_index, batch_kind=batch_kind, limit=limit,
            )
        if getattr(self, "trial", {}).get("active_region_mode") != "first_miss_expansion":
            return super().select_outer_admitted_candidates(
                candidates, round_index=round_index, batch_kind=batch_kind, limit=limit,
            )
        admitted, audit = super().select_outer_admitted_candidates(
            candidates, round_index=round_index, batch_kind=batch_kind, limit=len(candidates),
        )
        seen = set(self.trial.get("seen_search_plan_fingerprints") or [])
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for candidate in admitted:
            groups.setdefault(self._candidate_plan_id(candidate), []).append(candidate)
        plans = sorted(groups, key=lambda plan: plan in seen or not plan)
        ordered = []
        while any(groups.values()):
            for plan in plans:
                if groups[plan]:
                    ordered.append(groups[plan].pop(0))
        selected = ordered[:limit]
        audit_by_identity = {
            evidence_identity(row["optimized_params"]): row for row in audit.get("selected", [])
        }
        audit.update({"selected_count": len(selected), "first_miss_plan_diverse_selection": True,
                      "qps_used_for_selection": False,
                      "selected": [audit_by_identity[evidence_identity(row["optimized_params"])]
                                   for row in selected if evidence_identity(row["optimized_params"]) in audit_by_identity],
                      "selected_parameter_identities": [evidence_identity(row["optimized_params"]) for row in selected]})
        return selected, audit

    @staticmethod
    def _candidate_plan_id(candidate: Mapping[str, Any]) -> str:
        return str(base.candidate_full_plan_fingerprint(candidate) or "")

    @staticmethod
    def _confidence_values(candidate: Mapping[str, Any]) -> List[float]:
        values: List[float] = []
        confidence = candidate.get("confidence")
        if isinstance(confidence, Mapping):
            for raw in confidence.values():
                if isinstance(raw, (int, float)) and math.isfinite(float(raw)):
                    values.append(float(raw))
        elif isinstance(confidence, (int, float)) and math.isfinite(float(confidence)):
            values.append(float(confidence))
        metadata = candidate.get("final_estimation_metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        plan_confidence = metadata.get("plan_model_confidence")
        if isinstance(plan_confidence, Mapping):
            for raw in plan_confidence.values():
                if isinstance(raw, (int, float)) and math.isfinite(float(raw)):
                    values.append(float(raw))
        return [min(1.0, max(0.0, value)) for value in values]

    def _ranking_uncertainty(self, rankings: List[Dict[str, Any]]) -> float:
        confidences = [
            value
            for ranking in rankings
            for value in self._confidence_values(ranking)
        ]
        # An empty admitted pool after exact-plan/range deduplication means
        # there is no unresolved candidate in this search action. It is not
        # evidence of high predictor uncertainty.
        return 1.0 - min(confidences) if confidences else 0.0

    def _pending_region_upper_gain(self) -> float:
        values = [
            float(region.get("optimistic_resource_gain") or 0.0)
            for region in self.trial.get("region_queue") or []
            if isinstance(region, Mapping)
        ]
        return max(values, default=0.0)

    def adaptive_evidence(self) -> AdaptiveSearchEvidence:
        return AdaptiveSearchEvidence(
            completed_waves=int(self.trial.get("completed_search_waves") or 0),
            attempted_search_replays=int(self.trial.get("search_replays") or 0),
            valid_search_replays=int(self.trial.get("search_valid_replays") or 0),
            active_wall_s=self.wall_s(),
            consecutive_no_progress_waves=int(
                self.trial.get("consecutive_no_progress_waves") or 0
            ),
            novel_plan_fingerprints=int(
                self.trial.get("latest_novel_plan_fingerprints") or 0
            ),
            duplicate_plan_fraction=float(
                self.trial.get("latest_duplicate_plan_fraction") or 0.0
            ),
            normalized_resource_gain=float(
                self.trial.get("latest_normalized_resource_gain") or 0.0
            ),
            maximum_model_uncertainty=float(
                self.trial.get("latest_maximum_model_uncertainty", 1.0)
            ),
            pending_region_count=len(self.trial.get("region_queue") or []),
            best_pending_region_optimistic_gain=self._pending_region_upper_gain(),
            outer_qps_accepted=bool(self.trial.get("latest_outer_qps_accepted")),
            technical_failure_count=int(
                self.trial.get("search_technical_failures") or 0
            ),
            consecutive_empty_admissions=int(
                self.trial.get("consecutive_empty_admissions") or 0
            ),
            latest_valid_search_replays=int(
                self.trial.get("latest_valid_search_replays") or 0
            ),
            has_feasible_finalist=best_feasible(
                self.trial.get("feasible_archive") or {}
            ) is not None,
            pending_outer_confirmation_count=int(
                isinstance(self.trial.get("pending_outer_confirmation"), Mapping)
            ),
            completed_outer_confirmation_count=int(
                self.trial.get("completed_outer_confirmation_count") or 0
            ),
            consecutive_outer_non_accept_waves=int(
                self.trial.get("consecutive_outer_non_accept_waves") or 0
            ),
            consecutive_rejected_batches=int(self.trial.get("consecutive_rejected_batches") or 0),
            recovery_stage=int(self.trial.get("recovery_stage") or 0),
            stage_rejected_batches=int(self.trial.get("stage_rejected_batches") or 0),
            consecutive_no_progress_batches=int(self.trial.get("consecutive_no_progress_batches") or 0),
            consecutive_empty_numeric_windows=int(self.trial.get("consecutive_empty_numeric_windows") or 0),
            pending_numeric_candidate_count=self.pending_numeric_candidate_count(),
            normalized_interval_width=self.interval_progress_width(),
            normalized_interval_threshold=self.normalized_width_threshold,
        )

    def pending_numeric_candidate_count(self) -> int:
        # Diagnostic only: never run candidate admission before a due shrink
        # or precision termination. Numeric generation revalidates on use.
        return len(self.trial.get("numeric_pending_rankings") or [])

    def enter_recovery_action(self, action: str) -> None:
        stages = {
            "first_miss_expansion": 1,
            "gradient_step_expansion": 1,
            "frontier_diversification": 2,
            "retained_region_revisit": 2,
            "frontier_numeric_refinement": 3,
        }
        stage = stages.get(action)
        if stage is not None and (
            stage != int(self.trial.get("recovery_stage") or 0)
            or action == "retained_region_revisit"
        ):
            self.trial["recovery_stage"] = stage
            self.trial["stage_rejected_batches"] = 0
            self.trial["consecutive_empty_admissions"] = 0
            self.trial["consecutive_no_progress_batches"] = 0
            self.trial["consecutive_empty_numeric_windows"] = 0
            self.trial["numeric_pending_rankings"] = []

    def generate_recovered_gradient_rankings(self, round_index: int) -> List[Dict[str, Any]]:
        """Expand trusted steps and directional boundaries, keeping genuine partial pools."""
        try:
            return self.generate_rankings(
                round_index,
                relative_step_probe_pct=base.BEHAVIOR_CLUSTER_GRADIENT_PROBE_STEP_PCT,
                ranking_scope="adaptive_trusted_step_expansion",
            )
        except base.IncompleteSubsetOptimizationError as step_error:
            if (
                getattr(step_error, "plan_space_saturation_evidence", False) is not True
                or getattr(step_error, "technical_candidate_generation_failure", False) is True
            ):
                raise
            try:
                return self.boundary_inward_bisection_rankings(round_index, step_error)
            except base.IncompleteSubsetOptimizationError as boundary_error:
                if (
                    getattr(boundary_error, "plan_space_saturation_evidence", False) is not True
                    or getattr(boundary_error, "technical_candidate_generation_failure", False) is True
                ):
                    raise
                return self.merge_exhausted_partial_topk(
                    [step_error, boundary_error], round_index=round_index,
                )

    def next_adaptive_decision(self):
        if self.adaptive_policy is None:
            raise RuntimeError("adaptive decision requested without adaptive policy")
        evidence = self.adaptive_evidence()
        decision = adaptive_search_decision(self.adaptive_policy, evidence)
        if (
            decision.action == "shrink_incumbent"
            and int(self.trial.get(
                "outer_boundary_refinement_attempts_since_shrink", 0
            )) < 1
            and self.outer_boundary_pair() is not None
        ):
            decision = replace(
                decision,
                action="frontier_numeric_refinement",
                requested_candidates=self.adaptive_policy.maximum_batch_size,
                converged=False,
                reason=(
                    "c_only_outer_boundary_refinement_before_interval_shrink"
                ),
            )
        self.trial["latest_adaptive_evidence"] = asdict(evidence)
        self.trial["latest_adaptive_decision"] = asdict(decision)
        self.log(
            "algorithm_c_adaptive_search_decision",
            policy=adaptive_policy_audit(self.adaptive_policy),
            evidence=asdict(evidence),
            decision=asdict(decision),
            external_baseline_information_used=False,
        )
        self.save_state()
        return decision

    def record_adaptive_batch(
        self,
        candidates: List[Dict[str, Any]],
        rows: List[Dict[str, Any]],
        selected: Optional[Dict[str, Any]],
        *,
        archive_improved: bool = False,
    ) -> None:
        if self.adaptive_policy is None:
            return
        valid_rows = [row for row in rows if base.physical_valid(row)]
        complete = bool(candidates and len(rows) == len(candidates) == len(valid_rows))
        if candidates and not complete:
            self.log("algorithm_c_incomplete_batch_not_search_evidence",
                     candidates=len(candidates), rows=len(rows), valid_rows=len(valid_rows),
                     rejection_counter_changed=False)
            return
        if not candidates:
            self.trial["consecutive_empty_admissions"] = int(
                self.trial.get("consecutive_empty_admissions") or 0
            ) + 1
            if int(self.trial.get("recovery_stage") or 0) == 3:
                self.trial["consecutive_empty_numeric_windows"] = int(
                    self.trial.get("consecutive_empty_numeric_windows") or 0
                ) + 1
            self.trial["latest_valid_search_replays"] = 0
            self.log("algorithm_c_empty_admission_changes_generator",
                     recovery_stage=self.trial.get("recovery_stage", 0),
                     rejection_counter_changed=False)
            self.save_state()
            return

        self.trial["consecutive_empty_admissions"] = 0
        self.trial["consecutive_empty_numeric_windows"] = 0
        seen = set(self.trial.get("seen_search_plan_fingerprints") or [])
        novel = 0
        duplicates = 0
        reference = self.state.get("incumbent_validation") or self.state.get("canonical_baseline") or {}
        before = float(self.trial.get("best_observed_target_resource") or
                       (reference.get("resources") or {}).get(self.target) or 0.0)
        best_after = before
        resource_gain = 0.0
        for row in valid_rows:
            plan_id = self._candidate_plan_id(row.get("candidate_ranking") or {})
            if not plan_id or plan_id in seen:
                duplicates += 1
            else:
                seen.add(plan_id)
                novel += 1
            normalized = None
            if selected is not None and row is selected.get("raw"):
                normalized = selected.get("normalized")
            elif row.get("worker_key") in (self.state.get("baseline_by_worker") or {}):
                normalized = base.normalize_to_worker_baseline(
                    row, self.state["baseline_by_worker"][row["worker_key"]],
                    self.state["canonical_baseline"], self.capacity,
                )
            if normalized and before > 0.0:
                after = float((normalized.get("resources") or {}).get(self.target) or before)
                if after > 0.0:
                    best_after = min(best_after, after)
                resource_gain = max(resource_gain, max(0.0, min(1.0, (before - after) / before)))
        if best_after > 0.0:
            self.trial["best_observed_target_resource"] = best_after

        progress = bool(selected is not None or archive_improved or novel > 0
                        or resource_gain >= self.adaptive_policy.minimum_normalized_resource_gain)
        wave_count = math.ceil(len(rows) / self.adaptive_policy.worker_count)
        self.trial["completed_search_waves"] = int(self.trial.get("completed_search_waves") or 0) + wave_count
        self.trial["seen_search_plan_fingerprints"] = sorted(seen)
        self.trial["latest_novel_plan_fingerprints"] = novel
        self.trial["latest_valid_search_replays"] = len(valid_rows)
        self.trial["latest_duplicate_plan_fraction"] = duplicates / len(valid_rows)
        self.trial["latest_normalized_resource_gain"] = resource_gain
        self.trial["latest_maximum_model_uncertainty"] = self._ranking_uncertainty(candidates)
        self.trial["latest_outer_qps_accepted"] = selected is not None
        for key in ("consecutive_rejected_batches", "stage_rejected_batches"):
            self.trial[key] = 0 if selected is not None else int(self.trial.get(key) or 0) + 1
        self.trial["consecutive_no_progress_batches"] = (
            0 if progress else int(self.trial.get("consecutive_no_progress_batches") or 0) + 1
        )
        # Retain old wave fields only for historical log readers.
        self.trial["consecutive_outer_non_accept_waves"] = (
            0 if selected is not None else int(self.trial.get("consecutive_outer_non_accept_waves") or 0) + wave_count
        )
        self.trial["consecutive_no_progress_waves"] = (
            0 if progress else int(self.trial.get("consecutive_no_progress_waves") or 0) + wave_count
        )
        self.log(
            "algorithm_c_adaptive_batch_evidence",
            completed_wave_count=wave_count, completed_valid_batch_count=1,
            valid_replays=len(valid_rows), novel_plan_fingerprints=novel,
            duplicate_plan_fraction=self.trial["latest_duplicate_plan_fraction"],
            normalized_resource_gain=resource_gain,
            maximum_model_uncertainty=self.trial["latest_maximum_model_uncertainty"],
            outer_qps_accepted=selected is not None, archive_improved=archive_improved,
            consecutive_rejected_batches=self.trial["consecutive_rejected_batches"],
            stage_rejected_batches=self.trial["stage_rejected_batches"],
            consecutive_no_progress_batches=self.trial["consecutive_no_progress_batches"],
            recovery_stage=self.trial.get("recovery_stage", 0),
            qps_used_for_inner_scoring=False, qps_used_for_outer_interval_control=True,
        )
        self.save_state()

    def record_outer_boundary_observation(
        self,
        evaluation: Mapping[str, Any],
        decision: Mapping[str, Any],
        *,
        round_index: int,
        evidence_role: str = "single_physical_replay",
    ) -> Optional[Dict[str, Any]]:
        """Retain C-only Outer endpoints without feeding QPS into inner scoring."""
        raw = evaluation.get("raw")
        if not isinstance(raw, Mapping):
            return None
        ranking = raw.get("candidate_ranking")
        if not isinstance(ranking, Mapping):
            return None
        params = ranking.get("optimized_params")
        if not isinstance(params, Mapping) or any(
            name not in params for name in base.ACTIVE_PARAMS
        ):
            return None
        if (decision.get("qps_noise_gate_required") is True
                and decision.get("qps_noise_model_ready") is not True):
            return None
        try:
            qps_gain = float(decision["qps_gate_gain"])
            required_gain = float(decision["min_db_qps_gain_pct"]) / 100.0
        except (KeyError, TypeError, ValueError):
            return None
        if not math.isfinite(qps_gain) or not math.isfinite(required_gain):
            return None
        non_target_safe = decision.get("non_target_safe") is True
        target_down = decision.get("target_down") is True
        resource_feasible = bool(non_target_safe and target_down)
        qps_passed = decision.get("qps_up") is True
        category = "other_outer_observation"
        if resource_feasible and qps_passed:
            category = "resource_feasible_qps_passed"
        elif resource_feasible and 0.0 < qps_gain < required_gain and not qps_passed:
            category = "safe_positive_qps_shortfall"
        elif qps_passed and not non_target_safe:
            category = "qps_pass_resource_unsafe"
        elif resource_feasible and not qps_passed:
            # A resource-feasible endpoint with weak or negative QPS is still
            # the correct safe side of a multi-objective Outer bracket.
            category = "resource_feasible_qps_below_gate"
        elif qps_gain > 0.0 and not resource_feasible:
            # Do not require a noisy single replay to clear the complete QPS
            # gate before it can define the high-QPS, resource-infeasible side
            # of a boundary.  It may guide interval bisection, never acceptance.
            category = "positive_qps_resource_infeasible"
        utilization = decision.get("non_target_utilization_pct")
        utilization = dict(utilization) if isinstance(utilization, Mapping) else {}
        threshold = float(decision.get("non_target_bottleneck_threshold_pct") or 0.0)
        effective_thresholds = decision.get("effective_non_target_threshold_pct")
        effective_thresholds = (
            dict(effective_thresholds)
            if isinstance(effective_thresholds, Mapping)
            else {name: threshold for name in utilization}
        )
        unsafe_excess = max(
            [
                float(value) - float(effective_thresholds.get(name, threshold))
                for name, value in utilization.items()
            ] or [0.0]
        )
        params_copy = {
            name: float(params[name]) for name in base.ACTIVE_PARAMS
        }
        observation = {
            "contract": OUTER_BOUNDARY_REFINEMENT_CONTRACT,
            "observation_id": base.canonical_sha256({
                "label": raw.get("label"),
                "round_index": round_index,
                "evidence_role": evidence_role,
                "params": params_copy,
                "plan": base.candidate_full_plan_fingerprint(ranking),
            }),
            "evidence_role": evidence_role,
            "category": category,
            "round_index": int(round_index),
            "label": str(raw.get("label") or ""),
            "params": params_copy,
            "parameter_identity": evidence_identity(params_copy),
            "full_plan_fingerprint": base.candidate_full_plan_fingerprint(ranking),
            "qps_gate_gain": qps_gain,
            "required_qps_gain": required_gain,
            "qps_passed": qps_passed,
            "non_target_safe": non_target_safe,
            "target_down": target_down,
            "resource_feasible": resource_feasible,
            "non_target_utilization_pct": utilization,
            "non_target_threshold_pct": threshold,
            "effective_non_target_threshold_pct": effective_thresholds,
            "non_target_unsafe_excess_pct": max(0.0, unsafe_excess),
            "qps_noise_model_id": str(decision.get("qps_noise_model_id") or ""),
            "qps_used_for_inner_scoring": False,
            "qps_used_for_outer_interval_control": True,
            "external_baseline_information_used": False,
        }
        observations = self.trial.setdefault("outer_boundary_observations", [])
        known = {
            str(row.get("observation_id") or "")
            for row in observations
            if isinstance(row, Mapping)
        }
        if observation["observation_id"] not in known:
            observations.append(observation)
            # Bound state size, not search: the most recent direct/aggregate
            # Outer endpoints are sufficient to construct a local bracket.
            del observations[:-128]
        self.log(
            "algorithm_c_outer_boundary_observation",
            observation=copy.deepcopy(observation),
            inner_candidate_scores_modified=False,
            qps_used_for_inner_scoring=False,
            qps_used_for_outer_interval_control=True,
            external_baseline_information_used=False,
        )
        self.save_state()
        return observation

    def outer_boundary_pair(self) -> Optional[Dict[str, Any]]:
        """Choose a promising C-only safe/unsafe Outer bracket in the current box."""
        observations = [
            row
            for row in self.trial.get("outer_boundary_observations") or []
            if isinstance(row, Mapping)
        ]
        safe_rows = [
            row for row in observations
            if row.get("category") in {
                "safe_positive_qps_shortfall",
                "resource_feasible_qps_below_gate",
                "resource_feasible_qps_passed",
            }
        ]
        unsafe_rows = [
            row for row in observations
            if row.get("category") in {
                "qps_pass_resource_unsafe",
                "positive_qps_resource_infeasible",
            }
            and row.get("non_target_safe") is False
        ]
        pairs: List[Tuple[Tuple[float, ...], Dict[str, Any]]] = []
        for safe in safe_rows:
            for unsafe in unsafe_rows:
                if float(unsafe["qps_gate_gain"]) <= float(safe["qps_gate_gain"]):
                    continue
                if safe.get("parameter_identity") == unsafe.get("parameter_identity"):
                    continue
                safe_params = safe.get("params")
                unsafe_params = unsafe.get("params")
                if not isinstance(safe_params, Mapping) or not isinstance(
                    unsafe_params, Mapping
                ):
                    continue
                if any(
                    base.parameter_vector_range_audit(params, self.ranges).get("passed") is not True
                    for params in (safe_params, unsafe_params)
                ):
                    continue
                try:
                    distance = normalized_parameter_distance(
                        safe_params,
                        unsafe_params,
                        self.ranges,
                    )
                    shortfall = max(
                        0.0,
                        float(safe["required_qps_gain"])
                        - float(safe["qps_gate_gain"]),
                    )
                    unsafe_excess = max(
                        0.0,
                        float(unsafe["non_target_unsafe_excess_pct"]) / 100.0,
                    )
                except (KeyError, TypeError, ValueError):
                    continue
                pair = {
                    "safe": copy.deepcopy(dict(safe)),
                    "unsafe": copy.deepcopy(dict(unsafe)),
                    "normalized_parameter_distance": distance,
                    "safe_qps_shortfall": shortfall,
                    "unsafe_resource_excess_fraction": unsafe_excess,
                }
                # QPS is permitted only for physical Outer interval control.
                # Refine the most promising unsafe endpoint before preferring
                # a shorter but low-throughput bracket.
                pairs.append(((
                    -float(unsafe["qps_gate_gain"]),
                    unsafe_excess,
                    distance,
                    shortfall,
                ), pair))
        if not pairs:
            return None
        pairs.sort(key=lambda value: value[0])
        pair = pairs[0][1]
        pair["pair_id"] = base.canonical_sha256({
            "safe": pair["safe"]["observation_id"],
            "unsafe": pair["unsafe"]["observation_id"],
        })
        return pair

    def outer_boundary_refinement_vectors(
        self,
        limit: int,
    ) -> Tuple[List[Dict[str, float]], Dict[str, Any]]:
        """Generate unseen transformed-space interior points for one Outer bracket."""
        pair = self.outer_boundary_pair()
        if pair is None:
            return [], {
                "contract": OUTER_BOUNDARY_REFINEMENT_CONTRACT,
                "ready": False,
                "reason": "c_only_safe_unsafe_outer_bracket_unavailable",
            }
        fractions: List[float] = []
        safe_utilization = pair["safe"].get("non_target_utilization_pct") or {}
        unsafe_utilization = pair["unsafe"].get("non_target_utilization_pct") or {}
        unsafe_thresholds = (
            pair["unsafe"].get("effective_non_target_threshold_pct") or {}
        )
        boundary_fractions: List[float] = []
        for resource, unsafe_value in unsafe_utilization.items():
            safe_value = safe_utilization.get(resource)
            threshold = unsafe_thresholds.get(
                resource,
                pair["unsafe"].get("non_target_threshold_pct"),
            )
            try:
                span = float(unsafe_value) - float(safe_value)
                fraction = (float(threshold) - float(safe_value)) / span
            except (TypeError, ValueError, ZeroDivisionError):
                continue
            if span > 0.0 and 0.0 < fraction < 1.0:
                boundary_fractions.append(fraction)
        if boundary_fractions:
            boundary_fraction = min(boundary_fractions)
            fractions.extend(
                min(0.999, max(0.001, boundary_fraction * multiplier))
                for multiplier in (0.50, 0.75, 0.90, 0.98, 1.02)
            )
        fractions.extend([1.0 / 3.0, 2.0 / 3.0, 0.5])
        for level in range(2, 14):
            denominator = 2 ** level
            fractions.extend(
                numerator / denominator
                for numerator in range(1, denominator, 2)
            )
        generated = set(
            str(value)
            for value in self.trial.get("outer_boundary_generated_identities") or []
        )
        safe_params = pair["safe"]["params"]
        unsafe_params = pair["unsafe"]["params"]
        vectors: List[Dict[str, float]] = []
        vector_rows: List[Dict[str, Any]] = []
        for fraction in fractions:
            params = interpolate_parameter_vectors(
                safe_params,
                unsafe_params,
                fraction,
            )
            identity = evidence_identity(params)
            generated_key = base.canonical_sha256({
                "pair_id": pair["pair_id"],
                "parameter_identity": identity,
            })
            if generated_key in generated:
                continue
            generated.add(generated_key)
            vectors.append(params)
            vector_rows.append({
                "parameter_identity": identity,
                "fraction_from_safe_to_unsafe": fraction,
                "generated_key": generated_key,
            })
            if len(vectors) >= max(1, limit):
                break
        self.trial["outer_boundary_generated_identities"] = sorted(generated)
        return vectors, {
            "contract": OUTER_BOUNDARY_REFINEMENT_CONTRACT,
            "ready": bool(vectors),
            "reason": (
                "transformed_parameter_outer_bracket_interior"
                if vectors else "outer_bracket_interior_already_generated"
            ),
            "pair_id": pair["pair_id"],
            "safe_endpoint_id": pair["safe"]["observation_id"],
            "unsafe_endpoint_id": pair["unsafe"]["observation_id"],
            "safe_qps_gain": pair["safe"]["qps_gate_gain"],
            "safe_required_qps_gain": pair["safe"]["required_qps_gain"],
            "unsafe_qps_gain": pair["unsafe"]["qps_gate_gain"],
            "unsafe_resource_excess_pct": pair["unsafe"][
                "non_target_unsafe_excess_pct"
            ],
            "normalized_parameter_distance": pair[
                "normalized_parameter_distance"
            ],
            "vectors": vector_rows,
            "qps_used_for_inner_scoring": False,
            "qps_used_for_outer_interval_control": True,
            "external_baseline_information_used": False,
        }

    def progressive_numeric_refinement_vectors(
        self,
        count: int,
    ) -> Tuple[List[Dict[str, float]], Dict[str, Any]]:
        """Return a centered, progressively wider deterministic numeric window.

        Sampling every parameter across the full current interval makes most
        seven-dimensional Halton points fall outside a sparse C-only model's
        support hull.  Start around the current incumbent with axis probes and
        a few joint low-discrepancy probes, then double the normalized radius.
        This remains QPS-blind and scene-agnostic: only the current C state,
        current formal ranges, and a deterministic sequence are inputs.
        """
        if count < 1:
            raise ValueError("progressive numeric refinement count must be positive")
        cursor = max(
            1,
            int(self.trial.get("progressive_numeric_refinement_cursor") or 1),
        )
        window_index = max(
            0,
            int(self.trial.get("progressive_numeric_refinement_window") or 0),
        )
        radius = PROGRESSIVE_NUMERIC_REFINEMENT_RADII[
            min(window_index, len(PROGRESSIVE_NUMERIC_REFINEMENT_RADII) - 1)
        ]
        primes = (2, 3, 5, 7, 11, 13, 17)
        incumbent = {
            name: float(self.state["incumbent_params"][name])
            for name in base.ACTIVE_PARAMS
        }

        def fraction_of(name: str, value: float) -> float:
            low = float(self.ranges[name].min_value)
            high = float(self.ranges[name].max_value)
            if base.resolve_parameter_transform(name) == "log":
                if low <= 0.0 or high <= 0.0 or value <= 0.0:
                    raise ValueError(f"log-scale parameter must be positive: {name}")
                low, high, value = math.log(low), math.log(high), math.log(value)
            return min(1.0, max(0.0, (value - low) / max(high - low, 1e-12)))

        centers = {
            name: fraction_of(name, incumbent[name]) for name in base.ACTIVE_PARAMS
        }
        vectors: List[Dict[str, float]] = []
        signatures = set()

        def add(fractions: Mapping[str, float]) -> None:
            params = {
                name: base._parameter_value_at_fraction(
                    name,
                    float(self.ranges[name].min_value),
                    float(self.ranges[name].max_value),
                    fractions.get(name, centers[name]),
                )
                for name in base.ACTIVE_PARAMS
            }
            signature = base.exact_param_signature(params)
            if signature == base.exact_param_signature(incumbent) or signature in signatures:
                return
            signatures.add(signature)
            vectors.append(params)

        # Cover both sides of every coordinate before joint movement.  The
        # window rotates deterministically when callers request fewer than all
        # axis probes.
        axis_slots = 2 * len(base.ACTIVE_PARAMS)
        for offset in range(min(count, axis_slots)):
            slot = (window_index * count + offset) % axis_slots
            name = base.ACTIVE_PARAMS[slot // 2]
            direction = -1.0 if slot % 2 == 0 else 1.0
            add({name: min(1.0, max(0.0, centers[name] + direction * radius))})

        # Fill the rest with centered joint Halton points.  These can cross a
        # same-plan numeric boundary that isolated axis probes cannot expose.
        index = cursor
        joint_attempts = 0
        maximum_joint_attempts = max(1024, count * 128)
        while len(vectors) < count and joint_attempts < maximum_joint_attempts:
            add({
                name: min(
                    1.0,
                    max(
                        0.0,
                        centers[name]
                        + radius * (2.0 * radical_inverse(index, prime) - 1.0),
                    ),
                )
                for name, prime in zip(base.ACTIVE_PARAMS, primes)
            })
            index += 1
            joint_attempts += 1
        next_cursor = max(cursor + count, index)
        self.trial["progressive_numeric_refinement_cursor"] = next_cursor
        self.trial["progressive_numeric_refinement_window"] = window_index + 1
        return vectors, {
            "contract": PROGRESSIVE_NUMERIC_REFINEMENT_CONTRACT,
            "cursor_start": cursor,
            "cursor_end_exclusive": next_cursor,
            "window_index": window_index,
            "normalized_radius": radius,
            "center": centers,
            "axis_probe_count": min(count, axis_slots),
            "joint_probe_count": max(0, count - axis_slots),
            "joint_attempt_count": joint_attempts,
            "joint_attempt_limit_reached": (
                len(vectors) < count and joint_attempts >= maximum_joint_attempts
            ),
            "vector_count": len(vectors),
            "qps_used_for_inner_scoring": False,
            "qps_used_for_outer_interval_control": False,
            "external_baseline_information_used": False,
        }

    def generate_dynamic_numeric_refinement_rankings(
        self,
        round_index: int,
    ) -> List[Dict[str, Any]]:
        """Rank an Outer bracket first, then an unbounded C-only coverage window."""
        if self.adaptive_policy is None:
            raise RuntimeError("dynamic numeric refinement requires adaptive policy")

        def admitted(rows):
            if not rows:
                return []
            eligible, _ = self.select_outer_admitted_candidates(
                rows, round_index=round_index,
                batch_kind=self.execution_role.search_batch_kind, limit=len(rows),
            )
            return eligible

        context = base.canonical_sha256({
            "ranges": self.box(), "incumbent": self.state.get("incumbent_params"),
        })
        pending = self.trial.get("numeric_pending_rankings") or []
        if self.trial.get("numeric_pending_context") == context:
            pending = admitted(pending)
            if pending:
                self.trial["numeric_pending_rankings"] = pending
                return copy.deepcopy(pending)
        self.trial["numeric_pending_rankings"] = []
        self.trial["numeric_pending_context"] = context
        vector_limit = self.adaptive_policy.maximum_batch_size
        # Count the attempted boundary batch even if admission forces a
        # fallback. Otherwise a due shrink can retry an empty bracket forever.
        self.trial["outer_boundary_refinement_attempts_since_shrink"] = (
            int(self.trial.get("outer_boundary_refinement_attempts_since_shrink", 0)) + 1
        )
        vectors, audit = self.outer_boundary_refinement_vectors(vector_limit)
        mode = "outer_boundary"
        if not vectors:
            mode = "progressive_low_discrepancy"
            pool_size = max(
                4 * self.adaptive_policy.worker_count,
                2 * self.adaptive_policy.maximum_batch_size,
                2 * len(base.ACTIVE_PARAMS) + 2 * self.adaptive_policy.worker_count,
            )
            vectors, audit = self.progressive_numeric_refinement_vectors(pool_size)

        def rank_vectors(rows: List[Dict[str, float]]) -> List[Dict[str, Any]]:
            self._adaptive_coverage_override = copy.deepcopy(rows)
            try:
                return self.generate_global_plan_coverage_rankings(
                    round_index,
                    allow_numeric_refinement=True,
                )
            finally:
                self._adaptive_coverage_override = None

        rankings = rank_vectors(vectors)
        boundary_fallback: Optional[Dict[str, Any]] = None
        if mode == "outer_boundary" and not admitted(rankings):
            boundary_fallback = copy.deepcopy(audit)
            mode = "progressive_low_discrepancy"
            pool_size = max(
                4 * self.adaptive_policy.worker_count,
                2 * self.adaptive_policy.maximum_batch_size,
                2 * len(base.ACTIVE_PARAMS) + 2 * self.adaptive_policy.worker_count,
            )
            vectors, audit = self.progressive_numeric_refinement_vectors(pool_size)
            rankings = rank_vectors(vectors)
        vector_metadata = {
            row["parameter_identity"]: row
            for row in audit.get("vectors") or []
            if isinstance(row, Mapping)
        }
        for ranking in rankings:
            params = ranking.get("optimized_params") or {}
            identity = evidence_identity(params)
            ranking.update({
                "dynamic_numeric_refinement": True,
                "dynamic_numeric_refinement_mode": mode,
                "dynamic_numeric_refinement_contract": audit["contract"],
                "qps_used_for_inner_scoring": False,
                "qps_used_for_outer_interval_control": mode == "outer_boundary",
                "external_algorithm_evidence_used": False,
            })
            if identity in vector_metadata:
                ranking["outer_boundary_refinement"] = True
                ranking["outer_boundary_refinement_contract"] = (
                    OUTER_BOUNDARY_REFINEMENT_CONTRACT
                )
                ranking["outer_boundary_pair_id"] = audit["pair_id"]
                ranking["outer_boundary_fraction_from_safe_to_unsafe"] = (
                    vector_metadata[identity]["fraction_from_safe_to_unsafe"]
                )
        rankings = admitted(rankings)
        self.trial["numeric_pending_rankings"] = copy.deepcopy(rankings)
        self.log(
            "algorithm_c_dynamic_numeric_refinement",
            round_index=round_index,
            mode=mode,
            audit=copy.deepcopy(audit),
            boundary_fallback=boundary_fallback,
            generated_ranking_count=len(rankings),
            inner_candidate_scores_modified=False,
            qps_used_for_inner_scoring=False,
            qps_used_for_outer_interval_control=mode == "outer_boundary",
            external_baseline_information_used=False,
        )
        self.save_state()
        return rankings

    def allow_target_support_expansion_probe(
        self,
        candidate: Mapping[str, Any],
        support: Mapping[str, Any],
    ) -> bool:
        """Admit bounded, real-replay probes when the C-only model abstains.

        The target prediction remains neutral and is not used for candidate
        scoring.  The probe exists only to acquire Algorithm C's own physical
        evidence outside a sparse training support hull.
        """
        if self.adaptive_policy is None:
            return False
        if (
            candidate.get("global_plan_coverage_rescue") is not True
            or candidate.get("global_plan_coverage_contract")
            != base.GLOBAL_PLAN_COVERAGE_CONTRACT
            or support.get("support_abstained") is not True
        ):
            return False
        ratio = support.get("distance_to_threshold_ratio")
        return bool(
            isinstance(ratio, (int, float))
            and math.isfinite(float(ratio))
            and float(ratio)
            <= self.adaptive_policy.maximum_support_expansion_distance_ratio
        )

    def observe_valid_evaluation(self, evaluation: Mapping[str, Any], round_index: int) -> None:
        normalized = evaluation["normalized"]
        canonical = self.state["canonical_baseline"]
        decision = base.outer_semantic_acceptance_details(
            normalized, canonical, canonical, target=self.target, cfg=self.cfg,
            consecutive_missed_outer_rounds=0,
        )
        self.record_outer_boundary_observation(
            evaluation,
            decision,
            round_index=round_index,
        )
        raw = evaluation["raw"]
        ranking = raw["candidate_ranking"]
        if ranking.get("outer_near_gate_confirmation") is True:
            return
        params = ranking["optimized_params"]
        source = {"label": raw["label"], "round": round_index, "worker_key": raw["worker_key"]}
        if admit_feasible_evidence(self.trial["feasible_archive"], params=params,
                                   decision=decision, source=source):
            self.log("algorithm_c_feasible_candidate_archived", **source, params=params,
                     canonical_decision=decision, search_incumbent_unchanged=True,
                     qps_used_for_inner_scoring=False)
            self.save_state()
            return
        self.enqueue_outer_confirmation(evaluation, decision, round_index)

    def enqueue_outer_confirmation(
        self,
        evaluation: Mapping[str, Any],
        canonical_decision: Mapping[str, Any],
        round_index: int,
    ) -> bool:
        """Retain the best C-only positive QPS miss for outer confirmation."""
        if self.adaptive_policy is not None:
            return False  # Strict QPS increase has no noise-confirmation stage.
        if self.adaptive_policy is None or best_feasible(
            self.trial.get("feasible_archive") or {}
        ) is not None:
            return False
        if int(self.trial.get("completed_outer_confirmation_count") or 0) >= (
            self.adaptive_policy.maximum_outer_confirmations_without_finalist
        ):
            return False
        decision = dict(canonical_decision)
        qps_rejects = {
            "db_qps_not_up_vs_canonical_baseline",
            "db_qps_not_up_vs_last_accepted",
        }
        rejects = set(str(value) for value in (decision.get("rejects") or []))
        gain = float(decision.get("qps_gate_gain") or 0.0)
        static_floor = float(decision.get("static_min_db_qps_gain_pct") or 0.0) / 100.0
        noise_floor = float(decision.get("min_db_qps_gain_pct") or math.inf) / 100.0
        minimum_gain = max(
            static_floor,
            self.adaptive_policy.outer_confirmation_min_noise_fraction * noise_floor,
        )
        raw = evaluation["raw"]
        ranking = raw["candidate_ranking"]
        identity = evidence_identity(ranking.get("optimized_params") or {})
        exhausted = set(self.trial.get("outer_confirmation_exhausted_identities") or [])
        eligible = bool(
            decision.get("accepted") is not True
            and decision.get("non_target_safe") is True
            and decision.get("qps_noise_model_ready") is True
            and rejects
            and rejects.issubset(qps_rejects)
            and math.isfinite(gain)
            and gain >= minimum_gain
            and identity not in exhausted
        )
        if not eligible:
            return False
        pending = self.trial.get("pending_outer_confirmation")
        pending_gain = float(
            ((pending or {}).get("canonical_decision") or {}).get("qps_gate_gain")
            or -math.inf
        ) if isinstance(pending, Mapping) else -math.inf
        if gain <= pending_gain:
            return False
        self.trial["pending_outer_confirmation"] = {
            "contract": OUTER_CONFIRMATION_CONTRACT,
            "identity": identity,
            "ranking": copy.deepcopy(ranking),
            "original_physical_row": copy.deepcopy(raw),
            "canonical_decision": copy.deepcopy(decision),
            "created_round": round_index,
            "selection_role": "outer_confirmation_priority_only",
            "qps_used_for_inner_scoring": False,
            "external_baseline_information_used": False,
        }
        self.log(
            "algorithm_c_outer_near_gate_candidate_queued",
            round_index=round_index,
            identity=identity,
            qps_gain=gain,
            minimum_confirmation_gain=minimum_gain,
            replaced_lower_outer_candidate=isinstance(pending, Mapping),
            qps_used_for_inner_scoring=False,
            qps_used_for_outer_interval_control=True,
            external_baseline_information_used=False,
        )
        self.save_state()
        return True

    def outer_confirmation_candidates(
        self,
        entry: Mapping[str, Any],
    ) -> List[Dict[str, Any]]:
        """Complete a balanced 2x2 candidate cohort around one prior replay."""
        if self.adaptive_policy is None:
            raise RuntimeError("outer confirmation requires adaptive policy")
        repetitions = self.adaptive_policy.outer_confirmation_repetitions_per_worker
        original = entry.get("original_physical_row") or {}
        original_worker = str(original.get("worker_key") or "")
        worker_keys = [base.worker_key(worker) for worker in self.workers]
        if len(worker_keys) != 2 or original_worker not in worker_keys:
            raise RuntimeError("outer confirmation requires the original row from one of two workers")
        candidates: List[Dict[str, Any]] = []
        skipped_original = False
        for repetition in range(repetitions):
            for worker_index, worker_key in enumerate(worker_keys):
                if worker_key == original_worker and not skipped_original:
                    skipped_original = True
                    continue
                candidate = copy.deepcopy(entry["ranking"])
                candidate.update({
                    "confirmation_only": True,
                    "outer_near_gate_confirmation": True,
                    "outer_confirmation_contract": OUTER_CONFIRMATION_CONTRACT,
                    "outer_confirmation_identity": entry["identity"],
                    "outer_confirmation_repetition": repetition + 1,
                    "outer_confirmation_worker_key": worker_key,
                    "outer_confirmation_candidate_slot": (
                        repetition * len(worker_keys) + worker_index + 1
                    ),
                    "qps_used_for_inner_scoring": False,
                })
                candidates.append(candidate)
        expected = len(worker_keys) * repetitions - 1
        if len(candidates) != expected:
            raise RuntimeError("outer confirmation candidate construction is incomplete")
        return candidates

    def physical_replay_candidate_slot(
        self,
        candidate: Mapping[str, Any],
        *,
        local_position: int,
        batch_kind: str,
    ) -> int:
        slot = candidate.get("outer_confirmation_candidate_slot")
        if slot is not None:
            return int(slot)
        return super().physical_replay_candidate_slot(
            candidate,
            local_position=local_position,
            batch_kind=batch_kind,
        )

    def evaluate_outer_confirmation_batch(
        self,
        entry: Mapping[str, Any],
        rows: List[Dict[str, Any]],
        *,
        round_index: int,
    ) -> Tuple[Optional[Dict[str, Any]], int]:
        """Apply replicated QPS evidence only at the outer interval boundary."""
        valid_rows = [row for row in rows if base.physical_valid(row)]
        expected_new = (
            self.adaptive_policy.worker_count
            * self.adaptive_policy.outer_confirmation_repetitions_per_worker
            - 1
        )
        identity = str(entry["identity"])
        exhausted = set(self.trial.get("outer_confirmation_exhausted_identities") or [])
        exhausted.add(identity)
        self.trial["outer_confirmation_exhausted_identities"] = sorted(exhausted)
        self.trial["completed_outer_confirmation_count"] = int(
            self.trial.get("completed_outer_confirmation_count") or 0
        ) + 1
        if len(valid_rows) != expected_new:
            self.log(
                "algorithm_c_outer_near_gate_confirmation_incomplete",
                round_index=round_index,
                identity=identity,
                expected_new_replays=expected_new,
                valid_new_replays=len(valid_rows),
                qps_used_for_inner_scoring=False,
            )
            self.save_state()
            return None, len(valid_rows)
        original = copy.deepcopy(entry["original_physical_row"])
        candidate_rows = [original, *valid_rows]
        noise = self.state.get("qps_noise_model") or {}
        confirmation = balanced_outer_qps_confirmation(
            candidate_rows,
            list(self.state.get("baseline_replay_rows") or []),
            static_floor_pct=float(noise.get("static_floor_pct") or 0.0),
            confidence_z=float(noise.get("confidence_z") or 0.0),
        )
        if confirmation.get("ready") is not True:
            self.log(
                "algorithm_c_outer_near_gate_confirmation_invalid",
                round_index=round_index,
                identity=identity,
                confirmation=confirmation,
                qps_used_for_inner_scoring=False,
            )
            self.save_state()
            return None, len(valid_rows)
        normalized_rows = [
            base.normalize_to_worker_baseline(
                row,
                self.state["baseline_by_worker"][str(row["worker_key"])],
                self.state["canonical_baseline"],
                self.capacity,
            )
            for row in candidate_rows
        ]
        normalized = copy.deepcopy(normalized_rows[0])
        normalized["label"] = f"outer_confirmation_r{round_index:03d}_{identity[:12]}"
        normalized["worker_key"] = "balanced_two_worker_confirmation"
        normalized["resources"] = {
            resource: sum(float(row["resources"][resource]) for row in normalized_rows)
            / len(normalized_rows)
            for resource in ("cpu", "memory", "io")
        }
        canonical_qps = float(self.state["canonical_baseline"]["db_qps"])
        normalized["db_qps"] = canonical_qps * math.exp(
            float(confirmation["mean_log_gain"])
        )
        normalized["actual_utilization_pct"] = {
            resource: normalized["resources"][resource] / float(self.capacity[resource]) * 100.0
            for resource in ("cpu", "memory", "io")
        }
        normalized["qps_noise_model"] = {
            **copy.deepcopy(confirmation),
            "comparison_sigma_log": confirmation["standard_error_log"],
            "formal_role": "outer_acceptance_audit_only",
        }
        canonical = self.state["canonical_baseline"]
        decision = base.outer_semantic_acceptance_details(
            normalized,
            canonical,
            canonical,
            target=self.target,
            cfg=self.cfg,
            consecutive_missed_outer_rounds=0,
        )
        aggregate_raw = copy.deepcopy(original)
        aggregate_raw["label"] = normalized["label"]
        aggregate_raw["worker_key"] = "balanced_two_worker_confirmation"
        aggregate_raw["candidate_ranking"] = copy.deepcopy(entry["ranking"])
        evaluation = {
            "raw": aggregate_raw,
            "normalized": normalized,
            "decision": decision,
        }
        self.record_outer_boundary_observation(
            evaluation,
            decision,
            round_index=round_index,
            evidence_role="balanced_outer_confirmation",
        )
        archived = admit_feasible_evidence(
            self.trial["feasible_archive"],
            params=entry["ranking"]["optimized_params"],
            decision=decision,
            source={
                "label": normalized["label"],
                "round": round_index,
                "worker_key": "balanced_two_worker_confirmation",
            },
        )
        self.log(
            "algorithm_c_outer_near_gate_confirmation_decision",
            round_index=round_index,
            identity=identity,
            source_labels=[row.get("label") for row in candidate_rows],
            confirmation=confirmation,
            canonical_decision=decision,
            feasible_candidate_archived=archived,
            search_replays_added=len(valid_rows),
            qps_used_for_inner_scoring=False,
            qps_used_for_outer_interval_control=True,
            external_baseline_information_used=False,
        )
        self.save_state()
        return (evaluation if decision.get("accepted") is True else None), len(valid_rows)

    def account_physical_replay_rows(self, rows, *, round_index, batch_kind):
        accounting = super().account_physical_replay_rows(rows, round_index=round_index, batch_kind=batch_kind)
        # During super().__init__, interrupted-wave recovery can enter this hook.
        trial = self.state.get("budgeted_trial")
        if isinstance(trial, dict):
            labels = set(trial.setdefault("accounted_labels", []))
            for row in rows:
                label = str(row.get("label") or "")
                if label and label not in labels:
                    labels.add(label)
                    execution_role = getattr(self, "execution_role", None)
                    search_batch_kind = getattr(
                        execution_role, "search_batch_kind", "trial_search"
                    )
                    if batch_kind == search_batch_kind:
                        trial["search_replays"] += 1  # Failed physical attempts consume budget too.
                        if base.physical_valid(row):
                            trial["search_valid_replays"] = int(
                                trial.get("search_valid_replays") or 0
                            ) + 1
                        else:
                            trial["search_technical_failures"] = int(
                                trial.get("search_technical_failures") or 0
                            ) + 1
            trial["accounted_labels"] = sorted(labels)
        return accounting

    def coverage_vectors(self, incumbent_params: Mapping[str, float]) -> List[Dict[str, float]]:
        """A bounded deterministic design in the selected, previously retained region."""
        override = getattr(self, "_adaptive_coverage_override", None)
        if isinstance(override, list):
            return copy.deepcopy(override)
        count = int(self.policy.get("region_plan_probes", 32))
        if not 5 <= count <= 64:
            raise ValueError("region_plan_probes must be between 5 and 64")
        names = list(base.ACTIVE_PARAMS)
        box = self.box()
        center = {k: base._parameter_value_at_fraction(k, *box[k], 0.5) for k in names}
        vectors = [center]
        # Sparse alternatives around an in-region center; reference calibration
        # remains anchored at the measured incumbent, even if it is outside this region.
        for k in names:
            for fraction in (0.2, 0.8):
                row = dict(center)
                row[k] = base._parameter_value_at_fraction(k, *box[k], fraction)
                vectors.append(row)
        primes = (2, 3, 5, 7, 11, 13, 17)
        index = 1
        while len(vectors) < count:
            fractions = []
            for prime in primes:
                n, factor, value = index, 1.0 / prime, 0.0
                while n:
                    value += (n % prime) * factor
                    n //= prime
                    factor /= prime
                fractions.append(value)
            vectors.append({k: base._parameter_value_at_fraction(k, *box[k], f)
                            for k, f in zip(names, fractions)})
            index += 1
        return vectors[:count]

    def accept(self, evaluation: Dict[str, Any], round_index: int) -> None:
        before = self.box()
        reference = self.state.get("incumbent_validation") or self.state.get("canonical_baseline") or {}
        reference_target = float(((reference.get("resources") or {}).get(self.target)) or 0.0)
        candidate_target = float(
            ((evaluation.get("normalized") or {}).get("resources") or {}).get(self.target) or 0.0
        )
        measured_resource_gain = (
            max(0.0, min(1.0, (reference_target - candidate_target) / reference_target))
            if reference_target > 0.0 and math.isfinite(candidate_target)
            else 0.0
        )
        ranking = (evaluation.get("raw") or {}).get("candidate_ranking") or {}
        optimistic_resource_gain = min(
            1.0,
            measured_resource_gain + self._ranking_uncertainty([ranking]),
        )
        super().accept(evaluation, round_index)
        self.check_interval_shrink(before)
        self.trial.pop("expansion_parent_box", None)
        retained = self.box()
        known = {item["id"] for item in self.trial["region_queue"]}
        known.update(self.trial["visited_regions"])
        added = []
        for region in complement_boxes(before, retained):
            identity = box_identity(region)
            if identity not in known:
                self.trial["region_queue"].append({
                    "id": identity,
                    "box": region,
                    "created_round": round_index,
                    "optimistic_resource_gain": optimistic_resource_gain,
                    "optimistic_gain_source": "measured_resource_gain_plus_c_model_uncertainty",
                    "qps_used": False,
                })
                known.add(identity)
                added.append(identity)
        self.trial["active_region_mode"] = "gradient"
        self.trial["stagnation"] = 0
        self.trial["recovery_stage"] = 0
        self.trial["stage_rejected_batches"] = 0
        self.trial["consecutive_rejected_batches"] = 0
        self.trial["consecutive_no_progress_batches"] = 0
        self.trial["consecutive_empty_numeric_windows"] = 0
        self.trial["numeric_pending_rankings"] = []
        self.trial["outer_boundary_refinement_attempts_since_shrink"] = 0
        self.trial["actual_parameter_widths"] = actual_widths(retained, dict(base.DEFAULT_RANGES))
        self.reset_interval_misses()
        self.log("algorithm_c_complement_regions_retained", round_index=round_index,
                 added_region_ids=added, pending_regions=len(self.trial["region_queue"]),
                 actual_parameter_widths=self.trial["actual_parameter_widths"],
                 global_optimum_certified=False)
        self.save_state()

    def revisit_region(self, round_index: int) -> bool:
        queue = self.trial["region_queue"]
        if not queue:
            # No accepted shrink yet: try bounded full-domain coverage after
            # local stagnation, rather than endlessly repeating the same ray.
            if self.trial["active_region_mode"] == "coverage":
                return False
            self.trial["active_region_mode"] = "coverage"
            self.trial["stagnation"] = 0
            self.save_state()
            return True
        region = max(
            queue,
            key=lambda item: (
                float(item.get("optimistic_resource_gain") or 0.0),
                -int(item.get("created_round") or 0),
            ),
        )
        queue.remove(region)
        # A visited region is retained as dormant, not certified inferior.
        self.trial.setdefault("dormant_regions", []).append({"box": self.box(), "round": round_index})
        self.ranges = {k: base.ParameterRange(*v) for k, v in region["box"].items()}
        self.trial["numeric_pending_rankings"] = []
        self.trial["visited_regions"].append(region["id"])
        self.trial["active_region_mode"] = "coverage"
        self.trial["stagnation"] = 0
        self.log("algorithm_c_region_revisited", round_index=round_index, region=region,
                 search_incumbent_unchanged=True, extra_physical_budget_granted=False,
                 qps_used_for_region_priority=False)
        self.save_state()
        return True

    def finalist_ranking(self, finalist: Mapping[str, Any]) -> Dict[str, Any]:
        identity = finalist["identity"]
        with self.log_path.open() as stream:
            for line in stream:
                event = json.loads(line)
                if event.get("event") != "algorithm_c_candidate_replay":
                    continue
                row = event["validation"]
                ranking = row.get("candidate_ranking") or {}
                if base.physical_valid(row) and evidence_identity(ranking.get("optimized_params") or {}) == identity:
                    return copy.deepcopy(ranking)
        raise RuntimeError("finalist lacks original real replay evidence")

    def confirm_finalist(self, finalist: Mapping[str, Any]) -> Dict[str, Any]:
        """Two repetitions per worker, followed by two default controls per worker."""
        ranking = self.finalist_ranking(finalist)
        ranking["confirmation_only"] = True
        confirmations, controls = [], []
        for repetition in (1, 2):
            if self.stop_requested:
                return {"passed": False, "reason": "interrupted_during_finalization"}
            confirmations.extend(self.replay_batch([copy.deepcopy(ranking) for _ in self.workers],
                round_index=int(self.state["round_index"]),
                batch_kind=self.execution_role.finalist_batch_kind(repetition)))
        default = {"optimized_params": dict(self.initial_params), "confirmation_only": True,
                   "rank": 1, "active_subset": list(base.ACTIVE_PARAMS), "candidate_origin": "post_baseline_control"}
        for repetition in (1, 2):
            if self.stop_requested:
                return {"passed": False, "reason": "interrupted_during_post_baseline"}
            controls.extend(self.replay_batch([copy.deepcopy(default) for _ in self.workers],
                round_index=int(self.state["round_index"]),
                batch_kind=self.execution_role.post_baseline_batch_kind(repetition)))
        if len(confirmations) != 4 or len(controls) != 4 or not all(base.physical_valid(x) for x in confirmations + controls):
            return {"passed": False, "reason": "incomplete_or_technically_invalid_validation"}
        decisions, qps, ratios, drifts = [], [], [], {}
        for worker in self.workers:
            key = base.worker_key(worker)
            pre = self.state["baseline_by_worker"][key]["db_qps"]
            post = [r["db_qps"] for r in controls if r["worker_key"] == key]
            if pre <= 0 or len(post) != 2 or any(x <= 0 for x in post):
                return {"passed": False, "reason": "invalid_bracket_baseline"}
            post_mean = math.exp(sum(math.log(x) for x in post) / len(post))
            drifts[key] = post_mean / pre - 1.0
            bracket = math.sqrt(pre * post_mean)
            for raw in confirmations:
                if raw["worker_key"] != key:
                    continue
                normalized = base.normalize_to_worker_baseline(raw, self.state["baseline_by_worker"][key],
                    self.state["canonical_baseline"], self.capacity)
                normalized["qps_noise_model"] = copy.deepcopy(self.state.get("qps_noise_model") or {})
                decisions.append(base.outer_semantic_acceptance_details(normalized, self.state["canonical_baseline"],
                    self.state["canonical_baseline"], target=self.target, cfg=self.cfg, consecutive_missed_outer_rounds=0))
                qps.append(normalized["db_qps"])
                ratios.append(raw["db_qps"] / bracket)
        semantic_ok = all(d.get("accepted") is True for d in decisions)
        drift_ok = all(abs(x) <= float(self.policy.get("baseline_drift_limit", 0.15)) for x in drifts.values())
        return {"passed": semantic_ok and drift_ok, "semantic_passed": semantic_ok, "drift_passed": drift_ok,
                "baseline_drift": drifts, "decisions": decisions,
                "normalized_qps_geomean": math.exp(sum(map(math.log, qps)) / len(qps)),
                "bracketed_speedup_geomean": math.exp(sum(map(math.log, ratios)) / len(ratios)),
                "raw_finalist_qps": [r["db_qps"] for r in confirmations],
                "raw_post_baseline_qps": [r["db_qps"] for r in controls],
                "physical_replays": 8, "aggregation": "balanced_worker_repetition_geometric_mean"}

    def finish_search_only(self, reason: str) -> None:
        """finish search only."""
        finalist = best_feasible(self.trial["feasible_archive"])
        baseline = self.state.get("canonical_baseline") or {}
        baseline_qps = float(baseline.get("db_qps") or 0.0)
        baseline_available = bool(
            self.state.get("incumbent_params") and math.isfinite(baseline_qps) and baseline_qps > 0.0
            and int(self.state.get("accepted") or 0) == 0
        )
        self.trial["search_stop_reason"] = reason
        self.trial["search_finished_wall_s"] = self.wall_s()
        receipt = self.restore_clean_baseline(reason="search_only_final_restore")
        completed = bool(
            reason == "adaptive_convergence"
            and (finalist is not None or baseline_available)
            and not self.stop_requested
            and int(self.trial.get("search_technical_failures") or 0) == 0
            and receipt.get("passed") is True
        )
        selection = {
            "contract": "algorithm_c_search_observed_final_selection_v1",
            "params": copy.deepcopy(finalist["params"]) if finalist else (
                copy.deepcopy(self.state["incumbent_params"]) if baseline_available else None
            ),
            "observed_qps": finalist["observed_qps"] if finalist else (baseline_qps if baseline_available else None),
            "observations": copy.deepcopy(finalist["observations"]) if finalist else [],
            "aggregation": ("geometric_mean_of_archived_feasible_search_observations" if finalist
                            else "canonical_baseline_no_accepted_improvement"),
            "selection_source": "search_archive" if finalist else "baseline",
            "independently_validated": False,
        }
        self.trial.update({
            "wall_s": self.wall_s(),
            "finalist": finalist,
            "selection": selection,
            "validation": {
                "performed": False, "passed": False, "physical_replays": 0,
                "reason": "final_validation_removed_by_user",
            },
            "clean_restore_receipt": receipt,
            "normal_fixed_budget_used": False,
            "wall_budget_met": None,
            "search_budget_met": None,
            "operational_safety_guard_reached": reason in {
                "candidate_generation_exhausted_without_physical_evidence",
                "numeric_windows_exhausted_without_physical_evidence",
                "interval_shrink_progress_failure",
            },
            "algorithmic_convergence_reached": reason == "adaptive_convergence",
            "search_completed_successfully": completed,
            "valid_for_quality_or_efficiency_claim": False,
            "global_convergence_claimed": False,
        })
        self.state["phase"] = f"{self.execution_role.phase_prefix}_finished_clean"
        self.state["final_champion"] = copy.deepcopy(selection) if completed else None
        self.save_state()
        base.write_json(self.output_dir / self.execution_role.result_filename, {
            "contract": CONTRACT,
            "scene": self.algorithm_cfg["scene"],
            "status": "search_completed" if completed else "search_not_completed",
            "result_mode": "search_observation_only",
            "final_validation_enabled": False,
            "postterminal_abc_validation_enabled": False,
            "timing_scope": "controller_initialization_through_restore_without_final_validation",
            "trial": self.trial,
            "policy": self.policy,
            "adaptive_policy_audit": adaptive_policy_audit(self.adaptive_policy),
            "external_baseline_information_used_at_runtime": False,
            "four_scene_win_claimed": False,
        })
        self.log(
            self.execution_role.finished_event,
            reason=reason, wall_s=self.trial["wall_s"],
            search_completed_successfully=completed,
            final_validation_performed=False,
            final_validation_replays=0,
            search_replays=self.trial["search_replays"],
            observed_final_qps=selection["observed_qps"],
        )

    def finish_trial(self, reason: str) -> None:
        if self.adaptive_policy is not None:
            self.finish_search_only(reason)
            return
        self.trial["search_stop_reason"] = reason
        self.trial["search_finished_wall_s"] = self.wall_s()
        finalist = best_feasible(self.trial["feasible_archive"])
        self.state["phase"] = f"{self.execution_role.phase_prefix}_finalizing"
        self.save_state()
        validation = {"passed": False, "reason": "no_feasible_finalist"}
        convergence_valid = reason == "adaptive_convergence" or self.adaptive_policy is None
        if finalist and not self.stop_requested and convergence_valid:
            validation = self.confirm_finalist(finalist)
        receipt = self.restore_clean_baseline(
            reason=f"{self.execution_role.phase_prefix}_final_restore"
        )
        wall = self.wall_s()
        budget_evidence = {
            "wall_budget_met": wall < self.budget.max_wall_s,
            "search_budget_met": (
                self.trial["search_replays"] <= self.budget.max_search_replays
            ),
        } if self.budget is not None else {
            "wall_budget_met": None,
            "search_budget_met": None,
            "normal_fixed_budget_used": False,
            "operational_safety_guard_reached": (
                reason == "wide_operational_guard_reached_without_algorithmic_convergence"
            ),
        }
        self.trial.update({
            "wall_s": wall,
            "finalist": finalist,
            "validation": validation,
            "clean_restore_receipt": receipt,
            **budget_evidence,
            "active_time_contract": ACTIVE_TIME_CONTRACT,
            "host_outage_excluded_s": float(self.trial.get("host_outage_excluded_s") or 0.0),
            "algorithmic_convergence_reached": reason == "adaptive_convergence",
            "valid_for_quality_or_efficiency_claim": bool(
                validation.get("passed") and convergence_valid
            ),
            "global_convergence_claimed": False,
        })
        self.state["phase"] = f"{self.execution_role.phase_prefix}_finished_clean"
        self.state["final_champion"] = ({"params": finalist["params"], "validation": validation,
                                         "contract": CONTRACT} if finalist and validation["passed"] else None)
        self.save_state()
        report = {"contract": CONTRACT, "scene": self.algorithm_cfg["scene"],
                  "status": "validated" if validation["passed"] else "not_validated",
                  "timing_scope": "controller_initialization_through_final_restore_including_baseline_planning_wait_replay_validation",
                  "trial": self.trial, "policy": self.policy,
                  "adaptive_policy_audit": (
                      adaptive_policy_audit(self.adaptive_policy)
                      if self.adaptive_policy is not None else None
                  ),
                  "external_baseline_information_used_at_runtime": False,
                  "historical_timing_comparison_is_provisional": (
                      self.execution_role.historical_comparison_provisional
                  ),
                  "four_scene_win_claimed": False}
        base.write_json(self.output_dir / self.execution_role.result_filename, report)
        self.log(self.execution_role.finished_event, reason=reason, wall_s=wall,
                 wall_budget_met=self.trial["wall_budget_met"], validation_passed=validation["passed"],
                 search_replays=self.trial["search_replays"], global_convergence_claimed=False,
                 external_baseline_information_used_at_runtime=False)

    def run_startup_warmup(self) -> Dict[str, Any]:
        """Run one QPS-blind, synchronized replay before measured baseline waves."""
        policy = self.policy.get("startup_warmup") or {}
        if policy.get("enabled") is not True:
            return {"enabled": False, "completed": True}
        if policy.get("contract") != "algorithm_c_full_replay_startup_warmup_v1":
            raise ValueError("unexpected startup warmup contract")
        if self.state.get("startup_warmup"):
            raise RuntimeError("formal startup warmup cannot be reused or resumed")
        started = time.perf_counter()
        self.state["phase"] = "startup_warmup"
        self.save_state()
        prepared = self.prepare_parallel_baseline_workers(0)
        barrier = base.wait_for_algorithm_c_baseline_cohort(
            scene=str(self.algorithm_cfg.get("scene") or self.target),
            repetition=0,
            stop_requested=lambda: self.stop_requested,
        )
        rows: List[Dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=self.replay_parallelism) as executor:
            futures = {}
            for worker, restore in prepared:
                key = base.worker_key(worker)
                future = executor.submit(
                    base.run_real_validation_safe,
                    self.cfg,
                    self.initial_params,
                    output_dir=self.output_dir / "startup_warmup" / key,
                    label=f"algorithm_c_startup_warmup_{key}",
                    worker=base.worker_with_prepared_restore(worker),
                    force_new_replay=True,
                )
                futures[future] = (key, restore)
            for future in as_completed(futures):
                key, restore = futures[future]
                row = future.result()
                row["worker_key"] = key
                row["restore"] = restore
                row["event"] = "algorithm_c_startup_warmup_validation"
                rows.append(row)
        qualification = base.apply_ssd_wave_qualification(
            rows, target=self.target, phase="formal_baseline_startup_warmup", cfg=self.cfg,
        )
        wave_valid = bool(
            len(rows) == len(self.workers)
            and qualification.get("passed") is True
            and all(base.physical_valid(row) for row in rows)
        )
        completion = (
            base.wait_for_algorithm_c_baseline_cohort_completion(
                scene=str(self.algorithm_cfg.get("scene") or self.target),
                repetition=0,
                wave_valid=True,
                stop_requested=lambda: self.stop_requested,
            )
            if wave_valid else {"passed": False, "reason": "local_warmup_wave_invalid"}
        )
        _, counts = base.account_new_physical_rows(self.state, rows, 0)
        audit = {
            "contract": policy["contract"],
            "enabled": True,
            "completed": bool(wave_valid and completion.get("passed") is True),
            "worker_count": len(rows),
            "duration_s": time.perf_counter() - started,
            "start_barrier": barrier,
            "completion_barrier": completion,
            "ssd_qualification": qualification,
            "physical_accounting": counts,
            "rows": rows,
            "qps_used_for_baseline_or_candidate_scoring": False,
            "historical_qps_used": False,
        }
        self.state["startup_warmup"] = audit
        self.trial["startup_warmup_wall_s"] = audit["duration_s"]
        self.save_state()
        self.log("algorithm_c_startup_warmup_completed", startup_warmup=audit)
        if not audit["completed"]:
            self.state["phase"] = "startup_warmup_failed_clean"
            self.save_state()
            self.restore_clean_baseline(reason="startup_warmup_failed")
            raise RuntimeError("startup warmup failed: incomplete physical cohort")
        return audit

    def verify_baseline_repeatability(self) -> Dict[str, Any]:
        """Audit baseline QPS variation; optionally continue with a noisy baseline."""
        gate = self.policy.get("baseline_repeatability_gate") or {}
        if gate.get("enabled") is not True:
            return {"enabled": False, "passed": True}
        mode = str(gate.get("mode") or "hard_stop")
        if mode not in {"hard_stop", "advisory"}:
            raise ValueError(f"invalid baseline repeatability mode: {mode}")
        limit = float(gate.get("maximum_worker_repeat_ratio") or 1.10)
        if not math.isfinite(limit) or limit < 1.0:
            raise ValueError("invalid baseline repeatability limit")
        model = self.state.get("qps_noise_model") or {}
        ratios = model.get("worker_repeat_ratio") or {}
        expected_workers = len(self.workers)
        errors = []
        if model.get("ready") is not True or len(ratios) != expected_workers:
            errors.append("baseline_repeatability_evidence_incomplete")
        for worker, value in ratios.items():
            ratio = float(value)
            if not math.isfinite(ratio) or ratio > limit + 1e-12:
                errors.append(f"unstable_worker:{worker}:{ratio:.6f}>{limit:.6f}")
        search_allowed = not errors or (
            mode == "advisory" and model.get("ready") is True and len(ratios) == expected_workers
        )
        audit = {
            "enabled": True,
            "passed": not errors,
            "mode": mode,
            "search_allowed": search_allowed,
            "maximum_worker_repeat_ratio": limit,
            "worker_repeat_ratio": copy.deepcopy(dict(ratios)),
            "expected_workers": expected_workers,
            "errors": errors,
            "uses_historical_qps": False,
        }
        self.state["baseline_repeatability_gate"] = audit
        if not search_allowed:
            self.state["phase"] = "baseline_repeatability_failed_clean"
            self.save_state()
            self.restore_clean_baseline(reason="baseline_repeatability_failed")
            raise RuntimeError(f"baseline repeatability failed: {errors}")
        self.save_state()
        if errors:
            self.log("algorithm_c_baseline_repeatability_advisory", baseline_repeatability_gate=audit)
        return audit

    def run(self) -> None:
        finished_phase = f"{self.execution_role.phase_prefix}_finished_clean"
        finalizing_phase = f"{self.execution_role.phase_prefix}_finalizing"
        if self.state.get("phase") == finished_phase:
            return
        if self.state.get("phase") == finalizing_phase:
            raise RuntimeError("interrupted finalization requires evidence reconciliation; refusing duplicate validation")
        started = time.perf_counter()
        self.run_startup_warmup()
        self.baseline()
        self.trial["baseline_wall_s"] += time.perf_counter() - started
        self.verify_baseline_execution_domain()
        self.verify_baseline_repeatability()
        self.restore_clean_baseline(reason=f"{self.execution_role.phase_prefix}_post_baseline")
        self.state["formal_started"] = True
        self.state.setdefault("formal_started_at_epoch_s", time.time())
        self.save_state()
        reason = "outer_round_safety_limit"
        while (
            int(self.state["round_index"])
            < min(self.max_rounds, self.effective_outer_round_limit())
            and not self.stop_requested
        ):
            stop = self.budget_stop()
            if stop:
                reason = stop
                break
            adaptive_decision = (
                self.next_adaptive_decision()
                if self.adaptive_policy is not None else None
            )
            if adaptive_decision is not None and adaptive_decision.converged:
                reason = "adaptive_convergence"
                break
            if adaptive_decision is not None and adaptive_decision.action == "shrink_incumbent":
                try:
                    self.shrink_after_two_misses()
                except RuntimeError as exc:
                    self.log("algorithm_c_interval_progress_failure", error=str(exc),
                             algorithmic_convergence=False)
                    reason = "interval_shrink_progress_failure"
                    break
                continue
            if (
                adaptive_decision is not None
                and adaptive_decision.action == "operational_safety_stop_invalid"
            ):
                reason = adaptive_decision.reason
                break
            round_index = int(self.state["round_index"]) + 1
            self.state["round_index"] = round_index
            self.state["phase"] = f"{self.execution_role.phase_prefix}_planning"
            self.save_state()
            started = time.perf_counter()
            confirmation_entry: Optional[Dict[str, Any]] = None
            if adaptive_decision is not None:
                self.enter_recovery_action(adaptive_decision.action)
            if (
                adaptive_decision is not None
                and adaptive_decision.action == "outer_near_gate_confirmation"
            ):
                pending = self.trial.get("pending_outer_confirmation")
                if not isinstance(pending, Mapping):
                    reason = "adaptive_outer_confirmation_queue_inconsistent"
                    break
                confirmation_entry = copy.deepcopy(dict(pending))
                self.trial["pending_outer_confirmation"] = None
                rankings = self.outer_confirmation_candidates(confirmation_entry)
            elif adaptive_decision is not None and adaptive_decision.action == "first_miss_expansion":
                rankings = self.generate_first_miss_rankings(round_index)
            elif adaptive_decision is not None and adaptive_decision.action == "gradient_step_expansion":
                rankings = self.generate_recovered_gradient_rankings(round_index)
            elif adaptive_decision is not None and adaptive_decision.action == "retained_region_revisit":
                if not self.revisit_region(round_index):
                    reason = "adaptive_region_queue_inconsistent"
                    break
                rankings = self.generate_global_plan_coverage_rankings(round_index)
            elif adaptive_decision is not None and adaptive_decision.action in {
                "initial_global_coverage",
                "uncertainty_expansion",
                "frontier_diversification",
            }:
                self.trial["active_region_mode"] = "coverage"
                rankings = self.generate_global_plan_coverage_rankings(round_index)
            elif (
                adaptive_decision is not None
                and adaptive_decision.action == "frontier_numeric_refinement"
            ):
                self.trial["active_region_mode"] = "coverage_numeric_refinement"
                rankings = self.generate_dynamic_numeric_refinement_rankings(
                    round_index
                )
            elif self.trial["active_region_mode"] == "coverage" and self.adaptive_policy is None:
                rankings = self.generate_global_plan_coverage_rankings(round_index)
            else:
                try:
                    rankings = self.generate_rankings(round_index)
                except base.IncompleteSubsetOptimizationError as exc:
                    if (
                        getattr(exc, "plan_space_saturation_evidence", False) is not True
                        or getattr(exc, "technical_candidate_generation_failure", False) is True
                    ):
                        raise
                    # Preserve ordinary Top-5 recovery before declaring a
                    # genuinely exhausted partial pool. This changes only
                    # offline probes within the current interval, not miss.
                    try:
                        rankings = self.expand_behavior_cluster_rankings(round_index, exc)
                    except base.IncompleteSubsetOptimizationError as expansion_error:
                        if (
                            getattr(expansion_error, "plan_space_saturation_evidence", False) is not True
                            or getattr(expansion_error, "technical_candidate_generation_failure", False) is True
                        ):
                            raise
                        rankings = self.merge_exhausted_partial_topk(
                            list(getattr(expansion_error, "partial_topk_errors", None) or [exc, expansion_error]),
                            round_index=round_index,
                        )
            self.trial["planning_s"] += time.perf_counter() - started
            if self.budget_stop() or self.stop_requested:
                reason = self.budget_stop() or "signal_stop"
                break
            if adaptive_decision is not None:
                replay_limit = adaptive_decision.requested_candidates
                remaining = None
            else:
                remaining = self.budget.max_search_replays - self.trial["search_replays"]
                replay_limit = min(5, remaining)
            admission_started = time.perf_counter()
            if confirmation_entry is not None:
                candidates = rankings
                admission = {
                    "contract": OUTER_CONFIRMATION_CONTRACT,
                    "selected_count": len(candidates),
                    "repeated_candidate_identity": confirmation_entry["identity"],
                    "exact_replay_deduplication_bypassed_for_statistical_confirmation": True,
                    "all_replays_count_as_formal_search": True,
                    "qps_used_for_inner_scoring": False,
                    "external_baseline_information_used": False,
                }
            else:
                candidates, admission = self.select_outer_admitted_candidates(
                    rankings,
                    round_index=round_index,
                    batch_kind=self.execution_role.search_batch_kind,
                    limit=replay_limit,
                )
                if (
                    adaptive_decision is not None
                    and adaptive_decision.action in {"gradient_search", "gradient_step_expansion"}
                    and len(candidates) < replay_limit
                    and admission.get("exact_valid_replay_excluded_count", 0) > 0
                ):
                    candidates, backfill = self.backfill_replayed_identity_top5(
                        rankings, candidates, admission, round_index=round_index,
                        required_candidates=replay_limit,
                        expanded_rankings=(
                            rankings if adaptive_decision.action == "gradient_step_expansion" else None
                        ),
                    )
                    admission["adaptive_gradient_backfill"] = backfill
            self.trial["planning_s"] += time.perf_counter() - admission_started
            self.log("algorithm_c_budgeted_admission", round_index=round_index, admission=admission,
                     planning_s=self.trial["planning_s"], remaining_search_replays=remaining,
                     adaptive_decision=(asdict(adaptive_decision) if adaptive_decision else None))
            self.state["phase"] = f"{self.execution_role.phase_prefix}_replay"
            self.save_state()
            started = time.perf_counter()
            rows = self.replay_batch(
                candidates,
                round_index=round_index,
                batch_kind=self.execution_role.search_batch_kind,
            )
            self.trial["replay_batch_wall_s"] += time.perf_counter() - started
            if self.stop_requested:
                break
            if len(rows) != len(candidates) or any(not base.physical_valid(row) for row in rows):
                # Do not accept or archive the successful half of a failed batch.
                self.trial["search_technical_failures"] = max(
                    1, int(self.trial.get("search_technical_failures") or 0),
                )
                reason = "technical_incomplete_replay_batch" if len(rows) != len(candidates) else "technical_batch_failure"
                break
            prior_best = best_feasible(self.trial["feasible_archive"])
            if confirmation_entry is not None:
                selected, valid_count = self.evaluate_outer_confirmation_batch(
                    confirmation_entry,
                    rows,
                    round_index=round_index,
                )
            else:
                selected, valid_count = self.evaluate_batch(
                    rows,
                    round_index=round_index,
                    batch_kind=self.execution_role.search_batch_kind,
                )
            current_best = best_feasible(self.trial["feasible_archive"])
            archive_improved = bool(current_best and (
                prior_best is None or current_best["observed_qps"] > prior_best["observed_qps"]
            ))
            self.record_adaptive_batch(candidates, rows, selected, archive_improved=archive_improved)
            if rows and not valid_count:
                reason = "technical_batch_failure"
                break
            if selected is not None:
                self.accept(selected, round_index)
            elif rows:
                self.trial["stagnation"] += 1
                self.state["consecutive_missed_outer_rounds"] += 1
                if self.adaptive_policy is not None:
                    self.restore_rejected_expansion()
                if self.adaptive_policy is None and (self.trial["stagnation"] >= 2 or not candidates):
                    if not self.revisit_region(round_index):
                        reason = "sampled_regions_stalled_not_global_convergence"
                        break
            self.save_state()
        self.finish_trial("signal_stop" if self.stop_requested else reason)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--target", choices=("cpu", "memory", "io"), required=True)
    args = parser.parse_args()
    controller = BudgetedLaneController(args.config, args.output_dir, args.target)
    def stop(_signum, _frame):
        controller.stop_requested = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    controller.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
