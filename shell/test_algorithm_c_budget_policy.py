#!/usr/bin/env python3
"""Regressions for lost regions, discarded feasible points, and bounded trial cost."""

from __future__ import annotations

import itertools
import math
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from algorithm_c_budget_policy import (
    ACTIVE_TIME_CONTRACT, Budget, actual_widths, admit_feasible_evidence,
    balanced_outer_qps_confirmation, best_feasible, complement_boxes,
    resume_active_time_accounting,
)
from algorithm_c_adaptive_search import AdaptiveSearchPolicy
from shell.run_pg_algorithm_c_budgeted_scene import BudgetedLaneController
from shell import run_pg_algorithm_c_scene as base


class RegionTests(unittest.TestCase):
    def test_all_joint_regions_are_preserved(self):
        original = {"a": (0., 10.), "b": (0., 10.), "c": (0., 10.)}
        retained = {"a": (5., 10.), "b": (0., 5.), "c": (2., 8.)}
        pieces = complement_boxes(original, retained)
        volume = lambda box: math.prod(hi - lo for lo, hi in box.values())
        self.assertAlmostEqual(sum(map(volume, pieces)) + volume(retained), volume(original))
        # Non-boundary points are covered exactly once, including mixed discarded dimensions.
        for coordinates in itertools.product((.5, 3., 6., 9.), repeat=3):
            covers = sum(all(lo <= x <= hi for x, (lo, hi) in zip(coordinates, box.values()))
                         for box in pieces + [retained])
            self.assertEqual(covers, 1)

    def test_io_champion_side_remains_available(self):
        boxes = complement_boxes({"parallel_setup_cost": (100., 10000.)},
                                 {"parallel_setup_cost": (100., 5050.)})
        self.assertTrue(any(lo <= 9707.1 <= hi for box in boxes for lo, hi in box.values()))

    def test_actual_width_not_accepted_counter(self):
        result = actual_widths({"seq_page_cost": (.1, 10.), "cpu_tuple_cost": (.001, .01)},
                               {"seq_page_cost": (.1, 10.), "cpu_tuple_cost": (.001, .1)})
        self.assertEqual(result["seq_page_cost"], 1.)
        self.assertAlmostEqual(result["cpu_tuple_cost"], .5)

    def test_coverage_is_bounded_and_inside_region_with_external_incumbent(self):
        controller = BudgetedLaneController.__new__(BudgetedLaneController)
        controller.policy = {"region_plan_probes": 32}
        controller.ranges = {k: base.ParameterRange(*v) for k, v in base.DEFAULT_RANGES.items()}
        controller.ranges["parallel_setup_cost"] = base.ParameterRange(5050., 10000.)
        incumbent = {k: v.min_value for k, v in controller.ranges.items()}
        incumbent["parallel_setup_cost"] = 1203.
        vectors = controller.coverage_vectors(incumbent)
        self.assertEqual(len(vectors), 32)
        self.assertEqual(len({tuple(sorted(x.items())) for x in vectors}), 32)
        self.assertTrue(all(controller.ranges[k].min_value <= v <= controller.ranges[k].max_value
                            for row in vectors for k, v in row.items()))


class ArchiveTests(unittest.TestCase):
    def decision(self, qps, safe=True):
        return {"accepted": safe, "non_target_safe": safe, "qps_up": True, "db_qps": qps}

    def test_cpu_high_qps_point_survives_resource_preference(self):
        archive = {}
        for value, qps, label in ((1., 84.2806, "selected"), (2., 91.0709, "not_selected")):
            admit_feasible_evidence(archive, params={"p": value}, decision=self.decision(qps), source={"label": label})
        best = best_feasible(archive)
        self.assertEqual(best["params"], {"p": 2.})
        self.assertFalse(best["independently_validated"])

    def test_io_unsafe_high_qps_is_excluded(self):
        archive = {}
        self.assertFalse(admit_feasible_evidence(archive, params={"p": 1.}, decision=self.decision(555.26, False),
                                                source={"label": "cpu_over_80"}))
        self.assertIsNone(best_feasible(archive))

    def test_cached_redecisions_do_not_duplicate_evidence(self):
        archive = {}
        arguments = dict(params={"p": 1.}, decision=self.decision(90.), source={"label": "physical1"})
        self.assertTrue(admit_feasible_evidence(archive, **arguments))
        self.assertFalse(admit_feasible_evidence(archive, **arguments))
        self.assertEqual(len(best_feasible(archive)["observations"]), 1)

    def test_legacy_hook_does_not_change_state(self):
        controller = base.LaneController.__new__(base.LaneController)
        controller.state = {"accepted": 2}
        controller.observe_valid_evaluation({"decision": self.decision(90.)}, 3)
        self.assertEqual(controller.state, {"accepted": 2})


