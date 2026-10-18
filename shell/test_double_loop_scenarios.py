#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from double_loop_scenario import (
    FreshBaselineObjective,
    FreshExperienceModel,
    FreshModelQualityThresholds,
    ScenarioMeasurement,
)
from shell.run_double_loop_grid_scenarios import (
    deferred_dynamic_sweet_score_error,
    fatal_replay_workload_error,
    fresh_model_calibration_points,
    load_anchored_resource_training_seed,
    measurement_invalid_reason,
    wait_for_primary_postgres,
)
from shell.bonect_real_validate_candidates import calculate_configured_resource_score
from shell.export_double_loop_experiment_log import score_resources as export_score_resources


def assert_close(actual: float, expected: float) -> None:
    assert math.isclose(actual, expected, rel_tol=0.0, abs_tol=1e-12), (
        actual,
        expected,
    )


def test_measurement_validation() -> None:
    measurement = ScenarioMeasurement(100.0, 9.0, 5.0, 80.0)
    assert not measurement_invalid_reason(
        measurement,
        {
            "returncode": 0,
            "status": "failed",
            "monitor_error": "insufficient_runtime_samples",
            "resource_samples": {"cpu_cores": 1, "memory_bytes": 1, "io_bps": 1},
        },
    )
    assert "non-zero returncode" in measurement_invalid_reason(
        measurement,
        {
            "returncode": 2,
            "process_error": "HyBench XP exited with 2",
            "resource_samples": {"cpu_cores": 10, "memory_bytes": 10, "io_bps": 10},
        },
    )
    assert "runtime_errors" in measurement_invalid_reason(
        measurement,
        {
            "returncode": 0,
            "runtime_errors": ["database system is in recovery mode"],
            "resource_samples": {"cpu_cores": 10, "memory_bytes": 10, "io_bps": 10},
        },
    )
    assert fatal_replay_workload_error(
        {
            "status": "failed",
            "resource_stats": {
                "workload_output_error": (
                    "canceling statement due to statement timeout"
                )
            },
        }
    ) == "canceling statement due to statement timeout"
    assert fatal_replay_workload_error(
        {
            "status": "failed",
            "resource_stats": {"monitor_warning": "insufficient samples"},
        }
    ) is None
    deferred_payload = {
        "status": "failed",
        "error": "ValueError('target scenario requires a finite sweet upper value')",
    }
    assert deferred_dynamic_sweet_score_error("io", deferred_payload)
    assert not deferred_dynamic_sweet_score_error("cpu", deferred_payload)
    assert not deferred_dynamic_sweet_score_error(
        "io",
        {"status": "failed", "error": "database system is in recovery mode"},
    )


def test_primary_ready_wait_rejects_recovery_state() -> None:
    replies = iter(["t", "false"])
    queries = []
    sleeps = []

    def query(_pg, sql, *, timeout_s):
        queries.append((sql, timeout_s))
        return next(replies)

    wait_for_primary_postgres(
        object(),
        timeout_s=1.0,
        query_func=query,
        sleep_func=sleeps.append,
    )
    assert len(queries) == 2
    assert all("pg_is_in_recovery" in sql for sql, _timeout_s in queries)
    assert sleeps == [2.0]
    assert "CalledProcessError" in str(
        fatal_replay_workload_error(
            {
                "status": "failed",
                "error": "CalledProcessError(2, 'hybench')",
                "resources": {},
                "resource_stats": {},
            }
        )
    )
    assert fatal_replay_workload_error(
        {
            "status": "failed",
            "error": "legacy monitor sample-count warning",
            "resources": {"cpu": 1.0, "memory": 2.0, "io": 3.0},
            "resource_stats": {},
        }
    ) is None


def test_fresh_calibration_is_capped_at_top15_replays() -> None:
    names = [
        "seq_page_cost",
        "random_page_cost",
        "cpu_tuple_cost",
        "cpu_index_tuple_cost",
        "cpu_operator_cost",
        "parallel_setup_cost",
        "parallel_tuple_cost",
    ]
    base = {name: 1.0 for name in names}
    ranges = {name: (0.0, 2.0) for name in names}
    design = fresh_model_calibration_points(
        base,
        ranges,
        names,
        replay_budget=15,
        grid_points=15,
    )
    assert len(design) == 15
    assert all(
        row["design"] == "deterministic_coordinate_axis_grid" for row in design
    )
    # The baseline is measured separately.  Every calibration replay changes
    # exactly one planner coordinate so axis models can be genuinely validated.
    assert all(row["changed_parameters"] == [row["parameter"]] for row in design)
    assert all(
        sum(row["params"][name] != base[name] for name in names) == 1
        for row in design
    )
    # Each active dimension gets the two grid values bracketing the default.
    # The remaining slot expands the first coordinate outward deterministically.
    for name in names:
        ranks = {
            row["grid_rank"]
            for row in design
            if row["parameter"] == name
        }
        assert {6, 8}.issubset(ranks)
    assert sum(row["phase"] == "coordinate_axis_local_expand" for row in design) == 1


def test_fresh_calibration_uses_the_candidate_log_grid() -> None:
    design = fresh_model_calibration_points(
        {"cpu_operator_cost": 0.0025},
        {"cpu_operator_cost": (1e-6, 1.0)},
        ["cpu_operator_cost"],
        replay_budget=15,
        grid_points=15,
    )
    by_rank = {int(row["grid_rank"]): row for row in design}
    assert_close(by_rank[0]["params"]["cpu_operator_cost"], 1e-6)
    assert_close(by_rank[14]["params"]["cpu_operator_cost"], 1.0)
    assert_close(by_rank[7]["params"]["cpu_operator_cost"], 0.001)


