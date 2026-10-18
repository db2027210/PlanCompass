#!/usr/bin/env python3
"""Run PostgreSQL Algorithm B inside the proven four-lane outer framework.

Algorithm B changes only the offline candidate search: it uses a deterministic
15-point coordinate grid plus exhaustive 2--4 parameter interaction probes.
Physical replay, three-tier outer acceptance, binary range shrink, technical
failure isolation, baseline restoration, and convergence finalization are
inherited unchanged from the Algorithm C lane controller.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import signal
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from double_loop_grid_optimizer import DoubleLoopGridOptimizer, GridInnerLoopConfig
from double_loop_search_common import OuterLoopConfig, ParameterRange as GridParameterRange
from experience_table_subset_optimizer import attach_three_level_fingerprint
from resource_utility import ResourceUtilityCalculator
from shell import run_pg_algorithm_c_scene as framework


ALGORITHM_B_METHOD = "coordinate_grid_algorithm_b_v1"
ALGORITHM_B_SEARCH_CONTRACT = "coordinate_grid15_plus_exhaustive_interactions_2_to_4_v1"
ALGORITHM_B_GRID_POINTS = 15
ALGORITHM_B_MAX_PASSES = 20
ALGORITHM_B_NO_IMPROVEMENT_PATIENCE = 2
ALGORITHM_B_COMBINATION_MIN_SIZE = 2
ALGORITHM_B_COMBINATION_MAX_SIZE = 4
ALGORITHM_B_COMBINATION_VALUES_PER_PARAM = 3
ALGORITHM_B_COMBINATION_MAX_CANDIDATES = 0
ALGORITHM_B_AUDIT_CANDIDATE_LIMIT = 64


def _grid_target_resource(target: str) -> Optional[str]:
    """Map the all-resource no-bottleneck objective to no single target."""
    return None if framework.is_none_target(target) else target


def _utility_calculator(target: str) -> ResourceUtilityCalculator:
    options = copy.deepcopy(framework.formal_inner_scoring_options(target))
    options.pop("directional_scoring_contract_required", None)
    calculator = ResourceUtilityCalculator(**options)
    if framework.is_none_target(target):
        for resource in ("cpu", "memory", "io"):
            resource_range = calculator._resource_target_range(resource)
            expected_role = (
                "non_bottleneck_io_cost"
                if resource == "io"
                else "non_bottleneck"
            )
            if resource_range.get("role") != expected_role:
                raise RuntimeError(
                    f"Algorithm B none-target role mismatch: {resource}"
                )
            direction_ok = (
                calculator.calculate_resource_score(0.50, resource_name=resource)
                < calculator.calculate_resource_score(0.30, resource_name=resource)
                if resource == "io"
                else calculator.calculate_resource_score(
                    0.50, resource_name=resource
                )
                > calculator.calculate_resource_score(
                    0.30, resource_name=resource
                )
            )
            if not (
                math.isclose(float(resource_range["min"]), 0.30)
                and math.isclose(float(resource_range["max"]), 0.50)
                and math.isclose(float(resource_range["hard"]), 0.80)
                and direction_ok
            ):
                raise RuntimeError(
                    f"Algorithm B none-target scoring contract failed: {resource}"
                )
    else:
        audit = calculator.directional_band_target_contract_audit(
            target_resource=target,
        )
        if audit.get("passed") is not True:
            raise RuntimeError(
                f"Algorithm B directional scoring contract failed: {audit}"
            )
    return calculator


def _compact_grid_ranges(
    ranges: Mapping[str, framework.ParameterRange],
) -> Dict[str, GridParameterRange]:
    return {
        name: GridParameterRange(
            min_value=float(ranges[name].min_value),
            max_value=float(ranges[name].max_value),
            discrete_count=ALGORITHM_B_GRID_POINTS,
            priority=index,
        )
        for index, name in enumerate(framework.ACTIVE_PARAMS, start=1)
    }


def _plan_diverse_order(points: Sequence[Any]) -> Tuple[List[Any], int]:
    """Put the best representative of every full plan before refinements."""
    representatives: List[Any] = []
    refinements: List[Any] = []
    seen_plans: set[str] = set()
    for point in points:
        plan = str(getattr(point, "plan_fingerprint", "") or "")
        if plan and plan not in seen_plans:
            seen_plans.add(plan)
            representatives.append(point)
        else:
            refinements.append(point)
    return representatives + refinements, len(seen_plans)


class AlgorithmBLaneController(framework.LaneController):
    """Algorithm B candidate search with the shared authoritative outer loop."""

    def load_or_initialize_state(self) -> Dict[str, Any]:
        self.state_path = self.output_dir / "algorithm_b_state.json"
        self.log_path = self.output_dir / "algorithm_b.jsonl"
        state = super().load_or_initialize_state()
        method = str(state.get("method") or "")
        if self.state_path.is_file() and method not in {"", ALGORITHM_B_METHOD}:
            raise RuntimeError(
                f"Algorithm B state method mismatch: {method!r} != {ALGORITHM_B_METHOD!r}"
            )
        state["method"] = ALGORITHM_B_METHOD
        state["algorithm_variant"] = "B"
        state["candidate_search_contract"] = ALGORITHM_B_SEARCH_CONTRACT
        return state

    def log(self, event: str, **extra: Any) -> None:
        super().log(
            event,
            algorithm_variant="B",
            method=ALGORITHM_B_METHOD,
            candidate_search_contract=ALGORITHM_B_SEARCH_CONTRACT,
            inherited_event_namespace="algorithm_c_outer_framework_compatibility",
            **extra,
        )

    def _grid_estimator(
        self,
        predictor_cfg: Dict[str, Any],
        queries: Sequence[Dict[str, Any]],
        baseline_probe: Mapping[str, Any],
        baseline_real: Mapping[str, float],
        incumbent_params: Mapping[str, float],
        calculator: ResourceUtilityCalculator,
    ) -> Tuple[Any, framework.RatioScaleEstimator]:
        ratio_estimator = framework.RatioScaleEstimator(
            predictor_cfg,
            queries,
            baseline_probe,
            baseline_real,
            incumbent_params,
            self.experience_artifact["resource_prediction_calibration"],
        )

        def estimate(_sql_set: Any, params: Dict[str, float]) -> Dict[str, Any]:
            resources, stats, metadata = ratio_estimator(_sql_set, params)
            score = calculator.calculate_utility_score(resources, self.capacity)
            return {
                "resources": resources,
                "score": score,
                "estimated_score": score,
                "resource_stats": stats,
                "final_estimation_metadata": metadata,
            }

        return estimate, ratio_estimator

    def _ranking_from_grid_point(
        self,
        point: Any,
        ratio_estimator: framework.RatioScaleEstimator,
        calculator: ResourceUtilityCalculator,
        incumbent_params: Mapping[str, float],
        baseline_real: Mapping[str, float],
        initial_score: float,
        round_index: int,
        canonical_order: int,
    ) -> Dict[str, Any]:
        resources, _stats, metadata = ratio_estimator(
            {"contract": "read_only_full_workload_plan_set"},
            copy.deepcopy(point.params),
        )
        final_score = calculator.calculate_utility_score(resources, self.capacity)
        changed = [
            name
            for name in framework.ACTIVE_PARAMS
            if not math.isclose(
                float(point.params[name]),
                float(incumbent_params[name]),
                rel_tol=0.0,
                abs_tol=1e-15,
            )
        ]
        ranking = {
            "subset_id": str(point.subset_id),
            "active_subset": changed,
            "canonical_subset_order": canonical_order,
            "optimized_params": copy.deepcopy(point.params),
            "initial_params": copy.deepcopy(dict(incumbent_params)),
            "initial_resource": copy.deepcopy(dict(baseline_real)),
            "final_resource": copy.deepcopy(resources),
            "initial_score": initial_score,
            "final_score": final_score,
            "score_improvement": final_score - initial_score,
            "iterations": int(point.iterations),
            "log_path": str(
                self.output_dir
                / "grid_logs"
                / f"round_{round_index:03d}"
            ),
            "final_estimation_metadata": copy.deepcopy(metadata),
            "candidate_origin": "algorithm_b_coordinate_grid",
            "probe_kind": "coordinate_or_interaction_grid",
            "used_for_gradient": False,
            "algorithm_b_non_gradient_search": True,
            "algorithm_b_grid_point_id": str(point.subset_id),
            "algorithm_b_estimated_score": float(point.estimated_score),
            "qps_used_for_inner_scoring": False,
        }
        enriched = attach_three_level_fingerprint(ranking, self.capacity)
        enriched["dScore/dU"] = {
            "available": False,
            "reason": "algorithm_b_is_not_gradient_search",
        }
        enriched["T"] = {
            "available": False,
            "reason": "algorithm_b_reuses_prediction_calibration_not_algorithm_c_gradient",
        }
        enriched["confidence"] = {
            "value": float(
                min((metadata.get("plan_model_confidence") or {}).values() or [0.0])
            ),
            "classification": "algorithm_b_plan_prediction_confidence",
        }
        enriched["g_j_semantics"] = (
            "algorithm_b_normalized_parameter_direction_audit_not_gradient"
        )
        enriched["rank"] = canonical_order
        return enriched

    def generate_rankings(
        self,
        round_index: int,
        *,
        parameter_ranges: Optional[Mapping[str, framework.ParameterRange]] = None,
        ranking_scope: str = "incumbent_ranges",
        relative_step_probe_pct: Optional[Sequence[float]] = None,
    ) -> List[Dict[str, Any]]:
        if relative_step_probe_pct is not None:
            self.log(
                "algorithm_b_gradient_expansion_ignored",
                round_index=round_index,
                reason="Algorithm B has no eta or gradient expansion",
                requested_relative_step_probe_pct=list(relative_step_probe_pct),
                physical_workload_invocations=0,
            )
        incumbent_params = {
            name: float(value)
            for name, value in self.state["incumbent_params"].items()
        }
        incumbent_validation = (
            self.state["incumbent_validation"] or self.state["canonical_baseline"]
        )
        baseline_real = {
            name: float((incumbent_validation.get("resources") or {})[name])
            for name in ("cpu", "memory", "io")
        }
        queries, baseline_probe, predictor_cfg = self.queries_and_probe(
            incumbent_params
        )
        calculator = _utility_calculator(self.target)
        initial_score = calculator.calculate_utility_score(
            baseline_real,
            self.capacity,
        )
        estimate, ratio_estimator = self._grid_estimator(
            predictor_cfg,
            queries,
            baseline_probe,
            baseline_real,
            incumbent_params,
            calculator,
        )
        selected_ranges = parameter_ranges or self.ranges
        grid_ranges = _compact_grid_ranges(selected_ranges)
        grid = DoubleLoopGridOptimizer(
            estimate,
            lambda _sql_set, _params: {"resources": baseline_real, "score": initial_score},
            copy.deepcopy(self.capacity),
            grid_config=GridInnerLoopConfig(
                max_workers=int((self.cfg.get("algorithm_b") or {}).get("max_workers") or 8),
                max_passes=ALGORITHM_B_MAX_PASSES,
                no_improvement_patience=ALGORITHM_B_NO_IMPROVEMENT_PATIENCE,
                combination_probe_enabled=True,
                combination_min_size=ALGORITHM_B_COMBINATION_MIN_SIZE,
                combination_max_size=ALGORITHM_B_COMBINATION_MAX_SIZE,
                combination_values_per_param=ALGORITHM_B_COMBINATION_VALUES_PER_PARAM,
                combination_max_candidates=ALGORITHM_B_COMBINATION_MAX_CANDIDATES,
            ),
            outer_config=OuterLoopConfig(
                max_rounds=1,
                real_validation_top_k=framework.BASE_TOP_K,
                target_resource=_grid_target_resource(self.target),
            ),
            verbose=False,
        )
        points, grid_audit = grid._grid_small_loop(
            {"contract": "read_only_full_workload_plan_set"},
            copy.deepcopy(incumbent_params),
            list(framework.ACTIVE_PARAMS),
            grid_ranges,
            round_index=round_index,
            iteration_writer=None,
        )
        ranked_points = [
            point
            for point in grid._deduplicate_and_rank(points)
            if framework.exact_param_signature(point.params)
            != framework.exact_param_signature(incumbent_params)
        ]
        ordered_points, offline_distinct_plan_count = _plan_diverse_order(
            ranked_points
        )

        supported: List[Dict[str, Any]] = []
        excluded_support = 0
        for canonical_order, point in enumerate(ordered_points, start=1):
            ranking = self._ranking_from_grid_point(
                point,
                ratio_estimator,
                calculator,
                incumbent_params,
                baseline_real,
                initial_score,
                round_index,
                canonical_order,
            )
            if framework.target_support_abstention_enabled(
                self.experience_artifact,
                self.target,
            ):
                support = framework.candidate_target_support_assessment(
                    ranking,
                    self.target,
                )
                if (
                    not isinstance(support, Mapping)
                    or support.get("supported") is not True
                    or support.get("support_abstained") is True
                ):
                    excluded_support += 1
                    continue
            supported.append(ranking)
            if len(supported) >= ALGORITHM_B_AUDIT_CANDIDATE_LIMIT:
                break

        plan_counts: Dict[str, int] = {}
        for ranking in supported:
            plan = framework.candidate_full_plan_fingerprint(ranking)
            plan_counts[plan] = plan_counts.get(plan, 0) + 1
        for ranking in supported:
            plan = framework.candidate_full_plan_fingerprint(ranking)
            ranking["numeric_refinement"] = plan_counts.get(plan, 0) > 1
            ranking["numeric_refinement_reason"] = (
                "algorithm_b_exhaustive_grid_plan_space_saturation"
                if ranking["numeric_refinement"]
                else "distinct_full_plan_representative"
            )
        distinct: List[Dict[str, Any]] = []
        refinements: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for ranking in supported:
            plan = framework.candidate_full_plan_fingerprint(ranking)
            if plan and plan not in seen:
                seen.add(plan)
                distinct.append(ranking)
            else:
                refinements.append(ranking)
        rankings = distinct + refinements
        for rank, ranking in enumerate(rankings, start=1):
            ranking["rank"] = rank

        if not rankings:
            self.log(
                "algorithm_b_grid_plan_plateau",
                round_index=round_index,
                ranking_scope=ranking_scope,
                grid_audit=grid_audit,
                excluded_target_support_count=excluded_support,
                physical_workload_invocations=0,
            )
            return []
        self.validate_top5_audit_records(rankings[: framework.BASE_TOP_K])
        self.log(
            "algorithm_b_grid_ranking",
            round_index=round_index,
            ranking_scope=ranking_scope,
            algorithm_b_grid_config={
                "grid_points": ALGORITHM_B_GRID_POINTS,
                "max_passes": ALGORITHM_B_MAX_PASSES,
                "no_improvement_patience": ALGORITHM_B_NO_IMPROVEMENT_PATIENCE,
                "combination_min_size": ALGORITHM_B_COMBINATION_MIN_SIZE,
                "combination_max_size": ALGORITHM_B_COMBINATION_MAX_SIZE,
                "combination_values_per_param": ALGORITHM_B_COMBINATION_VALUES_PER_PARAM,
                "combination_max_candidates": ALGORITHM_B_COMBINATION_MAX_CANDIDATES,
            },
            grid_audit=grid_audit,
            offline_candidate_count=len(ranked_points),
            offline_distinct_plan_count=offline_distinct_plan_count,
            audited_candidate_count=len(supported),
            distinct_audited_plan_count=len(distinct),
            numeric_refinement_count=len(refinements),
            excluded_target_support_count=excluded_support,
            exhaustive_search_completed=True,
            qps_used_for_inner_scoring=False,
            prediction_calibration_reused=True,
            prediction_recalibration_invocations=0,
            top5=copy.deepcopy(rankings[: framework.BASE_TOP_K]),
        )
        return rankings

    def backfill_replayed_identity_top5(
        self,
        rankings: Sequence[Dict[str, Any]],
        admitted: Sequence[Dict[str, Any]],
        admission: Mapping[str, Any],
        *,
        round_index: int,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        combined = list(admitted)
        missing = framework.BASE_TOP_K - len(combined)
        if missing <= 0:
            return combined, {"attempted": False, "reason": "not_applicable"}
        existing = {
            (
                framework.exact_param_signature(row.get("optimized_params") or {}),
                framework.candidate_full_plan_fingerprint(row),
            )
            for row in combined
        }
        remaining = [
            row
            for row in rankings
            if (
                framework.exact_param_signature(row.get("optimized_params") or {}),
                framework.candidate_full_plan_fingerprint(row),
            )
            not in existing
        ]
        backfill, backfill_admission = self.select_outer_admitted_candidates(
            remaining,
            round_index=round_index,
            batch_kind="algorithm_b_grid_identity_backfill",
            limit=missing,
        )
        combined.extend(backfill)
        self.validate_top5_audit_records(combined)
        audit = {
            "attempted": True,
            "strategy": "remaining_ranked_algorithm_b_grid_points",
            "initial_count": len(admitted),
            "backfill_count": len(backfill),
            "final_count": len(combined),
            "outer_admission": backfill_admission,
            "gradient_expansion_used": False,
            "physical_workload_invocations": 0,
        }
        self.log("algorithm_b_grid_identity_backfill", round_index=round_index, **audit)
        return combined, audit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--target", required=True, choices=("cpu", "memory", "io", "none")
    )
    parser.add_argument("--finalize-existing-stability", action="store_true")
    args = parser.parse_args()
    controller = AlgorithmBLaneController(
        args.config.resolve(),
        args.output_dir.resolve(),
        args.target,
    )

    def request_stop(signum: int, _frame: Any) -> None:
        controller.stop_requested = True
        controller.log("algorithm_b_stop_requested", signal=signal.Signals(signum).name)

    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, request_stop)
    if args.finalize_existing_stability:
        if not controller.finalize_existing_stability_evidence():
            raise RuntimeError("existing Algorithm B stability evidence does not pass")
    else:
        controller.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
