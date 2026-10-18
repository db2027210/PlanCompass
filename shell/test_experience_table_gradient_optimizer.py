#!/usr/bin/env python3
"""Module."""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gradient_descent_optimizer import (  # noqa: E402
    GradientDescentParameterOptimizer,
    ParameterRange,
    optimize_with_gradient_descent,
)


class Estimator:
    def __init__(self) -> None:
        self.calls = []

    def __call__(self, sql_set: object, params: Dict[str, float]) -> Dict[str, float]:
        _ = sql_set
        self.calls.append(dict(params))
        return {"cpu": float(params["p"])}


class MetadataEstimator:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(
        self,
        sql_set: object,
        params: Dict[str, float],
    ) -> Tuple[Dict[str, float], Dict[str, Any], Dict[str, Any]]:
        _ = sql_set
        self.calls += 1
        return (
            {"cpu": float(params["p"])},
            {},
            {
                "call": self.calls,
                "optimistic_replay_pruning_input": {"predicted_resource": params["p"]},
            },
        )


def make_optimizer(estimator: Any) -> GradientDescentParameterOptimizer:
    return GradientDescentParameterOptimizer(
        estimator,
        {"cpu": 100.0},
        reward_type="linear",
        verbose=False,
    )


def test_t_direction_drives_multiplicative_sgd_iterations() -> None:
    estimator = Estimator()
    result = make_optimizer(estimator).optimize(
        p_real={"p": 10.0},
        p_start={"p": 99.0},
        sensitivity_table={"p": {"cpu": 1.0}},
        param_ranges={"p": ParameterRange(1.0, 50.0)},
        sql_set=None,
        eta=0.1,
        max_iter=3,
        patience=2,
        gamma=0.5,
    )

    assert result.iterations == 3
    assert len(estimator.calls) == 4

    assert math.isclose(result.optimized_params["p"], 10.0 * 1.125**3, rel_tol=1e-9)
    assert all(math.isclose(row["directions"]["p"], 1.25) for row in result.history)
    assert all(row["accepted_by_resource_utility"] for row in result.history)


def test_only_subset_parameters_change_and_updates_are_joint() -> None:
    calls = []

    def estimator(_: object, params: Dict[str, float]) -> Dict[str, float]:
        calls.append(dict(params))
        return {"cpu": float(params["a"] + params["b"])}

    optimizer = GradientDescentParameterOptimizer(
        estimator,
        {"cpu": 100.0},
        reward_type="linear",
        verbose=False,
    )
    result = optimizer.optimize(
        p_real={"a": 1.0, "b": 2.0, "fixed": 9.0},
        sensitivity_table={"a": {"cpu": 1.0}, "b": {"cpu": 2.0}},
        param_ranges={"a": ParameterRange(0.1, 5.0), "b": ParameterRange(0.1, 10.0)},
        sql_set=None,
        active_params=["a", "b"],
        eta=0.1,
        max_iter=1,
        patience=1,
        gamma=0.5,
    )

    assert math.isclose(result.optimized_params["a"], 1.125)
    assert math.isclose(result.optimized_params["b"], 2.5)
    assert result.optimized_params["fixed"] == 9.0
    assert calls[0] == {"a": 1.0, "b": 2.0, "fixed": 9.0}
    assert math.isclose(calls[1]["a"], 1.125)
    assert math.isclose(calls[1]["b"], 2.5)
    assert calls[1]["fixed"] == 9.0


def test_low_confidence_semantic_prior_scales_direction() -> None:
    estimator = Estimator()
    result = make_optimizer(estimator).optimize(
        p_real={"p": 10.0},
        sensitivity_table={
            "p": {
                "cpu": 1.0,
                "qps": 0.0,
                "confidence": 0.2,
                "evidence_count": 0.0,
            }
        },
        param_ranges={"p": ParameterRange(1.0, 50.0)},
        sql_set=None,
        eta=0.1,
        max_iter=1,
        patience=1,
        gamma=0.5,
    )

    assert math.isclose(result.history[0]["directions"]["p"], 0.25)
    assert math.isclose(result.optimized_params["p"], 10.25)


def test_patience_decays_eta_after_rejections() -> None:
    def estimator(_: object, params: Dict[str, float]) -> Dict[str, float]:
        _ = params
        return {"cpu": 10.0}

    result = make_optimizer(estimator).optimize(
        p_real={"p": 10.0},
        sensitivity_table={"p": {"cpu": 1.0}},
        param_ranges={"p": ParameterRange(1.0, 50.0)},
        sql_set=None,
        eta=0.2,
        max_iter=4,
        patience=2,
        gamma=0.5,
        min_eta=0.0,
    )

    assert [row["eta_after"] for row in result.history] == [0.2, 0.1, 0.05, 0.025]
    assert result.optimized_params == {"p": 10.0}


