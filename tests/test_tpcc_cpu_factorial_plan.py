from __future__ import annotations

import unittest

from tpcc_cpu_factorial_plan import build_plan, load_factorial


class TpccCpuFactorialPlanTest(unittest.TestCase):
    def test_is_complete_two_level_factorial_for_exact_owned_target(self):
        cfg = load_factorial()
        plan = build_plan(cfg)
        self.assertEqual(plan["mode"], "plan_only_no_sql_or_workload_executed")
        self.assertEqual(plan["run_count"], 8)
        self.assertEqual(plan["target"]["container"], "ra-mysql-grid-tpcc-cpu")
        self.assertEqual(plan["target"]["port"], 13308)
        combos = {tuple(sorted(point["values"].items())) for point in plan["points"]}
        self.assertEqual(len(combos), 8)

    def test_every_point_has_restore_and_only_reviewed_variables(self):
        cfg = load_factorial()
        plan = build_plan(cfg)
        allowed = set(cfg["factors"])
        for point in plan["points"]:
            self.assertEqual(set(point["values"]), allowed)
            self.assertIn("restore", point["finally"])
            self.assertEqual(len(point["mutation_template"]), 3)

    def test_plan_is_seed_deterministic(self):
        cfg = load_factorial()
        self.assertEqual(build_plan(cfg, 7), build_plan(cfg, 7))


if __name__ == "__main__":
    unittest.main()
