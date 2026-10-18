from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import mysql_algorithm_b_all_scene_incumbent_plan_reranker_v7 as ranker
from scripts import mysql_algorithm_b_all_scene_incumbent_plan_reranker_v8 as ranker_v8
from scripts import (
    run_mysql_four_scene_algorithm_b_dynamic_support_resume_core_v27 as core,
)
from scripts import run_mysql_four_scene_algorithm_b_dynamic_support_resume_v27 as controller
from scripts import run_mysql_four_scene_algorithm_b_pg_outer_fresh_v32 as pg_controller


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v26_formal_fresh_plan_stability_20260819_attempt025"
)


def load(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def test_dynamic_support_anchors_a_real_incumbent_outside_initial_calibration():
    ranges = {
        "x": {"lower": 0.01, "upper": 1.0},
        "y": {"lower": 0.05, "upper": 5.0},
    }
    calibration = [{"x": 0.1, "y": 0.5}]
    incumbent = {"x": 0.75, "y": 3.5}
    probe = {"x": 0.73, "y": 3.5}
    calibration_distance = ranker.calibration_support_distance(
        params=probe, calibration_seed_points=calibration,
        ranges=ranges, active=list(ranges),
    )
    dynamic_distance = ranker.dynamic_support_distance(
        params=probe, support_points=[*calibration, incumbent],
        ranges=ranges, active=list(ranges),
    )
    assert calibration_distance > 0.25
    assert dynamic_distance < 0.01
    assert ranker.dynamic_support_weight(dynamic_distance) > 0.999


def test_actual_v26_completed_outer_boundary_is_valid_and_partial_outer3_has_no_credit():
    previous = core.CONTINUATION_COMPLETED_OUTER_ATTEMPT
    core.CONTINUATION_COMPLETED_OUTER_ATTEMPT = 2
    try:
        evidence = core.validate_completed_outer_continuation(SOURCE)
    finally:
        core.CONTINUATION_COMPLETED_OUTER_ATTEMPT = previous
    assert evidence["completed_outer_attempt"] == 2
    assert evidence["interrupted_outer_attempt"] == 3
    assert evidence["interrupted_outer_measurement_credit"] is False
    assert set(evidence["completed_outer_evaluations"]) == {1, 2}
    assert all(
        len(evidence["completed_outer_evaluations"][outer][scene]) == 5
        for outer in (1, 2)
        for scene in core.SCENES
    )


def test_old_v27_contract_is_immutable_and_v32_successor_is_bound():
    # The old core file was deliberately changed.  Its immutable v27 contract
    # must now reject the changed hash rather than silently authorizing it.
    with pytest.raises(RuntimeError, match="v27 source binding differs"):
        controller.validate_contract()
    contract = pg_controller.validate_contract()
    drain = pg_controller.validate_stop_drain()
    assert contract["release_version"] == "v32"
    assert contract["pg_search_outer"]["accepted_target"] == 10
    assert contract["pg_search_outer"]["post_search_stability_confirmation"] is False
    assert drain["status"] == "PASS"
    assert drain["all_workers_zero_sessions_after"] is True


@pytest.mark.parametrize(
    "scene",
    ["hybench_xp_cpu", "hybench_xp_io", "tpch_sf4_memory"],
)
def test_actual_outer3_local_probe_gets_real_incumbent_support(scene: str):
    state = load(SOURCE / "run_state.json")
    rankings = load(SOURCE / "outer_003/predicted_rankings.json")
    local = next(
        row for row in rankings["selected_top_k"][scene]
        if "incumbent_local" in str(row.get("subset_id") or "")
    )
    calibration = load(SOURCE / "calibration_design.json")["scene_designs"][scene]
    calibration_points = [dict(row["params"]) for row in calibration]
    incumbent = dict(state["params_by_scene"][scene])
    ranges = dict(state["ranges_by_scene"][scene])
    active = list(state["active_by_scene"][scene])
    old_distance = ranker.calibration_support_distance(
        params=local["optimized_params"],
        calibration_seed_points=calibration_points,
        ranges=ranges, active=active,
    )
    new_distance = ranker.dynamic_support_distance(
        params=local["optimized_params"],
        support_points=[*calibration_points, incumbent],
        ranges=ranges, active=active,
    )
    assert new_distance < old_distance
    new_weight = ranker.dynamic_support_weight(new_distance)
    assert new_weight > 0.01
    assert new_weight > float(local["calibration_support_weight"])


def test_dynamic_support_score_does_not_mutate_inner_score_or_use_qps():
    row = {
        "candidate_score": 9.0,
        "qps": 123.0,
        "dynamic_support_weight": 0.8,
        "resource_model": {
            "confidence": 0.55,
            "predicted_resources": {"cpu": 0.5, "memory": 0.8, "io": 0.1},
            "raw_plan_work_ratios": {"cpu": 1.0, "memory": 1.0, "io": 1.0},
        },
    }
    ranker._apply_common_score(
        [row], scene="tpch_sf4_memory",
        incumbent_resources={"cpu": 0.5, "memory": 0.9, "io": 0.1},
    )
    assert row["candidate_score"] == 9.0
    assert row["qps"] == 123.0
    assert row["qps_used_for_incumbent_relative_plan_score"] is False
    assert row["candidate_hard_pruned_by_plan_stability"] is False


def test_v28_dynamic_support_keyword_is_not_leaked_to_frozen_base_ranker():
    captured = {}

    def frozen_base(**kwargs):
        captured.update(kwargs)
        return {"status": "PASS", "selected_top5": []}

    previous_base = ranker_v8.BASE_RANK_SCENE_OVERRIDE
    previous_outer = ranker_v8.EFFECTIVE_FROM_OUTER
    ranker_v8.BASE_RANK_SCENE_OVERRIDE = frozen_base
    ranker_v8.EFFECTIVE_FROM_OUTER = 99
    try:
        result = ranker_v8.rank_scene(
            scene="job_memory", round_index=1,
            dynamic_support_points=[{"row_evaluate_cost": 0.1}],
        )
    finally:
        ranker_v8.BASE_RANK_SCENE_OVERRIDE = previous_base
        ranker_v8.EFFECTIVE_FROM_OUTER = previous_outer
    assert result["status"] == "PASS"
    assert "dynamic_support_points" not in captured


def test_cached_replay_keeps_measured_candidate_identity_across_outer_rankings():
    measured = {"row_evaluate_cost": 0.8}
    row = {
        "rank": 1,
        "params": measured,
        "cached_valid_replay_redecision": True,
        "candidate_ranking": {
            "rank": 4,
            "optimized_params": measured,
            "subset_id": "outer_1_measured_candidate",
        },
    }
    current = [{
        "rank": 1,
        "optimized_params": {"row_evaluate_cost": 0.2},
        "subset_id": "outer_2_unrelated_rank_1",
    }]
    selected = core.evaluation_candidate_ranking(
        scene="hybench_xp_io", row=row, current_rankings=current,
    )
    assert selected["optimized_params"] == measured
    assert selected["subset_id"] == "outer_1_measured_candidate"


def test_fresh_evaluation_rejects_current_rank_parameter_mismatch():
    with pytest.raises(RuntimeError, match="evaluation candidate identity differs"):
        core.evaluation_candidate_ranking(
            scene="hybench_xp_io",
            row={"rank": 1, "params": {"row_evaluate_cost": 0.8}},
            current_rankings=[{
                "rank": 1,
                "optimized_params": {"row_evaluate_cost": 0.2},
            }],
        )