def test_training_seed_can_use_real_candidates_without_old_labels() -> None:
    baseline = ScenarioMeasurement(100.0, 10.0, 20.0, 30.0)
    objective = FreshBaselineObjective(
        "none",
        baseline,
        resource_limits={"cpu": 20.0, "memory": 40.0, "io": 60.0},
    )
    model = FreshExperienceModel(objective, {"x": (0.0, 2.0)})
    with tempfile.TemporaryDirectory() as temporary_dir:
        seed_dir = Path(temporary_dir)
        (seed_dir / "fresh_baseline.json").write_text(
            json.dumps(
                {
                    "params": {"x": 0.0},
                    "measurement": baseline.as_resource(),
                }
            ),
            encoding="utf-8",
        )
        calibration = {
            "event": "fresh_model_calibration_replay",
            "status": "usable",
            "params": {"x": 1.0},
            "measurement": ScenarioMeasurement(110.0, 11.0, 21.0, 31.0).as_resource(),
        }
        (seed_dir / "fresh_model_calibration.jsonl").write_text(
            json.dumps(calibration) + "\n",
            encoding="utf-8",
        )
        validation_dir = seed_dir / "real_validations"
        validation_dir.mkdir()
        candidate = {
            "status": "ok",
            "params": {"x": 2.0},
            "actual_resource": {"cpu": 12.0, "memory": 22.0, "io": 32.0},
            "db_delta": {"db_qps": 120.0},
            "actual_score": 999.0,
            "rank": 1,
        }
        (validation_dir / "none_generic_vm_replay_raw.jsonl").write_text(
            json.dumps(candidate) + "\n",
            encoding="utf-8",
        )
        rows, audit = load_anchored_resource_training_seed(
            model,
            seed_dir,
            include_real_candidates=True,
        )
    assert len(rows) == 3
    assert audit["source_real_candidate_row_count"] == 1
    assert audit["loaded_fields"] == ["params", "measurement"]
    assert not audit["old_scores_loaded"]
    assert not audit["old_ranks_loaded"]


def test_shared_formula() -> None:
    baseline = ScenarioMeasurement(100.0, 90.0, 90.0, 100.0)
    limits = {"cpu": 85.5, "memory": 100.0, "io": 100.0}
    policy = FreshBaselineObjective(
        "cpu",
        baseline,
        resource_limits=limits,
        target_sweet_lower=81.0,
        target_sweet_upper=85.5,
    )
    assert policy.score_policy == "sigmoid_linear_resource"
    assert_close(policy.sigmoid_linear_score(0.0), 0.0)
    assert_close(policy.sigmoid_linear_score(1.0), 1.0)
    assert_close(policy.sigmoid_linear_score(1.1), 1.0 - 10.0 * 0.1 - 30.0 * 0.1**2)
    try:
        FreshBaselineObjective(
            "cpu",
            baseline,
            score_policy="score_c",  # type: ignore[arg-type]
            resource_limits=limits,
            target_sweet_upper=85.5,
        )
        raise AssertionError("the superseded Score C policy must not be selectable")
    except ValueError:
        pass

    successful = ScenarioMeasurement(120.0, 81.0, 99.0, 110.0)
    decision = policy.decide(successful)
    assert decision.successful
    expected = policy.resource_score_details(successful)["total"]
    assert_close(decision.objective, expected)
    assert decision.qps_score_contribution == 0.0

    qps_higher = policy.decide(ScenarioMeasurement(130.0, 81.0, 99.0, 110.0))
    assert_close(qps_higher.objective, decision.objective)

    replay_score = calculate_configured_resource_score(
        {
            "scoring": {
                "score_mode": "band_target",
                "resource_target_ranges": {
                    "cpu": {
                        "min_pct": 81.0 / 85.5 * 100.0,
                        "max_pct": 100.0,
                    }
                },
            }
        },
        "cpu",
        successful.as_resource(),
        limits,
    )
    assert replay_score["formula"] == "sigmoid_linear_sweet_band_weighted_mean_v2"
    assert_close(replay_score["total"], expected)
    exported_score = export_score_resources(
        "cpu",
        successful.as_resource(),
        limits,
        81.0,
        85.5,
        0.25,
    )
    assert exported_score is not None
    assert_close(exported_score, expected)

    failed = ScenarioMeasurement(110.0, 86.4, 180.0, 200.0)
    failed_decision = policy.decide(failed)
    assert not failed_decision.successful
    failed_neutral = policy.decide(ScenarioMeasurement(110.0, 86.4, 90.0, 100.0))
    assert failed_decision.objective < failed_neutral.objective

    semantic = ScenarioMeasurement(101.0, 89.0, 80.0, 80.0)
    assert policy.outer_decide(semantic).accepted
    assert not policy.decide(semantic).successful
    assert not policy.outer_decide(ScenarioMeasurement(99.0, 89.0, 100.0, 100.0)).accepted

    # The target contribution is flat inside the sweet band and falls on either
    # side. The independent final gate still enforces the 5%-10% drop interval.
    overdrop = ScenarioMeasurement(120.0, 72.0, 99.0, 110.0)
    at_limit = ScenarioMeasurement(120.0, 85.5, 99.0, 110.0)
    in_band = ScenarioMeasurement(120.0, 82.0, 99.0, 110.0)
    assert policy.decide(at_limit).objective > policy.decide(overdrop).objective
    assert_close(policy.decide(at_limit).objective, policy.decide(in_band).objective)
    equally_above = ScenarioMeasurement(120.0, 85.5 * 1.05, 99.0, 110.0)
    equally_below = ScenarioMeasurement(120.0, 81.0 * 0.95, 99.0, 110.0)
    assert_close(
        policy.resource_score_details(equally_above)["contributions"]["cpu"],
        policy.resource_score_details(equally_below)["contributions"]["cpu"],
    )
    assert not policy.final_decide(overdrop).successful
    optimistic_band = policy.optimistic_score_in_resource_box(
        {"cpu": 80.0, "memory": 95.0, "io": 95.0},
        {"cpu": 84.0, "memory": 105.0, "io": 105.0},
    )
    assert_close(optimistic_band["score"], 1.0)
    assert 81.0 <= optimistic_band["optimistic_resource"]["cpu"] <= 85.5
    optimistic_below_band = policy.optimistic_score_in_resource_box(
        {"cpu": 60.0, "memory": 95.0, "io": 95.0},
        {"cpu": 70.0, "memory": 105.0, "io": 105.0},
    )
    assert optimistic_below_band["score"] < 1.0

    final_ok = ScenarioMeasurement(115.0, 85.5, 91.0, 100.0)
    final_decision = policy.final_decide(final_ok)
    assert final_decision.successful
    assert final_decision.target_not_bottleneck
    assert final_decision.target_drop_in_range

    none = FreshBaselineObjective(
        "none",
        baseline,
        resource_limits={"cpu": 100.0, "memory": 100.0, "io": 100.0},
    )
    none_success_measurement = ScenarioMeasurement(120.0, 99.0, 94.5, 90.0)
    none_success = none.decide(none_success_measurement)
    assert none_success.successful
    none_expected = none.resource_score_details(none_success_measurement)["total"]
    assert_close(none_success.objective, none_expected)
    none_details = none.resource_score_details(none_success_measurement)
    assert none_details["formula"] == "sigmoid_linear_sweet_band_weighted_mean_v2"
    assert none_details["weights"] == {"cpu": 1.0, "memory": 1.0, "io": 0.25}
    assert none_details["penalty_weights"] == {"cpu": 1.0, "memory": 1.0, "io": 1.0}
    assert_close(none_details["weight_sum"], 2.25)
    assert_close(
        none_details["weighted_contributions"]["io"],
        0.25 * none_details["contributions"]["io"],
    )
    none_io_overload = none.resource_score_details(
        ScenarioMeasurement(120.0, 100.0, 100.0, 110.0)
    )
    assert_close(
        none_io_overload["weighted_contributions"]["io"],
        none_io_overload["contributions"]["io"],
    )
    none_replay_score = calculate_configured_resource_score(
        {
            "scoring": {
                "score_mode": "band_target",
                "resource_target_ranges": {},
            }
        },
        "none",
        none_success_measurement.as_resource(),
        {"cpu": 100.0, "memory": 100.0, "io": 100.0},
    )
    assert_close(none_replay_score["total"], none_expected)
    io_target = FreshBaselineObjective(
        "io",
        ScenarioMeasurement(100.0, 90.0, 90.0, 110.0),
        resource_limits={"cpu": 100.0, "memory": 100.0, "io": 100.0},
        target_sweet_lower=95.0,
        target_sweet_upper=100.0,
    )
    io_target_details = io_target.resource_score_details(
        ScenarioMeasurement(100.0, 90.0, 90.0, 98.0)
    )
    assert io_target_details["weights"] == {"cpu": 1.0, "memory": 1.0, "io": 1.0}
    assert io_target_details["penalty_weights"] == {
        "cpu": 1.0,
        "memory": 1.0,
        "io": 1.0,
    }
    io_replay_score = calculate_configured_resource_score(
        {
            "scoring": {
                "score_mode": "band_target",
                "resource_target_ranges": {
                    "io": {"min_pct": 95.0, "max_pct": 100.0}
                },
            }
        },
        "io",
        {"cpu": 90.0, "memory": 90.0, "io": 98.0},
        {"cpu": 100.0, "memory": 100.0, "io": 100.0},
    )
    assert_close(io_replay_score["total"], io_target_details["total"])
    none_failure = none.decide(ScenarioMeasurement(110.0, 99.0, 90.0, 100.0))
    assert not none_failure.successful
    same_resources_higher_qps = none.decide(
        ScenarioMeasurement(200.0, 99.0, 90.0, 100.0)
    )
    assert_close(none_failure.objective, same_resources_higher_qps.objective)
    assert none.outer_decide(ScenarioMeasurement(101.0, 90.1, 89.0, 99.0)).accepted
    assert not none.final_decide(ScenarioMeasurement(114.999, 90.1, 89.0, 99.0)).successful


