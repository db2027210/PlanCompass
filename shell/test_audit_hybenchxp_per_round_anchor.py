#!/usr/bin/env python3
"""Tests for independent strict-audit recomputation of round anchors."""

from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.audit_hybenchxp_four_vm_recall_completion import (
    real_score_b_audit_details,
    real_score_b_audit_key,
    real_score_c_audit_details,
    real_score_c_audit_key,
    nested_final_goal_audit_details,
    nested_outer_audit_details,
    verify_sha256_manifest,
)


class AuditPerRoundAnchorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.baseline = {
            "db_qps": 100.0,
            "resources": {"cpu": 100.0, "memory": 100.0, "io": 100.0},
        }

    @staticmethod
    def candidate(label: str, *, qps: float, cpu: float, index: int) -> dict:
        return {
            "label": label,
            "candidate_index": index,
            "status": "ok",
            "accepted": False,
            "db_qps": qps,
            "resources": {"cpu": cpu, "memory": 100.0, "io": 100.0},
            "resource_stats": {"resource_monitor_ok": True},
            "p95_ms": 10.0,
            "p99_ms": 20.0,
        }

    def test_success_is_recomputed_not_copied_from_accepted_flag(self) -> None:
        row = self.candidate("feasible", qps=120.0, cpu=93.0, index=1)

        details = real_score_b_audit_details(row, self.baseline, "cpu")

        self.assertIsNotNone(details)
        self.assertTrue(details["successful"])
        self.assertAlmostEqual(1.20, details["score_b"])

    def test_feasible_candidate_beats_higher_qps_constraint_miss(self) -> None:
        feasible = self.candidate("feasible", qps=116.0, cpu=93.0, index=1)
        wrong_drop = self.candidate("wrong_drop", qps=180.0, cpu=96.0, index=2)
        ranked = []
        for row in (wrong_drop, feasible):
            details = real_score_b_audit_details(row, self.baseline, "cpu")
            self.assertIsNotNone(details)
            if details["successful"]:
                ranked.append((row, details))

        winner, _ = max(ranked, key=lambda item: real_score_b_audit_key(*item))

        self.assertEqual("feasible", winner["label"])

    def test_no_successful_candidate_yields_empty_success_set(self) -> None:
        rows = [
            self.candidate("close", qps=120.0, cpu=95.5, index=1),
            self.candidate("far_high_qps", qps=180.0, cpu=97.0, index=2),
        ]
        successful = [
            row
            for row in rows
            if real_score_b_audit_details(row, self.baseline, "cpu")["successful"]
        ]

        self.assertEqual([], successful)

    def test_score_c_ignores_qps_in_score_gate_and_tie_break(self) -> None:
        low_qps = self.candidate("low_qps", qps=1.0, cpu=93.0, index=1)
        high_qps = self.candidate("high_qps", qps=1000.0, cpu=93.0, index=2)
        ranked = []
        for row in (high_qps, low_qps):
            details = real_score_c_audit_details(row, self.baseline, "cpu")
            self.assertIsNotNone(details)
            self.assertTrue(details["successful"])
            self.assertFalse(details["qps_considered"])
            self.assertIsNone(details["qps_gate"])
            ranked.append((row, details))

        winner, details = max(ranked, key=lambda item: real_score_c_audit_key(*item))

        self.assertEqual("low_qps", winner["label"])
        self.assertAlmostEqual(1.0, details["score_c"])

    def test_nested_outer_audit_requires_qps_up_and_target_down_only(self) -> None:
        candidate = self.candidate("candidate", qps=110.0, cpu=93.0, index=1)
        candidate["resources"].update({"memory": 90.0, "io": 90.0})

        details = nested_outer_audit_details(
            candidate, self.baseline, self.baseline, "cpu"
        )

        self.assertTrue(details["accepted"])
        self.assertTrue(details["qps_up"])
        self.assertTrue(details["target_down"])
        self.assertEqual([], details["other_up_vs_reference"])

    def test_nested_final_goal_audit_recomputes_physical_gate(self) -> None:
        candidate = self.candidate("final", qps=120.0, cpu=93.0, index=1)
        candidate.update(
            {
                "db_xact_tps": 50.0,
                "p90_ms": 5.0,
                "actual_utilization_pct": {"cpu": 75.0},
                "runtime_scoring": {
                    "overload_threshold": 0.8,
                    "resource_target_ranges": {
                        "cpu": {"min_pct": 65.0, "max_pct": 80.0}
                    },
                },
            }
        )
        candidate["resources"]["memory"] = 110.0

        details = nested_final_goal_audit_details(candidate, self.baseline, "cpu")

        self.assertTrue(details["goal_reached"])
        self.assertTrue(details["target_drop_in_range"])
        self.assertTrue(details["target_not_bottleneck"])
        self.assertTrue(details["target_in_sweet_spot"])

    def test_frozen_launch_snapshot_hashes_are_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact = Path(temp_dir) / "artifact.txt"
            artifact.write_text("frozen\n", encoding="utf-8")
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
            manifest = Path(temp_dir) / "SHA256SUMS"
            manifest.write_text(f"{digest}  {artifact}\n", encoding="utf-8")

            entries, failures = verify_sha256_manifest(manifest)
            self.assertFalse(failures)
            self.assertEqual("true", entries[0]["passed"])

            artifact.write_text("changed\n", encoding="utf-8")
            _, failures = verify_sha256_manifest(manifest)
            self.assertTrue(any("snapshot_sha256_mismatch" in item for item in failures))


if __name__ == "__main__":
    unittest.main()
