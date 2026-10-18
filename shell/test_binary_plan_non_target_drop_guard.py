#!/usr/bin/env python3
"""Regression tests for non-target resource drop guard modes."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from binary_plan_resource_tuner import (
    apply_non_target_drop_guard_mode,
    none_resource_ceiling_details,
    resource_explanation_details,
    target_baseline_band_details,
)


class NonTargetDropGuardModeTest(unittest.TestCase):
    def test_waives_drop_when_same_resource_is_up_and_sweet(self) -> None:
        rejects, details = apply_non_target_drop_guard_mode(
            ["io_dropped_62.69%"],
            policy={"non_target_drop_guard_mode": "require_any_up_sweet"},
            other_up_resources=["memory"],
            other_sweet_resources=["memory"],
        )

        self.assertEqual([], rejects)
        self.assertTrue(details["waived"])
        self.assertEqual(["memory"], details["up_sweet_resources"])

    def test_does_not_combine_different_up_and_sweet_resources(self) -> None:
        rejects, details = apply_non_target_drop_guard_mode(
            ["io_dropped_62.69%"],
            policy={"non_target_drop_guard_mode": "require_any_up_sweet"},
            other_up_resources=["memory"],
            other_sweet_resources=["io"],
        )

        self.assertEqual(["io_dropped_62.69%"], rejects)
        self.assertFalse(details["waived"])

    def test_legacy_mode_keeps_per_resource_drop_guard(self) -> None:
        rejects, details = apply_non_target_drop_guard_mode(
            ["io_dropped_62.69%"],
            policy={},
            other_up_resources=["memory"],
            other_sweet_resources=["memory"],
        )

        self.assertEqual(["io_dropped_62.69%"], rejects)
        self.assertEqual("strict_each", details["mode"])
        self.assertFalse(details["waived"])


class NoneRequiredUpResourcesTest(unittest.TestCase):
    def test_requires_all_configured_none_resources_to_increase(self) -> None:
        cfg = {
            "binary_plan_tuning": {
                "acceptance_policy": {
                    "none_required_up_resources": ["cpu", "memory", "io"],
                }
            }
        }
        details = resource_explanation_details(
            {"cpu": 2.0, "memory": 2.0, "io": 0.9},
            {"cpu": 1.0, "memory": 1.0, "io": 1.0},
            target="none",
            cfg=cfg,
        )

        self.assertFalse(details["other_up_ok"])
        self.assertEqual(["io"], details["missing_required_other_up_resources"])

    def test_accepts_when_all_configured_none_resources_increase(self) -> None:
        cfg = {
            "binary_plan_tuning": {
                "acceptance_policy": {
                    "none_required_up_resources": ["cpu", "memory", "io"],
                }
            }
        }
        details = resource_explanation_details(
            {"cpu": 2.0, "memory": 2.0, "io": 2.0},
            {"cpu": 1.0, "memory": 1.0, "io": 1.0},
            target="none",
            cfg=cfg,
        )

        self.assertTrue(details["other_up_ok"])
        self.assertEqual([], details["missing_required_other_up_resources"])


class NoneCandidateResourceFloorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = {
            "binary_plan_tuning": {
                "acceptance_policy": {
                    "none_candidate_enforce_resource_floor": False,
                    "none_resource_floor": {"cpu_pct": 20, "memory_pct": 15, "io_mib_s": 12},
                    "none_resource_ceiling": {"cpu_pct": 80, "memory_pct": 80, "io_mib_s": 75},
                    "none_baseline_resource_ceiling": {"cpu_pct": 65, "memory_pct": 50, "io_mib_s": 30},
                    "target_overrides": {
                        "none": {"resource_capacity": {"cpu_cores": 8, "memory_gib": 8}},
                    },
                }
            }
        }

    def test_candidate_skips_individual_resource_floors(self) -> None:
        details = none_resource_ceiling_details(
            {"event": "candidate_validation", "target": "none", "resources": {"cpu": 5.8, "memory": 3.3, "io": 6.2}},
            self.cfg,
        )

        self.assertTrue(details["ok"])
        self.assertTrue(all(check["floor"] is None for check in details["checks"]))

    def test_baseline_still_enforces_resource_floors(self) -> None:
        details = none_resource_ceiling_details(
            {"event": "baseline_validation", "target": "none", "resources": {"cpu": 4.0, "memory": 3.2, "io": 6.2}},
            self.cfg,
        )

        self.assertFalse(details["ok"])
        self.assertIn("io_MiB/s_below_12", details["rejects"])


class TargetBaselineBandTest(unittest.TestCase):
    def test_cpu_baseline_uses_configured_capacity_percentage(self) -> None:
        cfg = {
            "binary_plan_tuning": {
                "acceptance_policy": {
                    "resource_capacity": {"cpu_cores": 10},
                    "target_baseline_band": {"cpu": {"min_pct": 85, "max_pct": 95}},
                }
            }
        }
        details = target_baseline_band_details(
            {"target": "cpu", "resources": {"cpu": 9.0}},
            cfg,
            "cpu",
        )

        self.assertTrue(details["ok"])
        self.assertEqual(90.0, details["checks"][0]["value"])

    def test_io_baseline_normalizes_against_physical_capacity(self) -> None:
        cfg = {
            "binary_plan_tuning": {
                "acceptance_policy": {
                    "resource_capacity": {"io_mib_s": 100},
                    "target_baseline_band": {"io": {"min_pct": 85, "max_pct": 95}},
                }
            }
        }
        details = target_baseline_band_details(
            {"target": "io", "resources": {"io": 90.0}},
            cfg,
            "io",
        )

        self.assertTrue(details["ok"])
        self.assertEqual("pct", details["checks"][0]["unit"])


if __name__ == "__main__":
    unittest.main()