def test_resource_estimation_uses_the_real_formula() -> None:
    baseline = ScenarioMeasurement(100.0, 90.0, 90.0, 100.0)
    objective = FreshBaselineObjective(
        "cpu",
        baseline,
        resource_limits={"cpu": 85.5, "memory": 100.0, "io": 100.0},
        target_sweet_lower=81.0,
        target_sweet_upper=85.5,
    )
    model = FreshExperienceModel(
        objective,
        {"x": (0.0, 1.0)},
        exploration_weight=0.25,
        max_neighbors=2,
    )
    # Complementary failures whose interpolated physical midpoint satisfies
    # both mandatory margins.  This catches accidental interpolation of scores.
    model.add(
        {"x": 0.0},
        ScenarioMeasurement(130.0, 90.0, 90.0, 100.0).as_resource(),
    )
    model.add(
        {"x": 1.0},
        ScenarioMeasurement(100.0, 72.0, 90.0, 100.0).as_resource(),
    )
    midpoint = model.predict(None, {"x": 0.5})
    predicted = ScenarioMeasurement(
        midpoint["resource"]["db_qps"],
        midpoint["resource"]["cpu"],
        midpoint["resource"]["memory"],
        midpoint["resource"]["io"],
    )
    assert_close(predicted.db_qps, 115.0)
    assert_close(predicted.cpu, 81.0)
    shared_score = objective.decide(predicted).objective
    assert_close(midpoint["estimated_score"], shared_score)
    assert_close(midpoint["model"]["predicted_objective"], shared_score)
    assert midpoint["model"]["training_target"] == "resource_vector"
    assert midpoint["model"]["estimated_and_real_share_formula"] is True
    assert midpoint["model"]["score_formula"] == "sigmoid_linear_sweet_band_weighted_mean_v2"
    assert midpoint["model"]["predicted_resource"] == midpoint["resource"]

    endpoint_average = sum(row.objective for row in model.snapshot()) / 2.0
    assert not math.isclose(midpoint["estimated_score"], endpoint_average)
    # Coordinate grid search exposes only the shared resource Score; novelty and
    # parameter direction are audit metadata and never alter candidate ranking.
    assert_close(midpoint["score"], midpoint["estimated_score"])
    assert midpoint["model"]["acquisition_enabled"] is False
    assert_close(
        objective.score(midpoint["resource"], {"db_qps": predicted.db_qps}),
        midpoint["estimated_score"],
    )

    local_model = FreshExperienceModel(
        objective,
        {"x": (0.0, 1.0)},
        exploration_weight=0.0,
        max_neighbors=2,
    )
    local_model.add({"x": 0.0}, ScenarioMeasurement(90.0, 100.0, 80.0, 80.0).as_resource())
    local_model.add({"x": 0.8}, ScenarioMeasurement(120.0, 85.0, 90.0, 100.0).as_resource())
    local_model.add({"x": 1.0}, ScenarioMeasurement(130.0, 80.0, 100.0, 120.0).as_resource())
    local = local_model.predict(None, {"x": 0.9})
    assert local["model"]["kind"] == "current_run_resource_vector_selected_mode"
    assert local["model"]["neighbor_count"] == 2
    assert_close(local["resource"]["db_qps"], 125.0)
    local_measurement = ScenarioMeasurement(
        local["resource"]["db_qps"],
        local["resource"]["cpu"],
        local["resource"]["memory"],
        local["resource"]["io"],
    )
    assert_close(local["estimated_score"], objective.decide(local_measurement).objective)


