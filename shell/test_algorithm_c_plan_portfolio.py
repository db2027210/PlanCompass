#!/usr/bin/env python3
"""Offline tests: coverage, cost, safety, determinism and unchanged outer gates."""

from __future__ import annotations

import copy
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) in sys.path:
    sys.path.remove(str(ROOT))
sys.path.insert(0, str(ROOT))

from algorithm_c_plan_portfolio import (
    CONTRACT, ParameterBox, PortfolioPolicy, configured_policy, diverse_order, identity, search,
)
from shell import run_pg_algorithm_c_scene as base
from shell.pg_algorithm_c_portfolio_adapter import generate
from shell.run_pg_algorithm_c_budgeted_scene import BudgetedLaneController


def config():
    return {"inner_search": {"enabled": True, "contract": CONTRACT}}


class PortfolioTest(unittest.TestCase):
    def setUp(self):
        self.bounds = dict(base.DEFAULT_RANGES)
        self.incumbent = {name: math.sqrt(low * high) for name, (low, high) in self.bounds.items()}
        self.logs = [name for name in self.bounds if base.resolve_parameter_transform(name) == "log"]
        self.box = ParameterBox(self.bounds, self.logs)
        self.observed = []

    def evaluate(self, params):
        self.observed.append(identity(params))
        score = sum(self.box.encode(params).values())
        return {"optimized_params": params, "candidate_score": score,
                "three_level_fingerprint": {"plan_level": f"plan-{int(score)}"}}

    def run_search(self, **kwargs):
        return search(bounds=self.bounds, log_params=self.logs, incumbent=self.incumbent,
                      directions={name: 1.0 for name in self.bounds}, evaluate=self.evaluate,
                      policy=PortfolioPolicy(), **kwargs)

    def test_normal_counts_bounded_unique_and_current_box_only(self):
        before = copy.deepcopy(self.bounds)
        rows, audit = self.run_search()
        self.assertEqual([s["evaluated"] for s in audit["stages"]], [16, 32, 16])
        self.assertEqual(len(self.observed), 64)
        self.assertEqual(len(set(self.observed)), 64)
        self.assertGreaterEqual(len(rows), 10)
        self.assertFalse(audit["qps_used"])
        self.assertEqual(self.bounds, before)
        for row in rows:
            self.box.encode(row["optimized_params"])

    def test_first_miss_reallocates_without_doubling_work(self):
        _, audit = self.run_search(first_miss=True)
        self.assertEqual([s["evaluated"] for s in audit["stages"]], [8, 48, 8])
        self.assertEqual(audit["offline_evaluations"], 64)

    def test_abstention_explores_before_first_miss_with_same_probe_cost(self):
        rows, audit = self.run_search(target_abstained=True)
        self.assertEqual([s["evaluated"] for s in audit["stages"]], [8, 48, 8])
        self.assertEqual(audit["selection_slots"], {"resource": 1, "plan": 2, "coverage": 2})
        self.assertFalse(audit["first_miss"])
        self.assertTrue(audit["target_prediction_abstained"])
        self.assertEqual(len(set(self.observed)), 64)
        self.assertTrue(all(set(row["optimized_params"]) == set(base.ACTIVE_PARAMS) for row in rows))

    def test_flat_directions_rotate_coordinate_ties_across_calls(self):
        calls = []
        for cursor in (1, 2):
            _, audit = search(
                bounds=self.bounds, log_params=self.logs, incumbent=self.incumbent,
                directions={}, evaluate=self.evaluate, policy=PortfolioPolicy(),
                cursor=cursor, target_abstained=True,
            )
            first = audit["evaluated_candidates"][0]["params"]
            moved = {name for name in self.bounds
                     if abs(self.box.encode(first)[name] - self.box.encode(self.incumbent)[name]) > 1e-9}
            calls.append(moved)
        self.assertEqual(calls[0], set(list(self.bounds)[:3]))
        self.assertEqual(calls[1], set(list(self.bounds)[1:4]))

    def test_refinement_recomputes_seed_directions(self):
        seeds = []
        def direction(row):
            seeds.append(identity(row["optimized_params"]))
            return {name: -1.0 for name in self.bounds}
        _, audit = self.run_search(refinement_direction=direction)
        self.assertEqual(len(seeds), 4)
        self.assertEqual(len(audit["evaluated_candidates"]), 64)

    def test_full_coordinate_coverage_before_any_miss(self):
        self.run_search()
        params = [dict((name, float.fromhex(value)) for name, value in key) for key in self.observed]
        for name, (low, high) in self.bounds.items():
            self.assertAlmostEqual(min(p[name] for p in params), low)
            self.assertAlmostEqual(max(p[name] for p in params), high)

    def test_deterministic_and_qps_cannot_change_output(self):
        rows, first = self.run_search()
        evaluator = self.evaluate
        def qps_contaminated(params):
            row = evaluator(params)
            row["db_qps"] = -1e99 * row["candidate_score"]
            row["decision"] = {"accepted": False, "db_qps": 1e99}
            return row
        self.evaluate = qps_contaminated
        other, second = self.run_search()
        self.assertEqual([r["optimized_params"] for r in rows], [r["optimized_params"] for r in other])
        self.assertEqual(first["next_cursor"], second["next_cursor"])

    def test_same_plan_keeps_ten_numeric_alternatives(self):
        evaluator = self.evaluate
        def same_plan(params):
            row = evaluator(params)
            row["three_level_fingerprint"]["plan_level"] = "one-plan"
            return row
        self.evaluate = same_plan
        rows, _ = self.run_search()
        self.assertEqual(len(rows), 10)
        self.assertEqual(len({identity(row["optimized_params"]) for row in rows}), 10)

    def test_diversity_survives_previously_seen_plan_labels(self):
        rows = [{"optimized_params": {"p": i}, "candidate_score": 100 - i,
                 "three_level_fingerprint": {"plan_level": "a" if i < 8 else "b"},
                 "portfolio_coverage_distance": 1.0 if i == 9 else 0.0}
                for i in range(10)]
        selected = diverse_order(rows, seen_plans=["a", "b"])
        self.assertEqual([r["optimized_params"]["p"] for r in selected[:5]], [0, 1, 2, 8, 9])

    def test_abstention_keeps_low_scoring_novel_plans_and_sparse_regions(self):
        rows = [{"optimized_params": {"p": i}, "candidate_score": 100 - i,
                 "three_level_fingerprint": {"plan_level": "a" if i < 7 else str(i)},
                 "portfolio_coverage_distance": float(i)} for i in range(10)]
        selected = diverse_order(rows, target_abstained=True)
        self.assertEqual([r["optimized_params"]["p"] for r in selected[:5]], [0, 7, 8, 9, 6])
        contaminated = copy.deepcopy(rows)
        for row in contaminated:
            row["db_qps"] = row["optimized_params"]["p"] * 1e9
            row["decision"] = {"accepted": True}
        self.assertEqual(
            [r["optimized_params"] for r in selected],
            [r["optimized_params"] for r in diverse_order(contaminated, target_abstained=True)],
        )

    def test_collapsed_box_does_not_duplicate_or_loop(self):
        self.bounds = {name: (value, value) for name, value in self.incumbent.items()}
        rows, audit = self.run_search()
        self.assertEqual(rows, [])
        self.assertEqual(audit["offline_evaluations"], 0)

    def test_technical_failures_are_explicit(self):
        def failing(_params):
            raise RuntimeError("offline probe failed")
        self.evaluate = failing
        rows, audit = self.run_search()
        self.assertEqual(rows, [])
        self.assertEqual(len(audit["errors"]), 64)

    def test_opt_in_and_no_outer_configuration(self):
        self.assertIsNone(configured_policy({}))
        self.assertIsInstance(configured_policy(config()), PortfolioPolicy)
        with self.assertRaises(ValueError):
            configured_policy({"inner_search": {"enabled": True, "contract": "typo"}})
        with self.assertRaises(ValueError):
            PortfolioPolicy(global_points=0)

    def test_controller_adapter_preserves_outer_state_and_audits(self):
        controller = BudgetedLaneController.__new__(BudgetedLaneController)
        controller.algorithm_cfg = config()
        controller.target = "cpu"
        controller.capacity = {"cpu": 10.0, "memory": 10.0, "io": 10.0}
        controller.ranges = {name: base.ParameterRange(*limits) for name, limits in self.bounds.items()}
        controller.state = {"incumbent_params": self.incumbent, "accepted": 3,
                            "consecutive_missed_outer_rounds": 0, "normalized_width": 0.5,
                            "canonical_baseline": {"resources": {"cpu": 9.0, "memory": 4.0, "io": 4.0}}}
        controller.experience_artifact = {
            "resource_prediction_calibration": {},
            "formal_admission": {"target_prediction_abstained": True},
        }
        controller.experience_table = {name: {"cpu": 1.0, "memory": 0.2, "io": 0.0, "confidence": 1.0}
                                       for name in self.bounds}
        controller.inner_scoring_reference_resources = lambda resources: resources
        controller.queries_and_probe = lambda params: ([{"sql": "select 1"}], {}, {})
        controller.log_path = Path("/tmp/not-written-portfolio-test.jsonl")
        controller.log = mock.Mock()
        controller.save_state = mock.Mock()
        prior = copy.deepcopy(controller.state)

        def estimate(_sql, params):
            return {"cpu": 8.6, "memory": 4.5, "io": 4.0}, {}, {"plan_model_confidence": {"cpu": 1.0}}

        def fingerprint(row, capacity):
            return {**row, "three_level_fingerprint": {"plan_level": "one-plan", "valid": True},
                    "feature_ratio": {name: 1.0 for name in capacity},
                    "predicted_resource": row["final_resource"],
                    "utilization": {name: row["final_resource"][name] / value for name, value in capacity.items()}}

        with mock.patch.object(base, "RatioScaleEstimator", return_value=estimate), mock.patch.object(
            base, "attach_three_level_fingerprint", side_effect=fingerprint
        ), mock.patch.object(base, "target_support_abstention_enabled", return_value=False):
            rows = generate(controller, 4)
        self.assertEqual(len(rows), 10)
        for name, value in prior.items():
            self.assertEqual(controller.state[name], value)
        self.assertTrue(all(row["numeric_refinement"] for row in rows))
        self.assertTrue(all(not row["plan_space_saturation_required"] for row in rows))
        self.assertEqual(controller.state["plan_portfolio"]["last_audit"]["offline_evaluations"], 64)
        self.assertTrue(controller.state["plan_portfolio"]["target_prediction_abstained"])
        self.assertEqual(
            [stage["evaluated"] for stage in controller.state["plan_portfolio"]["last_audit"]["stages"]],
            [8, 48, 8],
        )

    def test_new_inner_contract_cannot_resume_old_run(self):
        controller = base.LaneController.__new__(base.LaneController)
        controller.algorithm_cfg = config()
        with tempfile.TemporaryDirectory() as directory:
            controller.state_path = Path(directory) / "state.json"
            controller.state_path.write_text(json.dumps({"accepted": 7}), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "fresh C campaign"):
                controller.load_or_initialize_state()

    def test_previous_portfolio_contract_requires_fresh_configuration(self):
        with self.assertRaises(ValueError):
            configured_policy({"inner_search": {
                "enabled": True, "contract": "algorithm_c_resource_plan_portfolio_v1",
            }})

    def test_dynamic_numeric_recovery_marks_portfolio_saturation(self):
        controller = base.LaneController.__new__(base.LaneController)
        controller.algorithm_cfg = config()
        expected = [{"optimized_params": {"p": 1.0}}]

        with mock.patch(
            "shell.pg_algorithm_c_portfolio_adapter.generate",
            return_value=expected,
        ) as generate_mock:
            observed = base.LaneController.generate_global_plan_coverage_rankings(
                controller,
                9,
                allow_numeric_refinement=True,
            )

        self.assertIs(observed, expected)
        generate_mock.assert_called_once_with(
            controller,
            9,
            plan_space_saturation_required=True,
        )

    def test_first_miss_generates_only_one_portfolio(self):
        controller = BudgetedLaneController.__new__(BudgetedLaneController)
        controller.algorithm_cfg = config()
        controller.begin_first_miss_expansion = mock.Mock()
        controller.generate_rankings = mock.Mock(return_value=[{"rank": 1}])
        controller.generate_recovered_gradient_rankings = mock.Mock(side_effect=AssertionError("legacy search"))
        controller.generate_global_plan_coverage_rankings = mock.Mock(side_effect=AssertionError("duplicate search"))
        self.assertEqual(controller.generate_first_miss_rankings(2), [{"rank": 1}])
        controller.begin_first_miss_expansion.assert_called_once_with(2)
        controller.generate_rankings.assert_called_once()

    def test_admission_keeps_hard_exclusions_before_diverse_selection(self):
        controller = BudgetedLaneController.__new__(BudgetedLaneController)
        controller.algorithm_cfg = config()
        controller.target = "cpu"
        controller.state = {}
        controller.outer_pruning_policy = {"enabled": True}
        controller.workload_contract_sha256 = "unit"
        controller.current_outer_pruning_context = lambda: {}
        controller.ranges = {name: base.ParameterRange(*bounds) for name, bounds in self.bounds.items()}
        controller.log = mock.Mock()
        rows = []
        for i in range(12):
            params = dict(self.incumbent)
            params["seq_page_cost"] = 0.2 + i * 0.1 if i else 100.0
            rows.append({"optimized_params": params, "rank": i, "candidate_score": 100 - i,
                         "initial_resource": {"cpu": 9.0}, "final_resource": {"cpu": 8.0},
                         "three_level_fingerprint": {"plan_level": "a" if i < 8 else "b"},
                         "portfolio_coverage_distance": 1.0 if i == 11 else 0.0})
        with mock.patch.object(base, "assess_valid_replay_identity", side_effect=lambda memory, row: {
            "seen_valid_replay": row["rank"] == 1
        }), mock.patch.object(base, "assess_outer_pruning_candidate", side_effect=lambda *args, **kw: {
            "hard_prune": kw["candidate"]["rank"] == 2, "soft_deprioritize": False
        }):
            selected, audit = controller.select_outer_admitted_candidates(
                rows, round_index=1, batch_kind="base_top5", limit=5,
            )
        self.assertEqual([row["rank"] for row in selected], [3, 4, 5, 8, 11])
        self.assertEqual(audit["range_excluded_count"], 1)
        self.assertEqual(audit["exact_valid_replay_excluded_count"], 1)
        self.assertEqual(audit["hard_pruned_count"], 1)
        controller.state["plan_portfolio"] = {"target_prediction_abstained": True}
        with mock.patch.object(base, "assess_valid_replay_identity", side_effect=lambda memory, row: {
            "seen_valid_replay": row["rank"] == 1
        }), mock.patch.object(base, "assess_outer_pruning_candidate", side_effect=lambda *args, **kw: {
            "hard_prune": kw["candidate"]["rank"] == 2, "soft_deprioritize": False
        }):
            selected, audit = controller.select_outer_admitted_candidates(
                rows, round_index=2, batch_kind="base_top5", limit=5,
            )
        self.assertEqual([row["rank"] for row in selected], [3, 8, 4, 11, 5])
        self.assertEqual(audit["hard_pruned_count"], 1)
        self.assertFalse(audit["plan_portfolio_3_quality_1_plan_1_coverage"])
        self.assertTrue(audit["plan_portfolio_1_quality_2_plan_2_coverage"])


if __name__ == "__main__":
    unittest.main()
