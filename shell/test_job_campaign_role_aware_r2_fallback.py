"""Fail-closed tests for the PG5 JOB campaign role-aware R2 fallback."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
from typing import Any, Dict


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from parameter_optimizer import validate_resource_model_quality_gate  # noqa: E402


POLICY_PATH = (
    ROOT
    / "launch_packages/pg5_common_outer_bgallowed_20260722_v1/job_node02/"
    "job_role_aware_r2_fallback_policy.json"
)


def policy() -> Dict[str, Any]:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def metrics(
    *,
    r2: float,
    rel_p95: float,
    rel_max: float,
    label_cv: float,
    label_range_over_mean: float,
    abs_max_pp: float,
) -> Dict[str, Any]:
    return {
        "train": {"sample_count": 9},
        "test": {
            "sample_count": 9,
            "r2": r2,
            "relative_error_p95": rel_p95,
            "relative_error_max": rel_max,
            "abs_error_max": 1.0,
            "abs_error_max_utilization_pp": abs_max_pp,
            "label_distribution": {
                "coefficient_of_variation": label_cv,
                "range_over_abs_mean": label_range_over_mean,
            },
        },
        "train_samples": 9,
        "test_samples": 9,
        "split_strategy": "grouped_leave_one_unique_parameter_vector_out",
        "outer_fold_count": 9,
    }


def retrain_result() -> Dict[str, Any]:
    cpu = metrics(
        r2=-0.266097,
        rel_p95=0.057529,
        rel_max=0.071266,
        label_cv=0.025396,
        label_range_over_mean=0.076624,
        abs_max_pp=2.672345,
    )
    io = metrics(
        r2=-0.259549,
        rel_p95=0.084953,
        rel_max=0.108977,
        label_cv=0.036829,
        label_range_over_mean=0.115275,
        abs_max_pp=3.645533,
    )
    memory = metrics(
        r2=-0.745911,
        rel_p95=0.000185,
        rel_max=0.000192,
        label_cv=0.000097,
        label_range_over_mean=0.000286,
        abs_max_pp=0.016460,
    )
    return {
        "quality_gate_context": {
            "campaign_id": "pg5_common_outer_bgallowed_20260722_v1",
            "workload": "job",
            "target_resource": "memory",
            "unique_parameter_vector_count": 9,
        },
        "resources": {
            "cpu": {"JOB": cpu},
            "io": {"JOB": {"total": io}},
            "memory": {
                "status": "retrained",
                "source": "job_grouped_lovo_unique_parameter_vectors",
                "actual_memory_mb": 2560.95,
                "selected_model_set": "JOB",
                "updated_model_sets": {
                    "JOB": {
                        "metrics": memory,
                        "error_bound": {"sample_count": 9},
                    }
                },
            },
        },
    }


def gate_cfg() -> Dict[str, Any]:
    return {
        "enabled": True,
        "required_resources": ["cpu", "io", "memory"],
        "optional_resources": [],
        "min_test_samples": 5,
        "min_test_r2": 0.0,
        "enforce_test_r2": True,
        "max_test_relative_error_p95": None,
        "max_test_relative_error_max": None,
        "enforce_test_relative_error_p95": True,
        "enforce_test_relative_error_max": True,
        "selected_model_only": True,
        "workload_family": "CH",
        "io_hard_targets": ["total"],
        "target_resource": "memory",
        "r2_fallback": policy(),
    }


def evaluate(
    retrain: Dict[str, Any],
    cfg: Dict[str, Any] | None = None,
    *,
    include_runtime_metadata: bool = True,
) -> Dict[str, Any]:
    metadata = {
        "resource_breakdown": {
            resource: {
                "calibration_breakdown": {"learned_model": {"model_set": "JOB"}}
            }
            for resource in ("cpu", "io", "memory")
        }
    }
    return validate_resource_model_quality_gate(
        retrain,
        cfg or gate_cfg(),
        baseline_estimation_metadata=metadata if include_runtime_metadata else {},
    )


def assert_failed(summary: Dict[str, Any], needle: str) -> None:
    assert summary["passed"] is False, summary
    text = json.dumps(summary["failures"], sort_keys=True)
    assert needle in text, text


def test_exact_campaign_evidence_passes_without_relaxing_min_r2() -> None:
    summary = evaluate(retrain_result())
    assert summary["passed"] is True, summary["failures"]
    assert summary["thresholds"]["min_test_r2"] == 0.0
    checks = {item["resource"]: item for item in summary["checks"]}
    assert set(checks) == {"cpu", "io", "memory"}
    assert all(item["r2_fallback"]["eligible"] is True for item in checks.values())
    assert checks["memory"]["r2_fallback"]["role"] == "target"
    assert checks["cpu"]["r2_fallback"]["role"] == "non_target"


def test_target_resource_tamper_fails_closed() -> None:
    cfg = gate_cfg()
    cfg["target_resource"] = "cpu"
    assert_failed(evaluate(retrain_result(), cfg), "quality_gate_target_resource_mismatch")


def test_missing_label_distribution_fails_closed() -> None:
    value = retrain_result()
    del value["resources"]["memory"]["updated_model_sets"]["JOB"]["metrics"]["test"][
        "label_distribution"
    ]
    assert_failed(evaluate(value), "missing_label_cv")


def test_high_variance_target_fails_closed() -> None:
    value = retrain_result()
    value["resources"]["memory"]["updated_model_sets"]["JOB"]["metrics"]["test"][
        "label_distribution"
    ]["coefficient_of_variation"] = 0.01
    assert_failed(evaluate(value), "label_cv 0.01 > 0.001")


def test_non_target_tail_error_fails_closed() -> None:
    value = retrain_result()
    value["resources"]["io"]["JOB"]["total"]["test"]["relative_error_max"] = 0.16
    assert_failed(evaluate(value), "relative_error_max 0.16 > 0.15")


def test_wrong_campaign_fails_closed() -> None:
    value = retrain_result()
    value["quality_gate_context"]["campaign_id"] = "different_campaign"
    assert_failed(evaluate(value), "campaign_workload_or_target_scope_mismatch")


def test_non_grouped_split_fails_closed() -> None:
    value = retrain_result()
    value["resources"]["cpu"]["JOB"]["split_strategy"] = "random_80_20"
    assert_failed(evaluate(value), "split_strategy_is_not_grouped_lovo")


def test_eight_vectors_fail_closed() -> None:
    value = retrain_result()
    value["quality_gate_context"]["unique_parameter_vector_count"] = 8
    assert_failed(evaluate(value), "unique_parameter_vector_count 8 < 9")


def test_explicit_job_runtime_model_binding_survives_missing_cpu_metadata() -> None:
    cfg = gate_cfg()
    cfg["runtime_selected_model_sets"] = {
        "cpu": "JOB",
        "io": "JOB",
        "memory": "JOB",
    }
    summary = evaluate(
        retrain_result(),
        cfg,
        include_runtime_metadata=False,
    )
    assert summary["passed"] is True, summary
    assert summary["thresholds"]["runtime_selected_model_sets"] == {
        "cpu": "JOB",
        "io": "JOB",
        "memory": "JOB",
    }


def main() -> int:
    tests = [
        test_exact_campaign_evidence_passes_without_relaxing_min_r2,
        test_target_resource_tamper_fails_closed,
        test_missing_label_distribution_fails_closed,
        test_high_variance_target_fails_closed,
        test_non_target_tail_error_fails_closed,
        test_wrong_campaign_fails_closed,
        test_non_grouped_split_fails_closed,
        test_eight_vectors_fail_closed,
        test_explicit_job_runtime_model_binding_survives_missing_cpu_metadata,
    ]
    for test in tests:
        test()
        print(f"[PASS] {test.__name__}", flush=True)
    print(f"ALL_ROLE_AWARE_R2_FALLBACK_TESTS_PASSED={len(tests)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
