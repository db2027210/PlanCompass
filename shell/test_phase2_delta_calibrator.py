#!/usr/bin/env python3
from __future__ import annotations

import unittest
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from phase2_delta_calibrator import Phase2DeltaCalibrator, extract_plan_features


def artifact() -> dict:
    return {
        "baseline_params": {"seq_page_cost": 1.0},
        "baseline_plan_features": {"plan_total_cost": 10.0},
        "baseline_resource": {"cpu": 10.0, "memory": 20.0, "io": 30.0},
        "feature_names": [
            "param_log:seq_page_cost",
            "plan_delta:plan_total_cost",
        ],
        "feature_scales": [1.0, 10.0],
        "bandwidth": 0.4,
        "support": {"max_nearest_distance": 0.5},
        "training_gate": {"passed": True},
        "points": [
            {
                "x": [0.0, 0.0],
                "labels": {
                    "db_qps_delta_pct": 0.0,
                    "cpu_delta_pct": 0.0,
                    "memory_delta_pct": 0.0,
                    "io_delta_pct": 0.0,
                },
            },
            {
                "x": [0.6931471805599453, 1.0],
                "labels": {
                    "db_qps_delta_pct": 20.0,
                    "cpu_delta_pct": 10.0,
                    "memory_delta_pct": -10.0,
                    "io_delta_pct": 5.0,
                },
            },
        ],
    }


class Phase2DeltaCalibratorTests(unittest.TestCase):
    def test_exact_baseline_anchor_is_not_smoothed(self) -> None:
        model = Phase2DeltaCalibrator(artifact())
        result = model.predict(
            params={"seq_page_cost": 1.0},
            plan_features={"plan_total_cost": 10.0},
        )
        self.assertEqual(result["prediction"]["db_qps_delta_pct"], 0.0)
        self.assertEqual(result["predicted_resource"]["cpu"], 10.0)
        self.assertEqual(result["predicted_resource"]["memory"], 20.0)
        self.assertEqual(result["predicted_resource"]["io"], 30.0)

    def test_exact_candidate_anchor_preserves_real_delta(self) -> None:
        model = Phase2DeltaCalibrator(artifact())
        result = model.predict(
            params={"seq_page_cost": 2.0},
            plan_features={"plan_total_cost": 20.0},
        )
        self.assertEqual(result["prediction"]["db_qps_delta_pct"], 20.0)
        self.assertEqual(result["predicted_resource"]["cpu"], 11.0)
        self.assertEqual(result["predicted_resource"]["memory"], 18.0)
        self.assertEqual(result["predicted_resource"]["io"], 31.5)

    def test_resource_delta_uses_current_runtime_baseline(self) -> None:
        model = Phase2DeltaCalibrator(artifact())
        result = model.predict(
            params={"seq_page_cost": 2.0},
            plan_features={"plan_total_cost": 20.0},
            baseline_resource={"cpu": 20.0, "memory": 40.0, "io": 60.0},
        )
        self.assertEqual(
            result["predicted_resource_baseline"],
            {"cpu": 20.0, "memory": 40.0, "io": 60.0},
        )
        self.assertEqual(result["resource_baseline_source"], "current_runtime_baseline")
        self.assertEqual(result["predicted_resource"]["cpu"], 22.0)
        self.assertEqual(result["predicted_resource"]["memory"], 36.0)
        self.assertEqual(result["predicted_resource"]["io"], 63.0)

    def test_plan_delta_uses_current_runtime_plan_baseline(self) -> None:
        model = Phase2DeltaCalibrator(artifact())
        result = model.predict(
            params={"seq_page_cost": 1.0},
            plan_features={"plan_total_cost": 12.0},
            baseline_plan_features={"plan_total_cost": 12.0},
        )
        self.assertEqual(result["prediction"]["db_qps_delta_pct"], 0.0)
        self.assertEqual(result["nearest_distance"], 0.0)
        self.assertEqual(
            result["plan_feature_baseline_source"],
            "current_runtime_baseline",
        )

    def test_missing_required_plan_feature_is_visible(self) -> None:
        model = Phase2DeltaCalibrator(artifact())
        result = model.predict(params={"seq_page_cost": 1.0}, plan_features={})
        self.assertEqual(result["plan_feature_coverage"], 0.0)

    def test_out_of_support_prediction_is_visible(self) -> None:
        model = Phase2DeltaCalibrator(artifact())
        result = model.predict(
            params={"seq_page_cost": 10.0},
            plan_features={"plan_total_cost": 100.0},
        )
        self.assertFalse(result["within_support"])
        self.assertGreater(result["nearest_distance"], result["max_nearest_distance"])

    def test_extracts_fallback_learned_features(self) -> None:
        metadata = {
            "plan_resource_estimation": {
                "resource_breakdown": {
                    "io": {
                        "calibration_breakdown": {
                            "learned_model_fallback": {
                                "features": {"plan_seq_scan_count": 3.0}
                            }
                        }
                    }
                }
            }
        }
        self.assertEqual(
            extract_plan_features(metadata),
            {"plan_seq_scan_count": 3.0},
        )


if __name__ == "__main__":
    unittest.main()
