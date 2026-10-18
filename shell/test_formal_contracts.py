#!/usr/bin/env python3
"""Self-tests for the PG5 formal scoring and R0 resource contracts."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from formal_contracts import (
    PG5_SCORING_SHA256,
    evaluate_r0_profile_construction_gate,
    evaluate_r0_single_bottleneck_gate,
    evaluate_six_window_mean_stability,
    resolve_target_relative_scoring,
    validate_benchmark_target_resource,
    validate_common_scoring_contract,
)


CONTRACT = (
    ROOT
    / "config/formal/pg5_common_outer_bgallowed_20260722_v1"
    / "common_scoring_contract.json"
)


class FormalContractTests(unittest.TestCase):
    @staticmethod
    def _assessment(means: list[float]) -> dict:
        windows = []
        for index, value in enumerate(means):
            windows.append(
                {
                    "window_index": index,
                    "end_elapsed_seconds": (index + 1) * 30,
                    "sample_count": 30,
                    "expected_sample_count": 30,
                    "coverage_passed": True,
                    "fields": [
                        {"field": field, "mean": value}
                        for field in ("qps", "cpu_utilization", "memory_utilization", "io_utilization")
                    ],
                }
            )
        return {"windows": windows}

    def test_common_scoring_exact_match(self) -> None:
        scoring = json.loads(CONTRACT.read_text(encoding="utf-8"))
        evidence = validate_common_scoring_contract(
            scoring,
            {"path": str(CONTRACT), "sha256": PG5_SCORING_SHA256},
        )
        self.assertTrue(evidence["passed"])

    def test_common_scoring_rejects_lane_override(self) -> None:
        scoring = json.loads(CONTRACT.read_text(encoding="utf-8"))
        scoring["resource_weights"]["io"] = 2
        with self.assertRaisesRegex(ValueError, "resource_weights.io"):
            validate_common_scoring_contract(
                scoring,
                {"path": str(CONTRACT), "sha256": PG5_SCORING_SHA256},
            )

    def test_common_scoring_rejects_wrong_file_hash(self) -> None:
        scoring = json.loads(CONTRACT.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "contract.json"
            path.write_text(json.dumps(scoring), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "file SHA mismatch"):
                validate_common_scoring_contract(
                    scoring,
                    {"path": str(path), "sha256": PG5_SCORING_SHA256},
                )

    def test_target_relative_scoring_is_uniform_across_scenarios(self) -> None:
        scoring = json.loads(CONTRACT.read_text(encoding="utf-8"))
        cpu_scenario = resolve_target_relative_scoring(scoring, "cpu")
        memory_scenario = resolve_target_relative_scoring(scoring, "memory")
        io_scenario = resolve_target_relative_scoring(scoring, "io")

        self.assertEqual(
            cpu_scenario["resource_target_ranges"]["cpu"],
            memory_scenario["resource_target_ranges"]["memory"],
        )
        self.assertEqual(
            memory_scenario["resource_target_ranges"]["memory"],
            io_scenario["resource_target_ranges"]["io"],
        )
        self.assertEqual(
            cpu_scenario["resource_target_ranges"]["memory"],
            memory_scenario["resource_target_ranges"]["cpu"],
        )
        self.assertEqual(
            cpu_scenario["resource_target_ranges"]["cpu"]["min_pct"],
            85,
        )
        self.assertEqual(
            cpu_scenario["resource_target_ranges"]["cpu"]["max_pct"],
            90,
        )
        self.assertEqual(
            cpu_scenario["resource_target_ranges"]["memory"]["min_pct"],
            30,
        )
        self.assertEqual(
            cpu_scenario["resource_target_ranges"]["memory"]["max_pct"],
            50,
        )
        self.assertEqual(
            cpu_scenario["resource_target_ranges"]["cpu"]["role"],
            "target_bottleneck",
        )
        self.assertEqual(
            cpu_scenario["resource_target_ranges"]["memory"]["role"],
            "non_bottleneck",
        )

    def test_target_relative_scoring_requires_one_target(self) -> None:
        scoring = json.loads(CONTRACT.read_text(encoding="utf-8"))
        with self.assertRaisesRegex(ValueError, "target_resource"):
            resolve_target_relative_scoring(scoring, "")

    def test_r0_gate_boundaries(self) -> None:
        passed, _ = evaluate_r0_single_bottleneck_gate(
            {"cpu": 80.0001, "memory": 30.0, "io": 50.0},
            "cpu",
        )
        self.assertTrue(passed)
        passed_at_80, checks = evaluate_r0_single_bottleneck_gate(
            {"cpu": 80.0, "memory": 40.0, "io": 40.0},
            "cpu",
        )
        self.assertFalse(passed_at_80)
        self.assertFalse(next(item for item in checks if item["resource"] == "cpu")["passed"])

    def test_r0_gate_rejects_non_target_outside_band(self) -> None:
        passed, _ = evaluate_r0_single_bottleneck_gate(
            {"cpu": 81.0, "memory": 29.999, "io": 40.0},
            "cpu",
        )
        self.assertFalse(passed)

    def test_r0_profile_construction_gate_boundaries(self) -> None:
        passed, _ = evaluate_r0_profile_construction_gate(
            {"cpu": 85.0, "memory": 30.0, "io": 50.0},
            "cpu",
        )
        self.assertTrue(passed)
        passed, _ = evaluate_r0_profile_construction_gate(
            {"cpu": 95.0, "memory": 30.0, "io": 50.0},
            "cpu",
        )
        self.assertTrue(passed)

    def test_r0_profile_construction_gate_rejects_wrong_profile(self) -> None:
        passed, checks = evaluate_r0_profile_construction_gate(
            {"cpu": 84.999, "memory": 40.0, "io": 40.0},
            "cpu",
        )
        self.assertFalse(passed)
        self.assertFalse(next(item for item in checks if item["resource"] == "cpu")["passed"])

    def test_frozen_benchmark_target_mapping(self) -> None:
        self.assertEqual(validate_benchmark_target_resource("JOB", "memory"), "memory")
        self.assertEqual(validate_benchmark_target_resource("TPC-H", "memory"), "memory")
        self.assertEqual(validate_benchmark_target_resource("TPC-C", "cpu"), "cpu")
        self.assertEqual(validate_benchmark_target_resource("XP", "io"), "io")
        with self.assertRaisesRegex(ValueError, "unknown frozen benchmark"):
            validate_benchmark_target_resource("hybench-ap", "memory")

    def test_frozen_benchmark_target_mapping_rejects_change(self) -> None:
        with self.assertRaisesRegex(ValueError, "frozen target 'io'"):
            validate_benchmark_target_resource("hybench_xp", "memory")

    def test_six_window_mean_stability_passes_at_ten_percent(self) -> None:
        result = evaluate_six_window_mean_stability(
            self._assessment([1.00, 1.05, 0.95, 1.02, 0.98, 1.00]),
            ("qps", "cpu_utilization", "memory_utilization", "io_utilization"),
        )
        self.assertTrue(result["stable"])
        self.assertEqual(result["earliest_stable_stop_seconds"], 180)

    def test_six_window_mean_stability_rejects_high_cv(self) -> None:
        result = evaluate_six_window_mean_stability(
            self._assessment([0.1, 1.9, 0.1, 1.9, 0.1, 1.9]),
            ("qps", "cpu_utilization", "memory_utilization", "io_utilization"),
        )
        self.assertFalse(result["stable"])


if __name__ == "__main__":
    unittest.main()