def test_ridge_prediction_falls_back_outside_empirical_support() -> None:
    baseline = ScenarioMeasurement(100.0, 10.0, 20.0, 30.0)
    objective = FreshBaselineObjective(
        "none",
        baseline,
        resource_limits={"cpu": 100.0, "memory": 100.0, "io": 100.0},
    )
    model = FreshExperienceModel(objective, {"x": (0.0, 1.0)})
    measured = [
        ({"x": 0.0}, ScenarioMeasurement(100.0, 10.0, 20.0, 30.0)),
        ({"x": 0.05}, ScenarioMeasurement(105.0, 11.0, 21.0, 31.0)),
        ({"x": 0.10}, ScenarioMeasurement(110.0, 12.0, 22.0, 32.0)),
    ]
    for params, resource in measured:
        model.add(params, resource.as_resource())
    model._mode = {"kind": "ridge_linear", "alpha": 1e-6}

    interpolated = model.predict(None, {"x": 0.08})
    assert interpolated["model"]["outside_empirical_support"] is False
    assert interpolated["model"]["support_fallback_applied"] is False

    extrapolated = model.predict(None, {"x": 0.90})
    assert extrapolated["model"]["outside_empirical_support"] is True
    assert extrapolated["model"]["support_fallback_applied"] is True
    assert_close(extrapolated["model"]["empirical_support_radius"], 0.05)
    assert extrapolated["resource"] == measured[-1][1].as_resource()
    assert_close(
        extrapolated["estimated_score"],
        objective.score(extrapolated["resource"], None),
    )


def test_fresh_model_preoptimizer_quality_gate() -> None:
    baseline = ScenarioMeasurement(100.0, 10.0, 20.0, 30.0)
    objective = FreshBaselineObjective(
        "none",
        baseline,
        resource_limits={"cpu": 20.0, "memory": 40.0, "io": 60.0},
    )
    model = FreshExperienceModel(
        objective,
        {"x": (0.0, 1.0)},
        exploration_weight=0.0,
    )
    for index in range(15):
        x = index / 14.0
        model.add(
            {"x": x},
            ScenarioMeasurement(
                100.0 + 10.0 * x,
                10.0 + 2.0 * x,
                20.0 + 4.0 * x,
                30.0 + 6.0 * x,
            ).as_resource(),
        )
    gate = model.select_quality_mode(
        FreshModelQualityThresholds(
            min_samples=15,
            max_relative_error_p95=0.01,
            max_relative_error_max=0.01,
            min_r2=0.99,
            max_pairwise_inversion_rate=0.0,
        )
    )
    assert gate["passed"]
    assert gate["current_run_only"]
    assert not gate["historical_training_rows_used"]
    assert gate["selected_evaluation"]["resource_error_gate_passed"]
    assert gate["selected_evaluation"]["score_ranking_gate_passed"]
    assert gate["selected_evaluation"]["sample_count_gate_passed"]
    assert gate["selected_evaluation"]["train_sample_count_per_fold"] == 14
    assert gate["selected_evaluation"]["test_sample_count"] == 15
    assert gate["selected_evaluation"]["overall_pairwise_gate_passed"]
    assert gate["selected_evaluation"]["pairwise_inversion_rate"] == 0.0
    assert gate["selected_evaluation"]["ranking_prefixes"]["top10"]["passed"]
    assert gate["selected_evaluation"]["ranking_prefixes"]["top15"]["passed"]
    assert gate["selected_mode"]["kind"] in {
        "axis_linear_additive",
        "ridge_linear",
        "ridge_quadratic",
        "affine_calibrated",
    }
    affine_evaluations = [
        item
        for item in gate["mode_evaluations"]
        if item.get("passed") and item["mode"].get("kind") == "affine_calibrated"
    ]
    assert affine_evaluations
    affine_evaluation = affine_evaluations[0]
    affine_mode = affine_evaluation["mode"]
    assert affine_mode["base_mode"]["kind"] in {
        "axis_linear_additive",
        "ridge_linear",
        "ridge_quadratic",
    }
    assert affine_mode["calibration_provenance"] == (
        "nested_current_run_leave_one_out_predictions_only"
    )
    assert set(affine_mode["coefficients"]) == {
        "db_qps",
        "cpu",
        "memory",
        "io",
    }
    assert affine_evaluation["affine_calibration_current_run_only"]
    assert affine_evaluation["affine_calibration_fit_sample_count"] == 15

    insufficient = model.select_quality_mode(
        FreshModelQualityThresholds(min_samples=16)
    )
    assert not insufficient["passed"]
    assert insufficient["reason"] == "minimum_current_run_calibration_samples_not_reached"


