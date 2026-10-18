#!/usr/bin/env python3
"""Self-tests for compare_algorithm_c_artifacts_on_formal_holdout.py."""

from __future__ import annotations

import importlib.util
import json
import math
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


MODULE_PATH = Path(__file__).with_name("compare_algorithm_c_artifacts_on_formal_holdout.py")
SPEC = importlib.util.spec_from_file_location("compare_algorithm_c_artifacts_on_formal_holdout", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _scope() -> Dict[str, Any]:
    return {
        "benchmark": "tpch",
        "scene": "tpch_memory",
        "scale_factor": 5,
        "benchmark_concurrency": {"client_count": 1},
        "plan_evidence": {"query_sequence": ["Q1", "Q2"]},
        "physical_replay": "TPC-H_SF5_Q1_Q22_exactly_once",
    }


def _artifact(coefficient: float, *, scope: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    resources = {}
    for resource in ("cpu", "memory", "io"):
        resources[resource] = {
            "family": "parameter",
            "parameter_coefficients": {"p": coefficient},
            "plan_log_ratio_coefficient": 0.0,
            "qps_used": False,
            "zero_intercept": True,
        }
    return {
        "schema_version": 7,
        "benchmark": "tpch",
        "target_resource": "memory",
        "scope": scope if scope is not None else _scope(),
        "active_params": ["p"],
        "qps_used_for_inner_scoring": False,
        "resource_prediction_calibration": {
            "active_params": ["p"],
            "contract": "training_loo_selected_plan_parameter_hybrid_v1",
            "qps_used": False,
            "resources": resources,
        },
    }


def _anchor(group: int, candidate: int, x: float, *, contract: str = MODULE.ANCHOR_CONTRACT) -> Dict[str, Any]:
    return {
        "label": f"algorithm_c_r{group:03d}_base_top5_c{candidate:02d}_w1",
        "round_index": 1000000 + group,
        "worker_key": "w1",
        "workload": "tpch",
        "status": "ok",
        "technical_failure": False,
        "used_real_replay": True,
        "valid_replay_count": 1,
        "hybench_run_config": {"sf": "5"},
        "workload_completeness": {
            "passed": True,
            "expected_query_sequence": ["Q1", "Q2"],
            "expected_worker_count": 1,
        },
        "formal_anchor": {
            "contract": contract,
            "holdout_group": f"formal_round_{group:06d}",
            "original_round_index": group,
            "qps_used_for_resource_prediction": False,
            "parameter_log_deltas": {"p": x},
            "raw_plan_log_ratios": {"cpu": 0.0, "memory": 0.0, "io": 0.0},
            "outcomes": {"cpu": x, "memory": x, "io": x, "qps_outer_only": 1000000.0 * x},
            "workload_plan_fingerprint": f"workload-exact-physical-sha256:{group:06d}{candidate:02d}",
            "plan_fingerprint_coverage": {"complete": True},
        },
    }


class HoldoutComparisonTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_inputs(
        self,
        rows: List[Dict[str, Any]],
        old: Optional[Dict[str, Any]] = None,
        new: Optional[Dict[str, Any]] = None,
        *,
        manifest: bool = False,
    ) -> Tuple[Path, Path, Path]:
        anchor_path = self.root / "anchors.jsonl"
        anchor_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        old_path = self.root / "old.json"
        new_path = self.root / "new.json"
        old_path.write_text(json.dumps(old or _artifact(0.0)), encoding="utf-8")
        new_path.write_text(json.dumps(new or _artifact(1.0)), encoding="utf-8")
        if manifest:
            (self.root / "anchors.manifest.json").write_text(
                json.dumps(
                    {
                        "contract": "algorithm_c_formal_replay_anchor_export_v1",
                        "anchor_contract": MODULE.ANCHOR_CONTRACT,
                        "anchor_count": len(rows),
                        "qps_used_for_resource_prediction": False,
                        "source_campaign_mutated": False,
                        "accepted_complete_rounds": [1],
                    }
                ),
                encoding="utf-8",
            )
        return anchor_path, old_path, new_path

    def test_latest_complete_group_formula_metrics_and_qps_exclusion(self) -> None:
        values = [-0.20, -0.10, 0.0, 0.10, 0.20]
        rows = [_anchor(1, index, value) for index, value in enumerate(values, 1)]
        rows.extend(_anchor(2, index, value) for index, value in enumerate(values[:4], 1))
        anchor_path, old_path, new_path = self._write_inputs(rows, manifest=True)

        report = MODULE.compare_artifacts(anchor_path, old_path, new_path)

        self.assertEqual(report["holdout"]["holdout_group"], "formal_round_000001")
        self.assertEqual(report["holdout"]["point_count"], 5)
        self.assertTrue(report["validation"]["manifest"]["present"])
        self.assertFalse(report["validation"]["qps_used_for_resource_prediction"])
        self.assertTrue(report["validation"]["qps_field_present_but_ignored"])
        for resource in ("cpu", "memory", "io"):
            old_metrics = report["resources"][resource]["old"]
            new_metrics = report["resources"][resource]["new"]
            self.assertAlmostEqual(old_metrics["mae_log_ratio"], 0.12)
            self.assertEqual(old_metrics["direction_error_count"], 4)
            self.assertAlmostEqual(new_metrics["mae_log_ratio"], 0.0)
            self.assertAlmostEqual(new_metrics["spearman"], 1.0)
            self.assertEqual(new_metrics["direction_error_count"], 0)
            self.assertEqual(new_metrics["actual_best_predicted_rank"], 1)
            self.assertEqual(new_metrics["predicted"], values)
            self.assertFalse(new_metrics["qps_used"])
        self.assertTrue(report["promotion"]["recommend_promotion"])
        self.assertFalse(report["promotion"]["artifact_mutated"])
        self.assertFalse(report["promotion"]["experiment_mutated"])
        partial_audit = next(
            item for item in report["holdout"]["group_audits"] if item["holdout_group"] == "formal_round_000002"
        )
        self.assertFalse(partial_audit["complete"])

    def test_plan_term_is_included(self) -> None:
        rows = [_anchor(1, index, 0.05 * index) for index in range(1, 6)]
        for index, row in enumerate(rows, 1):
            row["formal_anchor"]["parameter_log_deltas"]["p"] = 0.0
            row["formal_anchor"]["raw_plan_log_ratios"] = {
                "cpu": 0.01 * index,
                "memory": 0.01 * index,
                "io": 0.01 * index,
            }
            row["formal_anchor"]["outcomes"].update(
                {"cpu": 0.02 * index, "memory": 0.02 * index, "io": 0.02 * index}
            )
        old = _artifact(0.0)
        new = _artifact(0.0)
        for model in new["resource_prediction_calibration"]["resources"].values():
            model["plan_log_ratio_coefficient"] = 2.0
        anchor_path, old_path, new_path = self._write_inputs(rows, old, new)

        report = MODULE.compare_artifacts(anchor_path, old_path, new_path)

        self.assertTrue(report["promotion"]["recommend_promotion"])
        self.assertAlmostEqual(report["resources"]["memory"]["new"]["mae_log_ratio"], 0.0)

    def test_scope_mismatch_is_rejected(self) -> None:
        rows = [_anchor(1, index, 0.01 * index) for index in range(1, 6)]
        changed_scope = _scope()
        changed_scope["scale_factor"] = 10
        anchor_path, old_path, new_path = self._write_inputs(rows, _artifact(0.0), _artifact(1.0, scope=changed_scope))
        with self.assertRaisesRegex(MODULE.ValidationError, "scopes differ"):
            MODULE.compare_artifacts(anchor_path, old_path, new_path)

    def test_missing_generic_worker_count_is_not_a_scope_mismatch(self) -> None:
        """Workload-specific completeness may certify concurrency elsewhere."""
        rows = [_anchor(1, index, 0.01 * index) for index in range(1, 6)]
        for row in rows:
            row["workload_completeness"].pop("expected_worker_count")
        anchor_path, old_path, new_path = self._write_inputs(rows)

        report = MODULE.compare_artifacts(anchor_path, old_path, new_path)

        self.assertEqual(report["holdout"]["point_count"], 5)

    def test_bad_anchor_contract_and_short_group_are_rejected(self) -> None:
        bad_contract_rows = [_anchor(1, index, 0.01 * index, contract="wrong") for index in range(1, 6)]
        anchor_path, old_path, new_path = self._write_inputs(bad_contract_rows)
        with self.assertRaisesRegex(MODULE.ValidationError, "no complete holdout group"):
            MODULE.compare_artifacts(anchor_path, old_path, new_path)

        short_rows = [_anchor(1, index, 0.01 * index) for index in range(1, 5)]
        anchor_path, old_path, new_path = self._write_inputs(short_rows)
        with self.assertRaisesRegex(MODULE.ValidationError, "no complete holdout group"):
            MODULE.compare_artifacts(anchor_path, old_path, new_path)

    def test_qps_in_model_is_rejected(self) -> None:
        rows = [_anchor(1, index, 0.01 * index) for index in range(1, 6)]
        bad = _artifact(1.0)
        bad["resource_prediction_calibration"]["qps_used"] = True
        anchor_path, old_path, new_path = self._write_inputs(rows, _artifact(0.0), bad)
        with self.assertRaisesRegex(MODULE.ValidationError, "qps_used=false"):
            MODULE.compare_artifacts(anchor_path, old_path, new_path)

    def test_cli_writes_only_report(self) -> None:
        rows = [_anchor(1, index, 0.01 * index) for index in range(1, 6)]
        anchor_path, old_path, new_path = self._write_inputs(rows)
        old_before = old_path.read_bytes()
        new_before = new_path.read_bytes()
        output = self.root / "report.json"

        rc = MODULE.main(
            [
                "--anchors",
                str(anchor_path),
                "--old-artifact",
                str(old_path),
                "--new-artifact",
                str(new_path),
                "--output",
                str(output),
            ]
        )

        self.assertEqual(rc, 0)
        self.assertTrue(output.exists())
        self.assertEqual(old_before, old_path.read_bytes())
        self.assertEqual(new_before, new_path.read_bytes())
        self.assertEqual(json.loads(output.read_text())["contract"], MODULE.REPORT_CONTRACT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
