#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from parameter_optimizer import _baseline_only_measurement_issues, _re_evaluate_single_bottleneck_gate
from shell.run_bonect_phase2_none_delta_calibration import measurement_issues


def test_baseline_only_measurement_issues() -> None:
    """Test _baseline_only_measurement_issues with all four required scenarios."""
    normalized = {"cpu": 18.0, "memory": 4.5, "io": 10.0}
    utilization = {"cpu": 22.0, "memory": 21.0, "io": 28.0}

    # 1. valid TP/mixed with positive TPS passes (default benchmark_mode="tp_mixed")
    assert not _baseline_only_measurement_issues(
        qps=400.0,
        tps=100.0,
        resource_normalized=normalized,
        resource_utilization_pct=utilization,
        benchmark={"xp_qps": "20.0"},
        collected_sql=["select 1"],
    ), "TP/mixed with positive TPS should pass"

    # 2. valid AP-only with positive qps and tps=0 passes when benchmark_mode="ap_only"
    ap_ok = _baseline_only_measurement_issues(
        qps=0.82,
        tps=None,
        resource_normalized=normalized,
        resource_utilization_pct=utilization,
        benchmark={"ap_qps": "0.82"},
        collected_sql=["select 1"],
        benchmark_mode="ap_only",
    )
    assert ap_ok == [], f"AP-only with positive QPS and absent TPS should pass, got {ap_ok}"

    # 3. valid AP-only with positive qps and tps=0 passes (explicit benchmark_mode="ap_only")
    ap_ok_zero_tps = _baseline_only_measurement_issues(
        qps=0.82,
        tps=0.0,
        resource_normalized=normalized,
        resource_utilization_pct=utilization,
        benchmark={"ap_qps": "0.82"},
        collected_sql=["select 1"],
        benchmark_mode="ap_only",
    )
    assert ap_ok_zero_tps == [], f"AP-only with positive QPS and tps=0 should pass, got {ap_ok_zero_tps}"

    # 4. missing qps fails even in AP-only
    ap_no_qps = _baseline_only_measurement_issues(
        qps=None,
        tps=None,
        resource_normalized=normalized,
        resource_utilization_pct=utilization,
        benchmark={"ap_qps": "0.82"},
        collected_sql=["select 1"],
        benchmark_mode="ap_only",
    )
    assert "db_qps" in ap_no_qps, f"AP-only without QPS should fail, got {ap_no_qps}"

    # 5. non-AP with missing/zero TPS still fails (default benchmark_mode="tp_mixed")
    no_tps_issues = _baseline_only_measurement_issues(
        qps=400.0,
        tps=None,
        resource_normalized=normalized,
        resource_utilization_pct=utilization,
        benchmark={"xp_qps": "20.0"},
        collected_sql=["select 1"],
    )
    assert "db_tps" in no_tps_issues, f"TP/mixed without TPS should fail, got {no_tps_issues}"

    # 6. non-AP with tps=0 still fails
    zero_tps_issues = _baseline_only_measurement_issues(
        qps=400.0,
        tps=0.0,
        resource_normalized=normalized,
        resource_utilization_pct=utilization,
        benchmark={"xp_qps": "20.0"},
        collected_sql=["select 1"],
    )
    assert "db_tps" in zero_tps_issues, f"TP/mixed with tps=0 should fail, got {zero_tps_issues}"

    # 7. missing resources/benchmark/sql still fails (never weakened)
    missing_all = _baseline_only_measurement_issues(
        qps=None,
        tps=None,
        resource_normalized={},
        resource_utilization_pct={},
        benchmark={},
        collected_sql=[],
    )
    assert "db_qps" in missing_all
    assert "resource_normalized.io" in missing_all
    assert "benchmark" in missing_all
    assert "collected_sql" in missing_all

    # 8. same with benchmark_mode="ap_only" — db_qps still required, resource/benchmark/sql still required
    missing_ap = _baseline_only_measurement_issues(
        qps=None,
        tps=None,
        resource_normalized={},
        resource_utilization_pct={},
        benchmark={},
        collected_sql=[],
        benchmark_mode="ap_only",
    )
    assert "db_qps" in missing_ap, f"AP-only without QPS should still fail, got {missing_ap}"
    # db_tps should NOT appear in AP-only mode
    assert "db_tps" not in missing_ap, f"AP-only should not report db_tps, got {missing_ap}"
    assert "resource_normalized.io" in missing_ap
    assert "benchmark" in missing_ap
    assert "collected_sql" in missing_ap

    print("PASS _baseline_only_measurement_issues (8 subtests)", flush=True)