def test_axis_nearest_additive_mode_keeps_interactions_off_coordinate_axes() -> None:
    baseline = ScenarioMeasurement(100.0, 10.0, 20.0, 30.0)
    objective = FreshBaselineObjective(
        "none",
        baseline,
        resource_limits={"cpu": 100.0, "memory": 100.0, "io": 100.0},
    )
    model = FreshExperienceModel(
        objective,
        {"x": (0.0, 1.0), "y": (0.0, 1.0)},
    )
    model.add({"x": 0.0, "y": 0.0}, baseline.as_resource())
    model.add(
        {"x": 1.0, "y": 0.0},
        ScenarioMeasurement(110.0, 12.0, 21.0, 33.0).as_resource(),
    )
    model.add(
        {"x": 0.0, "y": 1.0},
        ScenarioMeasurement(120.0, 13.0, 24.0, 35.0).as_resource(),
    )
    # This interaction row must not become evidence for either coordinate axis.
    model.add(
        {"x": 1.0, "y": 1.0},
        ScenarioMeasurement(1000.0, 90.0, 90.0, 90.0).as_resource(),
    )

    predicted, neighbors, _ = model._predict_measurement_from_rows(
        model.snapshot(),
        {"x": 0.9, "y": 0.9},
        {"kind": "axis_nearest_additive"},
    )
    assert_close(predicted.db_qps, 130.0)
    assert_close(predicted.cpu, 15.0)
    assert_close(predicted.memory, 25.0)
    assert_close(predicted.io, 38.0)
    assert len(neighbors) == 3
    assert {"kind": "axis_nearest_additive"} in model.candidate_modes(15)


def test_axis_reference_stays_on_fresh_baseline_after_online_interactions() -> None:
    baseline = ScenarioMeasurement(100.0, 10.0, 20.0, 30.0)
    objective = FreshBaselineObjective(
        "none",
        baseline,
        resource_limits={"cpu": 100.0, "memory": 100.0, "io": 100.0},
    )
    model = FreshExperienceModel(
        objective,
        {"x": (0.0, 1.0), "y": (0.0, 1.0)},
    )
    model.add({"x": 0.0, "y": 0.0}, baseline.as_resource())
    model.add(
        {"x": 1.0, "y": 0.0},
        ScenarioMeasurement(110.0, 12.0, 21.0, 33.0).as_resource(),
    )
    model.add(
        {"x": 0.0, "y": 1.0},
        ScenarioMeasurement(120.0, 13.0, 24.0, 35.0).as_resource(),
    )
    model._pinned_axis_reference = {"x": 0.0, "y": 0.0}
    model._mode = {"kind": "axis_nearest_additive"}

    # These multi-parameter online rows make x=0.8 the modal x value. Without
    # the pinned fresh-baseline anchor there is no y-axis through (0.8, 0.0),
    # which reproduced the formal E16 "uncovered parameter axis" collapse.
    for index in range(1, 10):
        model.add(
            {"x": 0.8, "y": index / 10.0},
            ScenarioMeasurement(1000.0 + index, 90.0, 90.0, 90.0).as_resource(),
        )

    predicted = model.predict(None, {"x": 0.9, "y": 0.9})
    assert_close(predicted["resource"]["db_qps"], 130.0)
    assert_close(predicted["resource"]["cpu"], 15.0)
    assert_close(predicted["resource"]["memory"], 25.0)
    assert_close(predicted["resource"]["io"], 38.0)
    assert predicted["model"]["pinned_axis_reference"] == {"x": 0.0, "y": 0.0}


def test_fresh_model_quality_gate_rejects_global_ranking_inversion() -> None:
    baseline = ScenarioMeasurement(100.0, 40.0, 40.0, 40.0)
    objective = FreshBaselineObjective(
        "none",
        baseline,
        resource_limits={"cpu": 100.0, "memory": 100.0, "io": 100.0},
    )
    model = FreshExperienceModel(objective, {"x": (0.0, 15.0)})
    for index in range(16):
        model.add(
            {"x": float(index)},
            ScenarioMeasurement(
                100.0,
                20.0 + index,
                40.0,
                40.0,
            ).as_resource(),
        )

    def predict_with_one_global_misranking(
        _train: object,
        params: object,
        _mode: object,
        **_kwargs: object,
    ) -> object:
        x = float(params["x"])  # type: ignore[index]
        cpu = 0.0 if x == 15.0 else 20.0 + x
        return ScenarioMeasurement(100.0, cpu, 40.0, 40.0), [], 0.0

    model._predict_measurement_from_rows = (  # type: ignore[method-assign]
        predict_with_one_global_misranking
    )
    evaluation = model._evaluate_mode(
        model.snapshot(),
        {"kind": "idw", "max_neighbors": 1, "distance_power": 1.0},
        FreshModelQualityThresholds(
            min_samples=15,
            max_relative_error_p95=10.0,
            max_relative_error_max=10.0,
            min_r2=-100.0,
            max_pairwise_inversion_rate=0.10,
        ),
    )
    assert evaluation["ranking_prefixes"]["top10"]["passed"]
    assert evaluation["ranking_prefixes"]["top15"]["passed"]
    assert evaluation["pairwise_inversion_rate"] > 0.10
    assert not evaluation["overall_pairwise_gate_passed"]
    assert not evaluation["score_ranking_gate_passed"]
    assert not evaluation["passed"]


def test_score_tie_margin_ignores_only_indistinguishable_pairs() -> None:
    estimated = [0.000, 0.005, 0.100]
    real = [0.004, 0.000, 0.100]
    raw = FreshExperienceModel._pairwise_inversion(estimated, real)
    robust = FreshExperienceModel._pairwise_inversion(
        estimated,
        real,
        tie_margin=0.01,
    )
    assert raw == (1, 3, 1.0 / 3.0)
    assert robust == (0, 2, 0.0)
    assert FreshExperienceModel._pairwise_inversion(
        [0.0, 0.005],
        [0.005, 0.0],
        tie_margin=0.01,
    ) == (0, 0, 0.0)
    assert FreshExperienceModel._pairwise_inversion(
        [0.0, 0.005],
        [0.0, 0.1],
        tie_margin=0.01,
    ) == (0, 1, 0.0)
    assert FreshExperienceModel._pairwise_inversion(
        [0.0, 0.0],
        [0.0, 0.1],
        tie_margin=0.01,
    ) == (1, 1, 1.0)


