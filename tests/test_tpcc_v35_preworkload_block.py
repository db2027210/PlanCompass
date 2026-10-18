from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from tools import tpcc_autonomous_target10_controller_v25 as v25
from tools import tpcc_autonomous_target10_controller_v35_preworkload_block as v35


def state(group_count: int = 5) -> dict:
    return {
        "status": "preparing_next_batch",
        "next_mode": "diagnostic",
        "next_ordinal": 7,
        "current_run": None,
        "formal_state_sha256": "pending",
        "last_quality_gate": {
            "passed": False,
            "formal_start_authorized": False,
            "complete_same_group_batch_count": group_count,
        },
        "audits": [
            {
                "mode": "diagnostic_validation",
                "clean": True,
                "physical_calls": 5,
                "valid_replays": 5,
                "technical_failures": 0,
                "baseline_restored": True,
                "complete_same_group_batch_count": group_count,
            }
        ],
        "acquisition_transitions": [],
    }


class TpccV35PreworkloadBlockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.control = root / "controller"
        self.preparation = self.control / "prepare_07"
        self.preparation.mkdir(parents=True)
        v25.atomic_json(self.preparation / "evidence_manifest.json", {"x": 1})
        v25.atomic_json(
            self.preparation / "exact_regime_calibration.json", {"x": 2}
        )
        (self.control / "controller.lock").touch()
        self.formal = root / "formal_state.json"
        v25.atomic_json(self.formal, {"accepted_count": 0})
        self.state_path = self.control / "controller_state.json"
        current = state()
        current["formal_state_sha256"] = v25.sha256(self.formal)
        v25.atomic_json(self.state_path, current)
        self.manifest_path = root / "manifest.json"
        v25.atomic_json(
            self.manifest_path,
            {
                "controller_root": str(self.control),
                "controller_state_path": str(self.state_path),
                "formal_state_path": str(self.formal),
            },
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_group5_preworkload_boundary_is_eligible(self) -> None:
        result = v35.handoff_eligibility(
            self.manifest_path,
            require_unlocked_controller=True,
        )
        self.assertTrue(result["passed"])
        self.assertEqual(result["complete_same_group_batch_count"], 5)
        self.assertIsNone(result["current_run"])

    def test_group4_or_active_replay_is_rejected(self) -> None:
        current = v25.load_json(self.state_path)
        current["audits"][-1]["complete_same_group_batch_count"] = 4
        current["last_quality_gate"]["complete_same_group_batch_count"] = 4
        v25.atomic_json(self.state_path, current)
        with self.assertRaisesRegex(RuntimeError, "fifth group"):
            v35.handoff_eligibility(
                self.manifest_path,
                require_unlocked_controller=False,
            )
        current = state()
        current["formal_state_sha256"] = v25.sha256(self.formal)
        current["current_run"] = {"mode": "diagnostic"}
        v25.atomic_json(self.state_path, current)
        with self.assertRaisesRegex(RuntimeError, "pre-workload boundary"):
            v35.handoff_eligibility(
                self.manifest_path,
                require_unlocked_controller=False,
            )

    def test_partial_selection_is_ambiguous_and_rejected(self) -> None:
        v25.atomic_json(self.preparation / "top5.json", {"top5": []})
        with self.assertRaisesRegex(RuntimeError, "ambiguous"):
            v35.handoff_eligibility(
                self.manifest_path,
                require_unlocked_controller=False,
            )

    def test_acquisition_failure_becomes_explicit_zero_call_block(self) -> None:
        block = v35.record_blocked_preworkload(
            self.manifest_path,
            RuntimeError("command failed rc=1: no candidates for novel Top-5"),
        )
        observed = v25.load_json(self.state_path)
        self.assertEqual(observed["status"], v35.BLOCKED_STATUS)
        self.assertEqual(observed["controller_runtime_state"], "stopped_fail_closed")
        self.assertEqual(block["workload_calls_added"], 0)
        self.assertFalse(block["formal_start_authorized"])
        self.assertFalse(block["manual_candidate_injection"])
        self.assertIsNone(observed["current_run"])

    def test_non_acquisition_exception_is_not_hidden(self) -> None:
        before = copy.deepcopy(v25.load_json(self.state_path))
        with self.assertRaisesRegex(RuntimeError, "non-acquisition"):
            v35.record_blocked_preworkload(
                self.manifest_path,
                RuntimeError("formal state SHA mismatch"),
            )
        self.assertEqual(v25.load_json(self.state_path), before)


if __name__ == "__main__":
    unittest.main()
