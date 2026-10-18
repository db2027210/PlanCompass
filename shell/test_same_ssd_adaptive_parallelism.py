#!/usr/bin/env python3
"""Tests for the evidence-based same-SSD replay concurrency policy."""

from __future__ import annotations

import unittest
from typing import Optional
from unittest import mock

from same_ssd_concurrency import choose_same_ssd_parallelism
from binary_plan_resource_tuner import (
    _physical_observer_disk_devices,
    ssd_utilization_from_validations,
    validation_workers,
)


class SameSsdParallelismTests(unittest.TestCase):
    def decide(self, current: int, mean: Optional[float], peak: Optional[float], **kwargs: object):
        return choose_same_ssd_parallelism(
            current=current,
            available_workers=16,
            pending_candidates=16,
            mean_util_pct=mean,
            peak_util_pct=peak,
            **kwargs,
        )

    def test_missing_observation_holds(self) -> None:
        decision = self.decide(4, None, None)
        self.assertEqual(decision["desired"], 4)
        self.assertEqual(decision["reason"], "no_valid_ssd_observation_hold")

    def test_low_utilization_grows_one_tested_tier(self) -> None:
        self.assertEqual(self.decide(1, 9.7, 16.4)["desired"], 4)
        self.assertEqual(self.decide(2, 20.0, 30.0)["desired"], 4)
        self.assertEqual(self.decide(4, 37.26, 64.0)["desired"], 8)

    def test_daily_policy_holds_at_recommended_eight(self) -> None:
        decision = self.decide(8, 59.74, 81.6)
        self.assertEqual(decision["desired"], 8)
        self.assertEqual(decision["stable_cap"], 12)

    def test_exclusive_ssd_opt_in_can_grow_to_twelve(self) -> None:
        decision = self.decide(8, 59.74, 81.6, allow_stable_cap=True)
        self.assertEqual(decision["desired"], 12)

    def test_warning_scales_down_and_never_selects_sixteen(self) -> None:
        decision = self.decide(12, 85.0, 95.0, allow_stable_cap=True)
        self.assertEqual(decision["desired"], 8)
        self.assertLessEqual(decision["desired"], 12)

    def test_effective_parallelism_respects_top_five(self) -> None:
        decision = choose_same_ssd_parallelism(
            current=8,
            available_workers=12,
            pending_candidates=5,
            mean_util_pct=40.0,
            peak_util_pct=60.0,
        )
        self.assertEqual(decision["effective"], 5)

    def test_round_observation_ignores_failed_or_unsampled_rows(self) -> None:
        rows = [
            {
                "status": "ok",
                "resource_stats": {
                    "ssd_util_percent": {"avg": 40.0, "max": 70.0},
                    "stable_sample_count": {"io_util_pct": 20},
                },
            },
            {
                "status": "ok",
                "resource_stats": {
                    "ssd_util_percent": {"avg": 44.0, "max": 75.0},
                    "stable_sample_count": {"io_util_pct": 20},
                },
            },
            {"status": "failed", "resource_stats": {}},
        ]
        observation = ssd_utilization_from_validations(rows)
        self.assertTrue(observation["valid"])
        self.assertEqual(observation["mean_util_pct"], 42.0)
        self.assertEqual(observation["peak_util_pct"], 75.0)

    def test_nvme1n1_hybench_xp_pool_auto_enables_full_worker_visibility(self) -> None:
        workers = [
            {
                "name": f"w{index}",
                "container": f"pg{index}",
                "port": 60000 + index,
                "disk_root": "/disk1",
                "targets": ["io"],
            }
            for index in range(8)
        ]
        cfg = {
            "binary_plan_tuning": {
                "workload": {"name": "hybench_xp"},
                "validation": {
                    "docker_workers": {
                        "enabled": True,
                        "io_parallelism": 2,
                        "workers": workers,
                    }
                },
            }
        }
        with mock.patch(
            "binary_plan_resource_tuner._disk_devices_for_root",
            return_value=["nvme1n1"],
        ):
            self.assertEqual(len(validation_workers(cfg, "io")), 8)

    def test_explicit_ssd_observer_does_not_depend_on_findmnt(self) -> None:
        with mock.patch(
            "binary_plan_resource_tuner._disk_devices_for_root",
            side_effect=AssertionError("findmnt fallback must not be used"),
        ):
            self.assertEqual(
                _physical_observer_disk_devices("/disk1", ["/dev/nvme1n1"]),
                ["nvme1n1"],
            )

    def test_ssd_observer_falls_back_to_mount_resolution(self) -> None:
        with mock.patch(
            "binary_plan_resource_tuner._disk_devices_for_root",
            return_value=["nvme1n1"],
        ) as resolver:
            self.assertEqual(
                _physical_observer_disk_devices("/disk1", None),
                ["nvme1n1"],
            )
        resolver.assert_called_once_with("/disk1")


if __name__ == "__main__":
    unittest.main()
