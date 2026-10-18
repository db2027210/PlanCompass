from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
import sys
import types


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from parameter_optimizer import (
    apply_baseline_correction_scales,
    baseline_anchor_correction_exempt_resources,
    validate_resource_model_baseline_sanity_gate,
    validate_resource_model_quality_gate,
)
from plan_resource_estimator import PlanResourceEstimator
from resource_model_retrainer import (
    _extract_model_prediction,
    apply_baseline_error_bounds,
)


class _CollapsedIoPredictor:
    def predict_io(self, **_: object) -> dict:
        return {
            "available": True,
            "prediction_mb_per_window": 14678.0,
            "prediction_before_error_bound": 3973.0,
            "prediction_before_anchor_calibration": 0.0,
            "baseline_anchor_calibration": {
                "applied": True,
                "actual_target": 3973.0,
                "predicted_target_before_error_bound": 0.0,
            },
        }


class _LargeIoFeaturePredictor:
    def predict_io(self, **_: object) -> dict:
        return {
            "available": True,
            "model_set": "feature_predictor",
            "prediction_mb_per_window": 5000.0,
            "prediction_before_error_bound": 5000.0,
            "prediction_before_anchor_calibration": 5000.0,
        }


class _BadCpuAnchorPredictor:
    def predict_cpu(self, **_: object) -> dict:
        return {
            "available": True,
            "prediction_cpu_seconds_per_window": 370.0,
            "prediction_before_error_bound": 34.0,
            "prediction_before_anchor_calibration": 370.0,
            "baseline_anchor_calibration": {
                "applied": True,
                "actual_target": 34.0,
                "predicted_target_before_error_bound": 370.0,
                "unclamped_delta_scale": 0.09,
            },
        }


class _AnchoredMemoryPlateauPredictor:
    def predict_memory(self, **_: object) -> dict:
        return {
            "available": True,
            "prediction_memory_mb": 80.08,
            "prediction_before_error_bound": 80.08,
            "prediction_before_anchor_calibration": 50.1,
            "baseline_anchor_calibration": {
                "applied": True,
                "actual_target": 80.0,
                "predicted_target_before_error_bound": 50.0,
                "delta_scale": 0.8,
                "plan_formula_predicted_target_before_error_bound": 100.0,
                "plan_formula_delta_scale": 0.8,
            },
        }


class _AnchoredIoDisagreementPredictor:
    def predict_io(self, **_: object) -> dict:
        return {
            "available": True,
            "model_set": "C",
            "prediction_mb_per_window": 72.0,
            "prediction_before_error_bound": 72.0,
            "prediction_before_anchor_calibration": 40.0,
            "prediction_window_seconds": 5.0,
            "baseline_anchor_calibration": {
                "applied": True,
                "actual_target": 80.0,
                "predicted_target_before_error_bound": 50.0,
                "delta_scale": 0.8,
                "plan_formula_predicted_target_before_error_bound": 100.0,
                "plan_formula_delta_scale": 0.8,
            },
        }


