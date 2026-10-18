from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tools"))
sys.path.insert(
    0,
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/"
    "gradient_debug_round14_robust_regime_interval_v2_20260729T1824",
)
RUN_ROOT = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/"
    "formal10_final_algorithm_fresh0_v36_20260803/autonomous_runs_v37/"
    "autonomous_diagnostic_validation_01/"
    "top5_real_replay_round15_diagnostic_v1"
)

from tools import tpcc_v38_replay_policy as policy
from tools import reclassify_tpcc_v37_false_technical as reclassifier
from tools import build_tpcc_v38_fresh_unique_validation as builder
from tools import run_tpcc_v36_unique_exact_strict_serial as diagnostic_runner


class TpccV38ReplayPolicyTests(unittest.TestCase):
    def test_correctness_policy_is_sha_pinned(self) -> None:
        policy_path = PROJECT / "tools/tpcc_round11_current_correctness_policy.py"
        actual = hashlib.sha256(policy_path.read_bytes()).hexdigest()
        self.assertEqual(actual, policy.CORRECTNESS_POLICY_SHA256)

    def test_any_physical_attempt_excludes_rank_from_same_batch(self) -> None:
        candidates = [{"rank": rank} for rank in range(1, 6)]
        selected = policy.select_uninvoked_unique_candidates(
            candidates,
            successful=[{"rank": 2, "attempt": 1}],
            technical_failures=[{"rank": 1, "attempt": 1}],
            audited_noncounting=[],
            remaining_physical_budget=3,
        )
        self.assertEqual([item["rank"] for item in selected], [3, 4, 5])

    def test_duplicate_audit_and_technical_attempts_do_not_retry_rank(self) -> None:
        candidates = [{"rank": rank} for rank in range(1, 6)]
        selected = policy.select_uninvoked_unique_candidates(
            candidates,
            successful=[{"rank": 1, "attempt": 1}],
            technical_failures=[{"rank": 2, "attempt": 1}],
            audited_noncounting=[{"rank": 1, "attempt": 2}],
            remaining_physical_budget=2,
        )
        self.assertEqual([item["rank"] for item in selected], [3, 4])
        self.assertEqual(
            policy.invoked_bindings(
                [{"rank": 1, "attempt": 1}],
                [{"rank": 2, "attempt": 1}],
                [{"rank": 1, "attempt": 2}],
            ),
            {(1, 1), (1, 2), (2, 1)},
        )

    def test_negative_remaining_budget_fails_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "negative"):
            policy.select_uninvoked_unique_candidates(
                [],
                successful=[],
                technical_failures=[],
                audited_noncounting=[],
                remaining_physical_budget=-1,
            )

    def test_reclassifier_requires_every_reserved_call_to_be_terminal(self) -> None:
        complete = [
            {
                "event": "tpcc_gradient_real_replay_workload_invocation_reserved",
                "rank": 1,
                "attempt": 1,
            },
            {
                "event": "tpcc_gradient_real_replay_technical_failure",
                "rank": 1,
                "attempt": 1,
            },
        ]
        self.assertEqual(reclassifier.validate_terminal_events(complete), {(1, 1)})
        incomplete = complete[:1]
        with self.assertRaisesRegex(RuntimeError, "unsettled"):
            reclassifier.validate_terminal_events(incomplete)

    def test_fixed_policy_accepts_all_v37_misclassified_measurements(self) -> None:
        if not RUN_ROOT.exists():
            self.skipTest("local v37 evidence is unavailable")
        summary_paths = sorted(RUN_ROOT.glob("rank_0[13]/attempt_*/point_*/summary.json"))
        self.assertEqual(len(summary_paths), 4)
        for summary_path in summary_paths:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            native_log_path = Path(summary["native_log"])
            gate = policy.classify_transaction_abort_gate(
                summary,
                native_log_path.read_text(encoding="utf-8", errors="replace"),
            )
            self.assertTrue(gate["valid_replay"], summary_path)
            self.assertFalse(gate["technical_failure"], summary_path)
            self.assertEqual(gate["reason"], "expected_transaction_abort_within_rate")
            self.assertLessEqual(gate["abort_rate"], 0.001)

    def test_sealed_v38_selection_is_fresh_role_separated_and_runner_valid(self) -> None:
        package = PROJECT / "history/tpcc_v38_fresh_unique_package_20260803"
        selection_path = package / "top5.json"
        selection = json.loads(selection_path.read_text(encoding="utf-8"))
        prior_path = Path(
            "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
            "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/"
            "formal10_final_algorithm_fresh0_v36_20260803/autonomous_runs_v37/"
            "autonomous_diagnostic_validation_01/top5.json"
        )
        prior = json.loads(prior_path.read_text(encoding="utf-8"))
        ids = {item["three_level_fingerprint"]["id"] for item in selection["top5"]}
        prior_ids = {item["three_level_fingerprint"]["id"] for item in prior["top5"]}
        self.assertEqual(len(ids), 5)
        self.assertFalse(ids.intersection(prior_ids))
        self.assertEqual(
            [item["selection_role"] for item in selection["top5"]],
            ["trusted", "trusted", "trusted", "uncertain_probe", "uncertain_probe"],
        )
        self.assertTrue(all(item.get("boundary_corner") is False for item in selection["top5"]))
        contract = selection["v38_build_contract"]
        self.assertFalse(contract["same_rank_retry_allowed"])
        self.assertTrue(contract["technical_call_advances_to_next_unique_rank"])
        analysis, _root, candidates, provenance = diagnostic_runner.validate_generation_and_top5(
            selection_path,
            package / "top5_real_replay_round16_zero_call_test",
        )
        self.assertEqual(analysis["selection_contract"], builder.SELECTION_CONTRACT)
        self.assertEqual(len(candidates), 5)
        self.assertEqual(provenance["correctness_policy_sha256"], policy.CORRECTNESS_POLICY_SHA256)


if __name__ == "__main__":
    unittest.main()
