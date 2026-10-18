from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from tools import build_tpch_autonomous_successor_top5_v9_bounded_retest as builder
from tools import run_tpch_round29_diagnostic_exact_plan_regime_batch_v40 as runner
from tools import tpch_fresh0_target10_v38_bounded_retest as controller
from tests.test_tpch_v38_bounded_retest import candidate


def selection(contract: str = runner.BOUNDED_CONTRACT) -> dict:
    top5 = []
    for index in range(1, 6):
        item = candidate(index)
        trusted = index <= 3
        item.update(
            {
                "selection_role": "trusted" if trusted else "probe",
                "diagnostic_only": True,
                "qps_used_for_inner_scoring": False,
                "incumbent_update_eligible": False,
                "eligible_for_best_candidate": False,
                "accepted_update_allowed": False,
                "incumbent_update_allowed": False,
                "range_update_allowed": False,
                "gradient_update_allowed": False,
            }
        )
        top5.append(item)
    return {
        "selection_contract": contract,
        "diagnostic_only": True,
        "formal_continuation": False,
        "serial_real_replay_required": True,
        "maximum_real_replay_invocations": 5,
        "qps_used_for_inner_scoring": False,
        "technical_failures_count_toward_physical_cap": True,
        "technical_failures_count_as_valid_or_accepted": False,
        "unknown_plan_regime_trusted_allowed": False,
        "accepted_update_allowed": False,
        "incumbent_update_allowed": False,
        "range_update_allowed": False,
        "gradient_update_allowed": False,
        "top5": top5,
    }


class TpchV40RunnerCompatibilityTests(unittest.TestCase):
    def test_controller_and_launcher_pin_compatibility_runner(self) -> None:
        expected = "c3d352b980daf403a3344b7381ad5699215f6b2ba59b2a40c10b6e71f91a908d"
        self.assertEqual(controller.EXPECTED[controller.DIAGNOSTIC_RUNNER], expected)
        launcher = Path("tools/launch_tpch_v38_bounded_retest.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn(str(controller.DIAGNOSTIC_RUNNER), launcher)
        self.assertIn(expected, launcher)

    def test_exact_bounded_contract_is_accepted(self) -> None:
        validated = runner.validate_selection(selection())
        self.assertEqual(
            [item["selection_role"] for item in validated],
            ["trusted", "trusted", "trusted", "probe", "probe"],
        )

    def test_exact_pending_retest_contract_is_accepted(self) -> None:
        validated = runner.validate_selection(
            selection(runner.PENDING_RETEST_CONTRACT)
        )

    def test_actual_pending_retest_builder_branch_passes_sealed_fields(self) -> None:
        source = {
            "diagnostic_only": True,
            "accepted_count_start": 0,
            "validated_plan_regimes": {},
            "top5": [candidate(index) for index in range(1, 6)],
        }
        successful = []
        for item in source["top5"]:
            rank = item["rank"]
            successful.append(
                {
                    "rank": rank,
                    "selection_role": "probe",
                    "three_level_fingerprint": item["three_level_fingerprint"],
                    "candidate_params": item["candidate_params"],
                    "technical_failure": False,
                    "valid_replay": True,
                    "actual_score": 3.9 + rank * 0.01,
                    "actual_resource": {
                        "cpu": 0.35 + rank * 0.01,
                        "io": 16.0,
                        "memory": 1.2,
                    },
                }
            )
        summary = {
            "diagnostic_only": True,
            "accepted_count": 0,
            "physical_call_count": 5,
            "valid_replay_count": 5,
            "technical_failure_count": 0,
            "baseline_restored": True,
            "accepted_update_applied": False,
            "incumbent_update_applied": False,
            "range_update_applied": False,
            "gradient_update_applied": False,
            "successful": successful,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selection_path = root / "selection.json"
            summary_path = root / "summary.json"
            expansion_path = root / "expansion.json"
            output_path = root / "output.json"
            selection_path.write_text(json.dumps(source), encoding="utf-8")
            summary_path.write_text(json.dumps(summary), encoding="utf-8")
            expansion_path.write_text(
                json.dumps({"ranked_candidates": []}), encoding="utf-8"
            )
            output = builder.build(
                selection_path,
                summary_path,
                expansion_path,
                output_path,
            )
            validated = runner.validate_selection(output)
        self.assertEqual(output["schema_version"], "tpch_autonomous_successor_top5_v2")
        self.assertEqual(output["selection_contract"], runner.PENDING_RETEST_CONTRACT)
        self.assertEqual(len(validated), 5)
        self.assertEqual(
            [item["selection_role"] for item in validated],
            ["trusted", "trusted", "trusted", "probe", "probe"],
        )

    def test_unknown_contract_is_still_rejected(self) -> None:
        value = selection("unknown_contract_must_fail_closed")
        with self.assertRaisesRegex(ValueError, "selection contract mismatch"):
            runner.validate_selection(value)

    def test_new_contract_does_not_bypass_role_checks(self) -> None:
        value = selection()
        value["top5"][0]["selection_role"] = "probe"
        with self.assertRaisesRegex(ValueError, "selection roles mismatch"):
            runner.validate_selection(value)

    def test_new_contract_does_not_bypass_required_field_checks(self) -> None:
        value = copy.deepcopy(selection())
        del value["top5"][0]["predicted_resource"]
        with self.assertRaisesRegex(ValueError, "missing fields"):
            runner.validate_selection(value)


if __name__ == "__main__":
    unittest.main()