class ResourceModelBaselineSanityTest(unittest.TestCase):
    def test_anchor_builder_selects_raw_prediction_not_already_anchored_value(self) -> None:
        prediction, window_seconds = _extract_model_prediction(
            {
                "prediction_before_anchor_calibration": 50.0,
                "prediction_before_error_bound": 80.0,
                "prediction_memory_mb": 180.0,
                "prediction_window_seconds": 5.0,
            },
            "prediction_memory_mb",
        )

        self.assertEqual(prediction, 50.0)
        self.assertEqual(window_seconds, 5.0)

    def test_job_point_0002_post_retrain_anchor_uses_current_raw_model(self) -> None:
        actual_normalized = 2.5460795610310645
        actual_mb = 2607.18547049581
        stale_pre_retrain_prediction_mb = 318.36073060657066
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory_path = root / "memory.json"
            memory_path.write_text(
                json.dumps(
                    {
                        "default_model_set": "TP_Hybrid",
                        "models": {
                            "TP_Hybrid": {
                                "model_type": "constant",
                                "intercept": actual_mb,
                                "features": [],
                                "coef": [],
                                "baseline_training": {
                                    "source": "current_baseline_actual",
                                    "actual_memory_mb": actual_mb,
                                    "predicted_after_retrain_before_error_bound_mb": actual_mb,
                                },
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            summary = apply_baseline_error_bounds(
                artifact_paths={"memory_json": str(memory_path)},
                baseline_actual_normalized={"memory": actual_normalized},
                baseline_estimation_metadata={
                    "resource_breakdown": {
                        "memory": {
                            "concurrency_breakdown": {
                                "concurrent_memory_bytes": 118.91480284905019
                                * 1024.0
                                * 1024.0,
                            },
                            "calibration_breakdown": {
                                "learned_model": {
                                    "model_set": "TP_Hybrid",
                                    "prediction_before_anchor_calibration": (
                                        stale_pre_retrain_prediction_mb
                                    ),
                                    "prediction_before_error_bound": actual_mb,
                                }
                            },
                        }
                    }
                },
                root=root,
            )

            self.assertIn("memory", summary["updated"])
            registry = json.loads(memory_path.read_text(encoding="utf-8"))
            anchor = registry["models"]["TP_Hybrid"]["baseline_anchor_calibration"]
            self.assertAlmostEqual(
                anchor["pre_model_mutation_prediction_before_anchor"],
                stale_pre_retrain_prediction_mb,
            )
            self.assertEqual(
                anchor["raw_prediction_source"],
                "current_model_baseline_training_after_retrain",
            )
            self.assertAlmostEqual(
                anchor["predicted_target_before_error_bound"], actual_mb
            )
            self.assertAlmostEqual(anchor["delta_scale"], 1.0)
            self.assertEqual(anchor["anchor_application_count"], 1)

    def test_cpu_fallback_ignores_candidate_scaled_planner_cost(self) -> None:
        estimator = PlanResourceEstimator.__new__(PlanResourceEstimator)
        estimator.estimator_cfg = {
            "cpu": {
                "cpu_tuple_cost": 0.01,
                "cpu_index_tuple_cost": 0.005,
                "cpu_operator_cost": 0.0025,
                "parallel_setup_cost": 1000.0,
                "parallel_tuple_cost": 0.1,
                "fallback_cost_scale": 1.0,
            }
        }
        base_node = {
            "node_type": "seq scan",
            "rows": 1000.0,
            "self_cost": 100.0,
            "children": [],
            "below_gather": False,
        }
        inflated_node = dict(base_node, self_cost=1_000_000.0)

        base = estimator._estimate_node_cpu(node=base_node, node_by_id={})
        inflated = estimator._estimate_node_cpu(node=inflated_node, node_by_id={})

        self.assertAlmostEqual(base["cpu_work"], 10.0)
        self.assertEqual(base, inflated)

    def test_quality_gate_checks_runtime_selected_io_model(self) -> None:
        def metrics(r2: float) -> dict:
            return {
                "train": {"sample_count": 20},
                "test": {
                    "sample_count": 10,
                    "relative_error_p95": 0.2,
                    "relative_error_max": 0.4,
                    "r2": r2,
                },
            }

        summary = validate_resource_model_quality_gate(
            {
                "resources": {
                    "io": {
                        "C": {"total": metrics(0.4)},
                        "CH": {"total": metrics(-0.5)},
                    }
                }
            },
            {
                "enabled": True,
                "required_resources": ["io"],
                "optional_resources": [],
                "selected_model_only": True,
                "workload_family": "CH",
                "io_hard_targets": ["total"],
                "min_test_r2": 0.0,
                "enforce_test_r2": True,
            },
            baseline_estimation_metadata={
                "resource_breakdown": {
                    "io": {
                        "calibration_breakdown": {
                            "learned_model": {"model_set": "C"}
                        }
                    }
                }
            },
        )

        self.assertTrue(summary["passed"])
        self.assertEqual(
            summary["thresholds"]["runtime_selected_model_sets"],
            {"io": "C"},
        )
        self.assertEqual(
            [item["model"] for item in summary["checks"]],
            ["C.total"],
        )

    def test_anchored_resources_are_exempt_from_outer_baseline_correction(self) -> None:
        exempt = baseline_anchor_correction_exempt_resources(
            applied_model_paths={"cpu": "cpu.json", "io": "io.json", "memory": "memory.json"},
            baseline_error_bounds={"updated": {"cpu": {}, "io": {}, "memory": {}}},
            baseline_sanity_gate={
                "passed": True,
                "checks": [
                    {
                        "resource": resource,
                        "passed": True,
                        "baseline_anchor_calibration": {"applied": True},
                    }
                    for resource in ("cpu", "io", "memory")
                ],
            },
        )

        self.assertEqual(exempt, ["cpu", "io", "memory"])
        corrected = apply_baseline_correction_scales(
            {"cpu": 20.0, "memory": 5.0, "io": 6.0},
            scales={"cpu": 0.5, "memory": 0.5, "io": 0.05},
            exempt_resources=exempt,
        )
        self.assertEqual(corrected, {"cpu": 20.0, "memory": 5.0, "io": 6.0})

    def test_learned_point_prediction_uses_value_before_error_bound(self) -> None:
        estimator = PlanResourceEstimator.__new__(PlanResourceEstimator)
        estimator.estimator_cfg = {
            "shared": {
                "learned_point_prediction_stage": (
                    "after_baseline_anchor_before_error_bound"
                ),
            }
        }
        breakdown = {"prediction_before_error_bound": 6.0}

        selected = estimator._select_learned_point_prediction(
            prediction=120.0,
            breakdown=breakdown,
        )

        self.assertEqual(selected, 6.0)
        self.assertEqual(breakdown["prediction_after_error_bound"], 120.0)
        self.assertEqual(breakdown["point_prediction_value"], 6.0)

    def test_io_plateau_uses_baseline_scaled_plan_formula_delta(self) -> None:
        estimator = PlanResourceEstimator.__new__(PlanResourceEstimator)
        estimator.estimator_cfg = {
            "shared": {
                "learned_io_plateau_use_baseline_scaled_plan_formula": True,
            },
            "io": {},
        }
        estimator.io_model_artifact = {"models": {"C": {}}}
        estimator._aggregate_io_model_features = types.MethodType(
            lambda self, query_estimates, workload_context: {"io_N_page": 1.0},
            estimator,
        )
        estimator._predict_io_model_mb = types.MethodType(
            lambda self, target_name, aggregate_features, workload_context: (
                10.0,
                {
                    "prediction_mb_per_window": 10.0,
                    "prediction_before_error_bound": 10.0,
                    "prediction_before_anchor_calibration": 100.0,
                    "prediction_window_seconds": 5.0,
                    "auto_selected_by_baseline_actual": True,
                    "baseline_anchor_calibration": {
                        "applied": True,
                        "actual_target": 10.0,
                        "predicted_target_before_error_bound": 100.0,
                        "plan_formula_predicted_target_before_error_bound": 20.0,
                        "plan_formula_delta_scale": 0.5,
                    },
                },
            ),
            estimator,
        )

        result = estimator._build_io_result(
            resource_type="io",
            query_estimates=[
                {
                    "queryid": 1,
                    "query": "select 1",
                    "replay_count": 1,
                    "query_resources": {
                        "io": {
                            "read_bytes_per_query": 40.0 * 1024.0 * 1024.0,
                            "write_bytes_per_query": 0.0,
                            "read_blocks_per_query": 0.0,
                            "write_blocks_per_query": 0.0,
                            "operator_breakdown": [],
                        }
                    },
                }
            ],
            workload_context={"elapsed_seconds": 5.0, "feature_window_seconds": 5.0},
            calibration_factor=1.0,
            explain_workers=1,
        )

        self.assertAlmostEqual(result["value"], 4.0)
        self.assertEqual(result["formula_name"], "io_baseline_scaled_plan_formula_v1")
        fallback = result["calibration_breakdown"]["learned_model"][
            "baseline_scaled_plan_formula_fallback"
        ]
        self.assertTrue(fallback["applied"])
        self.assertAlmostEqual(fallback["scaled_prediction_mb_per_window"], 20.0)

    def test_collapsed_io_model_uses_plan_formula_fallback(self) -> None:
        estimator = PlanResourceEstimator.__new__(PlanResourceEstimator)
        estimator.estimator_cfg = {"shared": {}}
        estimator.feature_predictor = _CollapsedIoPredictor()
        estimator.io_model_artifact = {}

        prediction, detail = estimator._predict_io_model_mb(
            target_name="total",
            aggregate_features={"io_N_page": 1.0},
            workload_context={},
        )

        self.assertIsNone(prediction)
        self.assertTrue(detail["fallback_to_plan_formula"])
        self.assertEqual(
            detail["fallback_reason"],
            "learned_io_raw_prediction_nonpositive_or_nonfinite",
        )

    def test_extreme_cpu_anchor_scale_uses_plan_formula_fallback(self) -> None:
        estimator = PlanResourceEstimator.__new__(PlanResourceEstimator)
        estimator.estimator_cfg = {"shared": {}}
        estimator.feature_predictor = _BadCpuAnchorPredictor()
        estimator.cpu_model_artifact = {}

        prediction, detail = estimator._predict_cpu_model_seconds(
            aggregate_features={"cpu_total_plan_rows": 1.0},
            workload_context={},
        )

        self.assertIsNone(prediction)
        self.assertTrue(detail["fallback_to_plan_formula"])
        self.assertEqual(
            detail["fallback_reason"],
            "learned_cpu_anchor_delta_scale_out_of_bounds",
        )

    def test_extreme_cpu_learned_vs_plan_formula_uses_plan_formula(self) -> None:
        estimator = PlanResourceEstimator.__new__(PlanResourceEstimator)
        estimator.estimator_cfg = {"cpu": {}, "shared": {}}
        estimator.cpu_model_artifact = {"models": {"H_serial": {}}}
        estimator._aggregate_cpu_model_features = types.MethodType(
            lambda self, query_estimates, workload_context: {"cpu_total_plan_rows": 1.0},
            estimator,
        )
        estimator._predict_cpu_model_seconds = types.MethodType(
            lambda self, aggregate_features, workload_context: (
                370.0,
                {
                    "prediction_cpu_seconds_per_window": 370.0,
                    "prediction_before_anchor_calibration": 370.0,
                    "prediction_window_seconds": 5.0,
                },
            ),
            estimator,
        )

        result = estimator._build_cpu_result(
            query_estimates=[
                {
                    "queryid": 1,
                    "query": "select 1",
                    "replay_count": 1,
                    "query_resources": {
                        "cpu": {
                            "cpu_work_per_query": 1.0,
                            "cpu_seconds_per_query": 30.0,
                            "operator_breakdown": [],
                        }
                    },
                }
            ],
            workload_context={"elapsed_seconds": 5.0, "feature_window_seconds": 5.0},
            calibration_factor=1.0,
            explain_workers=1,
        )

        self.assertEqual(result["formula_name"], "cpu_plan_estimator_v1")
        self.assertAlmostEqual(result["value"], 6.0)
        calibration = result["calibration_breakdown"]
        self.assertFalse(calibration["learned_model"])
        self.assertEqual(
            calibration["learned_model_fallback"]["fallback_reason"],
            "learned_cpu_vs_plan_formula_ratio_out_of_bounds",
        )

    def test_extreme_cpu_ratio_can_defer_to_baseline_sanity(self) -> None:
        estimator = PlanResourceEstimator.__new__(PlanResourceEstimator)
        estimator.estimator_cfg = {
            "cpu": {},
            "shared": {
                "learned_cpu_vs_plan_formula_ratio_guard_mode": "baseline_sanity",
            },
        }
        estimator.cpu_model_artifact = {"models": {"H_serial": {}}}
        estimator._aggregate_cpu_model_features = types.MethodType(
            lambda self, query_estimates, workload_context: {"cpu_total_plan_rows": 1.0},
            estimator,
        )
        estimator._predict_cpu_model_seconds = types.MethodType(
            lambda self, aggregate_features, workload_context: (
                370.0,
                {
                    "prediction_cpu_seconds_per_window": 370.0,
                    "prediction_before_anchor_calibration": 370.0,
                    "prediction_window_seconds": 5.0,
                },
            ),
            estimator,
        )

        result = estimator._build_cpu_result(
            query_estimates=[
                {
                    "queryid": 1,
                    "query": "select 1",
                    "replay_count": 1,
                    "query_resources": {
                        "cpu": {
                            "cpu_work_per_query": 1.0,
                            "cpu_seconds_per_query": 30.0,
                            "operator_breakdown": [],
                        }
                    },
                }
            ],
            workload_context={"elapsed_seconds": 5.0, "feature_window_seconds": 5.0},
            calibration_factor=1.0,
            explain_workers=1,
        )

        self.assertEqual(result["formula_name"], "cpu_learned_linear_v1")
        self.assertAlmostEqual(result["value"], 74.0)
        calibration = result["calibration_breakdown"]
        self.assertFalse(calibration["learned_model_fallback"])
        learned = calibration["learned_model"]
        self.assertEqual(
            learned["ratio_guard_warning"],
            "learned_cpu_vs_plan_formula_ratio_out_of_bounds",
        )
        self.assertEqual(
            learned["ratio_guard_action"],
            "keep_learned_for_baseline_sanity",
        )

    def test_extreme_io_learned_vs_plan_formula_uses_plan_formula(self) -> None:
        estimator = PlanResourceEstimator.__new__(PlanResourceEstimator)
        estimator.estimator_cfg = {"io": {}, "shared": {}}
        estimator.io_model_artifact = {"models": {"CH": {}}}
        estimator._aggregate_io_model_features = types.MethodType(
            lambda self, query_estimates, workload_context: {"io_total_replay_bytes": 1.0},
            estimator,
        )
        estimator._predict_io_model_mb = types.MethodType(
            lambda self, target_name, aggregate_features, workload_context: (
                2000.0,
                {
                    "prediction_mb_per_window": 2000.0,
                    "prediction_before_anchor_calibration": 2000.0,
                    "prediction_window_seconds": 5.0,
                },
            ),
            estimator,
        )

        result = estimator._build_io_result(
            resource_type="io",
            query_estimates=[
                {
                    "queryid": 1,
                    "query": "select 1",
                    "replay_count": 1,
                    "query_resources": {
                        "io": {
                            "read_bytes_per_query": 100.0 * 1024.0 * 1024.0,
                            "write_bytes_per_query": 0.0,
                            "read_blocks_per_query": 0.0,
                            "write_blocks_per_query": 0.0,
                            "operator_breakdown": [],
                        }
                    },
                }
            ],
            workload_context={"elapsed_seconds": 5.0, "feature_window_seconds": 5.0},
            calibration_factor=1.0,
            explain_workers=1,
        )

        self.assertEqual(result["formula_name"], "io_plan_estimator_v1")
        self.assertAlmostEqual(result["value"], 20.0)
        calibration = result["calibration_breakdown"]
        self.assertFalse(calibration["learned_model"])
        self.assertEqual(
            calibration["learned_model_fallback"]["fallback_reason"],
            "learned_io_vs_plan_formula_ratio_out_of_bounds",
        )
        self.assertGreater(
            calibration["learned_model_fallback"]["learned_vs_plan_formula_ratio"],
            calibration["learned_model_fallback"]["learned_vs_plan_formula_ratio_max"],
        )

    def test_anchored_io_bypasses_absolute_plan_formula_ratio_guard(self) -> None:
        estimator = PlanResourceEstimator.__new__(PlanResourceEstimator)
        estimator.estimator_cfg = {"io": {}, "shared": {}}
        estimator.io_model_artifact = {"models": {"CH": {}}}
        estimator._aggregate_io_model_features = types.MethodType(
            lambda self, query_estimates, workload_context: {"io_total_replay_bytes": 1.0},
            estimator,
        )
        estimator._predict_io_model_mb = types.MethodType(
            lambda self, target_name, aggregate_features, workload_context: (
                40.0,
                {
                    "prediction_mb_per_window": 40.0,
                    "prediction_before_anchor_calibration": 289.0,
                    "prediction_before_error_bound": 40.0,
                    "prediction_window_seconds": 5.0,
                    "baseline_anchor_calibration": {
                        "applied": True,
                        "actual_target": 40.0,
                        "predicted_target_before_error_bound": 289.0,
                        "delta_scale": 40.0 / 289.0,
                        "unclamped_delta_scale": 40.0 / 289.0,
                    },
                },
            ),
            estimator,
        )

        result = estimator._build_io_result(
            resource_type="io",
            query_estimates=[
                {
                    "queryid": 1,
                    "query": "select 1",
                    "replay_count": 1,
                    "query_resources": {
                        "io": {
                            "read_bytes_per_query": 300.0 * 1024.0 * 1024.0,
                            "write_bytes_per_query": 0.0,
                            "read_blocks_per_query": 0.0,
                            "write_blocks_per_query": 0.0,
                            "operator_breakdown": [],
                        }
                    },
                }
            ],
            workload_context={"elapsed_seconds": 5.0, "feature_window_seconds": 5.0},
            calibration_factor=1.0,
            explain_workers=1,
        )

        self.assertEqual(result["formula_name"], "io_learned_linear_v1")
        self.assertAlmostEqual(result["value"], 8.0)
        calibration = result["calibration_breakdown"]
        self.assertFalse(calibration["learned_model_fallback"])
        learned = calibration["learned_model"]
        self.assertTrue(learned["learned_vs_plan_formula_ratio_guard_bypassed"])
        self.assertEqual(
            learned["learned_vs_plan_formula_ratio_guard_bypass_reason"],
            "baseline_anchor_calibration",
        )

    def test_io_auto_model_set_selects_closest_baseline_actual(self) -> None:
        estimator = PlanResourceEstimator.__new__(PlanResourceEstimator)
        estimator.estimator_cfg = {
            "io": {
                "learned_model": {
                    "model_set": "auto",
                    "auto_select_by_baseline_actual": True,
                    "auto_candidate_model_sets": ["C", "CH", "H"],
                }
            },
            "shared": {"feature_window_seconds": 5.0},
        }
        estimator.feature_predictor = None
        estimator.io_model_artifact = {
            "default_model_set": "CH",
            "models": {
                "C": {
                    "targets": {
                        "total": {
                            "model_type": "linear",
                            "features": ["x"],
                            "coef": [0.0],
                            "intercept": 80.0,
                        }
                    }
                },
                "CH": {
                    "targets": {
                        "total": {
                            "model_type": "linear",
                            "features": ["x"],
                            "coef": [0.0],
                            "intercept": 2000.0,
                        }
                    }
                },
                "H": {
                    "targets": {
                        "total": {
                            "model_type": "linear",
                            "features": ["x"],
                            "coef": [0.0],
                            "intercept": 5000.0,
                        }
                    }
                },
            },
        }

        prediction, detail = estimator._predict_io_model_mb(
            target_name="total",
            aggregate_features={"x": 1.0},
            workload_context={
                "feature_window_seconds": 5.0,
                "runtime_resource_raw": {"io": 16.0 * 1024.0 * 1024.0},
                "workload_family": "hybench_xp",
            },
        )

        self.assertAlmostEqual(prediction or 0.0, 80.0)
        self.assertEqual(detail["model_set"], "C")
        self.assertTrue(detail["auto_selected_by_baseline_actual"])
        self.assertAlmostEqual(detail["auto_selection_actual_mb_s"], 16.0)

    def test_io_auto_baseline_selection_bypasses_bad_plan_formula_ratio(self) -> None:
        estimator = PlanResourceEstimator.__new__(PlanResourceEstimator)
        estimator.estimator_cfg = {
            "io": {
                "learned_model": {
                    "model_set": "auto",
                    "auto_select_by_baseline_actual": True,
                    "auto_candidate_model_sets": ["C", "CH", "H"],
                }
            },
            "shared": {"feature_window_seconds": 5.0},
        }
        estimator.feature_predictor = None
        estimator.io_model_artifact = {
            "default_model_set": "CH",
            "models": {
                "C": {
                    "targets": {
                        "total": {
                            "model_type": "linear",
                            "features": ["x"],
                            "coef": [0.0],
                            "intercept": 80.0,
                        }
                    }
                },
                "CH": {
                    "targets": {
                        "total": {
                            "model_type": "linear",
                            "features": ["x"],
                            "coef": [0.0],
                            "intercept": 2000.0,
                        }
                    }
                },
            },
        }

        result = estimator._build_io_result(
            resource_type="io",
            query_estimates=[
                {
                    "queryid": 1,
                    "query": "select 1",
                    "replay_count": 1,
                    "query_resources": {
                        "io": {
                            "read_bytes_per_query": 10000.0 * 1024.0 * 1024.0,
                            "write_bytes_per_query": 0.0,
                            "read_blocks_per_query": 0.0,
                            "write_blocks_per_query": 0.0,
                            "model_features": {"x": 1.0},
                            "operator_breakdown": [],
                        }
                    },
                }
            ],
            workload_context={
                "elapsed_seconds": 5.0,
                "feature_window_seconds": 5.0,
                "runtime_resource_raw": {"io": 16.0 * 1024.0 * 1024.0},
                "workload_family": "hybench_xp",
            },
            calibration_factor=1.0,
            explain_workers=1,
        )

        self.assertEqual(result["formula_name"], "io_learned_linear_v1")
        self.assertAlmostEqual(result["value"], 16.0)
        calibration = result["calibration_breakdown"]
        learned = calibration["learned_model"]
        self.assertEqual(learned["model_set"], "C")
        self.assertTrue(learned["auto_selected_by_baseline_actual"])
        self.assertTrue(learned["learned_vs_plan_formula_ratio_guard_bypassed"])
        self.assertFalse(calibration["learned_model_fallback"])

    def test_io_auto_artifact_selection_preempts_feature_predictor(self) -> None:
        estimator = PlanResourceEstimator.__new__(PlanResourceEstimator)
        estimator.estimator_cfg = {
            "io": {
                "learned_model": {
                    "model_set": "auto",
                    "auto_select_by_baseline_actual": True,
                    "auto_candidate_model_sets": ["C", "CH"],
                }
            },
            "shared": {"feature_window_seconds": 5.0},
        }
        estimator.feature_predictor = _LargeIoFeaturePredictor()
        estimator.io_model_artifact = {
            "default_model_set": "CH",
            "models": {
                "C": {
                    "targets": {
                        "total": {
                            "model_type": "linear",
                            "features": ["x"],
                            "coef": [0.0],
                            "intercept": 80.0,
                        }
                    }
                },
                "CH": {
                    "targets": {
                        "total": {
                            "model_type": "linear",
                            "features": ["x"],
                            "coef": [0.0],
                            "intercept": 5000.0,
                        }
                    }
                },
            },
        }

        prediction, detail = estimator._predict_io_model_mb(
            target_name="total",
            aggregate_features={"x": 1.0},
            workload_context={
                "feature_window_seconds": 5.0,
                "runtime_resource_raw": {"io": 16.0 * 1024.0 * 1024.0},
                "workload_family": "hybench_xp",
            },
        )

        self.assertAlmostEqual(prediction or 0.0, 80.0)
        self.assertEqual(detail["model_set"], "C")
        self.assertTrue(detail["auto_selected_by_baseline_actual"])
        self.assertNotEqual(detail.get("model_set"), "feature_predictor")

    def test_plan_resource_estimator_applies_baseline_anchor_calibration(self) -> None:
        estimator = PlanResourceEstimator.__new__(PlanResourceEstimator)
        estimator.estimator_cfg = {"shared": {}}

        calibrated, detail = estimator._apply_baseline_anchor_calibration(
            model={
                "baseline_anchor_calibration": {
                    "predicted_target_before_error_bound": 10.0,
                    "actual_target": 20.0,
                    "delta_scale": 0.5,
                }
            },
            prediction=14.0,
        )

        self.assertAlmostEqual(calibrated, 22.0)
        self.assertTrue(detail["applied"])
        self.assertAlmostEqual(detail["input_prediction"], 14.0)

    def test_memory_plateau_uses_baseline_scaled_plan_formula_delta(self) -> None:
        mib = 1024.0 ** 2
        estimator = PlanResourceEstimator(
            pg_host="",
            pg_port="",
            pg_user="",
            pg_dbname="",
            estimator_cfg={
                "shared": {
                    "learned_point_prediction_stage": (
                        "after_baseline_anchor_before_error_bound"
                    ),
                    "learned_memory_plateau_rel_tolerance": 0.01,
                },
                "memory": {"overlap_factor": 0.6},
            },
        )
        estimator.memory_model_artifact = {"models": {"TP_Hybrid": {}}}
        estimator.feature_predictor = _AnchoredMemoryPlateauPredictor()
        result = estimator._build_memory_result(
            query_estimates=[
                {
                    "queryid": "1",
                    "query": "SELECT 1",
                    "replay_count": 1,
                    "statement_kind": "select",
                    "nodes": [],
                    "query_resources": {
                        "memory": {
                            "mem_query_parallel_bytes": 100.0 * mib,
                            "derived_memory_mb": 1.0,
                        },
                        "cpu": {"model_features": {}},
                    },
                }
            ],
            workload_context={
                "baseline_runtime_memory_bytes": 100.0 * mib,
                "concurrency": {"avg": 1.0},
            },
            calibration_factor=1.0,
            explain_workers=1,
        )

        self.assertAlmostEqual(result["raw_value"] / mib, 128.0)
        calibration = result["calibration_breakdown"]
        self.assertTrue(calibration["baseline_scaled_plan_formula_used"])
        fallback = calibration["learned_model"][
            "baseline_scaled_plan_formula_fallback"
        ]
        self.assertEqual(
            fallback["reason"],
            "learned_memory_prediction_plateau_with_plan_change",
        )

    def test_io_direction_disagreement_uses_baseline_scaled_plan_formula_delta(self) -> None:
        mib = 1024.0 ** 2
        estimator = PlanResourceEstimator(
            pg_host="",
            pg_port="",
            pg_user="",
            pg_dbname="",
            estimator_cfg={
                "shared": {
                    "feature_window_seconds": 5.0,
                    "learned_point_prediction_stage": (
                        "after_baseline_anchor_before_error_bound"
                    ),
                    "learned_io_direction_disagreement_use_baseline_scaled_plan_formula": True,
                },
                "io": {},
            },
        )
        estimator.io_model_artifact = {"models": {"C": {}}}
        estimator.feature_predictor = _AnchoredIoDisagreementPredictor()
        result = estimator._build_io_result(
            resource_type="io",
            query_estimates=[
                {
                    "queryid": "1",
                    "query": "SELECT 1",
                    "replay_count": 1,
                    "nodes": [],
                    "query_resources": {
                        "io": {
                            "read_bytes_per_query": 120.0 * mib,
                            "write_bytes_per_query": 0.0,
                            "read_blocks_per_query": 1.0,
                            "write_blocks_per_query": 0.0,
                            "model_features": {},
                        }
                    },
                }
            ],
            workload_context={"elapsed_seconds": 5.0},
            calibration_factor=1.0,
            explain_workers=1,
        )

        self.assertAlmostEqual(result["value"], 19.2)
        calibration = result["calibration_breakdown"]
        self.assertTrue(calibration["baseline_scaled_plan_formula_used"])
        fallback = calibration["learned_model"][
            "baseline_scaled_plan_formula_fallback"
        ]
        self.assertEqual(
            fallback["reason"],
            "learned_io_direction_disagrees_with_plan_formula",
        )

    def test_current_workload_baseline_sanity_accepts_valid_formula_fallback(self) -> None:
        summary = validate_resource_model_baseline_sanity_gate(
            baseline_actual_normalized={"cpu": 5.0, "io": 100.0, "memory": 2.0},
            baseline_estimated_normalized={"cpu": 5.0, "io": 95.0, "memory": 2.0},
            baseline_estimation_metadata={
                "resource_breakdown": {
                    "cpu": {
                        "calibration_breakdown": {
                            "learned_model": {},
                            "learned_model_fallback": {
                                "fallback_to_plan_formula": True,
                                "fallback_reason": "learned_cpu_anchor_delta_scale_out_of_bounds",
                            }
                        }
                    },
                    "io": {
                        "formula_name": "io_plan_estimator_v1",
                        "calibration_breakdown": {
                            "learned_model": {},
                            "learned_model_fallback": {
                                "fallback_to_plan_formula": True,
                                "fallback_reason": "learned_io_raw_prediction_nonpositive_or_nonfinite",
                            },
                        },
                        "execution_breakdown": {
                            "workload_context": {"feature_window_seconds": 5.0}
                        },
                    },
                    "memory": {
                        "calibration_breakdown": {
                            "learned_model": {
                                "prediction_before_anchor_calibration": 2048.0,
                            }
                        }
                    },
                }
            },
            gate_cfg={"enabled": True, "max_relative_error": 1.0},
        )

        self.assertTrue(summary["passed"])
        cpu_check = next(item for item in summary["checks"] if item["resource"] == "cpu")
        self.assertEqual(cpu_check["source"], "plan_formula_fallback")
        io_check = next(item for item in summary["checks"] if item["resource"] == "io")
        self.assertEqual(io_check["source"], "plan_formula_fallback")

    def test_baseline_sanity_prefers_explicit_fallback_over_bad_learned_raw(self) -> None:
        summary = validate_resource_model_baseline_sanity_gate(
            baseline_actual_normalized={"cpu": 7.25},
            baseline_estimated_normalized={"cpu": 6.50},
            baseline_estimation_metadata={
                "resource_breakdown": {
                    "cpu": {
                        "calibration_breakdown": {
                            "learned_model": {
                                "prediction_before_anchor_calibration": 370.0,
                                "prediction_window_seconds": 5.0,
                            },
                            "learned_model_fallback": {
                                "fallback_to_plan_formula": True,
                                "fallback_reason": (
                                    "learned_cpu_vs_plan_formula_ratio_out_of_bounds"
                                ),
                            },
                        }
                    }
                }
            },
            gate_cfg={"enabled": True, "required_resources": ["cpu"], "max_relative_error": 1.0},
        )

        self.assertTrue(summary["passed"])
        check = summary["checks"][0]
        self.assertEqual(check["source"], "plan_formula_fallback")
        self.assertAlmostEqual(check["raw_prediction_normalized"], 6.5)

    def test_baseline_sanity_uses_io_plan_formula_when_learned_raw_fails(self) -> None:
        summary = validate_resource_model_baseline_sanity_gate(
            baseline_actual_normalized={"io": 20.0},
            baseline_estimated_normalized={"io": 400.0},
            baseline_estimation_metadata={
                "resource_breakdown": {
                    "io": {
                        "formula_name": "io_learned_linear_v1",
                        "calibration_breakdown": {
                            "total_io_bytes": 100.0 * 1024.0 * 1024.0,
                            "learned_model": {
                                "prediction_before_anchor_calibration": 2000.0,
                                "prediction_window_seconds": 5.0,
                                "model_set": "CH",
                                "model_type": "joblib_extra",
                            },
                            "learned_model_fallback": {},
                        },
                        "execution_breakdown": {
                            "workload_context": {"feature_window_seconds": 5.0}
                        },
                    }
                }
            },
            gate_cfg={
                "enabled": True,
                "required_resources": ["io"],
                "max_relative_error": 1.0,
                "allow_plan_formula_fallback": True,
            },
        )

        self.assertTrue(summary["passed"])
        check = summary["checks"][0]
        self.assertEqual(check["source"], "plan_formula_fallback_after_failed_raw")
        self.assertEqual(
            check["fallback_reason"],
            "baseline_sanity_plan_formula_fallback_after_failed_raw",
        )
        self.assertAlmostEqual(check["raw_prediction_normalized"], 20.0)

    def test_current_workload_baseline_sanity_rejects_zero_prediction(self) -> None:
        summary = validate_resource_model_baseline_sanity_gate(
            baseline_actual_normalized={"io": 100.0},
            baseline_estimated_normalized={"io": 100.0},
            baseline_estimation_metadata={
                "resource_breakdown": {
                    "io": {
                        "calibration_breakdown": {
                            "learned_model": {
                                "prediction_before_anchor_calibration": 0.0,
                                "prediction_window_seconds": 5.0,
                            }
                        }
                    }
                }
            },
            gate_cfg={
                "enabled": True,
                "required_resources": ["io"],
                "max_relative_error": 1.0,
            },
        )

        self.assertFalse(summary["passed"])
        self.assertIn(
            "raw_prediction_nonpositive_or_clip_to_zero",
            summary["failures"][0]["reasons"],
        )

    def test_baseline_sanity_accepts_good_anchor_despite_bad_raw(self) -> None:
        summary = validate_resource_model_baseline_sanity_gate(
            baseline_actual_normalized={"io": 5.0},
            baseline_estimated_normalized={"io": 5.0},
            baseline_estimation_metadata={
                "resource_breakdown": {
                    "io": {
                        "formula_name": "io_learned_linear_v1",
                        "calibration_breakdown": {
                            "learned_model": {
                                "prediction_before_anchor_calibration": 500.0,
                                "prediction_before_error_bound": 25.0,
                                "prediction_window_seconds": 5.0,
                                "model_set": "C",
                                "model_type": "joblib_hist_gb",
                                "baseline_anchor_calibration": {
                                    "applied": True,
                                    "actual_target": 25.0,
                                    "predicted_target_before_error_bound": 500.0,
                                    "delta_scale": 0.05,
                                },
                            }
                        },
                    }
                }
            },
            gate_cfg={
                "enabled": True,
                "required_resources": ["io"],
                "max_relative_error": 1.0,
            },
        )

        self.assertTrue(summary["passed"])
        check = summary["checks"][0]
        self.assertEqual(check["source"], "learned_model_after_baseline_anchor")
        self.assertAlmostEqual(check["raw_prediction_normalized"], 5.0)
        self.assertAlmostEqual(check["raw_pre_anchor_prediction_normalized"], 100.0)
        self.assertGreater(check["raw_pre_anchor_relative_error"], 1.0)
        self.assertNotIn("raw_pre_anchor_baseline_relative_error", str(check.get("reasons") or []))

    def test_baseline_sanity_accepts_zero_raw_with_good_anchor(self) -> None:
        summary = validate_resource_model_baseline_sanity_gate(
            baseline_actual_normalized={"io": 5.0},
            baseline_estimated_normalized={"io": 5.0},
            baseline_estimation_metadata={
                "resource_breakdown": {
                    "io": {
                        "calibration_breakdown": {
                            "learned_model": {
                                "prediction_before_anchor_calibration": 0.0,
                                "prediction_before_error_bound": 25.0,
                                "prediction_window_seconds": 5.0,
                            }
                        }
                    }
                }
            },
            gate_cfg={
                "enabled": True,
                "required_resources": ["io"],
                "max_relative_error": 1.0,
            },
        )

        self.assertTrue(summary["passed"])
        check = summary["checks"][0]
        self.assertAlmostEqual(check["raw_prediction_normalized"], 5.0)
        self.assertAlmostEqual(check["raw_pre_anchor_prediction_normalized"], 0.0)

    def test_baseline_sanity_enforces_configured_anchor_scale_bounds(self) -> None:
        summary = validate_resource_model_baseline_sanity_gate(
            baseline_actual_normalized={"cpu": 20.0},
            baseline_estimated_normalized={"cpu": 20.0},
            baseline_estimation_metadata={
                "resource_breakdown": {
                    "cpu": {
                        "calibration_breakdown": {
                            "learned_model": {
                                "prediction_before_anchor_calibration": 10.0,
                                "prediction_before_error_bound": 100.0,
                                "prediction_window_seconds": 5.0,
                                "baseline_anchor_calibration": {
                                    "applied": True,
                                    "delta_scale": 12.0,
                                    "unclamped_delta_scale": 12.0,
                                },
                            }
                        }
                    }
                }
            },
            gate_cfg={
                "enabled": True,
                "required_resources": ["cpu"],
                "max_relative_error": 1.0,
                "prediction_stage": "after_baseline_anchor_before_error_bound",
                "anchor_delta_scale_min_by_resource": {"cpu": 0.05},
                "anchor_delta_scale_max_by_resource": {"cpu": 10.0},
            },
        )

        self.assertFalse(summary["passed"])
        self.assertIn(
            "baseline_anchor_delta_scale 12 > 10",
            summary["failures"][0]["reasons"],
        )


    def test_baseline_sanity_accepts_good_anchor_with_bad_raw_v134_scenario(self) -> None:
        summary = validate_resource_model_baseline_sanity_gate(
            baseline_actual_normalized={"memory": 0.5461347210},
            baseline_estimated_normalized={"memory": 0.5461347210},
            baseline_estimation_metadata={
                "resource_breakdown": {
                    "memory": {
                        "calibration_breakdown": {
                            "learned_model": {
                                "prediction_before_anchor_calibration": 3581.117180,
                                "prediction_before_error_bound": 559.2419542910447,
                                "prediction_window_seconds": 5.0,
                                "baseline_anchor_calibration": {
                                    "applied": True,
                                    "actual_target": 559.2419542910447,
                                    "predicted_target_before_error_bound": 3581.117180,
                                    "delta_scale": 0.156,
                                },
                            }
                        }
                    }
                }
            },
            gate_cfg={
                "enabled": True,
                "required_resources": ["memory"],
                "max_relative_error": 1.0,
            },
        )

        self.assertTrue(summary["passed"])
        check = summary["checks"][0]
        self.assertEqual(check["source"], "learned_model_after_baseline_anchor")
        self.assertAlmostEqual(check["raw_prediction_normalized"],
                               559.2419542910447 / 1024.0, places=6)
        self.assertAlmostEqual(check["raw_pre_anchor_prediction_normalized"],
                               3581.117180 / 1024.0, places=6)
        self.assertGreater(check["raw_pre_anchor_relative_error"], 5.0)

    def test_baseline_sanity_rejects_bad_post_anchor_prediction(self) -> None:
        summary = validate_resource_model_baseline_sanity_gate(
            baseline_actual_normalized={"memory": 0.5},
            baseline_estimated_normalized={"memory": 0.5},
            baseline_estimation_metadata={
                "resource_breakdown": {
                    "memory": {
                        "calibration_breakdown": {
                            "learned_model": {
                                "prediction_before_anchor_calibration": 4000.0,
                                "prediction_before_error_bound": 1500.0,
                                "prediction_window_seconds": 5.0,
                                "baseline_anchor_calibration": {
                                    "applied": True,
                                    "actual_target": 559.0,
                                    "predicted_target_before_error_bound": 3581.0,
                                    "delta_scale": 0.156,
                                },
                            }
                        }
                    }
                }
            },
            gate_cfg={
                "enabled": True,
                "required_resources": ["memory"],
                "max_relative_error": 1.0,
            },
        )

        self.assertFalse(summary["passed"])
        check = summary["failures"][0]
        self.assertEqual(check["source"], "learned_model_after_baseline_anchor")
        self.assertIn("baseline_relative_error", check["reasons"][0])


if __name__ == "__main__":
    unittest.main()
