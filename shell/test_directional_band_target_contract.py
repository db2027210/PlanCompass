#!/usr/bin/env python3
"""Regression tests for the formal directional band-target implementation."""

from __future__ import annotations

import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from resource_utility import ResourceUtilityCalculator


def _job_memory_calculator() -> ResourceUtilityCalculator:
    return ResourceUtilityCalculator(
        reward_type="sigmoid_linear",
        penalty_type="sigmoid_linear",
        score_mode="band_target",
        resource_weights={"cpu": 1.0, "memory": 1.0, "io": 0.5},
        resource_target_ranges={
            "cpu": {
                "min_pct": 30,
                "max_pct": 50,
                "hard_overload_pct": 90,
                "role": "non_bottleneck",
            },
            "memory": {
                "min_pct": 85,
                "max_pct": 90,
                "hard_overload_pct": 95,
                "role": "target_bottleneck",
            },
            "io": {
                "min_pct": 30,
                "max_pct": 50,
                "hard_overload_pct": 90,
                "role": "non_bottleneck_io_cost",
            },
        },
    )


class DirectionalBandTargetContractTest(unittest.TestCase):
    def test_endpoint_direction_and_weights(self) -> None:
        calculator = _job_memory_calculator()
        audit = calculator.directional_band_target_contract_audit(
            target_resource="memory"
        )

        self.assertEqual(
            audit["contract_id"],
            "directional_band_target_v1",
        )
        self.assertGreater(
            audit["checks"]["memory"]["score_at_min"],
            audit["checks"]["memory"]["score_at_max"],
        )
        self.assertGreater(
            audit["checks"]["memory"]["score_below_min"],
            audit["checks"]["memory"]["score_at_min"],
        )
        self.assertGreater(
            audit["checks"]["cpu"]["score_at_max"],
            audit["checks"]["cpu"]["score_at_min"],
        )
        self.assertGreater(
            audit["checks"]["io"]["score_at_min"],
            audit["checks"]["io"]["score_at_max"],
        )

    def test_job_round3_rank1_fixture_rejects_flat_band_score(self) -> None:
        calculator = _job_memory_calculator()
        capability = {
            "cpu": 3.0,
            "memory": 2.91015625,
            "io": 256.0,
        }
        predicted_resource = {
            "cpu": 1.2144036648722234,
            "memory": 2.4717779937666946,
            "io": 94.07676307020462,
        }
        score = calculator.calculate_utility_score(
            predicted_resource,
            capability,
        )

        self.assertAlmostEqual(score, 2.221207510936387, places=12)
        self.assertFalse(
            math.isclose(
                score,
                4.999999437746094,
                rel_tol=0.0,
                abs_tol=1e-12,
            )
        )

    def test_job_baseline_directional_derivatives_are_not_flat(self) -> None:
        calculator = _job_memory_calculator()
        utilization = {
            "cpu": 0.40480122162407445,
            "io": 0.3674873557429868,
            "memory": 0.849362639468824,
        }
        epsilon = 1e-7
        derivatives = {}
        for resource_name, center in utilization.items():
            lower = calculator.calculate_resource_score(
                center - epsilon,
                resource_name=resource_name,
            )
            upper = calculator.calculate_resource_score(
                center + epsilon,
                resource_name=resource_name,
            )
            derivatives[resource_name] = (
                float(upper) - float(lower)
            ) / (2.0 * epsilon)

        self.assertAlmostEqual(derivatives["cpu"], 1.0, places=6)
        self.assertAlmostEqual(derivatives["io"], -0.5, places=6)
        self.assertLess(derivatives["memory"], 0.0)
        self.assertAlmostEqual(
            derivatives["memory"],
            -0.2 / 0.85,
            places=6,
        )


if __name__ == "__main__":
    unittest.main()
