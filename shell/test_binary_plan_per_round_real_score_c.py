#!/usr/bin/env python3
"""Regression tests for real-only score C without QPS influence."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any, Dict


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from binary_plan_resource_tuner import (  # noqa: E402
    REAL_SCORE_C_ANCHOR_POLICY,
    real_score_c_details,
    select_real_score_c_anchor,
)


CFG = {
    "binary_plan_tuning": {
        "acceptance_policy": {
            "min_target_drop_pct": 5.0,
            "max_target_drop_pct": 10.0,
            "min_db_qps_gain_pct": 15.0,
        },
        "binary_search": {
            "real_score_b": {"resource_up_scale": 0.10, "bonus_coefficient": 0.02},
            "real_score_c": {"resource_up_scale": 0.10, "bonus_coefficient": 0.02},
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


class RealScoreCTest(unittest.TestCase):
    def setUp(self) -> None:
        self.baseline = {
            "label": "baseline",
            "status": "ok",
            "db_qps": 100.0,
            "resources": {"cpu": 100.0, "memory": 100.0, "io": 100.0},
        }

    def test_qps_is_absent_from_score_and_gate(self) -> None:
        details = real_score_c_details(
            candidate("low_qps", qps=1.0, cpu=93.0, memory=110.0, io=105.0, index=1),
            self.baseline,
            target="cpu",
            cfg=CFG,
        )

        self.assertEqual(REAL_SCORE_C_ANCHOR_POLICY, details["policy"])
        self.assertFalse(details["qps_considered"])
        self.assertIsNone(details["qps_gate"])
        self.assertTrue(details["successful"])
        self.assertAlmostEqual(1.015, details["score_c"])

    def test_qps_is_not_a_tie_break(self) -> None:
        low_qps = candidate("low_qps", qps=1.0, cpu=93.0, memory=100.0, io=100.0, index=1)
        high_qps = candidate("high_qps", qps=1000.0, cpu=93.0, memory=100.0, io=100.0, index=2)

        selected, details = select_real_score_c_anchor(
            [high_qps, low_qps], self.baseline, target="cpu", cfg=CFG
        )

        self.assertEqual("low_qps", selected["label"])
        self.assertAlmostEqual(1.0, details["score_c"])

    def test_non_target_rise_breaks_equal_resource_progress(self) -> None:
        flat = candidate("flat", qps=500.0, cpu=93.0, memory=100.0, io=100.0, index=1)
        rising = candidate("rising", qps=1.0, cpu=93.0, memory=110.0, io=100.0, index=2)

        selected, details = select_real_score_c_anchor(
            [flat, rising], self.baseline, target="cpu", cfg=CFG
        )

        self.assertEqual("rising", selected["label"])
        self.assertAlmostEqual(1.02, details["score_c"])

    def test_no_success_falls_back_to_valid_real_score_not_small_loop(self) -> None:
        low_progress = candidate("low_progress", qps=500.0, cpu=99.0, memory=100.0, io=100.0, index=1)
        high_progress = candidate("high_progress", qps=1.0, cpu=96.0, memory=100.0, io=100.0, index=2)

        selected, details = select_real_score_c_anchor(
            [low_progress, high_progress], self.baseline, target="cpu", cfg=CFG
        )

        self.assertEqual("high_progress", selected["label"])
        self.assertFalse(details["successful"])
        self.assertTrue(details["valid"])
        self.assertAlmostEqual(0.8, details["score_c"])

    def test_none_requires_resource_rise_but_not_qps(self) -> None:
        details = real_score_c_details(
            candidate("none_up", qps=1.0, cpu=105.0, memory=100.0, io=100.0, index=1),
            self.baseline,
            target="none",
            cfg=CFG,
        )

        self.assertTrue(details["successful"])
        self.assertAlmostEqual(0.01, details["score_c"])


if __name__ == "__main__":
    unittest.main()
