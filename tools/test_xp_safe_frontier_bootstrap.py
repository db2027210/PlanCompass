#!/usr/bin/env python3
"""Offline regression tests for XP incumbent bootstrap trust semantics."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest


BUNDLE = Path("/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v6")
sys.path.insert(0, str(BUNDLE))

import xp_round10_calibrated_acquisition as acquisition
import xp_round10_diagnostic_bundle as bundle


class XpSafeFrontierBootstrapTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = "ratio-plan-sha256:" + "1" * 64
        self.fingerprint = "three-level-sha256:" + "2" * 64
        self.candidate = {
            "rank": 1,
            "selection_role": "incumbent_protocol_revalidation",
            "candidate_score": 4.0,
            "predicted_resource": {
                "cpu": 4.0,
                "io": 0.8,
                "memory": 12.0,
            },
            "final_resource": {
                "cpu": 4.0,
                "io": 0.8,
                "memory": 12.0,
            },
            "workload_plan_fingerprint": self.plan,
            "three_level_fingerprint": {
                "id": self.fingerprint,
                "plan_level": self.plan,
                "valid": True,
                "parameter_fallback_used": False,
            },
        }
        self.selection = {
            "contract": (
                "xp_incumbent_protocol_revalidation_candidate_v1"
            ),
            "candidate": copy.deepcopy(self.candidate),
        }
        self.replay = {
            "contract": "xp_incumbent_protocol_revalidation_summary_v1",
            "real_workload_invocation_count": 1,
            "valid_replay_count": 1,
            "technical_failure_count": 0,
            "baseline_snapshot_restored": True,
            "baseline_params_restored": True,
            "successful": [
                {
                    "rank": 1,
                    "fingerprint_id": self.fingerprint,
                    "three_level_fingerprint": copy.deepcopy(
                        self.candidate["three_level_fingerprint"]
                    ),
                    "workload_plan_fingerprint": self.plan,
                    "actual_resource": {
                        "cpu": 4.2,
                        "io": 0.85,
                        "memory": 12.3,
                    },
                    "actual_utilization": {
                        "cpu": 0.4,
                        "io": 0.85,
                        "memory": 0.4,
                    },
                    "actual_score": 4.1,
                }
            ],
        }

    def test_single_incumbent_is_bootstrap_not_entry_gate(self) -> None:
        calibration = acquisition.build_calibration(
            self.selection, self.replay
        )
        self.assertTrue(calibration["bootstrap_incumbent_only"])
        self.assertEqual(calibration["matched_valid_point_count"], 1)
        self.assertFalse(calibration["entry_gate"]["passed"])
        self.assertIsNone(
            calibration["ranking"]["spearman_predicted_vs_actual"]
        )

    def test_one_observation_plan_remains_probe_only(self) -> None:
        calibration = acquisition.build_calibration(
            self.selection, self.replay
        )
        neighbor = copy.deepcopy(self.candidate)
        neighbor["three_level_fingerprint"]["id"] = (
            "three-level-sha256:" + "3" * 64
        )
        calibrated = acquisition.calibrate_candidate(
            neighbor, calibration
        )
        self.assertTrue(calibrated["exact_plan_regime_validated"])
        regime = calibrated["plan_regime_calibration"]
        self.assertEqual(regime["minimum_trusted_observations"], 2)
        self.assertFalse(regime["trusted_eligible"])
        self.assertTrue(regime["single_observation_probe_only"])

    def test_bootstrap_consumes_one_physical_slot(self) -> None:
        self.assertEqual(
            bundle._replayed_ids(self.replay),
            {self.fingerprint},
        )


if __name__ == "__main__":
    unittest.main()