def test_flat_target_prediction_cannot_compound_parameter_moves() -> None:
    """A score tie is rejected because score is the only inner authority."""

    def estimator(_: object, params: Dict[str, float]) -> Dict[str, float]:
        _ = params
        return {"cpu": 90.0}

    optimizer = GradientDescentParameterOptimizer(
        estimator,
        {"cpu": 100.0},
        reward_type="linear",
        verbose=False,
    )
    result = optimizer.optimize(
        p_real={"p": 10.0},
        sensitivity_table={"p": {"cpu": 1.0}},
        param_ranges={"p": ParameterRange(1.0, 50.0)},
        sql_set=None,
        eta=0.1,
        max_iter=3,
        patience=1,
        gamma=0.5,
        target_bottleneck_resources=["cpu"],
    )

    assert result.optimized_params == {"p": 10.0}
    assert all(not row["accepted_by_resource_utility"] for row in result.history)
    assert all(
        row["acceptance_rule"]
        == "candidate_score_strictly_above_incumbent_score"
        for row in result.history
    )


def test_compatibility_wrapper_rejects_score_tie() -> None:
    """The compatibility wrapper uses the same strict score comparison."""

    def estimator(_: object, params: Dict[str, float]) -> Dict[str, float]:
        _ = params
        return {"cpu": 90.0}

    result = optimize_with_gradient_descent(
        resource_estimation_func=estimator,
        capability={"cpu": 100.0},
        p_real={"p": 10.0},
        p_candidate={"p": 10.0},
        sensitivity_table={"p": {"cpu": 1.0}},
        param_ranges={"p": ParameterRange(1.0, 50.0)},
        sql_set=None,
        eta=0.1,
        max_iter=2,
        patience=1,
        gamma=0.5,
        target_bottleneck_resources=["cpu"],
        verbose=False,
    )

    assert result.optimized_params == {"p": 10.0}
    assert all(not row["accepted_by_resource_utility"] for row in result.history)


def test_role_aware_band_score_guides_direction_and_acceptance() -> None:
    """The existing score supplies both gradient and strict acceptance."""

    def estimator(_: object, params: Dict[str, float]) -> Dict[str, float]:
        value = float(params["p"])
        return {
            "cpu": 77.5 + value,
            "memory": 50.0 - value,
        }

    result = optimize_with_gradient_descent(
        resource_estimation_func=estimator,
        capability={"cpu": 100.0, "memory": 100.0},
        p_real={"p": 10.0},
        p_candidate={"p": 10.0},
        sensitivity_table={"p": {"cpu": 1.0, "memory": -1.0}},
        param_ranges={"p": ParameterRange(1.0, 20.0)},
        sql_set=None,
        eta=0.1,
        max_iter=1,
        patience=1,
        gamma=0.5,
        target_bottleneck_resources=["cpu"],
        score_mode="band_target",
        resource_target_ranges={
            "cpu": {
                "min_pct": 85.0,
                "max_pct": 90.0,
                "hard_overload_pct": 95.0,
                "role": "target_bottleneck",
            },
            "memory": {
                "min_pct": 30.0,
                "max_pct": 50.0,
                "hard_overload_pct": 90.0,
                "role": "non_bottleneck",
            },
        },
        verbose=False,
    )

    row = result.history[0]
    assert math.isclose(row["directions"]["p"], -5.0, abs_tol=1e-6)
    assert result.final_score > result.initial_score
    assert math.isclose(result.optimized_params["p"], 5.0, abs_tol=1e-6)
    assert row["accepted_by_resource_utility"]
    assert row["acceptance_rule"] == "candidate_score_strictly_above_incumbent_score"


