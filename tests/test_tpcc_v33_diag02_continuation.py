from __future__ import annotations

import copy
import unittest

from tools import build_tpcc_v33_diag02_trusted3_probe2 as v33


def candidate(rank: int, role: str, plan: str, identifier: str) -> dict:
    return {
        "rank": rank,
        "selection_role": role,
        "trusted": role == "trusted",
        "boundary_corner": False,
        "diagnostic_only": True,
        "incumbent_update_eligible": False,
        "workload_plan_fingerprint": plan,
        "three_level_fingerprint": {"id": identifier},
    }


class TpccV33ContinuationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.selection = {
            "maximum_real_workload_invocations": 5,
            "qps_role": "outer_audit_only_not_inner_score",
            "top5": [
                candidate(1, "trusted", "plan-a", "new-1"),
                candidate(2, "trusted", "plan-a", "new-2"),
                candidate(3, "trusted", "plan-b", "new-3"),
                candidate(4, "uncertain_probe", "plan-c", "new-4"),
                candidate(5, "uncertain_probe", "plan-d", "new-5"),
            ],
        }
        self.source = {
            "known_plans": ["plan-a", "plan-b"],
            "source_fingerprint_ids": ["old-1", "old-2"],
        }

    def test_trusted3_probe2_new_fingerprints_pass(self) -> None:
        gate = v33.validate_generated_top5(
            self.selection,
            source_contract=self.source,
            globally_used_ids=["old-1", "old-2"],
        )
        self.assertTrue(gate["passed"])
        self.assertFalse(gate["source_probe_rows_retroactively_trusted"])

    def test_unknown_trusted_or_collision_fails_closed(self) -> None:
        changed = copy.deepcopy(self.selection)
        changed["top5"][0]["workload_plan_fingerprint"] = "unknown"
        with self.assertRaisesRegex(RuntimeError, "violates"):
            v33.validate_generated_top5(
                changed,
                source_contract=self.source,
                globally_used_ids=["old-1"],
            )
        changed = copy.deepcopy(self.selection)
        changed["top5"][4]["three_level_fingerprint"]["id"] = "old-1"
        with self.assertRaisesRegex(RuntimeError, "violates"):
            v33.validate_generated_top5(
                changed,
                source_contract=self.source,
                globally_used_ids=["old-1"],
            )

    def test_no_solution_is_single_attempt_and_zero_workload(self) -> None:
        audit = v33.bounded_no_solution_audit(
            elapsed_seconds=2.5,
            error=RuntimeError("no candidates"),
        )
        self.assertEqual(audit["generation_attempt_count"], 1)
        self.assertEqual(audit["maximum_generation_attempts"], 1)
        self.assertEqual(audit["physical_workload_calls_added"], 0)
        self.assertFalse(audit["formal_state_mutated"])
        self.assertFalse(audit["automatic_unbounded_retry"])


if __name__ == "__main__":
    unittest.main()
