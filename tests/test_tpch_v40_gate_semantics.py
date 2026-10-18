from __future__ import annotations

import copy
import unittest

from tools import analyze_top5_validation_gate as gate
from tests.test_tpch_v38_bounded_retest import candidate


def selection() -> dict:
    top5 = []
    for index in range(1, 6):
        item = candidate(index)
        item["selection_role"] = "trusted" if index <= 3 else "probe"
        item["candidate_score"] = 4.4 - index * 0.1
        top5.append(item)
    return {"top5": top5}


def observations() -> list[dict]:
    values = []
    for rank in range(1, 6):
        values.append(
            {
                "rank": rank,
                "technical_failure": False,
                "valid_replay": True,
                "actual_score": 4.4 - rank * 0.1,
                "actual_resource": {
                    "cpu": 0.4,
                    "io": 16.0,
                    "memory": 1.3,
                },
            }
        )
    return values


class TpchV40GateSemanticsTests(unittest.TestCase):
    def test_clean_trusted_order_passes(self) -> None:
        result = gate.analyze(selection(), observations())
        self.assertTrue(result["passed"])
        self.assertGreater(result["trusted_only_spearman"], 0.0)

    def test_probe_resource_error_is_excluded_from_trusted_resource_gate(self) -> None:
        values = observations()
        values[3]["actual_resource"] = {
            "cpu": 4000.0,
            "io": 160000.0,
            "memory": 13000.0,
        }
        result = gate.analyze(selection(), values)
        self.assertTrue(
            result["checks"][
                "trusted_resource_predictions_no_order_of_magnitude_error"
            ]
        )
        self.assertTrue(result["passed"])

    def test_probe_actual_best_outside_predicted_top3_fails(self) -> None:
        values = observations()
        values[3]["actual_score"] = 4.9
        result = gate.analyze(selection(), values)
        self.assertFalse(result["checks"]["actual_best_in_predicted_top3"])
        self.assertFalse(result["passed"])

    def test_probe_scores_do_not_enter_trusted_spearman(self) -> None:
        values = observations()
        values[3]["actual_score"] = -100.0
        values[4]["actual_score"] = 100.0
        result = gate.analyze(selection(), values)
        self.assertGreater(result["trusted_only_spearman"], 0.0)

    def test_inverted_trusted_order_and_technical_failure_fail_closed(self) -> None:
        inverted = observations()
        inverted[0]["actual_score"] = 4.0
        inverted[2]["actual_score"] = 4.3
        result = gate.analyze(selection(), inverted)
        self.assertLess(result["trusted_only_spearman"], 0.0)
        self.assertFalse(result["passed"])

        failed = copy.deepcopy(observations())
        failed[4]["technical_failure"] = True
        failed[4]["valid_replay"] = False
        result = gate.analyze(selection(), failed)
        self.assertFalse(result["checks"]["top5_no_technical_failure"])
        self.assertFalse(result["passed"])


if __name__ == "__main__":
    unittest.main()
