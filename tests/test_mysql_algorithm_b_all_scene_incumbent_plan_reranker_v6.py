from __future__ import annotations

import copy
import math

import pytest

from scripts import mysql_algorithm_b_all_scene_incumbent_plan_reranker_v6 as subject


def _model(*, ratios: dict[str, float], confidence: float,
           predicted: dict[str, float]) -> dict:
    return {
        "confidence": confidence,
        "predicted_resources": predicted,
        "raw_plan_work_ratios": ratios,
    }


def test_symmetric_plan_stability_ratio_handles_increase_and_decrease():
    assert subject.aggregate_plan_stability_ratio({
        "raw_plan_work_ratios": {"cpu": 1.0, "memory": 4.0, "io": 0.05},
    }) == pytest.approx(20.0)
    assert subject.aggregate_plan_stability_ratio({
        "raw_plan_work_ratios": {"cpu": 1.0, "memory": 1.0, "io": 1.0},
    }) == pytest.approx(1.0)


@pytest.mark.parametrize("bad", [0.0, -1.0, math.inf, math.nan])
def test_invalid_plan_ratio_is_softly_bounded_not_hard_pruned(bad: float):
    model = {"raw_plan_work_ratios": {"cpu": 1.0, "memory": 1.0, "io": bad}}
    confidence, ratio = subject.plan_stability_confidence(model, 0.35)
    assert ratio == pytest.approx(1_000_000.0)
    assert confidence == pytest.approx(subject.MINIMUM_PLAN_STABILITY_CONFIDENCE)


def test_plan_stable_candidate_outranks_extreme_optimistic_plan_jump():
    incumbent = {"cpu": 0.50, "memory": 0.94, "io": 0.10}
    stable = {
        "rank": 1,
        "optimized_params": {"row_evaluate_cost": 0.08},
        "resource_model": _model(
            ratios={"cpu": 1.0, "memory": 1.0, "io": 1.0},
            confidence=0.55,
            predicted={"cpu": 0.50, "memory": 0.928, "io": 0.10},
        ),
    }
    extreme = {
        "rank": 2,
        "optimized_params": {"row_evaluate_cost": 0.505},
        "resource_model": _model(
            ratios={"cpu": 0.997, "memory": 0.676, "io": 0.05},
            confidence=0.35,
            predicted={"cpu": 0.45, "memory": 0.90, "io": 0.094},
        ),
    }
    rows = [copy.deepcopy(stable), copy.deepcopy(extreme)]
    subject._apply_common_score(
        rows, scene="tpch_sf4_memory", incumbent_resources=incumbent,
    )

    stable_scored, extreme_scored = rows
    untrusted_extreme = subject.incumbent_relative_plan_score(
        target="memory",
        incumbent_resources=incumbent,
        predicted_resources=extreme["resource_model"]["predicted_resources"],
        confidence=1.0,
    )
    assert untrusted_extreme["raw_score"] > stable_scored[
        "incumbent_relative_plan_resource_score_before_confidence"
    ]
    assert extreme_scored["aggregate_plan_stability_ratio"] == pytest.approx(20.0)
    assert extreme_scored["incumbent_relative_plan_confidence"] == pytest.approx(
        0.35 / math.sqrt(20.0)
    )
    weight = 1.0 / math.sqrt(20.0)
    assert extreme_scored["plan_stability_prediction_weight"] == pytest.approx(weight)
    assert extreme_scored["plan_stability_adjusted_predicted_resources"] == pytest.approx({
        name: incumbent[name] + weight * (
            extreme["resource_model"]["predicted_resources"][name] - incumbent[name]
        )
        for name in ("cpu", "memory", "io")
    })
    assert stable_scored["incumbent_relative_plan_resource_score"] > extreme_scored[
        "incumbent_relative_plan_resource_score"
    ]
    assert all(row["candidate_hard_pruned_by_plan_stability"] is False for row in rows)


def test_soft_plan_stability_does_not_change_inner_score_or_use_qps():
    row = {
        "rank": 1,
        "optimized_params": {"row_evaluate_cost": 0.1},
        "candidate_score": 123.0,
        "qps": 999.0,
        "resource_model": _model(
            ratios={"cpu": 1.0, "memory": 1.0, "io": 0.05},
            confidence=0.35,
            predicted={"cpu": 0.4, "memory": 0.8, "io": 0.1},
        ),
    }
    subject._apply_common_score(
        [row],
        scene="tpch_sf4_memory",
        incumbent_resources={"cpu": 0.5, "memory": 0.9, "io": 0.1},
    )
    assert row["candidate_score"] == 123.0
    assert row["qps"] == 999.0
    assert row["qps_used_for_incumbent_relative_plan_score"] is False
    assert row["candidate_hard_pruned_by_plan_stability"] is False


def test_stable_plan_resource_prediction_is_bit_identical():
    incumbent = {"cpu": 0.5, "memory": 0.9, "io": 0.1}
    predicted = {"cpu": 0.4, "memory": 0.8, "io": 0.2}
    adjusted, weight = subject.plan_stability_adjusted_prediction(
        incumbent_resources=incumbent,
        predicted_resources=predicted,
        stability_ratio=1.0,
    )
    assert weight == 1.0
    assert adjusted == predicted


def test_calibration_support_softly_downweights_out_of_distribution_combo():
    ranges = {
        "row_evaluate_cost": {"lower": 0.01, "upper": 1.0},
        "disk_temptable_row_cost": {"lower": 0.05, "upper": 5.0},
    }
    seeds = [
        {"row_evaluate_cost": 0.1, "disk_temptable_row_cost": 0.5},
        {"row_evaluate_cost": 0.134, "disk_temptable_row_cost": 0.5},
        {"row_evaluate_cost": 0.1, "disk_temptable_row_cost": 2.525},
    ]
    supported = subject.calibration_support_distance(
        params=seeds[1], calibration_seed_points=seeds,
        ranges=ranges, active=list(ranges),
    )
    extrapolated = subject.calibration_support_distance(
        params={"row_evaluate_cost": 0.505, "disk_temptable_row_cost": 2.525},
        calibration_seed_points=seeds, ranges=ranges, active=list(ranges),
    )
    assert supported == 0.0
    assert extrapolated > 0.25
    assert subject.calibration_support_weight(supported) == 1.0
    assert 0.0 < subject.calibration_support_weight(extrapolated) < 0.61
