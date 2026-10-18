from __future__ import annotations

import copy
import unittest

from tools import build_tpch_autonomous_successor_top5_v6_hierarchical_probe as builder


def candidate(label: str, expected: float, acquisition: float) -> dict:
    value = 0.01 if label == "a" else 0.011
    return {
        "candidate_params": {
            "cpu_index_tuple_cost": value,
            "cpu_operator_cost": 0.0025,
            "cpu_tuple_cost": 0.01,
            "parallel_setup_cost": 1000,
            "parallel_tuple_cost": 0.1,
            "random_page_cost": 4,
            "seq_page_cost": 1,
        },
        "candidate_score": expected,
        "probe_acquisition_score": acquisition,
        "predicted_resource": {"cpu": 1.0, "io": 16.0, "memory": 1.2},
        "boundary_corner": False,
        "boundary_plateau": False,
        "actual_parameter_noncorner_audit": {
            "reason": "actual_parameter_space_noncorner"
        },
        "three_level_fingerprint": {
            "id": f"three-level-sha256:{label * 64}",
            "plan_level": f"ratio-plan-sha256:{label * 64}",
            "feature_ratio_level": {"cpu": 1, "io": 2, "memory": 3},
        },
        "final_estimation_metadata": {
            "query_features": [
                {
                    "queryid": "1",
                    "plan_fingerprint": f"exact-structure-sha256:{label * 64}",
                }
            ]
        },
    }


class HierarchicalProbeV6Tests(unittest.TestCase):
    def test_prior_and_probe_calibration_are_deterministic(self) -> None:
        registry = {
            "r1": {
                "evidence": [
                    {
                        "source_summary_sha256": "1" * 64,
                        "source_rank": 1,
                        "source_fingerprint": "f1",
                        "scalar_by_resource": {
                            "cpu": 0.02,
                            "io": 1.0,
                            "memory": 0.8,
                        },
                    }
                ]
            },
            "r2": {
                "evidence": [
                    {
                        "source_summary_sha256": "2" * 64,
                        "source_rank": 2,
                        "source_fingerprint": "f2",
                        "scalar_by_resource": {
                            "cpu": 0.04,
                            "io": 1.0,
                            "memory": 0.9,
                        },
                    }
                ]
            },
        }
        prior = builder.hierarchical_probe_prior(registry)
        raw = candidate("a", 4.9, 4.9)
        raw["predicted_resource"] = {"cpu": 10.5, "io": 16.0, "memory": 1.4}
        raw["confidence"] = {
            "exact_plan_regime": 1,
            "measurement_count": 3,
            "score_median": 4.9,
        }
        first = builder.hierarchical_calibrate_probe(raw, prior)
        second = builder.hierarchical_calibrate_probe(copy.deepcopy(raw), prior)
        self.assertEqual(first, second)
        self.assertAlmostEqual(first["predicted_resource"]["cpu"], 0.315)
        self.assertAlmostEqual(first["predicted_resource"]["io"], 16.0)
        self.assertAlmostEqual(first["predicted_resource"]["memory"], 1.19)
        self.assertEqual(first["confidence"]["exact_plan_regime"], 0.0)
        self.assertEqual(first["confidence"]["measurement_count"], 0)
        self.assertTrue(first["confidence"]["unknown_regime"])
        self.assertGreaterEqual(
            first["probe_acquisition_score"], first["candidate_score"]
        )
        self.assertEqual(
            first["three_level_fingerprint"]["post_prediction_calibration"],
            "unknown_regime_hierarchical_probe_prior_v1",
        )

    def test_score_ladder_uses_ucb_only_for_probe_acquisition(self) -> None:
        optimistic_probe = candidate("a", expected=4.0, acquisition=4.9)
        higher_mean = candidate("b", expected=4.5, acquisition=4.6)
        selected, audit = builder.score_ladder_probe_take(
            [higher_mean, optimistic_probe],
            1,
            4.8,
        )
        self.assertEqual(selected[0]["candidate_score"], 4.0)
        self.assertEqual(selected[0]["probe_acquisition_score"], 4.9)
        self.assertEqual(audit["target_bindings"][0]["selected_score"], 4.0)
        self.assertEqual(
            audit["target_bindings"][0]["selected_acquisition_score"], 4.9
        )

    def test_duplicate_evidence_is_counted_once(self) -> None:
        evidence = {
            "source_summary_sha256": "1" * 64,
            "source_rank": 1,
            "source_fingerprint": "f1",
            "scalar_by_resource": {"cpu": 0.1, "io": 1.0, "memory": 1.0},
        }
        prior = builder.hierarchical_probe_prior(
            {"r1": {"evidence": [evidence]}, "r2": {"evidence": [evidence]}}
        )
        self.assertEqual(prior["evidence_count"], 1)


if __name__ == "__main__":
    unittest.main()
