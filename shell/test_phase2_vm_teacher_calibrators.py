#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "shell") not in sys.path:
    sys.path.insert(0, str(ROOT / "shell"))

from train_bonect_phase2_vm_teacher_calibrators import (  # noqa: E402
    build_blind_deployment_artifact,
    params_equal,
    scenario_direction_pass,
)

from phase2_delta_calibrator import Phase2DeltaCalibrator


def test_direction_semantics() -> None:
    passed, _ = scenario_direction_pass(
        "memory",
        {
            "db_qps_delta_pct": 20.0,
            "cpu_delta_pct": 1.0,
            "memory_delta_pct": -5.0,
            "io_delta_pct": -2.0,
        },
        min_db_gain_pct=15.0,
    )
    assert passed
    passed, _ = scenario_direction_pass(
        "io",
        {
            "db_qps_delta_pct": 20.0,
            "cpu_delta_pct": -1.0,
            "memory_delta_pct": 2.0,
            "io_delta_pct": -5.0,
        },
        min_db_gain_pct=15.0,
    )
    assert passed
    passed, _ = scenario_direction_pass(
        "none",
        {
            "db_qps_delta_pct": 20.0,
            "cpu_delta_pct": -1.0,
            "memory_delta_pct": 0.0,
            "io_delta_pct": 2.0,
        },
        min_db_gain_pct=15.0,
    )
    assert passed
    failed, _ = scenario_direction_pass(
        "cpu",
        {
            "db_qps_delta_pct": 20.0,
            "cpu_delta_pct": -5.0,
            "memory_delta_pct": -1.0,
            "io_delta_pct": -2.0,
        },
        min_db_gain_pct=15.0,
    )
    assert not failed


def test_teacher_equality() -> None:
    params = {
        "seq_page_cost": 1.0,
        "random_page_cost": 4.0,
        "cpu_tuple_cost": 0.01,
        "cpu_index_tuple_cost": 0.005,
        "cpu_operator_cost": 0.0025,
        "parallel_setup_cost": 1000.0,
        "parallel_tuple_cost": 0.1,
    }
    assert params_equal(params, dict(params))
    changed = dict(params)
    changed["seq_page_cost"] = 1.1
    assert not params_equal(params, changed)


def test_blind_deployment_artifact_strips_coordinates() -> None:
    artifact = {
        "baseline_params": {
            "seq_page_cost": 1.0,
            "random_page_cost": 4.0,
            "cpu_tuple_cost": 0.01,
            "cpu_index_tuple_cost": 0.005,
            "cpu_operator_cost": 0.0025,
            "parallel_setup_cost": 1000.0,
            "parallel_tuple_cost": 0.1,
        },
        "baseline_plan_features": {},
        "baseline_resource": {"cpu": 1.0, "memory": 1.0, "io": 1.0},
        "feature_names": ["param_log:seq_page_cost"],
        "feature_scales": [1.0],
        "bandwidth": 1.0,
        "support": {"max_nearest_distance": 10.0},
        "training_gate": {"passed": True},
        "teacher_holdout": {"params": {"seq_page_cost": 75.0}},
        "bandwidth_evaluations": [{"bandwidth": 1.0, "loo_predictions": [[1.0]]}],
        "points": [
            {
                "point_name": "neighbor",
                "params": {"seq_page_cost": 50.0},
                "x": [1.0],
                "labels": {
                    "db_qps_delta_pct": 20.0,
                    "cpu_delta_pct": -5.0,
                    "memory_delta_pct": 2.0,
                    "io_delta_pct": 1.0,
                },
            }
        ],
    }
    deployment = build_blind_deployment_artifact(artifact)
    assert "teacher_holdout" not in deployment
    assert "params" not in deployment["points"][0]
    assert "loo_predictions" not in deployment["bandwidth_evaluations"][0]
    assert deployment["blind_deployment"]["exact_teacher_coordinates_present"] is False
    calibrator = Phase2DeltaCalibrator(deployment)
    prediction = calibrator.predict(
        params={**artifact["baseline_params"], "seq_page_cost": 2.718281828},
        plan_features={},
    )
    assert prediction["available"] is True


def main() -> None:
    test_direction_semantics()
    test_teacher_equality()
    test_blind_deployment_artifact_strips_coordinates()
    print("PASS Phase2 VM teacher calibrator tests", flush=True)


if __name__ == "__main__":
    main()
