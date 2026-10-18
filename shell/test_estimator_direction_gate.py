#!/usr/bin/env python3
"""Tests for the strict experience-table estimator direction gate."""

from __future__ import annotations

from pathlib import Path
import hashlib
import json
import sys
import tempfile
import unittest
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from parameter_optimizer import (
    _derive_ratio_scale_baseline_measurement_protocol,
    _direction_gate_baseline_estimation_metadata,
    validate_estimator_direction_gate,
    validate_estimator_direction_gate_config,
    load_replay_direction_calibration_override,
    validate_resource_model_baseline_sanity_gate,
)


def _sanity_metadata(check: dict) -> dict:
    return {
        "workload_context": {
            "resource_model_retraining": {
                "baseline_sanity_gate": {
                    "enabled": True,
                    "passed": bool(check.get("passed")),
                    "checks": [check],
                }
            }
        }
    }


def _ratio_scale_direction_metadata(
    *,
    target: str = "memory",
    stable: bool = True,
    correctness_passed: bool = True,
    qps_used_for_inner_scoring: bool = False,
    predicted_multiplier: float = 1.0,
) -> dict:
    query_fingerprint = "exact-structure-sha256:" + ("1" * 64)
    fingerprint_rows = [
        {"queryid": "q1", "fingerprint": query_fingerprint}
    ]
    fingerprint = "ratio-plan-sha256:" + hashlib.sha256(
        json.dumps(
            fingerprint_rows,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    ).hexdigest()
    baseline_real = {"cpu": 2.0, "memory": 4.0, "io": 8.0}
    predicted = dict(baseline_real)
    predicted[target] *= predicted_multiplier
    metadata = {
        "resource_estimation_mode": "ratio_scale",
        "cost_parameter_free_features": True,
        "qps_used_for_inner_scoring": qps_used_for_inner_scoring,
        "baseline_real": baseline_real,
        "baseline_features": {
            "cpu_total": 10.0,
            "memory_total": 20.0,
            "io_total": 30.0,
        },
        "current_features": {
            "cpu_total": 10.0,
            "memory_total": 20.0,
            "io_total": 30.0,
        },
        "feature_ratios": {"cpu": 1.0, "memory": 1.0, "io": 1.0},
        "feature_ratios_unclipped": {
            "cpu": 1.0,
            "memory": 1.0,
            "io": 1.0,
        },
        "ratio_clip_by_resource": {
            "cpu": [0.05, 20.0],
            "memory": [0.05, 20.0],
            "io": [0.05, 20.0],
        },
        "workload_plan_fingerprint": fingerprint,
        "baseline_plan_fingerprint": fingerprint,
        "query_features": [
            {
                "queryid": "q1",
                "plan_fingerprint": query_fingerprint,
            }
        ],
    }
    return {
        **metadata,
        "ratio_scale_direction_anchor": {
            "measured_resource": baseline_real,
            "predicted_resource": predicted,
            "capability": {"cpu": 16.0, "memory": 32.0, "io": 64.0},
            "measurement_protocol": {
                "correctness_passed": correctness_passed,
                "stable": stable,
                "point_id": "baseline-1",
            },
            "estimation_metadata": metadata,
        },
    }


STABILITY_CONTRACT_SHA256 = (
    "4b90d0126806ac16031fafbfe905d3aa3d693ddf28b64717eb348fb0911d4692"
)


def _sealed_ratio_runtime_stats(
    temp_root: Path,
    *,
    schema: str,
    mutate_summary=None,
    protocol_overrides: Optional[dict] = None,
    workload_exit_overrides: Optional[dict] = None,
) -> dict:
    """Create one current-point, SHA-bound synthetic measurement payload."""
    point_root = temp_root / f"point_{schema}"
    point_root.mkdir()
    formal_samples_path = point_root / "resource_samples.jsonl"
    formal_samples_path.write_text(
        "".join(
            json.dumps({"sample_index": index}) + "\n"
            for index in range(180)
        ),
        encoding="utf-8",
    )

    if schema == "tpcc":
        native_log = point_root / "measurement" / "native.log"
        native_log.parent.mkdir()
        native_log.write_text("benchmark completed\n", encoding="utf-8")
        runtime_root = point_root / "measurement" / "benchbase_runtime"
        runtime_root.mkdir()
        counter_samples_path = point_root / "database_counter_samples.jsonl"
        counter_samples_path.write_text(
            "".join(
                json.dumps(
                    {
                        "calls": 1000 + index,
                        "transactions": 100 + index,
                        "counter_reset": False,
                    }
                )
                + "\n"
                for index in range(170)
            ),
            encoding="utf-8",
        )
        summary = {
            "schema_version": 1,
            "status": "complete",
            "workload_returncode": -15,
            "intentionally_terminated_after_stability": True,
            "native_log": str(native_log),
            "native_log_sha256": hashlib.sha256(
                native_log.read_bytes()
            ).hexdigest(),
            "native_sql_exception_occurrences": 0,
            "database_counter_continuity": {
                "passed": True,
                "error_count": 0,
                "counter_reset_count": 0,
                "reconnect_count": 1,
                "last_error": None,
                "samples_path": str(counter_samples_path),
                "errors_path": None,
            },
            "worker_cleanup": {
                "fully_released": True,
                "counter_thread_stopped": True,
                "matched_pids": [123],
                "remaining_pids": [],
                "runtime_root": str(runtime_root),
            },
            "measurement_assessment": {
                "stable": True,
                "complete_window_count": 6,
            },
            "stability_target_resource": "cpu",
            "window_mean_stability": {
                "mode": "six_consecutive_30s_window_means_common_cv",
                "metric_fields": ["cpu_utilization"],
                "required_window_count": 6,
                "minimum_elapsed_seconds": 180,
                "stable": True,
                "groups": [
                    {
                        "stable": True,
                        "per_window_coverage_passed": True,
                        "cumulative_coverage": 1.0,
                        "field_checks": [
                            {
                                "field": "cpu_utilization",
                                "window_means": [0.5] * 6,
                                "coefficient_of_variation": 0.0,
                                "limit": 0.1,
                                "passed": True,
                            }
                        ],
                    }
                ],
            },
        }
        workload_contract = "tpcc_sensitive_stable_summary_v2"
        summary_schema = "tpcc_sensitive_summary_v1"
    elif schema == "tpch":
        summary = {
            "schema_version": 1,
            "status": "complete",
            "successful_queries": 100,
            "failed_queries": 0,
            "measurement_assessment": {
                "stable": True,
                "complete_window_count": 6,
                "consecutive_stable_windows": 6,
                "aggregate_sample_coverage": 1.0,
                "cross_window_resource_stability": {
                    "stable": True,
                    "stability_metric_fields": ["memory_utilization"],
                },
            },
            "worker_cleanup": {
                "controller_thread_stopped": True,
                "remaining_benchmark_backend_count": 0,
                "database_backends_fully_released": True,
            },
            "db_qps_counter": {
                "valid": False,
                "audit_only": True,
                "affects_workload_correctness": False,
            },
            "stability_target_resource": "memory",
        }
        workload_contract = "pg_weighted_sql_stable_summary_v2"
        summary_schema = "pg_weighted_sql_summary_v1"
    else:
        raise ValueError(schema)

    if mutate_summary is not None:
        mutate_summary(summary)
    summary_path = point_root / "summary.json"
    summary_bytes = (
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    summary_path.write_bytes(summary_bytes)
    protocol = {
        "correctness_passed": True,
        "stable": True,
        "workload_contract": workload_contract,
        "summary_schema": summary_schema,
        "point_id": point_root.name,
        "minimum_measurement_seconds": 180,
        "stability_contract_sha256": STABILITY_CONTRACT_SHA256,
        "summary_path": str(summary_path),
        "summary_sha256": hashlib.sha256(summary_bytes).hexdigest(),
        "runner_sha256": "a" * 64,
        "qps_used_for_inner_scoring": False,
    }
    protocol.update(protocol_overrides or {})
    workload_exit = {
        "returncode": 0,
        "stable_structured_measurement": True,
        "algorithmic_gate_rejection": False,
        "consumes_technical_failure_budget": False,
    }
    workload_exit.update(workload_exit_overrides or {})
    return {
        "candidate_measurement_protocol": protocol,
        "formal_pg_resource": {
            "sample_count": 180,
            "samples_path": str(formal_samples_path),
        },
        "workload_output": json.dumps(
            summary, ensure_ascii=False, sort_keys=True
        ),
        "workload_exit": workload_exit,
    }


class EstimatorDirectionGateTest(unittest.TestCase):
    def test_derives_tpcc_ratio_scale_baseline_correctness(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            protocol = _derive_ratio_scale_baseline_measurement_protocol(
                _sealed_ratio_runtime_stats(
                    Path(temp_dir),
                    schema="tpcc",
                )
            )

        self.assertTrue(protocol["correctness_passed"])
        self.assertEqual(
            protocol["correctness_evidence"]["schema"],
            "tpcc_sensitive_summary_v1",
        )
        self.assertFalse(
            protocol["correctness_evidence"]["qps_used_for_inner_scoring"]
        )

    def test_derives_tpch_ratio_scale_baseline_correctness(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            protocol = _derive_ratio_scale_baseline_measurement_protocol(
                _sealed_ratio_runtime_stats(
                    Path(temp_dir),
                    schema="tpch",
                )
            )

        self.assertTrue(protocol["correctness_passed"])
        self.assertEqual(
            protocol["correctness_evidence"]["schema"],
            "pg_weighted_sql_summary_v1",
        )

    def test_ratio_scale_correctness_derivation_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            protocol = _derive_ratio_scale_baseline_measurement_protocol(
                _sealed_ratio_runtime_stats(
                    Path(temp_dir),
                    schema="tpch",
                    mutate_summary=lambda summary: summary.update(
                        {"failed_queries": 1}
                    ),
                )
            )

        self.assertFalse(protocol["correctness_passed"])
        self.assertIn(
            "failed_check:failed_query_count_zero",
            protocol["correctness_evidence"]["reasons"],
        )

    def test_explicit_true_does_not_bypass_failed_workload_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            protocol = _derive_ratio_scale_baseline_measurement_protocol(
                _sealed_ratio_runtime_stats(
                    Path(temp_dir),
                    schema="tpch",
                    workload_exit_overrides={
                        "returncode": 2,
                        "consumes_technical_failure_budget": True,
                    },
                )
            )

        self.assertFalse(protocol["correctness_passed"])
        self.assertIn(
            "failed_check:workload_returncode_zero",
            protocol["correctness_evidence"]["reasons"],
        )
        self.assertIn(
            "failed_check:not_technical_failure",
            protocol["correctness_evidence"]["reasons"],
        )

    def test_rejects_summary_sha_or_point_identity_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            protocol = _derive_ratio_scale_baseline_measurement_protocol(
                _sealed_ratio_runtime_stats(
                    Path(temp_dir),
                    schema="tpch",
                    protocol_overrides={
                        "summary_sha256": "0" * 64,
                        "point_id": "stale_point",
                    },
                )
            )

        self.assertFalse(protocol["correctness_passed"])
        self.assertIn(
            "failed_check:summary_sha256_matches_marker",
            protocol["correctness_evidence"]["reasons"],
        )
        self.assertIn(
            "failed_check:point_id_matches_summary_parent",
            protocol["correctness_evidence"]["reasons"],
        )

    def test_rejects_tpcc_sql_exception(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            protocol = _derive_ratio_scale_baseline_measurement_protocol(
                _sealed_ratio_runtime_stats(
                    Path(temp_dir),
                    schema="tpcc",
                    mutate_summary=lambda summary: summary.update(
                        {"native_sql_exception_occurrences": 1}
                    ),
                )
            )

        self.assertFalse(protocol["correctness_passed"])
        self.assertIn(
            "failed_check:native_sql_exception_count_zero",
            protocol["correctness_evidence"]["reasons"],
        )

    def test_rejects_tpcc_counter_field_contradiction(self) -> None:
        def mutate(summary: dict) -> None:
            summary["database_counter_continuity"]["error_count"] = 1

        with tempfile.TemporaryDirectory() as temp_dir:
            protocol = _derive_ratio_scale_baseline_measurement_protocol(
                _sealed_ratio_runtime_stats(
                    Path(temp_dir),
                    schema="tpcc",
                    mutate_summary=mutate,
                )
            )

        self.assertFalse(protocol["correctness_passed"])
        self.assertIn(
            "failed_check:database_counter_continuity",
            protocol["correctness_evidence"]["reasons"],
        )

    def test_rejects_tpcc_remaining_worker(self) -> None:
        def mutate(summary: dict) -> None:
            summary["worker_cleanup"]["remaining_pids"] = [999]

        with tempfile.TemporaryDirectory() as temp_dir:
            protocol = _derive_ratio_scale_baseline_measurement_protocol(
                _sealed_ratio_runtime_stats(
                    Path(temp_dir),
                    schema="tpcc",
                    mutate_summary=mutate,
                )
            )

        self.assertFalse(protocol["correctness_passed"])
        self.assertIn(
            "failed_check:owned_workers_fully_released",
            protocol["correctness_evidence"]["reasons"],
        )

    def test_rejects_tpcc_incomplete_stability_window(self) -> None:
        def mutate(summary: dict) -> None:
            summary["window_mean_stability"]["groups"][0][
                "field_checks"
            ][0]["window_means"] = [0.5] * 5

        with tempfile.TemporaryDirectory() as temp_dir:
            protocol = _derive_ratio_scale_baseline_measurement_protocol(
                _sealed_ratio_runtime_stats(
                    Path(temp_dir),
                    schema="tpcc",
                    mutate_summary=mutate,
                )
            )

        self.assertFalse(protocol["correctness_passed"])
        self.assertIn(
            "failed_check:summary_stability_passed",
            protocol["correctness_evidence"]["reasons"],
        )

    def test_unwraps_plan_estimator_metadata_from_runtime_wrapper(self) -> None:
        estimator_metadata = {
            "physical_model_enabled": True,
            "physical_direction_support": {"memory": True},
            "resource_estimation_logs": [{"resource_type": "memory"}],
        }
        wrapper = {
            "effective_params": {"seq_page_cost": 1.0},
            "plan_resource_estimation": estimator_metadata,
        }

        self.assertEqual(
            _direction_gate_baseline_estimation_metadata(wrapper),
            estimator_metadata,
        )

    def test_keeps_direct_plan_estimator_metadata(self) -> None:
        estimator_metadata = {
            "physical_model_enabled": True,
            "physical_direction_support": {"memory": True},
        }

        self.assertEqual(
            _direction_gate_baseline_estimation_metadata(estimator_metadata),
            estimator_metadata,
        )

    def test_merges_current_retraining_sanity_for_reused_baseline(self) -> None:
        check = {
            "resource": "io",
            "passed": True,
            "source": "learned_model_after_baseline_anchor",
            "relative_error": 0.0,
            "baseline_anchor_calibration": {
                "applied": True,
                "delta_scale": 0.01,
            },
        }
        metadata = _direction_gate_baseline_estimation_metadata(
            {
                "workload_execution": {
                    "executed": False,
                    "mode": "reuse-outer-baseline-runtime",
                }
            },
            {
                "baseline_sanity_gate": {
                    "enabled": True,
                    "passed": True,
                    "checks": [check],
                }
            },
        )

        self.assertEqual(
            metadata["baseline_sanity_gate_source"],
            "current_run_resource_model_retraining",
        )
        summary = validate_estimator_direction_gate(
            target_resource="io",
            baseline_correction_state={
                "enabled": True,
                "completed": True,
                "success": True,
                "exempt_resources": ["io"],
                "scales": {"io": 1.0},
            },
            baseline_estimation_metadata=metadata,
            gate_cfg={"max_baseline_relative_error": 0.5},
        )
        self.assertTrue(summary["valid"])
        self.assertEqual(summary["reasons"], [])

    def test_nested_physical_memory_anchor_passes_direction_gate(self) -> None:
        wrapper = {
            "plan_resource_estimation": {
                "physical_model_enabled": True,
                "physical_direction_support": {"memory": True},
                "resource_estimation_logs": [
                    {
                        "resource_type": "memory",
                        "calibration_breakdown": {
                            "physical_delta_anchor": {
                                "applied": True,
                                "source": "first_measured_baseline_delta_anchor",
                                "baseline_actual": 2.5,
                            }
                        },
                    }
                ],
            }
        }
        summary = validate_estimator_direction_gate(
            target_resource="memory",
            baseline_correction_state={
                "enabled": True,
                "completed": True,
                "success": True,
                "scales": {"memory": 1.0},
            },
            baseline_estimation_metadata=(
                _direction_gate_baseline_estimation_metadata(wrapper)
            ),
            gate_cfg={
                "require_baseline_sanity": True,
                "require_calibrated_anchor": True,
                "require_baseline_correction_success": True,
                "max_baseline_relative_error": 0.5,
            },
        )

        self.assertTrue(summary["valid"])
        self.assertEqual(summary["reasons"], [])
        self.assertTrue(
            summary["checks"]["calibrated_anchor"][
                "physical_delta_anchor_valid"
            ]
        )

    def test_ratio_scale_stable_measured_anchor_passes_without_learned_sanity(
        self,
    ) -> None:
        summary = validate_estimator_direction_gate(
            target_resource="memory",
            baseline_correction_state={
                "enabled": True,
                "completed": True,
                "success": True,
                "scales": {"memory": 4.5},
                "max_scale": 1000.0,
            },
            baseline_estimation_metadata=_ratio_scale_direction_metadata(),
            gate_cfg={
                "require_baseline_sanity": True,
                "require_calibrated_anchor": True,
                "require_baseline_correction_success": True,
                "max_baseline_relative_error": 0.5,
            },
        )

        self.assertTrue(summary["valid"])
        self.assertEqual(summary["reasons"], [])
        self.assertTrue(summary["checks"]["ratio_scale_anchor"]["valid"])
        self.assertEqual(
            summary["checks"]["baseline_relative_error"]["value"],
            0.0,
        )
        self.assertTrue(
            summary["checks"]["calibrated_anchor"][
                "ratio_scale_anchor_valid"
            ]
        )

    def test_successful_correction_alone_does_not_bypass_missing_ratio_evidence(
        self,
    ) -> None:
        summary = validate_estimator_direction_gate(
            target_resource="memory",
            baseline_correction_state={
                "enabled": True,
                "completed": True,
                "success": True,
                "scales": {"memory": 4.5},
            },
            baseline_estimation_metadata={},
        )

        self.assertFalse(summary["valid"])
        self.assertIn(
            "target_baseline_sanity_missing_or_disabled",
            summary["reasons"],
        )
        self.assertIn(
            "target_baseline_relative_error_missing_or_nonfinite",
            summary["reasons"],
        )

    def test_ratio_scale_anchor_mismatch_respects_relative_error_limit(
        self,
    ) -> None:
        summary = validate_estimator_direction_gate(
            target_resource="memory",
            baseline_correction_state={
                "enabled": True,
                "completed": True,
                "success": True,
                "scales": {"memory": 4.5},
            },
            baseline_estimation_metadata=_ratio_scale_direction_metadata(
                predicted_multiplier=1.5,
            ),
            gate_cfg={"max_baseline_relative_error": 0.5},
        )

        self.assertFalse(summary["valid"])
        self.assertIn(
            "target_baseline_sanity_missing_or_disabled",
            summary["reasons"],
        )
        self.assertIn(
            "target_baseline_relative_error_missing_or_nonfinite",
            summary["reasons"],
        )
        self.assertIn(
            "ratio_scale_predicted_resource_formula_mismatch",
            summary["checks"]["ratio_scale_anchor"]["validation_errors"],
        )

    def test_ratio_scale_unstable_or_qps_scored_anchor_fails_closed(
        self,
    ) -> None:
        for metadata, expected_error in (
            (
                _ratio_scale_direction_metadata(stable=False),
                "ratio_scale_baseline_stability_missing_or_failed",
            ),
            (
                _ratio_scale_direction_metadata(
                    qps_used_for_inner_scoring=True
                ),
                "ratio_scale_qps_used_for_inner_scoring_not_false",
            ),
        ):
            with self.subTest(expected_error=expected_error):
                summary = validate_estimator_direction_gate(
                    target_resource="memory",
                    baseline_correction_state={
                        "enabled": True,
                        "completed": True,
                        "success": True,
                        "scales": {"memory": 4.5},
                    },
                    baseline_estimation_metadata=metadata,
                )
                self.assertFalse(summary["valid"])
                self.assertIn(
                    expected_error,
                    summary["checks"]["ratio_scale_anchor"][
                        "validation_errors"
                    ],
                )

    def test_direction_gate_config_rejects_nonpositive_error_limit(self) -> None:
        with self.assertRaisesRegex(ValueError, "max_baseline_relative_error"):
            validate_estimator_direction_gate_config(
                {"max_baseline_relative_error": 0.0}
            )

    def test_baseline_sanity_rejects_nearly_one_hundred_percent_error(self) -> None:
        summary = validate_resource_model_baseline_sanity_gate(
            baseline_actual_normalized={"cpu": 3.46429384998612},
            baseline_estimated_normalized={"cpu": 4.626065820524206e-08},
            baseline_estimation_metadata={
                "resource_breakdown": {
                    "cpu": {
                        "calibration_breakdown": {
                            "learned_model": {},
                            "learned_model_fallback": {
                                "fallback_to_plan_formula": True,
                                "fallback_reason": (
                                    "learned_cpu_anchor_delta_scale_out_of_bounds"
                                ),
                            },
                        }
                    }
                }
            },
            gate_cfg={
                "enabled": True,
                "required_resources": ["cpu"],
                "max_relative_error": 1.0,
                "threshold_epsilon": 1e-6,
            },
        )

        self.assertFalse(summary["passed"])
        self.assertIn("baseline_relative_error", summary["failures"][0]["reasons"][0])

    def test_valid_learned_anchor_allows_direction(self) -> None:
        check = {
            "resource": "cpu",
            "passed": True,
            "source": "learned_model_after_baseline_anchor",
            "relative_error": 0.05,
            "max_relative_error": 0.25,
            "baseline_anchor_calibration": {"applied": True, "delta_scale": 2.0},
        }
        summary = validate_estimator_direction_gate(
            target_resource="cpu",
            baseline_correction_state={
                "enabled": True,
                "completed": False,
                "success": False,
                "exempt_resources": ["cpu"],
                "scales": {"cpu": 1.0},
                "max_scale": 1000.0,
            },
            baseline_estimation_metadata=_sanity_metadata(check),
        )

        self.assertTrue(summary["valid"])
        self.assertEqual(summary["reasons"], [])

    def test_failed_saturated_fallback_blocks_direction(self) -> None:
        check = {
            "resource": "cpu",
            "passed": True,
            "source": "plan_formula_fallback",
            "fallback_reason": "learned_cpu_anchor_delta_scale_out_of_bounds",
            "relative_error": 0.99999998,
            "max_relative_error": 1.0,
            "baseline_anchor_calibration": None,
        }
        summary = validate_estimator_direction_gate(
            target_resource="cpu",
            baseline_correction_state={
                "enabled": True,
                "completed": True,
                "success": False,
                "round_count": 5,
                "max_rounds": 5,
                "skip_reason": "max_rounds_reached",
                "scales": {"cpu": 1000.0},
                "max_scale": 1000.0,
            },
            baseline_estimation_metadata=_sanity_metadata(check),
            gate_cfg={"max_baseline_relative_error": 1.0},
        )

        self.assertFalse(summary["valid"])
        self.assertIn("baseline_correction_failed", summary["reasons"])
        self.assertIn("baseline_correction_rounds_exhausted", summary["reasons"])
        self.assertIn("baseline_correction_saturated", summary["reasons"])
        self.assertIn("fallback_absolute_scale_invalid", summary["reasons"])

    def test_online_correction_saturation_blocks_next_round(self) -> None:
        check = {
            "resource": "cpu",
            "passed": True,
            "source": "learned_model_after_baseline_anchor",
            "relative_error": 0.05,
            "max_relative_error": 0.25,
            "baseline_anchor_calibration": {"applied": True, "delta_scale": 2.0},
        }
        summary = validate_estimator_direction_gate(
            target_resource="cpu",
            baseline_correction_state={
                "enabled": True,
                "completed": True,
                "success": True,
                "exempt_resources": ["cpu"],
                "online_correction_saturated_resources": ["cpu"],
            },
            baseline_estimation_metadata=_sanity_metadata(check),
        )

        self.assertFalse(summary["valid"])
        self.assertIn("online_correction_saturated", summary["reasons"])

    def test_formal_subset_path_wires_gate_before_candidate_generation(self) -> None:
        source = (ROOT / "parameter_optimizer.py").read_text(encoding="utf-8")
        gate_index = source.index('"event": "estimator_direction_invalid"')
        subset_index = source.index("optimize_all_experience_table_subsets(", gate_index)

        self.assertLess(gate_index, subset_index)
        self.assertIn('"candidate_generation_blocked": True', source)
        self.assertIn('"real_replay_blocked": True', source)

    def test_sha_pinned_probe_only_replay_direction_override(self) -> None:
        artifact = {
            "artifact_type": "pg_real_replay_direction_training_dataset_v1",
            "same_incumbent_scope_decisions": [
                {
                    "scope_id": "scope-1",
                    "target_resource": "cpu",
                    "decision": {
                        "runtime_policy": {
                            "action": "reverse_probe",
                            "probe_only": True,
                            "acceptance_proven": False,
                            "outer_acceptance_unchanged": True,
                            "outer_shrink_forbidden_until_real_acceptance": True,
                            "eta_multiplier": 0.2,
                            "direction_override_log_vector": {"p": -0.3},
                        }
                    },
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifact_path = root / "direction.json"
            payload = json.dumps(artifact).encode("utf-8")
            artifact_path.write_bytes(payload)
            config_path = root / "formal.json"
            config_path.write_text("{}", encoding="utf-8")
            result = load_replay_direction_calibration_override(
                {
                    "enabled": True,
                    "artifact_path": "direction.json",
                    "artifact_sha256": hashlib.sha256(payload).hexdigest(),
                    "scope_id": "scope-1",
                },
                config_path=str(config_path),
                active_params=["p"],
                target_resources=["cpu"],
            )

        self.assertTrue(result["enabled"])
        self.assertEqual(result["direction_override_log_vector"], {"p": -0.3})
        self.assertEqual(result["eta_multiplier"], 0.2)


if __name__ == "__main__":
    unittest.main()