class OuterConfirmationTests(unittest.TestCase):
    @staticmethod
    def rows(values):
        return [
            {"worker_key": worker, "db_qps": qps}
            for worker, worker_values in values.items()
            for qps in worker_values
        ]

    def test_balanced_stable_candidate_passes_replicated_outer_gate(self):
        baseline = self.rows({"w1": [100.0, 104.0], "w2": [98.0, 102.0]})
        candidate = self.rows({"w1": [108.0, 109.0], "w2": [105.0, 106.0]})
        result = balanced_outer_qps_confirmation(
            candidate,
            baseline,
            static_floor_pct=1.0,
            confidence_z=1.645,
        )
        self.assertTrue(result["ready"])
        self.assertTrue(result["passed"])
        self.assertEqual(result["candidate_physical_replays"], 4)
        self.assertFalse(result["qps_used_for_inner_scoring"])
        self.assertFalse(result["external_baseline_information_used"])

    def test_unbalanced_confirmation_fails_closed(self):
        baseline = self.rows({"w1": [100.0, 101.0], "w2": [100.0, 101.0]})
        candidate = self.rows({"w1": [105.0, 106.0], "w2": [105.0]})
        result = balanced_outer_qps_confirmation(
            candidate,
            baseline,
            static_floor_pct=1.0,
            confidence_z=1.645,
        )
        self.assertFalse(result["ready"])

    def test_controller_archives_only_balanced_outer_confirmation(self):
        controller = BudgetedLaneController.__new__(BudgetedLaneController)
        controller.adaptive_policy = base_policy = AdaptiveSearchPolicy()
        controller.target = "cpu"
        controller.capacity = {"cpu": 16.0, "memory": 32.0, "io": 100.0}
        controller.cfg = {
            "algorithm_c": {"qps_noise_gate": {"enabled": True}},
            "binary_plan_tuning": {
                "binary_search": {
                    "nested_loop": {
                        "outer_acceptance": {
                            "min_db_qps_gain_pct": 1.0,
                            "qps_reference_mode": "last_accepted",
                            "qps_threshold_mode": "max_static_and_noise",
                            "non_target_bottleneck_threshold_pct": 80.0,
                        }
                    }
                },
                "acceptance_policy": {
                    "resource_capacity": {
                        "cpu_cores": 16.0,
                        "memory_gib": 32.0,
                        "io_mib_s": 100.0,
                    }
                },
            },
        }
        baseline_rows = self.rows({"w1": [100.0, 104.0], "w2": [98.0, 102.0]})
        for row in baseline_rows:
            row.update({"status": "ok", "resources": {"cpu": 4.0, "memory": 8.0, "io": 20.0}})
        ranking = {
            "optimized_params": {"p": 1.0},
            "active_subset": ["p"],
        }
        original = {
            "status": "ok",
            "label": "original-w1",
            "worker_key": "w1",
            "db_qps": 108.0,
            "resources": {"cpu": 3.5, "memory": 8.0, "io": 20.0},
            "candidate_ranking": ranking,
        }
        new_rows = []
        for index, (worker, qps) in enumerate(
            (("w2", 105.0), ("w1", 109.0), ("w2", 106.0)),
            start=1,
        ):
            new_rows.append({
                "status": "ok",
                "label": f"confirmation-{index}",
                "worker_key": worker,
                "db_qps": qps,
                "resources": {"cpu": 3.5, "memory": 8.0, "io": 20.0},
                "candidate_ranking": ranking,
            })
        canonical = {
            "status": "ok",
            "label": "algorithm_c_canonical_parallel_baseline",
            "db_qps": 101.0,
            "resources": {"cpu": 4.0, "memory": 8.0, "io": 20.0},
        }
        controller.state = {
            "canonical_baseline": canonical,
            "incumbent_validation": canonical,
            "baseline_replay_rows": baseline_rows,
            "baseline_by_worker": {
                "w1": {**canonical, "db_qps": 102.0},
                "w2": {**canonical, "db_qps": 100.0},
            },
            "qps_noise_model": {"static_floor_pct": 1.0, "confidence_z": 1.645},
        }
        controller.trial = {
            "feasible_archive": {},
            "outer_confirmation_exhausted_identities": [],
            "completed_outer_confirmation_count": 0,
        }
        controller.log = lambda *_args, **_kwargs: None
        controller.save_state = lambda: None
        entry = {
            "identity": '{"p": "1"}',
            "ranking": ranking,
            "original_physical_row": original,
        }
        selected, valid_count = controller.evaluate_outer_confirmation_batch(
            entry,
            new_rows,
            round_index=3,
        )
        self.assertEqual(base_policy.worker_count, 2)
        self.assertEqual(valid_count, 3)
        self.assertIsNotNone(selected)
        self.assertEqual(len(controller.trial["feasible_archive"]), 1)
        self.assertEqual(controller.trial["completed_outer_confirmation_count"], 1)


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.budget = Budget(12, 18000., 1800., 6000., 1800.)

    def test_replay_and_planning_and_finalization_reservation(self):
        self.assertIsNone(self.budget.stop_reason(used=5, wall_s=100., planning_s=40.))
        self.assertEqual(self.budget.stop_reason(used=12, wall_s=100., planning_s=40.), "search_replay_budget_exhausted")
        self.assertEqual(self.budget.stop_reason(used=5, wall_s=100., planning_s=1800.), "planning_budget_exhausted")
        self.assertEqual(self.budget.stop_reason(used=5, wall_s=10200., planning_s=40.), "wall_budget_reserved_for_finalization")

    def test_failed_replays_consume_budget_and_recovery_is_idempotent(self):
        controller = BudgetedLaneController.__new__(BudgetedLaneController)
        controller.state = {"budgeted_trial": {"search_replays": 0, "accounted_labels": []}}
        rows = [{"label": "failed_physical", "technical_failure": True}]
        with mock.patch.object(base.LaneController, "account_physical_replay_rows", return_value={"technical_failures": 1}):
            controller.account_physical_replay_rows(rows, round_index=1, batch_kind="trial_search")
            controller.account_physical_replay_rows(rows, round_index=1, batch_kind="trial_search")
        self.assertEqual(controller.state["budgeted_trial"]["search_replays"], 1)

    def test_bad_limits_fail_closed(self):
        for bad in (float("nan"), float("inf"), -1.):
            with self.assertRaises(ValueError):
                Budget(5, bad, 50., 100., 50.)

    def test_host_outage_is_excluded_from_active_wall(self):
        result = resume_active_time_accounting(
            {"started_at_epoch_s": 100.},
            observed_activity_epoch_s=160.,
            resume_epoch_s=400.,
        )
        self.assertEqual(result["active_time_contract"], ACTIVE_TIME_CONTRACT)
        self.assertEqual(result["completed_active_wall_s"], 60.)
        self.assertEqual(result["host_outage_excluded_s"], 240.)
        self.assertEqual(result["active_session_started_at_epoch_s"], 400.)

    def test_second_restart_accumulates_active_time_without_double_counting(self):
        prior = {
            "started_at_epoch_s": 100.,
            "active_session_started_at_epoch_s": 400.,
            "completed_active_wall_s": 60.,
            "host_outage_excluded_s": 240.,
            "active_time_sessions": [],
        }
        result = resume_active_time_accounting(
            prior,
            observed_activity_epoch_s=460.,
            resume_epoch_s=500.,
        )
        self.assertEqual(result["completed_active_wall_s"], 120.)
        self.assertEqual(result["host_outage_excluded_s"], 280.)


if __name__ == "__main__":
    unittest.main()
