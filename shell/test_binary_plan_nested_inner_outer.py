#!/usr/bin/env python3
"""Tests for the nested inner-resource / outer-semantic architecture."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from typing import Any, Dict


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from binary_plan_resource_tuner import (  # noqa: E402
    NESTED_RESOURCE_SEMANTIC_POLICY,
    binary_tuner_method_identity_error,
    final_convergence_goal_details,
    inner_resource_score_details,
    outer_semantic_acceptance_details,
    resolve_same_worker_outer_references,
    select_inner_best_for_outer_acceptance,
    update_nested_outer_ranges,
)


CFG = {
    "binary_plan_tuning": {
        "acceptance_policy": {
            "min_target_drop_pct": 5.0,
            "max_target_drop_pct": 10.0,
            "min_db_qps_gain_pct": 15.0,
            "resource_capacity": {
                "cpu_cores": 200.0,
                "memory_gib": 200.0,
                "io_mib_s": 200.0,
            },
        },
        "binary_search": {
            "real_score_b": {"resource_up_scale": 0.10, "bonus_coefficient": 0.02},
            "real_score_c": {"resource_up_scale": 0.10, "bonus_coefficient": 0.02},
            "nested_loop": {
                "outer_acceptance": {
                    "min_db_qps_gain_pct": 0.0,
                    "min_target_drop_pct": 0.0,
                },
                "final_validation": {"min_db_qps_gain_pct": 15.0},
            },
        },
    }
}


def validation(
    label: str,
    *,
    qps: float,
    cpu: float,
    memory: float,
    io: float,
    index: int,
) -> Dict[str, Any]:
    return {
        "label": label,
        "status": "ok",
        "candidate_index": index,
        "db_qps": qps,
        "db_xact_tps": qps / 2.0,
        "resources": {"cpu": cpu, "memory": memory, "io": io},
        "resource_stats": {"resource_monitor_ok": True},
        "resource_samples": {"cpu_cores": 10, "memory_bytes": 10, "io_bps": 10},
        "params": {"seq_page_cost": float(index)},
        "p90_ms": 8.0,
        "p95_ms": 10.0,
        "p99_ms": 20.0,
    }


class NestedInnerOuterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.baseline = validation(
            "baseline", qps=100.0, cpu=100.0, memory=100.0, io=100.0, index=0
        )

    def test_binary_tuner_rejects_false_algorithm_c_identity(self) -> None:
        false_algorithm_c = {
            "binary_plan_tuning": {
                "experiment_meta": {
                    "exp_id": "PG-ALG-C-test",
                    "method": "algorithm_c_nested_top5_parallel2",
                }
            }
        }
        honest_binary = {
            "binary_plan_tuning": {
                "experiment_meta": {
                    "exp_id": "PG-BINARY-PLAN-test",
                    "method": "binary_interval_corner_nested_top5_parallel2_v2",
                }
            }
        }

        self.assertIn(
            "method_identity_mismatch",
            binary_tuner_method_identity_error(false_algorithm_c) or "",
        )
        self.assertIsNone(binary_tuner_method_identity_error(honest_binary))

    def test_inner_score_ignores_qps(self) -> None:
        low_qps = validation(
            "low_qps", qps=1.0, cpu=93.0, memory=110.0, io=100.0, index=1
        )
        details = inner_resource_score_details(
            low_qps, self.baseline, target="cpu", cfg=CFG
        )

        self.assertEqual(NESTED_RESOURCE_SEMANTIC_POLICY, details["policy"])
        self.assertFalse(details["qps_considered"])
        self.assertAlmostEqual(1.02, details["score"])

    def test_outer_evaluates_all_topk_before_starting_new_attempt(self) -> None:
        resource_best_qps_down = validation(
            "resource_best_qps_down",
            qps=90.0,
            cpu=93.0,
            memory=110.0,
            io=100.0,
            index=1,
        )
        next_candidate = validation(
            "next_candidate",
            qps=110.0,
            cpu=94.0,
            memory=90.0,
            io=90.0,
            index=2,
        )

        accepted, _, decisions = select_inner_best_for_outer_acceptance(
            [next_candidate, resource_best_qps_down],
            self.baseline,
            self.baseline,
            target="cpu",
            cfg=CFG,
        )

        self.assertEqual("resource_best_qps_down", decisions[0]["candidate_label"])
        self.assertFalse(decisions[0]["accepted"])
        self.assertIn("db_qps_not_up_vs_last_accepted", decisions[0]["rejects"])
        self.assertEqual("next_candidate", accepted["label"])
        self.assertEqual(2, len(decisions))
        self.assertEqual("next_candidate", decisions[1]["candidate_label"])
        self.assertTrue(decisions[1]["acceptance_eligible"])

        accepted, _, decisions = select_inner_best_for_outer_acceptance(
            [next_candidate],
            self.baseline,
            self.baseline,
            target="cpu",
            cfg=CFG,
            consecutive_missed_outer_rounds=2,
        )

        self.assertEqual("next_candidate", accepted["label"])
        self.assertTrue(decisions[0]["accepted"])
        self.assertFalse(decisions[0]["other_resource_preference_met"])
        self.assertEqual("secondary", decisions[0]["acceptance_tier"])
        self.assertEqual("fallback", decisions[0]["required_acceptance_tier"])

    def test_outer_fallback_is_available_in_every_round(self) -> None:
        qps_only = validation(
            "qps_only", qps=101.0, cpu=101.0, memory=90.0, io=90.0, index=3
        )
        strict = outer_semantic_acceptance_details(
            qps_only, self.baseline, self.baseline, target="cpu", cfg=CFG,
            consecutive_missed_outer_rounds=0,
        )
        secondary = outer_semantic_acceptance_details(
            qps_only, self.baseline, self.baseline, target="cpu", cfg=CFG,
            consecutive_missed_outer_rounds=2,
        )
        fallback = outer_semantic_acceptance_details(
            qps_only, self.baseline, self.baseline, target="cpu", cfg=CFG,
            consecutive_missed_outer_rounds=4,
        )
        self.assertTrue(strict["accepted"])
        self.assertTrue(secondary["accepted"])
        self.assertTrue(fallback["accepted"])
        self.assertEqual("strict", strict["preferred_acceptance_tier"])
        self.assertEqual("strict", secondary["preferred_acceptance_tier"])
        self.assertEqual("strict", fallback["preferred_acceptance_tier"])
        self.assertEqual("fallback", strict["required_acceptance_tier"])
        self.assertFalse(fallback["accepted_escape"])
        self.assertTrue(fallback["formal_state_mutation_allowed"])
        self.assertEqual("fallback", fallback["acceptance_tier"])

    def test_strict_candidate_wins_over_higher_inner_secondary_candidate(self) -> None:
        inner_best_secondary = validation(
            "inner_best_secondary",
            qps=110.0,
            cpu=92.0,
            memory=95.0,
            io=95.0,
            index=1,
        )
        lower_inner_strict = validation(
            "lower_inner_strict",
            qps=105.0,
            cpu=94.0,
            memory=105.0,
            io=95.0,
            index=2,
        )

        accepted, _, decisions = select_inner_best_for_outer_acceptance(
            [inner_best_secondary, lower_inner_strict],
            self.baseline,
            self.baseline,
            target="cpu",
            cfg=CFG,
        )

        self.assertEqual("lower_inner_strict", accepted["label"])
        by_label = {decision["candidate_label"]: decision for decision in decisions}
        self.assertTrue(by_label["inner_best_secondary"]["acceptance_eligible"])
        self.assertTrue(by_label["lower_inner_strict"]["selected_for_acceptance"])

    def test_cross_worker_incumbent_is_ratio_normalized(self) -> None:
        worker_a_baseline = validation(
            "baseline-a", qps=100.0, cpu=100.0, memory=100.0, io=100.0, index=0
        )
        worker_a_baseline["worker"] = {"name": "worker-a"}
        worker_b_baseline = validation(
            "baseline-b", qps=200.0, cpu=50.0, memory=80.0, io=120.0, index=0
        )
        worker_b_baseline["worker"] = {"name": "worker-b"}
        incumbent = validation(
            "accepted-a", qps=110.0, cpu=90.0, memory=105.0, io=95.0, index=1
        )
        incumbent["worker"] = {"name": "worker-a"}
        candidate = validation(
            "candidate-b", qps=225.0, cpu=44.0, memory=86.0, io=110.0, index=2
        )
        candidate["worker"] = {"name": "worker-b"}

        reference, baseline, metadata = resolve_same_worker_outer_references(
            candidate,
            incumbent,
            self.baseline,
            {"worker-a": worker_a_baseline, "worker-b": worker_b_baseline},
        )

        self.assertTrue(metadata["valid"])
        self.assertTrue(metadata["normalized"])
        self.assertAlmostEqual(220.0, reference["db_qps"])
        self.assertAlmostEqual(45.0, reference["resources"]["cpu"])
        self.assertEqual("baseline-b", baseline["label"])

    def test_outer_fallback_allows_qps_up_when_target_does_not_drop(self) -> None:
        reference = validation(
            "accepted_r1", qps=110.0, cpu=94.0, memory=100.0, io=100.0, index=1
        )
        qps_up_target_up = validation(
            "bad_direction", qps=120.0, cpu=95.0, memory=110.0, io=100.0, index=2
        )

        details = outer_semantic_acceptance_details(
            qps_up_target_up, reference, self.baseline, target="cpu", cfg=CFG
        )

        self.assertTrue(details["accepted"])
        self.assertTrue(details["qps_up"])
        self.assertFalse(details["target_down"])
        self.assertEqual("fallback", details["acceptance_tier"])

    def test_outer_canonical_qps_reference_avoids_compounded_gain(self) -> None:
        cfg = copy.deepcopy(CFG)
        outer = cfg["binary_plan_tuning"]["binary_search"]["nested_loop"][
            "outer_acceptance"
        ]
        outer["qps_reference_mode"] = "canonical_baseline"
        outer["qps_threshold_mode"] = "static_floor"
        reference = validation(
            "accepted_r1", qps=150.0, cpu=94.0, memory=100.0, io=100.0, index=1
        )
        candidate = validation(
            "candidate_r2", qps=110.0, cpu=90.0, memory=105.0, io=100.0, index=2
        )

        details = outer_semantic_acceptance_details(
            candidate, reference, self.baseline, target="cpu", cfg=cfg
        )

        self.assertTrue(details["accepted"], details)
        self.assertLess(details["qps_gain_vs_reference"], 0.0)
        self.assertGreater(details["qps_gain_vs_baseline"], 0.0)
        self.assertEqual("canonical_baseline", details["qps_reference_mode"])
        self.assertEqual("static_floor", details["qps_threshold_mode"])
        self.assertEqual("baseline", details["qps_gate_reference_label"])

    def test_fallback_qps_only_is_escape_without_shrink(self) -> None:
        cfg = copy.deepcopy(CFG)
        outer = cfg["binary_plan_tuning"]["binary_search"]["nested_loop"][
            "outer_acceptance"
        ]
        outer["acceptance_tier_downgrade_after_rounds"] = 2
        outer["qps_reference_mode"] = "canonical_baseline"
        outer["qps_threshold_mode"] = "static_floor"
        reference = validation(
            "accepted_r1", qps=110.0, cpu=90.0, memory=100.0, io=100.0, index=1
        )
        candidate = validation(
            "qps_only_escape", qps=120.0, cpu=95.0, memory=100.0, io=100.0, index=2
        )

        details = outer_semantic_acceptance_details(
            candidate,
            reference,
            self.baseline,
            target="cpu",
            cfg=cfg,
            consecutive_missed_outer_rounds=4,
        )

        self.assertTrue(details["accepted"])
        self.assertTrue(details["accepted_shrink"])
        self.assertFalse(details["accepted_escape"])
        self.assertTrue(details["formal_state_mutation_allowed"])
        self.assertNotIn("fallback_qps_only_escape_no_shrink", details["rejects"])

    def test_outer_rejection_keeps_interval_and_acceptance_halves_it(self) -> None:
        ranges = {"seq_page_cost": (0.0001, 100.0)}
        kept, kept_action = update_nested_outer_ranges(
            ranges, ["seq_page_cost"], None
        )
        accepted, accepted_action = update_nested_outer_ranges(
            ranges,
            ["seq_page_cost"],
            {"params": {"seq_page_cost": 0.001}},
        )

        self.assertEqual(ranges, kept)
        self.assertEqual("keep_interval_and_continue_inner_loop", kept_action)
        self.assertNotEqual(ranges, accepted)
        self.assertEqual("shrink_around_accepted_candidate", accepted_action)

    def test_final_goal_uses_qps_not_bottleneck_and_any_other_up(self) -> None:
        confirmation = validation(
            "final", qps=120.0, cpu=93.0, memory=105.0, io=100.0, index=1
        )
        confirmation.update(
            {
                "actual_utilization_pct": {"cpu": 75.0, "memory": 70.0, "io": 50.0},
                "runtime_scoring": {
                    "overload_threshold": 0.8,
                    "resource_target_ranges": {
                        "cpu": {"min_pct": 65.0, "max_pct": 80.0, "target_pct": 72.5}
                    },
                },
            }
        )

        details = final_convergence_goal_details(
            confirmation, self.baseline, target="cpu", cfg=CFG
        )

        self.assertTrue(details["goal_reached"])
        self.assertTrue(details["qps_goal_reached"])
        self.assertTrue(details["target_drop_in_range"])
        self.assertTrue(details["target_not_bottleneck"])
        self.assertTrue(details["target_in_sweet_spot"])
        self.assertEqual(["memory"], details["other_up_resources"])

    def test_sweet_spot_is_preferred_but_not_required_after_debottleneck(self) -> None:
        confirmation = validation(
            "final", qps=120.0, cpu=93.0, memory=105.0, io=100.0, index=1
        )
        confirmation.update(
            {
                "actual_utilization_pct": {"cpu": 50.0},
                "runtime_scoring": {
                    "overload_threshold": 0.8,
                    "resource_target_ranges": {
                        "cpu": {"min_pct": 65.0, "max_pct": 80.0, "target_pct": 72.5}
                    },
                },
            }
        )

        details = final_convergence_goal_details(
            confirmation, self.baseline, target="cpu", cfg=CFG
        )

        self.assertTrue(details["goal_reached"])
        self.assertTrue(details["target_not_bottleneck"])
        self.assertFalse(details["target_in_sweet_spot"])

    def test_final_goal_rejects_target_drop_above_ten_percent(self) -> None:
        confirmation = validation(
            "final", qps=120.0, cpu=75.0, memory=105.0, io=100.0, index=1
        )
        confirmation.update(
            {
                "actual_utilization_pct": {"cpu": 75.0},
                "runtime_scoring": {"overload_threshold": 0.8},
            }
        )

        details = final_convergence_goal_details(
            confirmation, self.baseline, target="cpu", cfg=CFG
        )

        self.assertFalse(details["goal_reached"])
        self.assertFalse(details["target_drop_in_range"])
        self.assertTrue(details["target_not_bottleneck"])

    def test_target_final_goal_does_not_require_other_resource_up(self) -> None:
        confirmation = validation(
            "final", qps=120.0, cpu=93.0, memory=90.0, io=90.0, index=1
        )
        confirmation.update(
            {
                "actual_utilization_pct": {"cpu": 75.0},
                "runtime_scoring": {"overload_threshold": 0.8},
            }
        )

        details = final_convergence_goal_details(
            confirmation, self.baseline, target="cpu", cfg=CFG
        )

        self.assertTrue(details["goal_reached"])
        self.assertFalse(details["any_other_resource_up"])
        self.assertFalse(details["other_resource_up_required"])

    def test_none_final_goal_has_no_target_gate(self) -> None:
        confirmation = validation(
            "final", qps=120.0, cpu=101.0, memory=100.0, io=100.0, index=1
        )

        details = final_convergence_goal_details(
            confirmation, self.baseline, target="none", cfg=CFG
        )

        self.assertTrue(details["goal_reached"])
        self.assertIsNone(details["target_not_bottleneck"])
        self.assertEqual(["cpu"], details["other_up_resources"])


if __name__ == "__main__":
    unittest.main()
