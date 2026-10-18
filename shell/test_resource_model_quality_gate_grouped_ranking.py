#!/usr/bin/env python3
"""Focused CPU quality-gate tests for grouped pairwise ranking evidence."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest
from typing import Any, Dict


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from parameter_optimizer import validate_resource_model_quality_gate  # noqa: E402


def _cpu_retrain_result(
    *,
    split_policy: str,
    pairwise_inversion_rate: float,
    pairwise_pair_count: int = 10,
) -> Dict[str, Any]:
    return {
        "resources": {
            "cpu": {
                "C_only": {
                    "split_policy": split_policy,
                    "train": {"sample_count": 20},
                    "test": {
                        "sample_count": 10,
                        "r2": 0.5,
                        "relative_error_p95": 0.05,
                        "relative_error_max": 0.10,
                        "pairwise_pair_count": pairwise_pair_count,
                        "pairwise_inversion_rate": pairwise_inversion_rate,
                    },
                }
            }
        }
    }


def _quality_gate_config() -> Dict[str, Any]:
    return {
        "enabled": True,
        "required_resources": ["cpu"],
        "optional_resources": [],
        "min_test_samples": 5,
        "selected_model_only": True,
        "workload_family": "C",
        "enforce_test_r2": False,
        "enforce_test_relative_error_p95": False,
        "enforce_test_relative_error_max": False,
        "enforce_test_pairwise_ranking": True,
        "require_grouped_holdout": True,
        "max_test_pairwise_inversion_rate": 0.10,
        "min_test_pairwise_pair_count": 10,
    }


def _failure_reasons(summary: Dict[str, Any]) -> str:
    return " ".join(
        str(reason)
        for failure in summary.get("failures") or []
        for reason in failure.get("reasons") or []
    )


class ResourceModelQualityGateGroupedRankingTests(unittest.TestCase):
    def test_grouped_holdout_at_pairwise_threshold_passes(self) -> None:
        summary = validate_resource_model_quality_gate(
            _cpu_retrain_result(
                split_policy="grouped_holdout_v1",
                pairwise_inversion_rate=0.10,
                pairwise_pair_count=10,
            ),
            _quality_gate_config(),
        )

        self.assertTrue(summary["passed"], summary.get("failures"))
        self.assertEqual(len(summary["checks"]), 1)
        self.assertEqual(summary["checks"][0]["split_policy"], "grouped_holdout_v1")
        self.assertEqual(summary["checks"][0]["test_pairwise_pair_count"], 10)
        self.assertEqual(summary["checks"][0]["test_pairwise_inversion_rate"], 0.10)

    def test_row_random_split_is_rejected(self) -> None:
        summary = validate_resource_model_quality_gate(
            _cpu_retrain_result(
                split_policy="row_random_holdout_legacy",
                pairwise_inversion_rate=0.05,
            ),
            _quality_gate_config(),
        )

        self.assertFalse(summary["passed"])
        self.assertIn(
            "resource model split_policy is not grouped_holdout_v1",
            _failure_reasons(summary),
        )

    def test_pairwise_inversion_above_threshold_is_rejected(self) -> None:
        summary = validate_resource_model_quality_gate(
            _cpu_retrain_result(
                split_policy="grouped_holdout_v1",
                pairwise_inversion_rate=0.11,
            ),
            _quality_gate_config(),
        )

        self.assertFalse(summary["passed"])
        self.assertIn(
            "test.pairwise_inversion_rate 0.11 > 0.1",
            _failure_reasons(summary),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
