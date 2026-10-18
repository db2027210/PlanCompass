from __future__ import annotations

import math
import unittest

from scripts import launch_mysql_four_scene_algorithm_b_independent_v1 as launcher
from scripts import run_mysql_four_scene_algorithm_b_independent_v1 as algorithm_b


class IndependentAlgorithmBContractTest(unittest.TestCase):
    def test_frozen_source_contract(self) -> None:
        contract = launcher.validate_contract()
        self.assertEqual(contract["status"], "FROZEN_RUNTIME_VALIDATED")
        self.assertEqual(
            contract["independence"]["algorithm_c_state_or_result_inputs"], []
        )
        self.assertEqual(contract["algorithm"]["real_validation_top_k"], 5)
        self.assertEqual(contract["algorithm"]["accepted_binary_shrinks_required"], 10)

    def test_fresh_state_and_top15_design(self) -> None:
        state = algorithm_b.state_document("algorithm_b_offline_test")
        self.assertTrue(all(value == 0 for value in state["accepted_rounds"].values()))
        self.assertTrue(
            all(value == 0 for value in state["rejected_outer_attempts"].values())
        )
        self.assertFalse(state["independence_contract"]["algorithm_c_checkpoint_read"])
        params, ranges, active = algorithm_b.parameter_state()
        for scene in algorithm_b.SCENES:
            design = algorithm_b.mysql_fresh_model_calibration_points(
                params[scene],
                algorithm_b.transformed_ranges(ranges[scene], active[scene]),
                active[scene],
                replay_budget=15,
            )
            self.assertEqual(len(design), 15)
            self.assertEqual(
                len(
                    {
                        algorithm_b.alg.optimizer_params_key(row["params"])
                        for row in design
                    }
                ),
                15,
            )

    def test_ten_binary_shrinks_reach_one_percent_interval(self) -> None:
        params, ranges, active = algorithm_b.parameter_state()
        for scene in algorithm_b.SCENES:
            current = dict(params[scene])
            current_ranges = ranges[scene]
            for _ in range(10):
                current.update(
                    {
                        name: math.sqrt(
                            float(current_ranges[name]["lower"])
                            * float(current_ranges[name]["upper"])
                        )
                        for name in active[scene]
                    }
                )
                current_ranges = algorithm_b.alg.shrink_ranges(
                    current_ranges, current, active[scene]
                )
            self.assertTrue(
                all(
                    float(current_ranges[name]["upper"])
                    / float(current_ranges[name]["lower"])
                    <= 1.01
                    for name in active[scene]
                )
            )


if __name__ == "__main__":
    unittest.main()