def test_fixed_training_rows_keep_independent_exact_replays_as_evidence() -> None:
    baseline = ScenarioMeasurement(100.0, 20.0, 40.0, 40.0)
    objective = FreshBaselineObjective(
        "none",
        baseline,
        resource_limits={"cpu": 100.0, "memory": 100.0, "io": 100.0},
    )
    model = FreshExperienceModel(objective, {"x": (0.0, 14.0)})
    training = []
    validation = []
    for index in range(15):
        params = {"x": float(index)}
        fresh_measurement = ScenarioMeasurement(
            100.0 + index,
            20.0 + 4.0 * index,
            40.0 + index,
            40.0 + 2.0 * index,
        )
        stale_measurement = ScenarioMeasurement(
            300.0 - index,
            90.0 - 3.0 * index,
            80.0 - index,
            70.0 - 2.0 * index,
        )
        training.append(
            model.build_experience(params, stale_measurement.as_resource())
        )
        validation.append(
            model.build_experience(params, fresh_measurement.as_resource())
        )

    original_predict = model._predict_measurement_from_rows
    inspected_fold_count = 0

    def assert_independent_historical_support(
        train: object,
        params: object,
        mode: object,
        **kwargs: object,
    ) -> object:
        nonlocal inspected_fold_count
        target_x = float(params["x"])  # type: ignore[index]
        train_rows = list(train)  # type: ignore[arg-type]
        for row in train_rows:
            x = float(row.params["x"])
            if x == target_x:
                assert_close(row.measurement.cpu, 90.0 - 3.0 * x)
            else:
                assert_close(row.measurement.cpu, 20.0 + 4.0 * x)
        inspected_fold_count += 1
        return original_predict(train_rows, params, mode, **kwargs)  # type: ignore[arg-type]

    model._predict_measurement_from_rows = (  # type: ignore[method-assign]
        assert_independent_historical_support
    )

    quality = model.select_quality_mode(
        FreshModelQualityThresholds(
            max_relative_error_p95=10.0,
            max_relative_error_max=10.0,
            min_r2=-100.0,
            max_pairwise_inversion_rate=1.0,
            score_tie_margin=0.01,
        ),
        validation_rows=validation,
        independent_exact_training_rows=training,
    )
    assert quality["passed"]
    assert quality["historical_training_rows_used"]
    selected = quality["selected_evaluation"]
    assert selected["validation"] == (
        "anchored_historical_support_plus_independent_exact_replays_"
        "plus_current_top15_leave_one_out"
    )
    assert selected["test_sample_count"] == 15
    assert selected["train_sample_count_per_fold"] == 15
    assert selected["train_sample_counts_per_fold"] == [15] * 15
    assert selected["independent_historical_exact_signature_support_count"] == 15
    assert not selected["current_top15_exact_cache_accepted_as_quality_evidence"]
    assert inspected_fold_count == 15 * len(quality["mode_evaluations"])


def test_failed_historical_seed_falls_back_to_current_top15() -> None:
    baseline = ScenarioMeasurement(100.0, 10.0, 20.0, 30.0)
    objective = FreshBaselineObjective(
        "none",
        baseline,
        resource_limits={"cpu": 20.0, "memory": 40.0, "io": 60.0},
    )
    model = FreshExperienceModel(objective, {"x": (0.0, 1.0)})
    validation = []
    historical = []
    for index in range(15):
        x = index / 14.0
        params = {"x": x}
        current = ScenarioMeasurement(
            100.0 + 10.0 * x,
            10.0 + 2.0 * x,
            20.0 + 4.0 * x,
            30.0 + 6.0 * x,
        )
        stale = ScenarioMeasurement(
            200.0 - 10.0 * x,
            18.0 - 2.0 * x,
            35.0 - 4.0 * x,
            55.0 - 6.0 * x,
        )
        validation.append(model.build_experience(params, current.as_resource()))
        historical.append(model.build_experience(params, stale.as_resource()))

    original_evaluate = model._evaluate_mode

    def force_historical_strategy_failure(
        rows: object,
        mode: object,
        thresholds: object,
        *,
        fixed_training_rows: object = None,
        independent_exact_training_rows: object = None,
        always_training_rows: object = None,
    ) -> object:
        evaluation = original_evaluate(  # type: ignore[arg-type]
            rows,
            mode,
            thresholds,
            fixed_training_rows=fixed_training_rows,
            independent_exact_training_rows=independent_exact_training_rows,
            always_training_rows=always_training_rows,
        )
        if fixed_training_rows is not None:
            evaluation["passed"] = False
            evaluation["resource_error_gate_passed"] = False
            for metric in evaluation["resource_metrics"].values():
                metric["passed"] = False
            evaluation["selection_key"] = [1, 1.0, 1.0, 1.0, 1.0]
        return evaluation

    model._evaluate_mode = force_historical_strategy_failure  # type: ignore[method-assign]
    quality = model.select_quality_mode(
        FreshModelQualityThresholds(
            min_samples=15,
            max_relative_error_p95=0.01,
            max_relative_error_max=0.01,
            min_r2=0.99,
            max_pairwise_inversion_rate=0.0,
        ),
        validation_rows=validation,
        fixed_training_rows=historical,
    )
    assert quality["passed"]
    assert quality["selected_training_strategy"] == "current_top15_only"
    assert quality["current_run_only"]
    assert not quality["historical_training_rows_used"]
    assert quality["historical_training_rows_evaluated"]
    assert quality["historical_seed_discarded_after_quality_failure"]
    assert quality["selected_evaluation"]["training_strategy"] == (
        "current_top15_only"
    )


