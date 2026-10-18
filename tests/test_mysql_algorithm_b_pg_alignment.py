from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MYSQL_ROOT = Path("/home/anonymous/program/resource_adaptive_mysql")
PG_ROOT = Path("/home/anonymous/program/resource_adaptive")
for path in (MYSQL_ROOT, PG_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import mysql_double_loop_grid_runner as runner
from double_loop_scenario import FreshBaselineObjective, ScenarioMeasurement
from double_loop_search_common import ParameterRange
from mysql_algorithm_b_policy import mysql_fresh_model_calibration_points


class MySQLAlgorithmBAlignmentTest(unittest.TestCase):
    def parse_minimal(self, output_dir: str, *extra: str):
        argv = [
            "mysql_double_loop_grid_runner.py",
            "--exp-id",
            "alignment-test",
            "--scenario",
            "cpu",
            "--benchmark",
            "tpcc",
            "--port",
            "3306",
            "--database",
            "tpcc",
            "--mysqld-pid",
            "1",
            "--output-dir",
            output_dir,
            *extra,
        ]
        with mock.patch.object(sys, "argv", argv):
            return runner.parse_args()

    def test_defaults_match_algorithm_b_except_fixed_global_replay_cap(self):
        with tempfile.TemporaryDirectory() as output_dir:
            args = self.parse_minimal(output_dir)

        self.assertEqual((args.grid_points, args.inner_passes), (15, 20))
        self.assertEqual(args.convergence_patience, 2)
        self.assertEqual(args.combination_max_size, 4)
        self.assertEqual(args.combination_values_per_param, 3)
        self.assertEqual(args.combination_max_candidates, 0)
        self.assertEqual(args.outer_rounds, 0)
        self.assertEqual(args.adaptive_topk_min_k, 5)
        self.assertEqual(args.adaptive_topk_batch_size, 1)
        self.assertAlmostEqual(args.adaptive_topk_inversion_threshold, 0.10)
        self.assertEqual(args.real_top_k, 5)

    def test_topk_above_global_hard_cap_fails_before_mysql_access(self):
        with tempfile.TemporaryDirectory() as output_dir:
            args = self.parse_minimal(output_dir, "--real-top-k", "8")
            with self.assertRaisesRegex(ValueError, "global hard cap 5"):
                runner.run(args)

    def test_calibration_is_one_fixed_top15_effective_design(self):
        baseline = {"cost": 10.0, "boolean": 1.0, "small_integer": 1.0}
        ranges = {
            "cost": ParameterRange(1.0, 10.0, discrete_count=11, transform="log"),
            "boolean": ParameterRange(0.0, 1.0, values=(0.0, 1.0)),
            "small_integer": ParameterRange(
                1.0, 2.0, values=(1.0, 2.0), transform="log"
            ),
        }

        rows = mysql_fresh_model_calibration_points(
            baseline, ranges, list(baseline)
        )

        self.assertEqual(len(rows), 15)
        self.assertEqual(
            len({tuple(sorted(row["params"].items())) for row in rows}), 15
        )
        self.assertTrue(all(row["params"] != baseline for row in rows))
        self.assertTrue(
            any(row["phase"] == "effective_interaction_fill" for row in rows)
        )

    def test_qps_cannot_change_inner_score(self):
        baseline = ScenarioMeasurement(100.0, 0.98, 0.7, 0.6)
        objective = FreshBaselineObjective(
            "cpu",
            baseline,
            resource_limits={"cpu": 1.0, "memory": 1.0, "io": 1.0},
            target_sweet_upper=0.95,
        )

        contract = runner.assert_qps_guidance_isolation(objective, baseline)

        self.assertTrue(contract["passed"])
        self.assertEqual(contract["qps_role"], "outer_acceptance_only")
        self.assertEqual(len(set(contract["probe_scores"])), 1)

if __name__ == "__main__":
    unittest.main()