def test_role_aware_band_score_has_no_non_target_direction_hard_gate() -> None:
    """A score-improving point is accepted even if one non-target decreases."""

    def estimator(_: object, params: Dict[str, float]) -> Dict[str, float]:
        value = float(params["p"])
        return {
            "cpu": 77.5 + value,
            "memory": 30.0 + value,
        }

    optimizer = GradientDescentParameterOptimizer(
        estimator,
        {"cpu": 100.0, "memory": 100.0},
        score_mode="band_target",
        resource_target_ranges={
            "cpu": {
                "min_pct": 85.0,
                "max_pct": 90.0,
                "hard_overload_pct": 95.0,
                "role": "target_bottleneck",
            },
            "memory": {
                "min_pct": 30.0,
                "max_pct": 50.0,
                "hard_overload_pct": 90.0,
                "role": "non_bottleneck",
            },
        },
        verbose=False,
    )
    result = optimizer.optimize(
        p_real={"p": 10.0},
        sensitivity_table={"p": {"cpu": 1.0, "memory": 1.0}},
        param_ranges={"p": ParameterRange(1.0, 20.0)},
        sql_set=None,
        eta=0.1,
        max_iter=1,
        patience=1,
        gamma=0.5,
        target_bottleneck_resources=["cpu"],
    )

    row = result.history[0]
    assert math.isclose(row["directions"]["p"], -3.0, abs_tol=1e-6)
    assert result.final_resource["memory"] < result.initial_resource["memory"]
    assert result.final_score > result.initial_score
    assert row["accepted_by_resource_utility"]


def test_replay_calibrated_direction_override_replaces_table_direction() -> None:
    """A SHA-pinned replay posterior can safely override one table direction."""

    def estimator(_: object, params: Dict[str, float]) -> Dict[str, float]:
        # Baseline is deliberately overloaded; decreasing p lowers the target
        # resource and therefore improves the shared utility.
        return {"cpu": 100.0 + float(params["p"])}

    result = make_optimizer(estimator).optimize(
        p_real={"p": 10.0},
        sensitivity_table={"p": {"cpu": 1.0}},
        param_ranges={"p": ParameterRange(1.0, 50.0)},
        sql_set=None,
        eta=0.1,
        max_iter=1,
        patience=1,
        gamma=0.5,
        original_gradient_only=False,
        direction_override_log_vector={"p": -0.5},
    )

    assert math.isclose(result.optimized_params["p"], 9.5)
    assert result.history[0]["directions"] == {"p": -0.5}
    assert result.history[0]["direction_override_log_vector"] == {"p": -0.5}


def test_final_estimation_metadata_is_preserved_for_outer_pruning() -> None:
    estimator = MetadataEstimator()
    result = make_optimizer(estimator).optimize(
        p_real={"p": 10.0},
        sensitivity_table={"p": {"cpu": 1.0}},
        param_ranges={"p": ParameterRange(1.0, 50.0)},
        sql_set=None,
        eta=0.1,
        max_iter=2,
        patience=1,
        gamma=0.5,
    )

    assert result.initial_estimation_metadata["call"] == 1
    assert result.final_estimation_metadata["call"] == 3
    assert result.final_estimation_metadata["optimistic_replay_pruning_input"] == {
        "predicted_resource": 12.65625
    }


def test_fixed_relative_step_probes_are_exact_and_baseline_relative() -> None:
    estimator = Estimator()
    probes = [5, 10, 20, 40, 80, 160, 320, 600]
    result = make_optimizer(estimator).optimize(
        p_real={"p": 10.0},
        sensitivity_table={"p": {"cpu": 1.0}},
        param_ranges={"p": ParameterRange(0.1, 100.0)},
        sql_set=None,
        eta=0.01,
        max_iter=99,
        patience=1,
        gamma=0.5,
        relative_step_probe_pct=probes,
    )

    assert result.iterations == len(probes)
    assert len(result.candidate_trajectory) == len(probes)
    assert [
        item["requested_relative_step_pct"]
        for item in result.candidate_trajectory
    ] == probes
    assert [
        round(
            100.0
            * abs(item["params"]["p"] - 10.0)
            / 10.0,
            10,
        )
        for item in result.candidate_trajectory
    ] == [5.0, 10.0, 20.0, 40.0, 80.0, 160.0, 320.0, 600.0]
    assert all(
        row["gradient_debug"]["gradient_point"]["predicted_resource"]
        == result.initial_resource
        for row in result.history
    )
    assert len({
        row["gradient_debug"]["dScore/dU"]["p"]["cpu"]
        for row in result.history
    }) == 1


