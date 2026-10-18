#!/usr/bin/env python3
"""Regression tests for strategy A small-loop Top1 anchoring."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from binary_plan_resource_tuner import (  # noqa: E402
    SMALL_LOOP_TOP1_ANCHOR_POLICY,
    select_small_loop_top1_anchor,
)


class SmallLoopTop1AnchorTest(unittest.TestCase):
    @staticmethod
    def validation(label: str, *, raw_index: int, topk_rank: int, qps: float) -> dict:
        return {
            "label": label,
            "status": "ok",
            "raw_candidate_index": raw_index,
            "candidate_index": raw_index,
            "topk_rank": topk_rank,
            "mask": "LR",
            "params": {"seq_page_cost": float(raw_index)},
            "db_qps": qps,
            "resources": {"cpu": 1.0, "memory": 2.0, "io": 3.0},
        }

    def test_real_qps_does_not_change_small_loop_top1(self) -> None:
        top1_candidate = {
            "target": "cpu",
            "raw_candidate_index": 7,
            "topk_rank": 1,
            "mask": "LR",
            "params": {"seq_page_cost": 7.0},
        }
        top1_probe = {
            "topk_rank_key": [0.8, 0.2, 0.0, 1, -1.0],
            "candidate_resource_avg": {"cpu": 0.2},
        }
        real_top1 = self.validation("top1_low_real_qps", raw_index=7, topk_rank=1, qps=80.0)
        unrelated_high_qps = self.validation("not_recalled", raw_index=8, topk_rank=2, qps=300.0)

        anchor, details = select_small_loop_top1_anchor(
            [(7, top1_candidate, top1_probe)],
            [real_top1, unrelated_high_qps],
        )

        self.assertEqual("top1_low_real_qps", anchor["label"])
        self.assertEqual(SMALL_LOOP_TOP1_ANCHOR_POLICY, details["policy"])
        self.assertEqual(top1_probe["topk_rank_key"], details["small_loop_score"])
        self.assertFalse(details["real_metrics_used_for_anchor_selection"])

    def test_resume_can_recover_top1_from_existing_validation(self) -> None:
        existing = self.validation("existing_top1", raw_index=11, topk_rank=1, qps=120.0)
        existing["small_loop_score"] = [0.7, 0.1, 0.0, 1, -2.0]
        existing["plan_input_changes"] = {
            "topk_rank_key": existing["small_loop_score"],
            "candidate_resource_avg": {"cpu": 0.3},
        }

        anchor, details = select_small_loop_top1_anchor([], [existing])

        self.assertEqual("existing_top1", anchor["label"])
        self.assertEqual(existing["small_loop_score"], details["small_loop_score"])
        self.assertTrue(details["real_replay_attempted"])

    def test_no_small_loop_candidate_returns_none(self) -> None:
        anchor, details = select_small_loop_top1_anchor([], [])

        self.assertIsNone(anchor)
        self.assertIsNone(details)


if __name__ == "__main__":
    unittest.main()
