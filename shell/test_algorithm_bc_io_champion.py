#!/usr/bin/env python3
"""Regression tests for weak IO suppression and process-best champions."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from resource_utility import ResourceUtilityCalculator
from shell.run_pg_algorithm_c_scene import (
    LaneController,
    accepted_batch_selection_key,
    feasible_champion_sort_key,
    formal_inner_scoring_options,
)


def evaluation(tier: str, qps: float, target_drop: float, io_gain: float) -> dict:
    return {
        "decision": {
            "accepted": True,
            "acceptance_tier": tier,
            "qps_gate_gain": qps,
            "qps_gain_vs_baseline": qps,
            "target_drop_vs_reference": target_drop,
            "target_drop_vs_baseline": target_drop,
            "target_resource": "memory",
            "other_gain_vs_reference": {"io": io_gain},
            "other_gain_vs_baseline": {"io": io_gain},
        },
        "normalized": {"db_qps": 100.0 * (1.0 + qps)},
    }


def champion(identity: str, tier: str, qps: float, io_gain: float) -> dict:
    row = evaluation(tier, qps, 0.05, io_gain)
    return {
        **row,
        "candidate_identity": identity,
        "params": {"random_page_cost": 1.0},
        "accepted_index": 1,
    }


class AlgorithmBCIoChampionTest(unittest.TestCase):
    def test_non_target_io_has_weak_negative_derivative(self) -> None:
        options = formal_inner_scoring_options("memory")
        required = options.pop("directional_scoring_contract_required")
        self.assertTrue(required)
        calculator = ResourceUtilityCalculator(**options)
        epsilon = 1e-7
        derivative = (
            calculator.calculate_resource_score(0.4 + epsilon, resource_name="io")
            - calculator.calculate_resource_score(0.4 - epsilon, resource_name="io")
        ) / (2.0 * epsilon)
        self.assertAlmostEqual(-0.5, derivative, places=6)
        self.assertEqual(
            "non_bottleneck_io_cost",
            options["resource_target_ranges"]["io"]["role"],
        )

    def test_target_io_keeps_strong_lower_is_better_contract(self) -> None:
        options = formal_inner_scoring_options("io")
        options.pop("directional_scoring_contract_required")
        calculator = ResourceUtilityCalculator(**options)
        self.assertEqual(3.0, options["resource_weights"]["io"])
        self.assertGreater(
            calculator.calculate_resource_score(0.85, resource_name="io"),
            calculator.calculate_resource_score(0.90, resource_name="io"),
        )

    def test_acceptance_tier_remains_the_primary_batch_key(self) -> None:
        strict = evaluation("strict", 0.01, 0.01, 0.50)
        fallback = evaluation("fallback", 1.00, 0.00, 0.00)
        self.assertGreater(
            accepted_batch_selection_key(strict),
            accepted_batch_selection_key(fallback),
        )

    def test_weak_io_cost_changes_only_same_tier_order(self) -> None:
        low_io = evaluation("secondary", 0.10, 0.05, 0.00)
        high_io = evaluation("secondary", 0.10, 0.05, 0.20)
        self.assertGreater(
            accepted_batch_selection_key(low_io),
            accepted_batch_selection_key(high_io),
        )

    def test_champion_order_is_tier_then_qps_then_resource(self) -> None:
        strict = champion("strict", "strict", 0.05, 0.20)
        secondary = champion("secondary", "secondary", 0.80, 0.00)
        strict_fast = champion("strict-fast", "strict", 0.10, 0.30)
        self.assertGreater(
            feasible_champion_sort_key(strict),
            feasible_champion_sort_key(secondary),
        )
        self.assertGreater(
            feasible_champion_sort_key(strict_fast),
            feasible_champion_sort_key(strict),
        )

    def test_champion_archive_deduplicates_exact_parameter_identity(self) -> None:
        controller = LaneController.__new__(LaneController)
        older = champion("same", "strict", 0.05, 0.10)
        newer = copy.deepcopy(older)
        newer["decision"]["qps_gain_vs_baseline"] = 0.10
        newer["normalized"]["db_qps"] = 110.0
        controller.state = {
            "accepted_candidate_archive": [older, newer, champion("other", "fallback", 1.0, 0.0)]
        }
        ordered = controller._ordered_feasible_champions()
        self.assertEqual(2, len(ordered))
        self.assertEqual("same", ordered[0]["candidate_identity"])
        self.assertEqual(0.10, ordered[0]["decision"]["qps_gain_vs_baseline"])


if __name__ == "__main__":
    unittest.main()
