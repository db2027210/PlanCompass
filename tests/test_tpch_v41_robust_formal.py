from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from tools import run_tpch_formal_exact_plan_regime_batch_v41_robust4 as robust
from tests.test_tpch_v38_bounded_retest import candidate


def formal_selection() -> dict:
    top5 = []
    for index in range(1, 6):
        item = candidate(index)
        trusted = index <= 3
        item["selection_role"] = "trusted" if trusted else "probe"
        item["exact_plan_regime_validated"] = trusted
        item["confidence"] = {"measurement_count": 3}
        item["three_observation_stability"] = {
            "score_values": [4.0, 4.01, 4.02],
            "passed": trusted,
        }
        fingerprint = item["three_level_fingerprint"]
        fingerprint["resource_formula_audit"] = {
            "cpu": {"source_actual_resources": [1.0, 3.0, 5.0]},
            "io": {"source_actual_resources": [10.0, 20.0, 30.0]},
            "memory": {"source_actual_resources": [0.9, 1.0, 1.1]},
        }
        top5.append(item)
    return {
        "selection_contract": (
            "exact_structural_plan_regime_trusted3_unknown_probe2_top5_v2"
        ),
        "top5": top5,
    }


class TpchV41RobustFormalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_apply = robust.original.apply_params
        self.original_classify = robust.original.classify_payload
        self.original_atomic = robust.original.atomic_json

    def tearDown(self) -> None:
        robust.original.apply_params = self.original_apply
        robust.original.classify_payload = self.original_classify
        robust.original.atomic_json = self.original_atomic

    def test_noisy_fourth_observation_is_aggregated_before_scoring(self) -> None:
        robust.original.apply_params = lambda *_args: "ok"
        robust.original.classify_payload = lambda *_args: (
            {"cpu": 100.0, "io": 40.0, "memory": 1.2},
            {"cpu": 10.0, "io": 0.8, "memory": 0.6},
            99.0,
        )
        selection = formal_selection()
        audits, _ = robust.install_robust_measurement(selection)
        first = selection["top5"][0]
        robust.original.apply_params("container", "database", first["candidate_params"])
        resource, utilization, score = robust.original.classify_payload(
            {}, 0, {"cpu": 10.0, "io": 50.0, "memory": 2.0}
        )
        self.assertEqual(resource, {"cpu": 4.0, "io": 25.0, "memory": 1.05})
        expected, expected_utilization = robust.original.score_resource(
            resource, {"cpu": 10.0, "io": 50.0, "memory": 2.0}
        )
        self.assertAlmostEqual(score, expected)
        self.assertEqual(utilization, expected_utilization)
        self.assertNotEqual(score, 99.0)
        self.assertEqual(audits[1]["raw_formal_score"], 99.0)
        self.assertEqual(audits[1]["observation_count"], 4)

    def test_atomic_outputs_persist_raw_and_aggregate_audit(self) -> None:
        robust.original.apply_params = lambda *_args: "ok"
        robust.original.classify_payload = lambda *_args: (
            {"cpu": 7.0, "io": 40.0, "memory": 1.2},
            {"cpu": 0.7, "io": 0.8, "memory": 0.6},
            4.9,
        )
        selection = formal_selection()
        robust.install_robust_measurement(selection)
        first = selection["top5"][0]
        robust.original.apply_params("container", "database", first["candidate_params"])
        resource, utilization, score = robust.original.classify_payload(
            {}, 0, {"cpu": 10.0, "io": 50.0, "memory": 2.0}
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rank_01/attempt_01/result.json"
            robust.original.atomic_json(
                path,
                {
                    "rank": 1,
                    "valid_replay": True,
                    "actual_resource": resource,
                    "actual_utilization": utilization,
                    "actual_score": score,
                },
            )
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["raw_formal_score"], 4.9)
        self.assertEqual(
            payload["formal_four_observation_aggregation"]["contract"],
            robust.AGGREGATION_CONTRACT,
        )

    def test_trusted_candidate_requires_sealed_three_observation_stability(self) -> None:
        selection = formal_selection()
        selection["top5"][0]["three_observation_stability"]["passed"] = False
        with self.assertRaisesRegex(RuntimeError, "sealed three-observation"):
            robust.install_robust_measurement(selection)

    def test_missing_resource_history_fails_closed(self) -> None:
        selection = formal_selection()
        del selection["top5"][0]["three_level_fingerprint"][
            "resource_formula_audit"
        ]["cpu"]
        with self.assertRaisesRegex(RuntimeError, "three positive cpu"):
            robust.install_robust_measurement(selection)


if __name__ == "__main__":
    unittest.main()
