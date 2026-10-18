from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from tools import build_tpcc_unique_exact_validation_v6 as builder
from tools import tpcc_autonomous_target10_controller_v25 as v25
from tools import tpcc_autonomous_target10_controller_v36_unique_exact as v36


PROJECT = Path("/home/anonymous/program/resource_adaptive")
SOURCE_SELECTION = PROJECT / (
    "history/tpcc_final_algorithm_fresh0_v34_20260803/"
    "autonomous_controller/prepare_05/sealed_run/top5.json"
)
SOURCE_LEDGER = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/"
    "formal10_final_algorithm_fresh0_v34_20260803/autonomous_runs_v34/"
    "autonomous_diagnostic_05/strict_top5_real_replay_round12_ledger_v1.json"
)
FAILED_SELECTION = PROJECT / (
    "history/tpcc_final_algorithm_fresh0_v34_20260803/"
    "autonomous_controller/prepare_06/top5.json"
)


class TpccV36UniqueExactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.selection = builder.build(
            builder._load(SOURCE_SELECTION),
            source_selection_path=SOURCE_SELECTION,
            source_ledger_path=SOURCE_LEDGER,
        )

    def test_reproduces_byte_identical_unique_top5(self) -> None:
        second = builder.build(
            builder._load(SOURCE_SELECTION),
            source_selection_path=SOURCE_SELECTION,
            source_ledger_path=SOURCE_LEDGER,
        )
        self.assertEqual(builder._canonical(self.selection), builder._canonical(second))
        ids = [item["three_level_fingerprint"]["id"] for item in self.selection["top5"]]
        self.assertEqual(len(set(ids)), 5)
        for item in self.selection["top5"]:
            fingerprint = item["three_level_fingerprint"]
            self.assertEqual(
                fingerprint["feature_ratio_level"],
                builder._quantize(item["feature_ratio"]),
            )
            self.assertEqual(
                fingerprint["predicted_resource_level"],
                builder._quantize(item["utilization"]),
            )
        v25.validate_selection(self.selection, mode="diagnostic_validation")

    def test_trusted_uses_exact_clean_ledger_evidence_only(self) -> None:
        candidates = self.selection["top5"]
        self.assertEqual(
            [item["selection_role"] for item in candidates],
            ["trusted", "trusted", "trusted", "uncertain_probe", "uncertain_probe"],
        )
        self.assertEqual([item["exact_evidence_count"] for item in candidates], [5, 5, 5, 1, 1])
        for item in candidates[:3]:
            self.assertGreaterEqual(item["exact_evidence_count"], 2)
            self.assertTrue(item["confidence"]["exact_clean_ledger_evidence_only"])
            self.assertEqual(
                item["exact_evidence_count"], len(item["independent_actual_scores"])
            )
        for item in candidates[3:]:
            self.assertFalse(item["trusted"])

    def test_old_postcalibration_collapse_remains_fail_closed(self) -> None:
        failed = v25.load_json(FAILED_SELECTION)
        ids = [item["three_level_fingerprint"]["id"] for item in failed["top5"]]
        self.assertLess(len(set(ids)), 5)
        with self.assertRaisesRegex(RuntimeError, "fingerprints are not unique"):
            v25.validate_selection(failed, mode="diagnostic_validation")

    def test_prelaunch_rebuild_is_zero_call_and_tamper_fails(self) -> None:
        admission = v36.unique_exact_prelaunch_gate_v36(
            self.selection, "diagnostic_validation", {"audits": [], "sources": []}
        )
        self.assertTrue(admission["passed"])
        self.assertTrue(admission["deterministic_rebuild_verified"])
        self.assertEqual(admission["workload_calls_added"], 0)

        changed = copy.deepcopy(self.selection)
        changed["top5"][0]["exact_evidence_count"] = 1
        with self.assertRaisesRegex(RuntimeError, "deterministic evidence rebuild"):
            v36.unique_exact_prelaunch_gate_v36(
                changed, "diagnostic_validation", {"audits": [], "sources": []}
            )

    def test_cli_writes_byte_identical_files(self) -> None:
        base = builder.repeat_v2.import_base()
        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "first.json"
            second = Path(temporary) / "second.json"
            base.atomic_json(first, self.selection)
            base.atomic_json(second, self.selection)
            self.assertEqual(first.read_bytes(), second.read_bytes())


if __name__ == "__main__":
    unittest.main()
