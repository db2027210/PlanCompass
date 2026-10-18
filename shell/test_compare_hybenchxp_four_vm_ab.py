#!/usr/bin/env python3
"""Tests for the strict A/B real-runtime effect table."""

from __future__ import annotations

from pathlib import Path
import unittest

from shell.compare_hybenchxp_four_vm_ab import scenario_row


class FourVmAbComparisonTest(unittest.TestCase):
    @staticmethod
    def baseline() -> dict:
        return {
            "label": "baseline",
            "status": "ok",
            "db_qps": 100.0,
            "resources": {"cpu": 100.0, "memory": 100.0, "io": 100.0},
        }

    def test_cpu_physical_goal_uses_real_final_point(self) -> None:
        scenario = {
            "target": "cpu",
            "baseline_reference": self.baseline(),
            "completed_rounds": 17,
            "candidate_validation_count": 17,
            "strategy_final_point": {
                "label": "a_final",
                "db_qps": 120.0,
                "resources": {"cpu": 93.0, "memory": 100.0, "io": 100.0},
                "params": {},
            },
        }

        row = scenario_row(strategy="A", run_root=Path("/tmp/a"), scenario=scenario)

        self.assertAlmostEqual(20.0, row["db_qps_gain_pct"])
        self.assertAlmostEqual(7.0, row["target_drop_pct"])
        self.assertTrue(row["physical_goal_reached"])
        self.assertIsNone(row["score_b"])

    def test_none_reports_b_score_and_requires_one_resource_up(self) -> None:
        scenario = {
            "target": "none",
            "baseline_reference": self.baseline(),
            "completed_rounds": 17,
            "candidate_validation_count": 340,
            "strategy_final_point": {
                "label": "b_final",
                "db_qps": 120.0,
                "resources": {"cpu": 105.0, "memory": 100.0, "io": 100.0},
                "params": {},
            },
            "global_score_b_best_details": {"score_b": 0.21, "successful": True},
        }

        row = scenario_row(strategy="B", run_root=Path("/tmp/b"), scenario=scenario)

        self.assertTrue(row["physical_goal_reached"])
        self.assertAlmostEqual(0.21, row["score_b"])
        self.assertTrue(row["score_b_successful"])


if __name__ == "__main__":
    unittest.main()
