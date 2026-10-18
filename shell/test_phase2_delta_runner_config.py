#!/usr/bin/env python3
"""Regression checks for Phase2 delta-calibration runner plumbing."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import run_pg_benchmark_bottleneck_matrix as runner


EXPECTED_COST_PARAMS = {
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
}


def parse_args(extra: list[str], *, bottleneck: str = "none") -> object:
    old_argv = sys.argv
    try:
        sys.argv = [
            "run_pg_benchmark_bottleneck_matrix.py",
            "--scenario-set",
            "bonect",
            "--bonect-bottlenecks",
            bottleneck,
            "--run-id",
            "phase2_delta_runner_config_test",
            "--use-resource-estimator",
            *extra,
        ]
        return runner.parse_args()
    finally:
        sys.argv = old_argv


def build_config(args: object, run_id: str, *, bottleneck: str = "none") -> dict:
    spec = runner.CommandSpec(
        benchmark="hybench_xp",
        bottleneck=bottleneck,
        command=[],
        cwd=Path(runner.PROJECT_ROOT),
        env={},
        log_path=Path("/tmp/phase2_delta_runner_config_test.log"),
        pg_dbname="hybench_sf10x",
    )
    path = runner.build_optimizer_config(spec, args, run_id)
    return json.loads(path.read_text(encoding="utf-8"))


def test_top15_sparse_delta_config() -> None:
    with tempfile.TemporaryDirectory(prefix="phase2-delta-runner-") as temp_dir:
        root = Path(temp_dir)
        artifact = root / "delta_calibrator.json"
        artifact.write_text(
            json.dumps({"training_gate": {"passed": True}}),
            encoding="utf-8",
        )
        args = parse_args([
            "--generated-config-dir",
            str(root / "generated"),
            "--bruteforce-candidate-top-k",
            "15",
            "--bruteforce-candidate-replay-top-k",
            "15",
            "--phase2-real-delta-calibration-artifact",
            str(artifact),
            "--phase2-real-delta-min-plan-feature-coverage",
            "0.85",
            "--bonect-pair-probe-mode",
            "sparse",
            "--bonect-pair-probe-max-combination-size",
            "3",
            "--bonect-pair-probe-max-candidates",
            "4935",
        ])
        cfg = build_config(args, "phase2_delta_runner_sparse_test")

    bruteforce = cfg["optimizer"]["bruteforce"]
    assert bruteforce["candidate_top_k"] == 15, bruteforce["candidate_top_k"]
    assert bruteforce["candidate_replay_top_k"] == 15, bruteforce[
        "candidate_replay_top_k"
    ]
    assert bruteforce["small_loop_min_validated_candidate_count"] == 15
    pair_probe = bruteforce["candidate_pair_probe"]
    assert pair_probe["full_factorial_grid"] is False, pair_probe
    assert pair_probe["max_combination_size"] == 3, pair_probe
    assert pair_probe["max_candidates"] == 4935, pair_probe
    calibration = bruteforce["candidate_gate"]["real_delta_calibration"]
    assert (
        bruteforce["candidate_gate"]["adaptive_replay_controller_enabled"]
        is False
    )
    assert calibration["enabled"] is True, calibration
    assert calibration["artifact_path"] == str(artifact.resolve()), calibration
    assert calibration["require_training_gate"] is True, calibration
    assert calibration["require_prediction"] is True, calibration
    assert calibration["require_support"] is True, calibration
    assert calibration["unsupported_policy"] == "keep_uncertain", calibration
    assert calibration["uncertain_replay_min_count"] == 3, calibration
    assert calibration["min_plan_feature_coverage"] == 0.85, calibration
    interaction_probe = bruteforce["candidate_gate"][
        "preflight_param_feature_filter"
    ]["interaction_probe"]
    assert interaction_probe == {
        "enabled": True,
        "max_partner_params": 3,
        "min_changed_pair_count": 1,
    }, interaction_probe
    assert set(cfg["initial_params"]) == EXPECTED_COST_PARAMS, cfg["initial_params"]
    assert set(cfg["param_ranges"]) == EXPECTED_COST_PARAMS, cfg["param_ranges"]
    iteration_goal = cfg["final_evaluation"]["feedback_loop"][
        "resource_iteration_goal"
    ]
    strong = iteration_goal["strong_constraints"]
    assert iteration_goal["resource_direction_policy"] == "none_any_sweet_up"
    assert strong["none_require_all_resources_up"] is False
    assert strong["none_require_any_final_resource_in_good_range"] is True
    assert strong["none_initial_resource_ranges"] == {
        "cpu": [20.0, 35.0],
        "memory": [15.0, 50.0],
        "io": [12.0, 30.0],
    }
    assert strong["none_require_any_resource_lifted_into_good_range"] is True
    assert strong["none_min_sweet_lift"] == {
        "cpu": 10.0,
        "memory": 10.0,
        "io": 10.0,
    }
    assert strong["io_good_range_uses_normalized_mib_s"] is True
    assert strong["bottleneck_require_single_initial_bottleneck"] is True
    assert strong["bottleneck_initial_target_min_util_pct"] == 80.0
    assert strong["bottleneck_initial_non_target_max_util_pct"] == 79.999
    assert strong["bottleneck_require_target_not_bottleneck"] is True
    assert strong["bottleneck_require_target_in_good_range"] is False
    assert strong["bottleneck_require_any_other_in_good_range"] is True
    assert strong["target_min_decrease_pp"] == 5.0
    assert strong["preferred_target_decrease_pp"] == 10.0


def test_default_full_grid_is_unchanged() -> None:
    with tempfile.TemporaryDirectory(prefix="phase2-delta-runner-default-") as temp_dir:
        args = parse_args(["--generated-config-dir", str(Path(temp_dir) / "generated")])
        cfg = build_config(args, "phase2_delta_runner_default_test")
    bruteforce = cfg["optimizer"]["bruteforce"]
    pair_probe = bruteforce["candidate_pair_probe"]
    assert bruteforce["candidate_top_k"] == 20, bruteforce["candidate_top_k"]
    assert bruteforce["candidate_replay_top_k"] == 20
    assert pair_probe["full_factorial_grid"] is True, pair_probe
    assert pair_probe["max_combination_size"] == 7, pair_probe
    assert pair_probe["max_candidates"] == 78125, pair_probe
    assert bruteforce["candidate_gate"]["real_delta_calibration"]["enabled"] is False


def test_missing_delta_artifact_is_rejected() -> None:
    with tempfile.TemporaryDirectory(prefix="phase2-delta-runner-missing-") as temp_dir:
        root = Path(temp_dir)
        args = parse_args([
            "--generated-config-dir",
            str(root / "generated"),
            "--phase2-real-delta-calibration-artifact",
            str(root / "missing.json"),
        ])
        try:
            build_config(args, "phase2_delta_runner_missing_test")
        except RuntimeError as exc:
            assert "artifact is not a file" in str(exc), exc
        else:
            raise AssertionError("missing delta artifact must fail config generation")


def test_blind_recovery_contains_no_teacher_or_historical_seed_memory() -> None:
    with tempfile.TemporaryDirectory(prefix="phase2-blind-runner-") as temp_dir:
        args = parse_args([
            "--generated-config-dir",
            str(Path(temp_dir) / "generated"),
            "--phase2-blind-recovery",
        ])
        cfg = build_config(args, "phase2_blind_runner_test")
    bruteforce = cfg["optimizer"]["bruteforce"]
    gate = bruteforce["candidate_gate"]
    pair_probe = bruteforce["candidate_pair_probe"]
    expected_ranges = {
        "seq_page_cost": (0.0001, 100.0),
        "random_page_cost": (0.0001, 20.0),
        "cpu_tuple_cost": (0.000001, 1.0),
        "cpu_index_tuple_cost": (0.000001, 1.0),
        "cpu_operator_cost": (0.000001, 1.0),
        "parallel_setup_cost": (0.0, 10000.0),
        "parallel_tuple_cost": (0.000001, 10.0),
    }
    assert cfg["initial_params"] == {
        "seq_page_cost": 1.0,
        "random_page_cost": 4.0,
        "cpu_tuple_cost": 0.01,
        "cpu_index_tuple_cost": 0.005,
        "cpu_operator_cost": 0.0025,
        "parallel_setup_cost": 1000.0,
        "parallel_tuple_cost": 0.1,
    }
    assert set(cfg["param_ranges"]) == set(expected_ranges)
    for name, bounds in expected_ranges.items():
        actual = cfg["param_ranges"][name]
        assert (actual["min_value"], actual["max_value"]) == bounds, (name, actual)
        assert actual["discrete_count"] == 5, (name, actual)
    assert set(bruteforce["active_params"]) == set(expected_ranges)
    assert bruteforce["max_param_delta_rel_pct"] == 0.0
    assert bruteforce["max_iter"] >= 24
    assert bruteforce["candidate_top_k"] >= 60
    assert bruteforce["candidate_replay_top_k"] >= 15
    assert bruteforce["range_update_strategy"] == "binary_interval"
    assert bruteforce["blind_recovery"]["enabled"] is True
    assert bruteforce["force_best_replay_anchor"] is True
    assert bruteforce["force_best_replay_anchor_seed_records"] == []
    assert bruteforce["teacher_recovery_audit"] == {}
    assert pair_probe["full_factorial_grid"] is True
    assert pair_probe["values_per_param"] == 5
    assert pair_probe["max_combination_size"] == 7
    assert pair_probe["max_candidates"] == 78125
    assert pair_probe["anchor_probe"]["anchors"] == []
    assert pair_probe["near_hit_anchor_probe"]["anchors"] == []
    assert pair_probe["near_hit_anchor_probe"]["boundary_anchors"] == []
    assert gate["preflight_param_feature_filter"]["enabled"] is True
    assert gate["min_delta_util_pct"] == 0.0
    assert gate["score_goal_prefix_min_target_down_util_pct"] == 0.0
    assert gate["score_goal_prefix_min_other_up_util_pct"] == 0.0
    assert (
        gate["preflight_param_feature_filter"]["interaction_probe"]["max_partner_params"]
        == 6
    )
    assert gate["allow_known_real_direction_seed_gate_bypass"] is False
    assert gate["known_real_seed_prefix_reserve"]["enabled"] is False
    assert gate["db_qps_known_risk_penalty_enabled"] is False
    assert gate["feedback_rejected_neighborhoods"] == []
    assert gate["cpu_low_tuple_cost_risk_enabled"] is False
    assert gate["cpu_low_operator_cost_risk_enabled"] is False
    assert gate["cpu_low_seq_high_parallel_risk_enabled"] is False


def test_blind_recovery_rejects_coordinate_overrides() -> None:
    with tempfile.TemporaryDirectory(prefix="phase2-blind-override-") as temp_dir:
        args = parse_args([
            "--generated-config-dir",
            str(Path(temp_dir) / "generated"),
            "--phase2-blind-recovery",
            "--bonect-initial-param",
            "seq_page_cost=75",
        ])
        try:
            build_config(args, "phase2_blind_override_test")
        except RuntimeError as exc:
            assert "coordinate-bearing overrides" in str(exc), exc
        else:
            raise AssertionError("blind recovery must reject coordinate overrides")


def test_teacher_holdout_uses_same_broad_search_without_affecting_selection() -> None:
    with tempfile.TemporaryDirectory(prefix="phase2-teacher-holdout-") as temp_dir:
        args = parse_args([
            "--generated-config-dir",
            str(Path(temp_dir) / "generated"),
            "--phase2-teacher-recovery-config",
            str(
                Path(runner.PROJECT_ROOT)
                / "config/bonect_phase2_vm_teacher_recovery_20260711.json"
            ),
        ])
        cfg = build_config(args, "phase2_teacher_holdout_test")
    bruteforce = cfg["optimizer"]["bruteforce"]
    blind = bruteforce["blind_recovery"]
    pair_probe = bruteforce["candidate_pair_probe"]
    assert blind["enabled"] is True
    assert blind["answer_blind"] is False
    assert blind["teacher_holdout_diagnostic"] is True
    assert blind["teacher_coordinates_loaded"] is True
    assert blind["teacher_coordinates_affect_selection"] is False
    assert bruteforce["teacher_recovery_audit"]["enabled"] is True
    assert bruteforce["teacher_recovery_audit"]["stop_after_natural_recall"] is True
    assert bruteforce["force_best_replay_anchor_seed_records"] == []
    assert bruteforce["max_param_delta_rel_pct"] == 0.0
    assert bruteforce["range_update_strategy"] == "binary_interval"
    assert bruteforce["candidate_top_k"] >= 60
    assert pair_probe["anchor_probe"]["anchors"] == []
    assert pair_probe["near_hit_anchor_probe"]["anchors"] == []
    assert all(
        item["discrete_count"] == 5 for item in cfg["param_ranges"].values()
    )


def test_bottleneck_candidate_direction_gate_keeps_near_miss_recall_margin() -> None:
    with tempfile.TemporaryDirectory(prefix="phase2-cpu-gate-") as temp_dir:
        args = parse_args(
            ["--generated-config-dir", str(Path(temp_dir) / "generated")],
            bottleneck="cpu",
        )
        cfg = build_config(args, "phase2_cpu_gate_test", bottleneck="cpu")
    gate = cfg["optimizer"]["bruteforce"]["candidate_gate"]
    assert gate["min_delta_util_pct"] == 0.5
    assert gate["min_other_up_util_pct"] == 0.01
    assert gate["score_goal_prefix_min_target_down_util_pct"] == 0.5
    assert gate["score_goal_prefix_min_other_up_util_pct"] == 0.01


def main() -> None:
    test_top15_sparse_delta_config()
    test_default_full_grid_is_unchanged()
    test_missing_delta_artifact_is_rejected()
    test_blind_recovery_contains_no_teacher_or_historical_seed_memory()
    test_blind_recovery_rejects_coordinate_overrides()
    test_teacher_holdout_uses_same_broad_search_without_affecting_selection()
    test_bottleneck_candidate_direction_gate_keeps_near_miss_recall_margin()
    print("PASS Phase2 delta runner config tests", flush=True)


if __name__ == "__main__":
    main()