def test_fixed_probes_preserve_full_trajectory_for_zero_gradient() -> None:
    def estimator(_: object, params: Dict[str, float]) -> Dict[str, float]:
        _ = params
        return {"cpu": 10.0}

    probes = [5, 10, 20, 40, 80, 160, 320, 600]
    result = make_optimizer(estimator).optimize(
        p_real={"p": 10.0},
        sensitivity_table={"p": {"cpu": 0.0}},
        param_ranges={"p": ParameterRange(0.1, 100.0)},
        sql_set=None,
        max_iter=99,
        patience=1,
        min_eta=1e-4,
        relative_step_probe_pct=probes,
    )

    assert result.iterations == len(probes)
    assert len(result.candidate_trajectory) == len(probes)
    assert [
        item["requested_relative_step_pct"]
        for item in result.candidate_trajectory
    ] == probes
    assert all(row["eta_after"] == 0.0 for row in result.history)
    assert all(item["params"] == {"p": 10.0} for item in result.candidate_trajectory)
    assert all(item["parameter_noop"] for item in result.candidate_trajectory)
    assert all(
        item["candidate_origin"] == "parameter_noop_probe"
        for item in result.candidate_trajectory
    )
    assert not any(
        item["incumbent_update_eligible"]
        for item in result.candidate_trajectory
    )


def test_boundary_plateau_trajectory_is_offline_only() -> None:
    def estimator(_: object, params: Dict[str, float]) -> Dict[str, float]:
        _ = params
        return {"cpu": 10.0}

    result = make_optimizer(estimator).optimize(
        p_real={"p": 10.0},
        sensitivity_table={"p": {"cpu": 1.0}},
        param_ranges={"p": ParameterRange(0.1, 100.0)},
        sql_set=None,
        max_iter=99,
        patience=1,
        relative_step_probe_pct=[5, 10],
        boundary_aware_eta_enabled=True,
    )

    assert all(item["boundary_plateau"] for item in result.candidate_trajectory)
    assert all(
        item["candidate_origin"] == "boundary_plateau_probe"
        for item in result.candidate_trajectory
    )
    assert not any(
        item["incumbent_update_eligible"]
        for item in result.candidate_trajectory
    )


def test_initial_score_plateau_is_offline_without_boundary_mode() -> None:
    def estimator(_: object, params: Dict[str, float]) -> Dict[str, float]:
        _ = params
        return {"cpu": 10.0}

    result = make_optimizer(estimator).optimize(
        p_real={"p": 10.0},
        sensitivity_table={"p": {"cpu": 1.0}},
        param_ranges={"p": ParameterRange(0.1, 100.0)},
        sql_set=None,
        max_iter=99,
        patience=1,
        relative_step_probe_pct=[5, 10],
        boundary_aware_eta_enabled=False,
    )

    assert not any(item["boundary_plateau"] for item in result.candidate_trajectory)
    assert all(
        item["initial_score_plateau"] and item["offline_plateau"]
        for item in result.candidate_trajectory
    )
    assert all(
        item["candidate_origin"] == "initial_score_plateau_probe"
        for item in result.candidate_trajectory
    )
    assert not any(
        item["incumbent_update_eligible"]
        for item in result.candidate_trajectory
    )


def test_incumbent_plateau_is_offline_without_boundary_mode() -> None:
    def estimator(_: object, params: Dict[str, float]) -> Dict[str, float]:
        return {"cpu": 10.0 if params["p"] <= 10.0 else 20.0}

    result = make_optimizer(estimator).optimize(
        p_real={"p": 10.0},
        sensitivity_table={"p": {"cpu": 1.0}},
        param_ranges={"p": ParameterRange(0.1, 100.0)},
        sql_set=None,
        max_iter=99,
        patience=1,
        relative_step_probe_pct=[5, 10],
        boundary_aware_eta_enabled=False,
    )

    first, second = result.candidate_trajectory
    assert first["incumbent_update_eligible"]
    assert not first["incumbent_score_plateau"]
    assert second["incumbent_score_plateau"]
    assert not second["boundary_plateau"]
    assert second["offline_plateau"]
    assert not second["incumbent_update_eligible"]
    assert second["candidate_origin"] == "incumbent_score_plateau_probe"
    assert result.history[0]["accepted_by_resource_utility"]
    assert not result.history[1]["accepted_by_resource_utility"]
    assert math.isclose(result.optimized_params["p"], first["params"]["p"])


def test_sub_epsilon_positive_gain_cannot_update_incumbent() -> None:
    def estimator(_: object, params: Dict[str, float]) -> Dict[str, float]:
        return {"cpu": 10.0 if params["p"] <= 10.0 else 10.00001}

    result = make_optimizer(estimator).optimize(
        p_real={"p": 10.0},
        sensitivity_table={"p": {"cpu": 1.0}},
        param_ranges={"p": ParameterRange(0.1, 100.0)},
        sql_set=None,
        max_iter=99,
        patience=1,
        relative_step_probe_pct=[5],
        boundary_aware_eta_enabled=False,
        plateau_score_epsilon=1e-6,
    )

    point = result.candidate_trajectory[0]
    assert point["score"] > result.initial_score
    assert point["initial_score_plateau"]
    assert point["offline_plateau"]
    assert not point["incumbent_update_eligible"]
    assert not result.history[0]["accepted_by_resource_utility"]
    assert result.optimized_params == {"p": 10.0}


