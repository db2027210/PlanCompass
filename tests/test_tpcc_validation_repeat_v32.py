from __future__ import annotations

import copy
import unittest
from pathlib import Path

from tools import build_tpcc_exact_plan_repeat_validation_v3 as repeat_v3
from tools import build_tpcc_exact_plan_repeat_validation_v4 as repeat_v4
from tools import tpcc_autonomous_target10_controller_v25 as v25
from tools import tpcc_autonomous_target10_controller_v31_cross_run_stability as v31
from tools import tpcc_autonomous_target10_controller_v32_validation_repeat as v32


PROJECT_ROOT = Path("/home/anonymous/program/resource_adaptive")
STATE_PATH = (
    PROJECT_ROOT
    / "history/tpcc_final_algorithm_fresh0_v31_20260803/"
    "autonomous_controller/controller_state.json"
)


class ValidationRepeatFingerprintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        observed_state = v25.load_json(STATE_PATH)
        cls.audit = next(
            copy.deepcopy(item)
            for item in observed_state["audits"]
            if str(item.get("root") or "").endswith(
                "autonomous_diagnostic_validation_01"
            )
        )
        cls.state = {
            "audits": [cls.audit],
            "sources": copy.deepcopy(observed_state.get("sources") or []),
            "quarantined_fingerprints": [],
            "next_ordinal": 2,
        }
        source_path = Path(cls.audit["selection_path"])
        ledger_path = Path(cls.audit["ledger_path"])
        cls.source = v25.load_json(source_path)
        output = repeat_v3.build(
            cls.source,
            v25.load_json(ledger_path),
            0.0,
        )
        cls.repeat = repeat_v4.add_repeat_contract(
            output,
            cls.source,
            source_selection_path=source_path,
            source_selection_sha256=v25.sha256(source_path),
            source_ledger_path=ledger_path,
            source_ledger_sha256=v25.sha256(ledger_path),
        )

    def test_v31_failure_continues_exact_repeat_without_diagnostic_reroute(self) -> None:
        gate = {"passed": False}
        mode = v31.choose_next_mode_v31([copy.deepcopy(self.audit)], gate)
        self.assertEqual(mode, "diagnostic_validation")
        state_before = copy.deepcopy(self.state)
        admission = v32.validation_repeat_prelaunch_fingerprint_gate(
            self.repeat,
            mode,
            self.state,
        )
        self.assertEqual(self.state, state_before)
        self.assertTrue(admission["passed"])
        self.assertEqual(admission["collision_ids"], [])
        self.assertGreaterEqual(
            len(admission["allowed_exact_repeat_collision_ids"]),
            1,
        )
        self.assertEqual(admission["novel_fingerprint_credit"], 0)
        self.assertEqual(admission["independent_run_ordinal"], 4)

    def test_new_and_formal_candidates_still_use_global_novelty(self) -> None:
        for mode in ("diagnostic", "formal"):
            admission = v32.validation_repeat_prelaunch_fingerprint_gate(
                self.source,
                mode,
                self.state,
            )
            self.assertFalse(admission["passed"])
            self.assertTrue(admission["collision_ids"])

    def test_changed_parameter_or_plan_fails_closed(self) -> None:
        changed = copy.deepcopy(self.repeat)
        changed["top5"][0]["params"]["seq_page_cost"] += 0.01
        with self.assertRaisesRegex(RuntimeError, "parameters or plan"):
            v32.validation_repeat_prelaunch_fingerprint_gate(
                changed,
                "diagnostic_validation",
                self.state,
            )

        changed = copy.deepcopy(self.repeat)
        changed["top5"][0]["workload_plan_fingerprint"] = (
            "ratio-plan-sha256:" + "0" * 64
        )
        with self.assertRaisesRegex(RuntimeError, "parameters or plan"):
            v32.validation_repeat_prelaunch_fingerprint_gate(
                changed,
                "diagnostic_validation",
                self.state,
            )

    def test_source_sha_and_repeat_ordinal_fail_closed(self) -> None:
        changed = copy.deepcopy(self.repeat)
        changed["validation_repeat_contract"]["source_selection_sha256"] = (
            "0" * 64
        )
        with self.assertRaisesRegex(RuntimeError, "source_selection_sha256"):
            v32.validation_repeat_prelaunch_fingerprint_gate(
                changed,
                "diagnostic_validation",
                self.state,
            )

        changed = copy.deepcopy(self.repeat)
        changed["validation_repeat_contract"]["independent_run_ordinal"] += 1
        with self.assertRaisesRegex(RuntimeError, "ordinal/provenance"):
            v32.validation_repeat_prelaunch_fingerprint_gate(
                changed,
                "diagnostic_validation",
                self.state,
            )

    def test_repeat_builder_preserves_exact_top5_set(self) -> None:
        source = repeat_v4.exact_top5_identity(self.source)
        repeated = repeat_v4.exact_top5_identity(self.repeat)
        self.assertEqual(source["sha256"], repeated["sha256"])
        self.assertEqual(
            self.repeat["validation_repeat_contract"][
                "source_selection_sha256"
            ],
            self.audit["selection_sha256"],
        )


if __name__ == "__main__":
    unittest.main()
