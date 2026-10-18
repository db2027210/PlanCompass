from __future__ import annotations

import math
import unittest

from formal_recost_estimator import (
    ESTIMATOR_ID,
    EstimatorParameter,
    FrozenEstimatorError,
    FrozenOnlineRecostEstimator,
)


PARAMETERS = (
    EstimatorParameter("a", 0.1, 10.0, "log"),
    EstimatorParameter("b", 0.0, 100.0, "linear"),
)
DEFAULTS = {"a": 1.0, "b": 40.0}


def build() -> FrozenOnlineRecostEstimator:
    return FrozenOnlineRecostEstimator(
        PARAMETERS,
        DEFAULTS,
        method_seed=20260721,
        engine="postgresql",
        case_id="pg_ap_memory",
        scenario="memory",
    )


class FormalRecostEstimatorTests(unittest.TestCase):
    def test_initial_surrogate_is_deterministic_and_non_flat(self) -> None:
        left = build()
        right = build()
        point_a = {"a": 0.5, "b": 30.0}
        point_b = {"a": 2.0, "b": 50.0}
        self.assertEqual(left.evaluate(point_a), right.evaluate(point_a))
        self.assertNotEqual(
            left.evaluate(point_a)["estimated_objective"],
            left.evaluate(point_b)["estimated_objective"],
        )
        self.assertEqual(left.evaluate(point_a)["metadata"]["estimator_id"], ESTIMATOR_ID)

    def test_rejected_outer_observation_refits_without_live_access(self) -> None:
        estimator = build()
        before = estimator.evaluate({"a": 2.0, "b": 50.0})["estimated_objective"]
        estimator.observe_outer(
            params={"a": 2.0, "b": 50.0},
            observed_utility_improvement=-0.2,
            outer_candidate_accepted=False,
        )
        after = estimator.evaluate({"a": 2.0, "b": 50.0})["estimated_objective"]
        self.assertNotEqual(before, after)
        self.assertFalse(estimator.uses_live_database)
        self.assertEqual(estimator.observation_count, 2)

    def test_accepted_outer_observation_resets_incumbent_relative_target(self) -> None:
        estimator = build()
        accepted = {"a": 2.0, "b": 50.0}
        estimator.observe_outer(
            params=accepted,
            observed_utility_improvement=0.1,
            outer_candidate_accepted=True,
        )
        state = estimator.state()
        self.assertEqual(state["outer_observation_count"], 1)
        self.assertEqual(len(state["observations"]), 1)
        self.assertEqual(state["observations"][0]["objective_improvement"], 0.0)

    def test_failure_uses_frozen_penalty(self) -> None:
        estimator = build()
        estimator.observe_outer(
            params={"a": 2.0, "b": 50.0},
            observed_utility_improvement=None,
            outer_candidate_accepted=False,
        )
        self.assertEqual(estimator.state()["observations"][-1]["objective_improvement"], -1.0)

    def test_invalid_order_and_bounds_fail_closed(self) -> None:
        estimator = build()
        with self.assertRaises(FrozenEstimatorError):
            estimator.evaluate({"b": 40.0, "a": 1.0})
        with self.assertRaises(FrozenEstimatorError):
            estimator.evaluate({"a": 100.0, "b": 40.0})

    def test_all_reported_values_are_finite(self) -> None:
        estimator = build()
        value = estimator.evaluate({"a": 1.5, "b": 60.0})
        self.assertTrue(math.isfinite(float(value["estimated_objective"])))
        self.assertTrue(
            all(math.isfinite(float(item)) for item in estimator.state()["coefficients"])
        )


if __name__ == "__main__":
    unittest.main()