def test_quality_selection_skips_inapplicable_mode() -> None:
    baseline = ScenarioMeasurement(100.0, 10.0, 20.0, 30.0)
    objective = FreshBaselineObjective(
        "none",
        baseline,
        resource_limits={"cpu": 20.0, "memory": 40.0, "io": 60.0},
    )
    model = FreshExperienceModel(objective, {"x": (0.0, 1.0)})
    for index in range(15):
        x = index / 14.0
        model.add(
            {"x": x},
            ScenarioMeasurement(
                100.0 + x,
                10.0 + x,
                20.0 + x,
                30.0 + x,
            ).as_resource(),
        )
    original_evaluate = model._evaluate_mode

    def reject_axis_mode(
        rows: object,
        mode: object,
        thresholds: object,
        *,
        fixed_training_rows: object = None,
        independent_exact_training_rows: object = None,
        always_training_rows: object = None,
    ) -> object:
        if mode.get("kind") == "axis_nearest_additive":  # type: ignore[union-attr]
            raise RuntimeError("uncovered parameter axis")
        return original_evaluate(  # type: ignore[arg-type]
            rows,
            mode,
            thresholds,
            fixed_training_rows=fixed_training_rows,
            independent_exact_training_rows=independent_exact_training_rows,
            always_training_rows=always_training_rows,
        )

    model._evaluate_mode = reject_axis_mode  # type: ignore[method-assign]
    quality = model.select_quality_mode(
        FreshModelQualityThresholds(
            max_relative_error_p95=1.0,
            max_relative_error_max=1.0,
            min_r2=-100.0,
            max_pairwise_inversion_rate=1.0,
        )
    )
    assert quality["passed"]
    failed_axis = [
        item
        for item in quality["mode_evaluations"]
        if item["mode"].get("kind") == "axis_nearest_additive"
        and item.get("evaluation_error")
    ]
    assert failed_axis
    assert all(not item["passed"] for item in failed_axis)


def test_fresh_baseline_is_a_fixed_leave_one_out_anchor() -> None:
    baseline = ScenarioMeasurement(100.0, 10.0, 20.0, 30.0)
    objective = FreshBaselineObjective(
        "none",
        baseline,
        resource_limits={"cpu": 40.0, "memory": 80.0, "io": 120.0},
    )
    model = FreshExperienceModel(objective, {"x": (0.0, 15.0)})
    baseline_row = model.build_experience({"x": 0.0}, baseline.as_resource())
    validation = [
        model.build_experience(
            {"x": float(index)},
            ScenarioMeasurement(
                100.0 + index,
                10.0 + 0.5 * index,
                20.0 + index,
                30.0 + 1.5 * index,
            ).as_resource(),
        )
        for index in range(1, 16)
    ]
    quality = model.select_quality_mode(
        FreshModelQualityThresholds(
            max_relative_error_p95=0.05,
            max_relative_error_max=0.05,
            min_r2=0.95,
            max_pairwise_inversion_rate=0.10,
        ),
        validation_rows=validation,
        always_training_rows=[baseline_row],
    )
    assert quality["passed"]
    selected = quality["selected_evaluation"]
    assert selected["always_training_row_count"] == 1
    assert selected["train_sample_count_per_fold"] == 15
    assert selected["train_sample_counts_per_fold"] == [15] * 15


def test_fresh_baseline_anchor_discards_pruned_parameter_axes() -> None:
    baseline = ScenarioMeasurement(100.0, 10.0, 20.0, 30.0)
    objective = FreshBaselineObjective(
        "none",
        baseline,
        resource_limits={"cpu": 40.0, "memory": 80.0, "io": 120.0},
    )
    model = FreshExperienceModel(objective, {"active": (0.0, 15.0)})
    baseline_row = model.build_experience(
        {"active": 0.0, "pruned": 7.0},
        baseline.as_resource(),
    )
    validation = [
        model.build_experience(
            {"active": float(index), "pruned": 7.0},
            ScenarioMeasurement(
                100.0 + index,
                10.0 + 0.5 * index,
                20.0 + index,
                30.0 + 1.5 * index,
            ).as_resource(),
        )
        for index in range(1, 16)
    ]
    quality = model.select_quality_mode(
        FreshModelQualityThresholds(
            max_relative_error_p95=0.05,
            max_relative_error_max=0.05,
            min_r2=0.95,
            max_pairwise_inversion_rate=0.10,
        ),
        validation_rows=validation,
        always_training_rows=[baseline_row],
    )
    assert quality["passed"]
    assert quality["pinned_axis_reference"] == {"active": 0.0}


