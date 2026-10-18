from __future__ import annotations

import copy
import unittest
from pathlib import Path
from unittest import mock

from tools import build_tpcc_exact_plan_repeat_validation_v3 as repeat_v3
from tools import build_tpcc_exact_plan_repeat_validation_v5 as repeat_v5
from tools import tpcc_autonomous_target10_controller_v25 as v25
from tools import tpcc_autonomous_target10_controller_v34_heterogeneous_repeat as v34


PROJECT = Path("/home/anonymous/program/resource_adaptive")
V33_ROOT = PROJECT / "history/tpcc_final_algorithm_fresh0_v33_20260803"
V33_STATE = V33_ROOT / "autonomous_controller/controller_state.json"


class TpccV34HeterogeneousRepeatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        persisted = v25.load_json(V33_STATE)
        cls.audit = copy.deepcopy(persisted["audits"][-1])
        cls.audit["exact_top5_group_identity_sha256"] = v34._audit_identity(
            cls.audit
        )
        cls.audit["complete_same_group_batch_count"] = 1
        cls.audit["group_origin_novel_fingerprint_gate_passed"] = True
        cls.source_path = Path(cls.audit["selection_path"])
        cls.ledger_path = Path(cls.audit["ledger_path"])
        cls.source = v25.load_json(cls.source_path)
        provisional = repeat_v3.build(
            cls.source,
            v25.load_json(cls.ledger_path),
            0.0,
        )
        cls.repeat = repeat_v5.add_repeat_contract(
            provisional,
            cls.source,
            source_selection_path=cls.source_path,
            source_selection_sha256=v25.sha256(cls.source_path),
            source_ledger_path=cls.ledger_path,
            source_ledger_sha256=v25.sha256(cls.ledger_path),
        )
        cls.state = {
            "audits": [copy.deepcopy(cls.audit)],
            "sources": [],
            "quarantined_fingerprints": [],
            "next_ordinal": 2,
        }

    def test_builder_accepts_observation_vector_2_1_2_2_2(self) -> None:
        contract = self.repeat["validation_repeat_contract"]
        counts = [
            item["total_exact_observation_count"]
            for item in contract["per_point_total_exact_observation_counts"]
        ]
        self.assertEqual(sorted(counts), [1, 2, 2, 2, 2])
        self.assertEqual(contract["minimum_total_exact_observation_count"], 1)
        self.assertEqual(contract["maximum_total_exact_observation_count"], 2)
        self.assertEqual(contract["prior_complete_same_group_batch_count"], 1)
        self.assertEqual(contract["independent_same_group_batch_ordinal"], 2)

    def test_prelaunch_uses_same_group_count_not_max_point_count(self) -> None:
        admission = v34.validation_repeat_prelaunch_fingerprint_gate_v34(
            self.repeat,
            "diagnostic_validation",
            self.state,
        )
        self.assertTrue(admission["passed"])
        self.assertEqual(admission["independent_same_group_batch_ordinal"], 2)
        self.assertEqual(
            sorted(admission["per_point_total_exact_observation_counts"]),
            [1, 2, 2, 2, 2],
        )

    def test_repeat_route_ignores_large_total_point_observation_count(self) -> None:
        latest = {
            "mode": "diagnostic_validation",
            "clean": True,
            "complete_same_group_batch_count": 1,
            "minimum_total_exact_observation_count": 2,
            "maximum_total_exact_observation_count": 99,
            "all_trusted_complete_run_repeatable": False,
        }
        gate = {"passed": False}
        self.assertEqual(
            v34.choose_next_mode_v34([latest], gate),
            "diagnostic_validation",
        )
        self.assertEqual(gate["repeat_cap_basis"], "complete_same_group_batch_count")

        latest["complete_same_group_batch_count"] = 5
        self.assertNotEqual(
            v34.choose_next_mode_v34([latest], {"passed": False}),
            "diagnostic_validation",
        )

    def test_quality_gate_requires_each_point_three_and_inherits_origin_novelty(self) -> None:
        latest = {
            "mode": "diagnostic_validation",
            "fingerprint_ids": [f"fp-{index}" for index in range(5)],
            "group_origin_novel_fingerprint_gate_passed": True,
            "minimum_total_exact_observation_count": 2,
            "complete_same_group_batch_count": 2,
        }
        base = {
            "passed": False,
            "checks": {
                "five_novel_noncorner_fingerprints": False,
                "at_least_three_exact_observations_per_candidate": False,
            },
        }
        with mock.patch.object(v34.v31, "quality_gate_v31", return_value=base):
            gate = v34.quality_gate_v34([latest])
        self.assertTrue(gate["checks"]["five_novel_noncorner_fingerprints"])
        self.assertFalse(
            gate["checks"]["at_least_three_exact_observations_per_candidate"]
        )
        latest["minimum_total_exact_observation_count"] = 3
        with mock.patch.object(v34.v31, "quality_gate_v31", return_value=base):
            gate = v34.quality_gate_v34([latest])
        self.assertTrue(
            gate["checks"]["at_least_three_exact_observations_per_candidate"]
        )

    def test_changed_group_or_count_vector_fails_closed(self) -> None:
        changed = copy.deepcopy(self.repeat)
        changed["validation_repeat_contract"][
            "prior_complete_same_group_batch_count"
        ] = 2
        with self.assertRaisesRegex(RuntimeError, "ordinal/provenance"):
            v34.validation_repeat_prelaunch_fingerprint_gate_v34(
                changed,
                "diagnostic_validation",
                self.state,
            )

        changed = copy.deepcopy(self.repeat)
        changed["validation_repeat_contract"][
            "per_point_total_exact_observation_counts"
        ][0]["total_exact_observation_count"] += 1
        with self.assertRaisesRegex(RuntimeError, "ordinal/provenance"):
            v34.validation_repeat_prelaunch_fingerprint_gate_v34(
                changed,
                "diagnostic_validation",
                self.state,
            )


if __name__ == "__main__":
    unittest.main()
