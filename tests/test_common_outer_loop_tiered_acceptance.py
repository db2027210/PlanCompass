from __future__ import annotations

import unittest

from common_outer_loop import (
    advance_acceptance_tier,
    cap_real_replay_top_k,
    decide_validation_acceptance,
)
from mysql_parameter_optimizer import _eligible_replay_selection_key


class TieredOuterAcceptanceTest(unittest.TestCase):
    def test_formal_real_replay_cap_is_fixed_at_five(self):
        self.assertEqual(cap_real_replay_top_k(0), 5)
        self.assertEqual(cap_real_replay_top_k(8), 5)
        self.assertEqual(cap_real_replay_top_k(5, available_count=3), 3)

    def setUp(self) -> None:
        self.incumbent = {
            "db_qps": 100.0,
            "utilization_pct": {"cpu": 0.90, "memory": 0.40, "io": 0.40},
        }

    def decision(self, candidate, tier, hard_gate_passed=True):
        return decide_validation_acceptance(
            self.incumbent,
            candidate,
            target_resource="cpu",
            hard_gate_passed=hard_gate_passed,
            acceptance_tier=tier,
        )

    def test_strict_tier_requires_qps_up_target_down_and_all_non_targets_up(self):
        candidate = {
            "db_qps": 101.0,
            "utilization_pct": {"cpu": 0.85, "memory": 0.41, "io": 0.42},
        }
        decision = self.decision(candidate, "strict_three_level")
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.satisfied_acceptance_tier, "strict_three_level")

    def test_secondary_accepts_without_non_target_increase(self):
        candidate = {
            "db_qps": 101.0,
            "utilization_pct": {"cpu": 0.85, "memory": 0.39, "io": 0.42},
        }
        strict = self.decision(candidate, "strict_three_level")
        secondary = self.decision(candidate, "qps_target")
        self.assertFalse(strict.accepted)
        self.assertTrue(secondary.accepted)
        self.assertEqual(secondary.satisfied_acceptance_tier, "qps_target")

    def test_fallback_accepts_qps_only(self):
        candidate = {
            "db_qps": 101.0,
            "utilization_pct": {"cpu": 0.91, "memory": 0.39, "io": 0.39},
        }
        self.assertFalse(self.decision(candidate, "qps_target").accepted)
        fallback = self.decision(candidate, "fallback_qps")
        self.assertTrue(fallback.accepted)
        self.assertEqual(fallback.satisfied_acceptance_tier, "fallback_qps")

    def test_qps_must_increase_and_hard_gates_never_degrade(self):
        candidate = {
            "db_qps": 100.0,
            "utilization_pct": {"cpu": 0.85, "memory": 0.41, "io": 0.41},
        }
        self.assertFalse(self.decision(candidate, "fallback_qps").accepted)
        candidate["db_qps"] = 101.0
        self.assertFalse(
            self.decision(candidate, "fallback_qps", hard_gate_passed=False).accepted
        )

    def test_downgrade_occurs_on_exactly_second_consecutive_miss(self):
        tier = "strict_three_level"
        failed = 0
        progress = advance_acceptance_tier(
            acceptance_tier=tier,
            consecutive_failed_rounds=failed,
            accepted=False,
            downgrade_after_rounds=2,
        )
        tier = progress.acceptance_tier
        failed = progress.consecutive_failed_rounds
        self.assertFalse(progress.downgraded)
        self.assertEqual(tier, "strict_three_level")
        progress = advance_acceptance_tier(
            acceptance_tier=tier,
            consecutive_failed_rounds=failed,
            accepted=False,
            downgrade_after_rounds=2,
        )
        self.assertTrue(progress.downgraded)
        self.assertEqual(progress.downgraded_from, "strict_three_level")
        self.assertEqual(progress.acceptance_tier, "qps_target")
        self.assertEqual(progress.consecutive_failed_rounds, 0)

    def test_acceptance_resets_failure_count_without_upgrading_tier(self):
        progress = advance_acceptance_tier(
            acceptance_tier="qps_target",
            consecutive_failed_rounds=1,
            accepted=True,
            downgrade_after_rounds=2,
        )
        self.assertEqual(progress.acceptance_tier, "qps_target")
        self.assertEqual(progress.consecutive_failed_rounds, 0)
        self.assertFalse(progress.downgraded)

    def test_secondary_downgrades_to_fallback_and_fallback_stays_bounded(self):
        progress = advance_acceptance_tier(
            acceptance_tier="qps_target",
            consecutive_failed_rounds=1,
            accepted=False,
            downgrade_after_rounds=2,
        )
        self.assertEqual(progress.acceptance_tier, "fallback_qps")
        self.assertTrue(progress.downgraded)
        fallback = advance_acceptance_tier(
            acceptance_tier="fallback_qps",
            consecutive_failed_rounds=2,
            accepted=False,
            downgrade_after_rounds=2,
        )
        self.assertEqual(fallback.acceptance_tier, "fallback_qps")
        self.assertEqual(fallback.consecutive_failed_rounds, 2)
        self.assertFalse(fallback.downgraded)

    def test_fixed_batch_selection_prefers_stricter_satisfied_tier(self):
        queue = [
            {"subset_id": "strict", "optimized_params": {"p": 1.0}},
            {"subset_id": "fallback", "optimized_params": {"p": 2.0}},
        ]
        rows = [
            {
                "candidate_subset_id": "strict",
                "candidate_final_validation": {
                    "utilization_pct": {"cpu": 0.88},
                    "outer_gate": {
                        "acceptance_decision": {
                            "satisfied_acceptance_tier_rank": 3,
                        }
                    },
                },
            },
            {
                "candidate_subset_id": "fallback",
                "candidate_final_validation": {
                    "utilization_pct": {"cpu": 0.70},
                    "outer_gate": {
                        "acceptance_decision": {
                            "satisfied_acceptance_tier_rank": 1,
                        }
                    },
                },
            },
        ]
        chosen = min(
            rows,
            key=lambda row: _eligible_replay_selection_key(
                row,
                queue=queue,
                baseline_params={"p": 0.5},
                target_resource="cpu",
            ),
        )
        self.assertEqual(chosen["candidate_subset_id"], "strict")


if __name__ == "__main__":
    unittest.main()
