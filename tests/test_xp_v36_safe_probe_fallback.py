#!/usr/bin/env python3
"""Offline regression tests for the bounded XP v36 cont001 recovery."""

from __future__ import annotations

import copy
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest


PROJECT = Path(__file__).resolve().parents[1]
TOOLS = PROJECT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import resume_xp_fresh0_fixed_contract_target10_v36 as v36  # noqa: E402
import prepare_xp_v36_from_v35_zero_call as prepare  # noqa: E402


BUNDLE = Path(
    "/tmp/pg_xp_unattended_20260730_1235/"
    "bundle_fix_v33_plan_quarantine"
)
CAMPAIGN = PROJECT / "history/xp_final_algorithm_fresh10_v35_20260803T1220"
SOURCE_MANIFEST = CAMPAIGN / "batches/cont001/diagnostic_bundle/manifest.json"
SOURCE_CANDIDATES = CAMPAIGN / "batches/cont001/explain_only/explain_only_generation.json"
SOURCE_INCUMBENT = CAMPAIGN / "batches/cont001/accepted_incumbent.json"
SOURCE_STATE = CAMPAIGN / "state.json"


class SafeProbeFallbackTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        v36.verify_sources(BUNDLE)
        if str(BUNDLE) not in sys.path:
            sys.path.insert(0, str(BUNDLE))
        import xp_round10_diagnostic_bundle as bundle_module
        import xp_unattended_continuation as continuation

        cls.bundle = bundle_module
        cls.continuation = continuation
        cls.source = json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))
        cls.source_state_sha = v36.sha256(SOURCE_STATE)

    def _build(self, output_dir: Path) -> dict:
        source = self.source["source"]
        risk_audit = copy.deepcopy(
            self.source["diagnostic_discovery"]["safe_frontier_contract"][
                "stability_risk_audit"
            ]
        )
        original = self.bundle.build_bundle(
            selection_path=Path(source["selection"]),
            replay_path=Path(source["replay"]),
            candidate_path=SOURCE_CANDIDATES,
            accepted_incumbent_path=SOURCE_INCUMBENT,
            output_dir=output_dir,
            extra_excluded_ids={
                str(value)
                for value in json.loads(SOURCE_STATE.read_text(encoding="utf-8"))[
                    "replayed_fingerprints"
                ]
            },
            risk_utilization_upper={},
            risk_audit=risk_audit,
        )
        return v36.apply_resource_safe_probe5_fallback(
            self.bundle,
            original,
            selection_path=Path(source["selection"]),
            replay_path=Path(source["replay"]),
            candidate_path=SOURCE_CANDIDATES,
            accepted_incumbent_path=SOURCE_INCUMBENT,
            output_dir=output_dir,
            extra_excluded_ids={
                str(value)
                for value in json.loads(SOURCE_STATE.read_text(encoding="utf-8"))[
                    "replayed_fingerprints"
                ]
            },
            risk_utilization_upper={},
            risk_audit=risk_audit,
        )

    def test_v35_artifacts_yield_one_safe_diagnostic_probe5(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            campaign = Path(directory) / "campaign"
            output = campaign / "batches/cont001/diagnostic_bundle"
            manifest = self._build(output)
            selection = json.loads(
                (output / "selection.json").read_text(encoding="utf-8")
            )
            top5 = selection["top5"]
            self.assertEqual(manifest["status"], "ready")
            self.assertEqual(len(top5), 5)
            self.assertEqual(
                [item["selection_role"] for item in top5],
                ["maximin_uncertain_probe"] * 5,
            )
            self.assertTrue(all(item["boundary_corner"] is False for item in top5))
            self.assertTrue(all(item["used_for_gradient"] is False for item in top5))
            self.assertTrue(
                all(item["used_for_inner_incumbent_update"] is False for item in top5)
            )
            identifiers = [item["three_level_fingerprint"]["id"] for item in top5]
            self.assertEqual(len(set(identifiers)), 5)
            recovery = selection["v36_resource_safe_maximin_fallback"]
            self.assertFalse(recovery["resource_envelope_relaxed"])
            self.assertFalse(recovery["trusted_promotion_applied"])
            self.assertEqual(recovery["new_workload_calls"], 0)
            status, normalized = self.continuation.normalize_selection(
                selection,
                replayed_fingerprints=json.loads(
                    SOURCE_STATE.read_text(encoding="utf-8")
                )["replayed_fingerprints"],
            )
            self.assertEqual(status, "ready")
            self.assertIsNotNone(normalized)
            self.assertEqual(v36.sha256(SOURCE_STATE), self.source_state_sha)

            second = self._build(
                campaign / "batches/cont002/diagnostic_bundle"
            )
            self.assertEqual(second["status"], "blocked_missing_input")
            budget = json.loads(
                (campaign / "v36_safe_probe_fallback_budget.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(budget["fallback_batches_used"], 1)
            self.assertEqual(budget["workload_calls"], 0)
            self.assertEqual(v36.sha256(SOURCE_STATE), self.source_state_sha)

    def test_zero_call_preparer_reuses_r0_and_seals_partial_cont001(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            previous_target = prepare.TARGET
            prepare.TARGET = Path(directory) / "xp_v36_campaign"
            try:
                with contextlib.redirect_stdout(io.StringIO()) as stdout:
                    self.assertEqual(prepare.main(), 0)
                report = json.loads(stdout.getvalue())
                state = json.loads(
                    (prepare.TARGET / "state.json").read_text(encoding="utf-8")
                )
                selection = json.loads(
                    (prepare.TARGET / "batches/cont001/selection.json").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(report["workload_calls_added"], 0)
                self.assertFalse(report["source_v35_modified"])
                self.assertEqual(state["accepted_count"], 0)
                self.assertEqual(state["gradient_state"]["update_count"], 0)
                self.assertEqual(state["last_batch"]["batch_id"], "r0fresh000")
                self.assertEqual(len(selection["top5"]), 5)
                self.assertFalse(
                    (prepare.TARGET / "batches/cont001/replay_summary.json").exists()
                )
                self.assertFalse((prepare.TARGET / "batches/cont001/replay").exists())
                self.assertEqual(v36.sha256(SOURCE_STATE), self.source_state_sha)
            finally:
                prepare.TARGET = previous_target


if __name__ == "__main__":
    unittest.main()