def test_multi_resource_convex_blend_repairs_joint_ranking_gate() -> None:
    """A gate requiring all three blends must not stop at one-resource search."""
    baseline = ScenarioMeasurement(100.0, 20.0, 30.0, 40.0)
    objective = FreshBaselineObjective(
        "none",
        baseline,
        resource_limits={"cpu": 100.0, "memory": 100.0, "io": 100.0},
    )
    model = FreshExperienceModel(objective, {"x": (0.0, 14.0)})
    rows = [
        model.build_experience(
            {"x": float(index)},
            ScenarioMeasurement(
                100.0,
                20.0 + index,
                30.0 + index,
                40.0 + index,
            ).as_resource(),
        )
        for index in range(15)
    ]

    def evaluation(offset: float, label: str) -> dict:
        predictions = [
            {
                "db_qps": 100.0,
                "cpu": row.measurement.cpu + offset,
                "memory": row.measurement.memory + offset,
                "io": row.measurement.io + offset,
            }
            for row in rows
        ]
        metrics = {
            name: {
                "passed": True,
                "relative_error_p95": 0.05,
                "relative_error_max": 0.05,
                "r2": 0.9,
            }
            for name in ("cpu", "memory", "io")
        }
        return {
            "mode": {"kind": "test_mode", "label": label},
            "estimated_measurements": predictions,
            "resource_metrics": metrics,
            "sample_count_gate_passed": True,
            "train_sample_count_per_fold": 14,
            "train_sample_counts_per_fold": [14] * 15,
            "always_training_row_count": 0,
            "required_train_sample_count_per_fold": 14,
            "required_test_sample_count": 15,
            "selection_key": [1, 1.0, 0.05, 0.05, -0.9],
        }

    def joint_gate(
        _rows: object,
        predicted: object,
        _thresholds: object,
    ) -> dict:
        measurements = list(predicted)  # type: ignore[arg-type]
        passed = all(
            abs(getattr(measurements[index], name) - getattr(rows[index].measurement, name))
            <= 1e-12
            for index in range(15)
            for name in ("cpu", "memory", "io")
        )
        metrics = {
            name: {
                "sample_count": 15,
                "mae": 0.0 if passed else 1.0,
                "relative_error_p95": 0.0 if passed else 0.05,
                "relative_error_max": 0.0 if passed else 0.05,
                "r2": 1.0 if passed else 0.9,
                "signed_residual_lower": 0.0,
                "signed_residual_upper": 0.0,
                "passed": True,
            }
            for name in ("cpu", "memory", "io")
        }
        rate = 0.0 if passed else 1.0
        return {
            "resource_metrics": metrics,
            "estimated_measurements": [item.as_resource() for item in measurements],
            "estimated_scores": [0.0] * 15,
            "real_scores": [row.objective for row in rows],
            "pairwise_inversion_count": 0 if passed else 1,
            "pairwise_pair_count": 1,
            "pairwise_inversion_rate": rate,
            "overall_pairwise_gate_passed": passed,
            "ranking_prefixes": {
                "top10": {"passed": passed, "pairwise_inversion_rate": rate},
                "top15": {"passed": passed, "pairwise_inversion_rate": rate},
            },
            "resource_error_gate_passed": True,
            "score_ranking_gate_passed": passed,
            "passed": passed,
            "selection_key": [0, 0.0, 0.0, 0.0, -1.0]
            if passed
            else [1, 1.0, 0.05, 0.05, -0.9],
        }

    model._prediction_quality_core = joint_gate  # type: ignore[method-assign]
    selected, audit = model._resourcewise_model_evaluations(
        rows,
        [evaluation(-1.0, "low"), evaluation(1.0, "high")],
        FreshModelQualityThresholds(),
        training_strategy="synthetic_joint_blend",
    )
    assert audit["passed"]
    assert audit["multi_resource_blend_candidate_count"] > 0
    assert selected[0]["resourcewise_search_phase"] == "multi_resource_convex_blend"
    for name in ("cpu", "memory", "io"):
        components = selected[0]["mode"]["resource_models"][name]
        assert len(components) == 2
        assert_close(sum(item["weight"] for item in components), 1.0)


def test_resourcewise_blend_recovers_when_no_base_resource_mode_passes() -> None:
    """Complementary failing modes may form a gate-passing resource model."""
    baseline = ScenarioMeasurement(100.0, 20.0, 30.0, 40.0)
    objective = FreshBaselineObjective(
        "none",
        baseline,
        resource_limits={"cpu": 100.0, "memory": 100.0, "io": 100.0},
    )
    model = FreshExperienceModel(objective, {"x": (0.0, 14.0)})
    rows = [
        model.build_experience(
            {"x": float(index)},
            ScenarioMeasurement(
                100.0,
                20.0 + index,
                30.0 + index,
                40.0 + index,
            ).as_resource(),
        )
        for index in range(15)
    ]

    def failing_evaluation(offset: float, label: str) -> dict:
        predictions = [
            {
                "db_qps": 100.0,
                "cpu": row.measurement.cpu + offset,
                "memory": row.measurement.memory + offset,
                "io": row.measurement.io + offset,
            }
            for row in rows
        ]
        metrics = {
            name: {
                "passed": False,
                "relative_error_p95": 0.25,
                "relative_error_max": 0.25,
                "r2": -0.3,
            }
            for name in ("cpu", "memory", "io")
        }
        return {
            "mode": {"kind": "test_mode", "label": label},
            "estimated_measurements": predictions,
            "resource_metrics": metrics,
            "sample_count_gate_passed": True,
            "train_sample_count_per_fold": 14,
            "train_sample_counts_per_fold": [14] * 15,
            "always_training_row_count": 0,
            "required_train_sample_count_per_fold": 14,
            "required_test_sample_count": 15,
            "selection_key": [1, 1.0, 0.25, 0.25, 0.3],
        }

    selected, audit = model._resourcewise_model_evaluations(
        rows,
        [
            failing_evaluation(-5.0, "low"),
            failing_evaluation(5.0, "high"),
        ],
        FreshModelQualityThresholds(),
        training_strategy="current_top15_only",
    )
    assert audit["passed"]
    assert audit["resource_error_recovery_candidate_count"] > 0
    assert audit["resource_error_recovery_passing_count"] > 0
    assert selected[0]["resourcewise_search_phase"] == (
        "multi_resource_convex_blend"
    )
    for name in ("cpu", "memory", "io"):
        components = selected[0]["mode"]["resource_models"][name]
        assert len(components) == 2
        assert_close(sum(item["weight"] for item in components), 1.0)


def main() -> None:
    test_measurement_validation()
    test_primary_ready_wait_rejects_recovery_state()
    test_fresh_calibration_is_capped_at_top15_replays()
    test_fresh_calibration_uses_the_candidate_log_grid()
    test_shared_formula()
    test_resource_estimation_uses_the_real_formula()
    test_ridge_prediction_falls_back_outside_empirical_support()
    test_fresh_model_preoptimizer_quality_gate()
    test_axis_nearest_additive_mode_keeps_interactions_off_coordinate_axes()
    test_axis_reference_stays_on_fresh_baseline_after_online_interactions()
    test_fresh_model_quality_gate_rejects_global_ranking_inversion()
    test_score_tie_margin_ignores_only_indistinguishable_pairs()
    test_fixed_training_rows_keep_independent_exact_replays_as_evidence()
    test_failed_historical_seed_falls_back_to_current_top15()
    test_quality_selection_skips_inapplicable_mode()
    test_fresh_baseline_is_a_fixed_leave_one_out_anchor()
    test_fresh_baseline_anchor_discards_pruned_parameter_axes()
    test_multi_resource_convex_blend_repairs_joint_ranking_gate()
    test_resourcewise_blend_recovers_when_no_base_resource_mode_passes()
    print("double_loop_scenario tests passed", flush=True)


if __name__ == "__main__":
    main()
