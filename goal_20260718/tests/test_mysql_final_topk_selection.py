from types import SimpleNamespace
import unittest

from framework.double_loop_scenario import FreshBaselineObjective, ScenarioMeasurement
from mysql_double_loop_grid_runner import select_strict_final_topk


def validation(value: float, qps: float, memory: float, score: float):
    resource = {"db_qps": qps, "cpu": .5, "memory": memory, "io": .1}
    return SimpleNamespace(
        params={"candidate": value}, resource=resource, raw_result={"resource": resource},
        score=score, failure_reason="",
    )


class StrictFinalTopKSelectionTest(unittest.TestCase):
    def test_prefers_maximin_constraint_margin_among_strict_real_replays(self):
        objective = FreshBaselineObjective(
            "memory", ScenarioMeasurement(100.0, .5, 10.0, .1),
            resource_limits={"cpu": 1.0, "memory": 10.526315789, "io": 1.0},
            target_sweet_upper=9.4,
        )
        result = SimpleNamespace(rounds=[SimpleNamespace(validated_points=[
            validation(1.0, 114.0, 9.2, .99),
            validation(2.0, 120.0, 9.2, .80),
            validation(3.0, 125.0, 9.1, .90),
        ])])
        params, audit = select_strict_final_topk(result, objective)
        self.assertEqual(params, {"candidate": 2.0})
        self.assertEqual(audit["strict_eligible"], 2)
        self.assertTrue(audit["selected"])

    def test_returns_none_when_no_real_topk_point_passes_strict_gates(self):
        objective = FreshBaselineObjective(
            "memory", ScenarioMeasurement(100.0, .5, 10.0, .1),
            resource_limits={"cpu": 1.0, "memory": 10.526315789, "io": 1.0},
            target_sweet_upper=9.4,
        )
        result = SimpleNamespace(rounds=[SimpleNamespace(validated_points=[
            validation(1.0, 114.0, 9.2, .99),
        ])])
        params, audit = select_strict_final_topk(result, objective)
        self.assertIsNone(params)
        self.assertEqual(audit["strict_eligible"], 0)
        self.assertFalse(audit["selected"])


if __name__ == "__main__":
    unittest.main()