def test_measurement_issues_from_calibration() -> None:
    """Test measurement_issues from run_bonect_phase2_none_delta_calibration."""
    normalized = {"cpu": 18.0, "memory": 4.5, "io": 10.0}
    utilization = {"cpu": 22.0, "memory": 21.0, "io": 28.0}

    complete = {
        "db_qps": 400.0,
        "db_xact_tps": 100.0,
        "resource_normalized": normalized,
        "utilization_pct": utilization,
        "resource_sources": {
            "cpu_source": "vm_pg_proc_cpu",
            "memory_source": "vm_pg_proc_rss",
            "io_source": "vm_disk_device",
            "io_disk_devices": "vda",
        },
        "stable_sample_count": {"cpu": 90, "memory": 90, "io": 90},
        "benchmark": {"xp_qps": "20.0"},
        "plan_capture_count": 4,
        "all_sql_count": 8,
        "plan_feature_count": 10,
    }
    assert not measurement_issues(complete)
    incomplete = dict(complete)
    incomplete.update(
        {
            "db_qps": None,
            "resource_normalized": {},
            "resource_sources": {},
            "stable_sample_count": {},
            "plan_capture_count": None,
        }
    )
    calibration_issues = measurement_issues(incomplete)
    assert "db_qps" in calibration_issues
    assert "resource_normalized.io" in calibration_issues
    assert "resource_sources.io_source" in calibration_issues
    assert "stable_sample_count.io" in calibration_issues
    assert "plan_capture_count" in calibration_issues
    print("PASS measurement_issues from calibration", flush=True)


def test_re_evaluate_single_bottleneck_gate() -> None:
    """Test _re_evaluate_single_bottleneck_gate with raw vs calibrated scenarios."""

    # Scenario A: raw-below-80 then calibrated-above-80 passes
    util_raw = {"cpu": 40.0, "memory": 61.459, "io": 30.0}
    util_calibrated = {"cpu": 40.0, "memory": 90.0, "io": 30.0}
    strong_cfg = {
        "bottleneck_initial_target_min_util_pct": 80.0,
        "bottleneck_initial_non_target_min_util_pct": 30.0,
        "bottleneck_initial_non_target_max_util_pct": 50.0,
    }

    raw_passed, raw_checks = _re_evaluate_single_bottleneck_gate(
        util_raw, "memory", strong_cfg,
    )
    assert not raw_passed, "Raw 61.459% should fail gate (<80%)"
    target_check = [c for c in raw_checks if c["resource"] == "memory"][0]
    assert not target_check["passed"]

    calibrated_passed, calibrated_checks = _re_evaluate_single_bottleneck_gate(
        util_calibrated, "memory", strong_cfg,
    )
    assert calibrated_passed, "Calibrated 90% should pass gate (>80%)"
    target_check = [c for c in calibrated_checks if c["resource"] == "memory"][0]
    assert target_check["passed"]

    # Scenario B: calibrated target still below 80 fails
    util_still_low = {"cpu": 40.0, "memory": 70.0, "io": 30.0}
    still_low_passed, _ = _re_evaluate_single_bottleneck_gate(
        util_still_low, "memory", strong_cfg,
    )
    assert not still_low_passed, "Calibrated 70% should still fail (<80%)"

    # Scenario C: No calibration (same values as raw) gate still fails
    no_cal_passed, _ = _re_evaluate_single_bottleneck_gate(
        util_raw, "memory", strong_cfg,
    )
    assert not no_cal_passed, "No calibration should keep raw gate failure"

    # Scenario D: raw already passes without calibration
    util_already_high = {"cpu": 40.0, "memory": 85.0, "io": 30.0}
    already_passed, _ = _re_evaluate_single_bottleneck_gate(
        util_already_high, "memory", strong_cfg,
    )
    assert already_passed, "Raw 85% should pass gate without calibration"

    # Scenario E: IO target with calibration
    util_io_calibrated = {"cpu": 30.0, "memory": 50.0, "io": 92.0}
    io_passed, io_checks = _re_evaluate_single_bottleneck_gate(
        util_io_calibrated, "io", strong_cfg,
    )
    assert io_passed, "Calibrated IO 92% should pass gate"
    io_check = [c for c in io_checks if c["resource"] == "io"][0]
    assert io_check["passed"]
    assert io_check["role"] == "target"
    np_checks = [c for c in io_checks if c["role"] == "non_target"]
    assert all(c["passed"] for c in np_checks), "Non-target resources should pass"

    # Scenario F: non-target exceeds max after calibration
    util_non_target_overload = {"cpu": 85.0, "memory": 90.0, "io": 40.0}
    overload_passed, overload_checks = _re_evaluate_single_bottleneck_gate(
        util_non_target_overload, "memory", strong_cfg,
    )
    assert not overload_passed, "Non-target CPU 85% > 50 should fail"
    cpu_check = [c for c in overload_checks if c["resource"] == "cpu"][0]
    assert not cpu_check["passed"]

    # Scenario G: default thresholds when strong_cfg empty
    default_passed, _ = _re_evaluate_single_bottleneck_gate(
        {"cpu": 30.0, "memory": 82.0, "io": 50.0}, "memory", {},
    )
    assert default_passed, "Should use default 80% target min"

    print("PASS _re_evaluate_single_bottleneck_gate (7 subtests)", flush=True)


def main() -> None:
    test_baseline_only_measurement_issues()
    test_measurement_issues_from_calibration()
    test_re_evaluate_single_bottleneck_gate()
    print("PASS all baseline-only measurement guard tests", flush=True)


if __name__ == "__main__":
    main()
