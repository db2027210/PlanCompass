#!/usr/bin/env python3
"""Regression tests for the user-defined real score B."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any, Dict


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from binary_plan_resource_tuner import (  # noqa: E402
    REAL_SCORE_B_ANCHOR_POLICY,
    real_score_b_details,
    select_real_score_b_anchor,
)


CFG = {
    "binary_plan_tuning": {
        "acceptance_policy": {
            "min_target_drop_pct": 5.0,
            "max_target_drop_pct": 10.0,
            "min_db_qps_gain_pct": 15.0,
        },
        "binary_search": {
            "real_score_b": {
                "resource_up_scale": 0.10,
                "bonus_coefficient": 0.02,
            }
        },
    }
}


def candidate(
    label: str,
    *,
    qps: float,
    cpu: float,
    memory: float,
    io: float,
    index: int,
    status: str = "ok",
) -> Dict[str, Any]:
    return {
        "label": label,
        "status": status,
        "candidate_index": index,
        "db_qps": qps,
        "resources": {"cpu": cpu, "memory": memory, "io": io},
        "resource_stats": {"resource_monitor_ok": True},
        "params": {"seq_page_cost": float(index)},
        "p95_ms": 10.0,
        "p99_ms": 20.0,
    }


class RealScoreBTest(unittest.TestCase):
    def setUp(self) -> None:
        self.baseline = {
            "label": "baseline",
            "status": "ok",
            "db_qps": 100.0,
            "resources": {"cpu": 100.0, "memory": 100.0, "io": 100.0},
        }

    def test_exact_cpu_formula_and_gate(self) -> None:
        details = real_score_b_details(
            candidate("cpu_success", qps=120.0, cpu=95.0, memory=110.0, io=105.0, index=1),
            self.baseline,
            target="cpu",
            cfg=CFG,
        )

        self.assertEqual(REAL_SCORE_B_ANCHOR_POLICY, details["policy"])
        self.assertAlmostEqual(0.20, details["qps_gain"])
        self.assertAlmostEqual(95.0, details["target_sweet_upper"])
        self.assertAlmostEqual(1.0, details["target_progress"])
        self.assertAlmostEqual(1.0, details["bonus_r"]["memory"])
        self.assertAlmostEqual(0.0, details["bonus_r"]["io"])
        self.assertAlmostEqual(0.5, details["io_up_penalty"])
        self.assertAlmostEqual(1.215, details["score_b"])
        self.assertTrue(details["successful"])

    def test_qps_gain_is_uncapped(self) -> None:
        lower = candidate("lower", qps=120.0, cpu=93.0, memory=100.0, io=100.0, index=1)
        higher = candidate("higher", qps=180.0, cpu=93.0, memory=100.0, io=100.0, index=2)

        selected, details = select_real_score_b_anchor(
            [lower, higher], self.baseline, target="cpu", cfg=CFG
        )

        self.assertEqual("higher", selected["label"])
        self.assertAlmostEqual(0.80, details["qps_gain"])
        self.assertAlmostEqual(1.80, details["score_b"])

    def test_non_target_resource_rise_is_rewarded_and_drop_is_not_penalized(self) -> None:
        rising = candidate("rising", qps=120.0, cpu=93.0, memory=110.0, io=100.0, index=1)
        flat = candidate("flat", qps=120.0, cpu=93.0, memory=100.0, io=100.0, index=2)
        dropping = candidate("dropping", qps=120.0, cpu=93.0, memory=90.0, io=100.0, index=3)

        rising_details = real_score_b_details(rising, self.baseline, target="cpu", cfg=CFG)
        flat_details = real_score_b_details(flat, self.baseline, target="cpu", cfg=CFG)
        dropping_details = real_score_b_details(dropping, self.baseline, target="cpu", cfg=CFG)

        self.assertAlmostEqual(1.22, rising_details["score_b"])
        self.assertAlmostEqual(1.20, flat_details["score_b"])
        self.assertAlmostEqual(1.20, dropping_details["score_b"])

    def test_target_reward_is_flat_inside_sweet_spot(self) -> None:
        at_five = candidate("five", qps=120.0, cpu=95.0, memory=100.0, io=100.0, index=1)
        at_ten = candidate("ten", qps=120.0, cpu=90.0, memory=100.0, io=100.0, index=2)

        five_details = real_score_b_details(at_five, self.baseline, target="cpu", cfg=CFG)
        ten_details = real_score_b_details(at_ten, self.baseline, target="cpu", cfg=CFG)

        self.assertTrue(five_details["successful"])
        self.assertTrue(ten_details["successful"])
        self.assertAlmostEqual(1.0, five_details["target_progress"])
        self.assertAlmostEqual(1.0, ten_details["target_progress"])
        self.assertAlmostEqual(five_details["score_b"], ten_details["score_b"])

    def test_bottleneck_drop_gate_requires_five_to_ten_percent(self) -> None:
        too_small = real_score_b_details(
            candidate("too_small", qps=150.0, cpu=96.0, memory=100.0, io=100.0, index=1),
            self.baseline,
            target="cpu",
            cfg=CFG,
        )
        too_large = real_score_b_details(
            candidate("too_large", qps=150.0, cpu=89.0, memory=100.0, io=100.0, index=2),
            self.baseline,
            target="cpu",
            cfg=CFG,
        )

        self.assertFalse(too_small["successful"])
        self.assertFalse(too_large["successful"])

    def test_none_has_zero_target_progress_and_requires_resource_up(self) -> None:
        successful = real_score_b_details(
            candidate("none_up", qps=120.0, cpu=105.0, memory=100.0, io=100.0, index=1),
            self.baseline,
            target="none",
            cfg=CFG,
        )
        no_up = real_score_b_details(
            candidate("none_flat", qps=120.0, cpu=100.0, memory=100.0, io=100.0, index=2),
            self.baseline,
            target="none",
            cfg=CFG,
        )

        self.assertEqual(0.0, successful["target_progress"])
        self.assertAlmostEqual(0.21, successful["score_b"])
        self.assertTrue(successful["successful"])
        self.assertFalse(no_up["successful"])

    def test_argmax_filters_out_higher_score_unsuccessful_candidate(self) -> None:
        wrong_drop = candidate("wrong_drop", qps=300.0, cpu=96.0, memory=100.0, io=100.0, index=1)
        successful = candidate("successful", qps=116.0, cpu=93.0, memory=100.0, io=100.0, index=2)

        selected, details = select_real_score_b_anchor(
            [wrong_drop, successful], self.baseline, target="cpu", cfg=CFG
        )

        self.assertEqual("successful", selected["label"])
        self.assertTrue(details["successful"])

    def test_failed_real_replay_is_not_successful(self) -> None:
        failed = candidate(
            "failed", qps=300.0, cpu=93.0, memory=100.0, io=100.0, index=1, status="failed"
        )

        selected, details = select_real_score_b_anchor(
            [failed], self.baseline, target="cpu", cfg=CFG
        )

        self.assertIsNone(selected)
        self.assertIsNone(details)


if __name__ == "__main__":
    unittest.main()
