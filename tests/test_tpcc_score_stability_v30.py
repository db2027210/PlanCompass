from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from tools import tpcc_autonomous_target10_controller_v30_score_stability as v30


REAL_ROOT = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/"
    "formal10_final_algorithm_fresh10_v29_20260731T2005/"
    "autonomous_runs_v32/autonomous_diagnostic_validation_153"
)
REAL_SELECTION = Path(
    "history/tpcc_final_algorithm_fresh10_v29_20260731T2005/"
    "autonomous_controller_v32/prepare_153/sealed_run/top5.json"
)


class TpccScoreStabilityV30Test(unittest.TestCase):
    def _real_payloads(self):
        ledger_path = REAL_ROOT / "strict_top5_real_replay_round14_ledger_v1.json"
        if not REAL_SELECTION.is_file() or not ledger_path.is_file():
            self.skipTest("real validation153 evidence is unavailable")
        selection = json.loads(REAL_SELECTION.read_text(encoding="utf-8"))
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        if len(ledger.get("successful") or []) != 5:
            self.skipTest("real validation153 has not completed clean5 yet")
        return selection, ledger

    def _base_audit(self, selection, ledger):
        return {
            "mode": "diagnostic_validation",
            "root": str(REAL_ROOT),
            "clean": True,
            "physical_calls": 5,
            "valid_replays": 5,
            "technical_failures": 0,
            "baseline_restored": True,
            "trusted_count": 3,
            "trusted_pairs": [
                (float(item["predicted_score"]), float(result["actual_score"]))
                for item, result in zip(selection["top5"][:3], ledger["successful"][:3])
            ],
            "trusted_spearman": -1.0,
            "actual_best_predicted_rank": 3,
            "top3_hit": True,
            "trusted_resource_no_order_of_magnitude_error": True,
            "score_span_gate_passed": True,
            "minimum_total_exact_observation_count": 2,
            "maximum_total_exact_observation_count": 2,
            "calibration_rows": [
                {
                    "plan": result["workload_plan_fingerprint"],
                    "rank": int(result["rank"]),
                    "resource_scalar": {"cpu": 1.0, "io": 1.0, "memory": 1.0},
                    "actual_resource": copy.deepcopy(result["actual_resource"]),
                    "actual_score": float(result["actual_score"]),
                }
                for result in ledger["successful"]
            ],
            "fingerprint_ids": [
                item["three_level_fingerprint"]["id"] for item in selection["top5"]
            ],
        }

    def test_real_validation153_high_io_cv_is_valid_probe_only(self):
        selection, ledger = self._real_payloads()
        audit = v30.apply_score_stability_audit(
            self._base_audit(selection, ledger),
            selection,
            ledger,
        )

        self.assertEqual(audit["physical_calls"], 5)
        self.assertEqual(audit["valid_replays"], 5)
        self.assertEqual(audit["technical_failures"], 0)
        self.assertEqual(audit["stable_trusted_count"], 0)
        self.assertFalse(audit["all_trusted_score_resources_stable"])
        self.assertFalse(audit["trusted_score_confidence_intervals_separable"])
        self.assertTrue(audit["formal_admission_blocked_by_score_stability"])
        self.assertEqual(audit["calibration_rows"], [])
        self.assertEqual(len(audit["calibration_excluded_rows"]), 5)
        for row in audit["score_stability_assessments"]:
            self.assertTrue(row["valid_replay"])
            self.assertFalse(row["technical_failure"])
            self.assertFalse(row["calibration_eligible"])
            self.assertEqual(row["effective_role"], "uncertain_probe")
            self.assertEqual(
                row["classification"],
                "valid_rejected_probe_only_unstable_score_resource",
            )
            self.assertFalse(row["resource_stability"]["resources"]["io"]["stable"])

    def test_real_validation153_quality_gate_fails_closed(self):
        selection, ledger = self._real_payloads()
        audit = v30.apply_score_stability_audit(
            self._base_audit(selection, ledger),
            selection,
            ledger,
        )
        gate = v30.quality_gate_v30([audit])

        self.assertFalse(gate["passed"])
        self.assertFalse(gate["formal_start_authorized"])
        self.assertFalse(gate["checks"]["trusted_cpu_io_memory_stable"])
        self.assertFalse(
            gate["checks"]["trusted_score_confidence_intervals_separable"]
        )
        self.assertTrue(
            gate["checks"]["unstable_score_resources_excluded_from_calibration"]
        )

    def test_legacy_calibration_rows_are_excluded(self):
        output = v30.build_exact_calibration_v30(
            [
                {
                    "clean": True,
                    "calibration_rows": [
                        {
                            "plan": "legacy-plan",
                            "resource_scalar": {
                                "cpu": 1.0,
                                "io": 1.0,
                                "memory": 1.0,
                            },
                        }
                    ],
                }
            ]
        )

        self.assertEqual(output["regimes"], {})
        self.assertTrue(output["legacy_rows_without_v30_stability_excluded"])


if __name__ == "__main__":
    unittest.main()