def test_boundary_corner_scan_is_deterministic_and_inner_incumbent_is_frozen(
) -> None:
    calls: List[Dict[str, float]] = []

    def estimator(_: object, params: Dict[str, float]) -> Dict[str, float]:
        calls.append(dict(params))
        return {"cpu": float(params["a"] + params["b"])}

    optimizer = GradientDescentParameterOptimizer(
        estimator,
        {"cpu": 100.0},
        reward_type="linear",
        verbose=False,
    )
    result = optimizer.optimize(
        p_real={"a": 2.0, "b": 3.0, "fixed": 7.0},
        sensitivity_table={
            "a": {"cpu": 1.0},
            "b": {"cpu": 1.0},
        },
        param_ranges={
            "a": ParameterRange(1.0, 9.0),
            "b": ParameterRange(1.0, 11.0),
        },
        sql_set=None,
        active_params=["a", "b"],
        relative_step_probe_pct=[5],
        max_iter=99,
        patience=1,
        gamma=0.5,
        parameter_boundary_corner_scan_enabled=True,
    )

    assert len(calls) == 1 + 1 + 4
    assert len(result.candidate_trajectory) == 5
    gradient_point = result.candidate_trajectory[0]
    corners = result.candidate_trajectory[1:]
    assert gradient_point["probe_kind"] == "gradient_relative_step_probe"
    assert result.iterations == 1
    assert result.optimized_params == gradient_point["params"]
    assert math.isclose(result.optimized_params["a"], 2.1)
    assert math.isclose(result.optimized_params["b"], 3.15)
    assert max(item["score"] for item in corners) > result.final_score
    assert [item["boundary_corner_mask"] for item in corners] == [
        "00",
        "01",
        "10",
        "11",
    ]
    assert [
        (item["params"]["a"], item["params"]["b"])
        for item in corners
    ] == [
        (1.0, 1.0),
        (1.0, 11.0),
        (9.0, 1.0),
        (9.0, 11.0),
    ]
    for item in corners:
        assert item["params"]["fixed"] == 7.0
        assert item["probe_kind"] == "parameter_boundary_corner"
        assert item["used_for_gradient"] is False
        assert item["used_for_inner_incumbent_update"] is False
        assert item["eta"] == 0.0
        assert item["eta_applied_to_candidate"] is False
        assert item["eta_semantics"] == (
            "not_applicable_parameter_boundary_corner"
        )
        for parameter in ("a", "b"):
            recomputed = sum(
                float(item["dScore/dU"][parameter][resource])
                * float(item["T"][parameter][resource])
                * float(item["confidence"][parameter])
                for resource in item["dScore/dU"][parameter]
            )
            assert math.isclose(
                recomputed,
                float(item["g_j"][parameter]),
                rel_tol=1e-12,
                abs_tol=1e-12,
            )


def main() -> None:
    test_t_direction_drives_multiplicative_sgd_iterations()
    test_only_subset_parameters_change_and_updates_are_joint()
    test_low_confidence_semantic_prior_scales_direction()
    test_patience_decays_eta_after_rejections()
    test_flat_target_prediction_cannot_compound_parameter_moves()
    test_compatibility_wrapper_rejects_score_tie()
    test_role_aware_band_score_guides_direction_and_acceptance()
    test_role_aware_band_score_has_no_non_target_direction_hard_gate()
    test_replay_calibrated_direction_override_replaces_table_direction()
    test_final_estimation_metadata_is_preserved_for_outer_pruning()
    test_fixed_relative_step_probes_are_exact_and_baseline_relative()
    test_fixed_probes_preserve_full_trajectory_for_zero_gradient()
    test_boundary_plateau_trajectory_is_offline_only()
    test_initial_score_plateau_is_offline_without_boundary_mode()
    test_incumbent_plateau_is_offline_without_boundary_mode()
    test_sub_epsilon_positive_gain_cannot_update_incumbent()
    test_boundary_corner_scan_is_deterministic_and_inner_incumbent_is_frozen()
    print("experience-table gradient optimizer tests passed", flush=True)


if __name__ == "__main__":
    main()
