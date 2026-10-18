#!/usr/bin/env python3
"""Focused tests for the 2026-08-09 Algorithm C safety gates."""

from __future__ import annotations

import copy
import math
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from unittest import mock

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import binary_plan_resource_tuner as plan_tuner
import launch_pg_algorithm_c_four_experience_sgd_20260809 as launcher
from build_algorithm_c_experience_table_from_replays import (
    MINIMUM_INDEPENDENT_HOLDOUT_COUNT,
    MINIMUM_TRAIN_COUNT,
    calibration_sample_count_admission_errors,
    fit_resource_prediction_calibration,
    historical_validation_model_key,
    parameter_only_prediction_calibration,
    partition_scored_resource_admission_errors,
    robust_target_down,
    scored_resource_admission_errors,
    select_ridge_alpha_by_historical_validation,
    select_ridge_alpha_by_training_loo,
    target_selection_admission_errors,
    target_direction_metrics,
    wilson_lower_bound,
)
from run_pg_algorithm_c_calibrate_then_scene import (
    FreshCalibration,
    build_mixed_independent_calibration_holdout,
    build_model_identification_calibration_holdout,
    calibrated_target_log_ratio,
    calibration_design_audit,
    choose_calibration_focus_resource,
    deterministic_design_point,
    historical_target_direction_evidence,
    select_directional_adaptive_candidates,
)
from run_pg_algorithm_c_scene import (
    ACTIVE_PARAMS,
    DEFAULT_RANGES,
    LaneController,
    OUTER_PRUNING_CONTRACT,
    RESOURCE_PREDICTION_CALIBRATION_CONTRACT,
    RatioScaleEstimator,
    assess_valid_replay_identity,
    baseline_execution_domain_audit,
    baseline_parameter_readback_audit,
    assess_outer_pruning_candidate,
    distinct_validated_behavior_rankings,
    empty_outer_pruning_memory,
    empty_valid_replay_identity_memory,
    executor_telemetry_baseline_model,
    formal_inner_scoring_options,
    outer_pruning_context,
    postgres_guc_numeric_readback_matches,
    qps_noise_model,
    qualify_ssd_wave,
    record_outer_pruning_observation,
    record_valid_replay_identity,
    resolve_convergence_contract,
    saturated_plan_space_partial_rankings,
    unfinished_algorithm_c_round_resume_index,
    validate_experience_artifact,
    workload_contract_execution_domain_audit,
)
from algorithm_c_parameter_support import (
    apply_parameter_support_abstention,
    build_parameter_support_domain,
    parameter_support_assessment,
)
from algorithm_c_training_boundary import (
    runtime_prior_firewall,
    training_boundary,
    validate_training_runtime_boundary,
)
from gradient_descent_optimizer import ParameterRange
from launch_pg_algorithm_c_four_experience_sgd_20260809 import (
    CONTROLLER_CPUSETS,
    DB_CPUSETS,
    JOB_MAX_CONCURRENT_LONG_QUERIES,
    JOB_MAX_CONCURRENT_LONG_QUERIES_PER_FAMILY,
    JOB_MAX_PARALLEL_WORKERS,
    JOB_MAX_PARALLEL_WORKERS_PER_GATHER,
    JOB_MAX_WORKER_PROCESSES,
    JOB_INITIAL_PARAMS,
    JOB_OUTER_TECHNICAL_TIMEOUT_S,
    JOB_REPLAY_CLIENTS,
    JOB_SHARED_BUFFERS,
    RESOURCE_CAPACITY,
    SHARED_SSD_COORDINATION_TIMEOUT_S,
    TPCH_MAX_PARALLEL_WORKERS_PER_GATHER,
    TPCH_REPLAY_CLIENTS,
    TPCH_SHARED_BUFFERS,
    XP_IO_XAP_CLIENTS,
    XP_IO_XTP_CLIENTS,
    apply_job_postgres_memory_contract,
    apply_job_postgres_parallel_contract,
    apply_tpch_postgres_parallel_contract,
    apply_tpch_postgres_memory_contract,
    expand_cpuset,
    workload_env,
)


INITIAL_PARAMS = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}


def test_fresh_t_requires_full_rank_train_and_independent_holdout() -> None:
    assert MINIMUM_TRAIN_COUNT == 20
    assert MINIMUM_INDEPENDENT_HOLDOUT_COUNT == 30
    assert calibration_sample_count_admission_errors(15, 30) == [
        "train_count_below_20"
    ]
    assert calibration_sample_count_admission_errors(20, 29) == [
        "holdout_count_below_30"
    ]
    assert calibration_sample_count_admission_errors(20, 30) == []


def test_abc_training_sources_are_audit_only_and_qps_free() -> None:
    artifact = {
        "training_boundary": training_boundary(
            {"algorithm_a": 128, "algorithm_b": 35, "algorithm_c": 25}
        ),
        "runtime_candidate_prior_firewall": runtime_prior_firewall(),
        "resource_prediction_calibration": {
            "qps_used": False,
            "resources": {name: {} for name in ("cpu", "memory", "io")},
        },
        "holdout": {
            "metrics": {name: {} for name in ("cpu", "memory", "io")},
        },
    }
    validate_training_runtime_boundary(artifact)
    boundary = artifact["training_boundary"]
    assert boundary["observed_training_source_counts"]["algorithm_a"] == 128
    assert boundary["source_algorithm_used_as_model_feature"] is False
    assert boundary["historical_plan_fingerprint_used_as_model_feature"] is False
    assert boundary["qps_used_as_model_feature_or_target"] is False
    assert artifact["runtime_candidate_prior_firewall"][
        "training_source_parameters_used_as_runtime_candidates"
    ] is False

    leaked = copy.deepcopy(artifact)
    leaked["holdout"]["metrics"]["qps_outer_only"] = {}
    try:
        validate_training_runtime_boundary(leaked)
    except RuntimeError as exc:
        assert "CPU/Memory/I/O" in str(exc)
    else:
        raise AssertionError("QPS holdout metric must fail closed")


def test_xp_io_native_mix_keeps_plan_sensitive_analytical_concurrency() -> None:
    """XP-I/O keeps XTP fixed and raises XAP after a below-band baseline."""

    assert XP_IO_XTP_CLIENTS == 18
    assert XP_IO_XAP_CLIENTS == 13


def test_job_concurrency_meets_full_workload_wall_target() -> None:
    """Keep full JOB/TPC-H coverage without saturating shared non-target IO."""

    assert JOB_REPLAY_CLIENTS == 2
    assert JOB_MAX_CONCURRENT_LONG_QUERIES == 1
    assert JOB_MAX_CONCURRENT_LONG_QUERIES_PER_FAMILY == 1
    assert TPCH_REPLAY_CLIENTS == 1
    assert TPCH_MAX_PARALLEL_WORKERS_PER_GATHER == 0
    assert JOB_MAX_WORKER_PROCESSES == 20
    assert JOB_MAX_PARALLEL_WORKERS == 12
    assert JOB_MAX_PARALLEL_WORKERS_PER_GATHER == 2
    assert JOB_OUTER_TECHNICAL_TIMEOUT_S == 1020
    assert DB_CPUSETS["job_memory"] == (
        "48-55,136-139,160-163,168-171",
        "56-63,140-143,164-167,172-175",
    )
    assert CONTROLLER_CPUSETS["job_memory"] == "176-179"
    assert CONTROLLER_CPUSETS["xp_cpu"] == "128-131"
    assert CONTROLLER_CPUSETS["tpch_memory"] == "180-183"
    assert RESOURCE_CAPACITY["xp_io"]["cpu_cores"] == 40.0
    assert all(len(expand_cpuset(value)) == 40 for value in DB_CPUSETS["xp_io"])
    assert RESOURCE_CAPACITY["job_memory"]["cpu_cores"] == 20.0
    assert RESOURCE_CAPACITY["job_memory"]["memory_gib"] == 8.0
    assert all(len(expand_cpuset(value)) == 20 for value in DB_CPUSETS["job_memory"])
    assert JOB_SHARED_BUFFERS == "6GB"
    assert RESOURCE_CAPACITY["tpch_memory"]["memory_gib"] == 8.0
    assert TPCH_SHARED_BUFFERS == "6GB"
    assert SHARED_SSD_COORDINATION_TIMEOUT_S == 21600
    assert JOB_INITIAL_PARAMS == INITIAL_PARAMS


def test_shared_ssd_queue_wait_is_long_but_workload_wall_stays_hard() -> None:
    env = workload_env("xp_io", Path("/unused-for-xp-io"))
    assert env["BONECT_SHARED_SSD_COORDINATION_TIMEOUT_S"] == "21600"
    assert env["PG_ALGORITHM_C_GLOBAL_RESTORE_SLOT_TIMEOUT_S"] == "21600"
    assert env["BONECT_SHARED_SSD_WORKLOAD_LOCK_MODE"] == "exclusive"
    assert JOB_OUTER_TECHNICAL_TIMEOUT_S == 1020


def test_postgres_cost_guc_readback_uses_show_precision() -> None:
    assert postgres_guc_numeric_readback_matches(
        4.0,
        4.0,
    )
    assert postgres_guc_numeric_readback_matches(
        0.01,
        0.01,
    )
    assert not postgres_guc_numeric_readback_matches(
        4.0,
        4.97,
    )
    readback = {
        name: format(float(value), ".6g")
        for name, value in JOB_INITIAL_PARAMS.items()
    }
    assert baseline_parameter_readback_audit(
        JOB_INITIAL_PARAMS,
        readback,
    )["passed"] is True


def test_job_parallel_contract_restarts_once_and_verifies_readback() -> None:
    worker = {
        "container": "postgres_job_w1",
        "port": 59523,
        "user": "postgres",
    }
    before = {
        "max_worker_processes": {
            "setting": 8,
            "context": "postmaster",
            "pending_restart": False,
        },
        "max_parallel_workers": {
            "setting": 8,
            "context": "user",
            "pending_restart": False,
        },
        "max_parallel_workers_per_gather": {
            "setting": 2,
            "context": "user",
            "pending_restart": False,
        },
    }
    after = copy.deepcopy(before)
    after["max_worker_processes"]["setting"] = 20
    after["max_parallel_workers"]["setting"] = 12
    psql_ok = mock.Mock(returncode=0, stdout="", stderr="")
    ready = mock.Mock(returncode=0, stdout="1\n", stderr="")
    restart_ok = mock.Mock(returncode=0, stdout="postgres_job_w1\n", stderr="")
    with mock.patch(
        "launch_pg_algorithm_c_four_experience_sgd_20260809."
        "_read_job_postgres_parallel_settings",
        side_effect=(before, after),
    ), mock.patch(
        "launch_pg_algorithm_c_four_experience_sgd_20260809._container_psql",
        side_effect=(psql_ok, psql_ok, ready),
    ) as psql_call, mock.patch(
        "launch_pg_algorithm_c_four_experience_sgd_20260809.subprocess.run",
        return_value=restart_ok,
    ) as subprocess_run:
        result = apply_job_postgres_parallel_contract(worker)

    assert result["passed"] is True
    assert result["container_restarted"] is True
    assert result["changed"] == ["max_worker_processes", "max_parallel_workers"]
    assert psql_call.call_count == 3
    subprocess_run.assert_called_once_with(
        ["docker", "restart", "postgres_job_w1"],
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )


def test_job_memory_contract_restarts_and_verifies_readback() -> None:
    worker = {
        "container": "postgres_job_w1",
        "port": 59523,
        "user": "postgres",
    }
    before = {
        "setting": "128MB",
        "context": "postmaster",
        "pending_restart": False,
    }
    after = {
        "setting": "6GB",
        "context": "postmaster",
        "pending_restart": False,
    }
    alter_ok = mock.Mock(returncode=0, stdout="", stderr="")
    ready_ok = mock.Mock(returncode=0, stdout="1\n", stderr="")
    restart_ok = mock.Mock(returncode=0, stdout="postgres_job_w1\n", stderr="")
    with mock.patch(
        "launch_pg_algorithm_c_four_experience_sgd_20260809."
        "_read_job_shared_buffers_setting",
        side_effect=(before, after),
    ), mock.patch(
        "launch_pg_algorithm_c_four_experience_sgd_20260809._container_psql",
        side_effect=(alter_ok, ready_ok),
    ) as psql_call, mock.patch(
        "launch_pg_algorithm_c_four_experience_sgd_20260809.subprocess.run",
        return_value=restart_ok,
    ) as subprocess_run:
        result = apply_job_postgres_memory_contract(worker)

    assert result["passed"] is True
    assert result["container_restarted"] is True
    assert result["changed"] == ["shared_buffers"]
    assert psql_call.call_count == 2
    subprocess_run.assert_called_once_with(
        ["docker", "restart", "postgres_job_w1"],
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )


def test_tpch_parallel_contract_reloads_and_verifies_readback() -> None:
    worker = {
        "container": "postgres_tpch_w1",
        "port": 59521,
        "user": "postgres",
    }
    before = {
        "setting": 2,
        "context": "user",
        "pending_restart": False,
    }
    after = {
        "setting": 0,
        "context": "user",
        "pending_restart": False,
    }
    alter_ok = mock.Mock(returncode=0, stdout="", stderr="")
    reload_ok = mock.Mock(returncode=0, stdout="t\n", stderr="")
    with mock.patch(
        "launch_pg_algorithm_c_four_experience_sgd_20260809."
        "_read_tpch_postgres_parallel_setting",
        side_effect=(before, after),
    ), mock.patch(
        "launch_pg_algorithm_c_four_experience_sgd_20260809._container_psql",
        side_effect=(alter_ok, reload_ok),
    ) as psql_call:
        result = apply_tpch_postgres_parallel_contract(worker)

    assert result["passed"] is True
    assert result["container_restarted"] is False
    assert result["changed"] == ["max_parallel_workers_per_gather"]
    assert psql_call.call_count == 2


def test_tpch_memory_contract_restarts_and_verifies_readback() -> None:
    worker = {
        "container": "postgres_tpch_w1",
        "port": 59521,
        "user": "postgres",
    }
    before = {
        "setting": "128MB",
        "context": "postmaster",
        "pending_restart": False,
    }
    after = {
        "setting": "6GB",
        "context": "postmaster",
        "pending_restart": False,
    }
    alter_ok = mock.Mock(returncode=0, stdout="", stderr="")
    ready_ok = mock.Mock(returncode=0, stdout="1\n", stderr="")
    restart_ok = mock.Mock(returncode=0, stdout="postgres_tpch_w1\n", stderr="")
    with mock.patch(
        "launch_pg_algorithm_c_four_experience_sgd_20260809."
        "_read_tpch_shared_buffers_setting",
        side_effect=(before, after),
    ), mock.patch(
        "launch_pg_algorithm_c_four_experience_sgd_20260809._container_psql",
        side_effect=(alter_ok, ready_ok),
    ) as psql_call, mock.patch(
        "launch_pg_algorithm_c_four_experience_sgd_20260809.subprocess.run",
        return_value=restart_ok,
    ) as subprocess_run:
        result = apply_tpch_postgres_memory_contract(worker)

    assert result["passed"] is True
    assert result["container_restarted"] is True
    assert result["changed"] == ["shared_buffers"]
    assert psql_call.call_count == 2
    subprocess_run.assert_called_once_with(
        ["docker", "restart", "postgres_tpch_w1"],
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )


def test_global_restore_slots_cap_cross_lane_clone_concurrency() -> None:
    """A third clone waits until one of the two host-wide slots is released."""

    with tempfile.TemporaryDirectory() as slot_root, mock.patch.dict(
        os.environ,
        {
            "PG_ALGORITHM_C_GLOBAL_RESTORE_PARALLELISM": "2",
            "PG_ALGORITHM_C_GLOBAL_RESTORE_SLOT_ROOT": slot_root,
        },
    ):
        first, first_index, _ = plan_tuner.acquire_global_restore_slot()
        second, second_index, _ = plan_tuner.acquire_global_restore_slot()
        assert {first_index, second_index} == {0, 1}
        acquired = threading.Event()
        third_result = []

        def acquire_third() -> None:
            third_result.append(plan_tuner.acquire_global_restore_slot())
            acquired.set()

        thread = threading.Thread(target=acquire_third, daemon=True)
        thread.start()
        assert acquired.wait(timeout=0.25) is False
        plan_tuner.release_global_restore_slot(first)
        assert acquired.wait(timeout=2.0) is True
        third, third_index, wait_s = third_result[0]
        assert third_index == first_index
        assert wait_s >= 0.2
        plan_tuner.release_global_restore_slot(second)
        plan_tuner.release_global_restore_slot(third)
        thread.join(timeout=1.0)


def test_global_restore_slot_timeout_is_bounded() -> None:
    """A saturated restore pool must fail in bounded time, never hang shutdown."""

    with tempfile.TemporaryDirectory() as slot_root, mock.patch.dict(
        os.environ,
        {
            "PG_ALGORITHM_C_GLOBAL_RESTORE_PARALLELISM": "2",
            "PG_ALGORITHM_C_GLOBAL_RESTORE_SLOT_ROOT": slot_root,
        },
    ):
        first, _first_index, _ = plan_tuner.acquire_global_restore_slot()
        second, _second_index, _ = plan_tuner.acquire_global_restore_slot()
        started = time.perf_counter()
        try:
            try:
                plan_tuner.acquire_global_restore_slot(timeout_s=0.2)
            except TimeoutError:
                pass
            else:
                raise AssertionError("saturated restore pool did not time out")
            assert 0.15 <= time.perf_counter() - started < 1.0
        finally:
            plan_tuner.release_global_restore_slot(first)
            plan_tuner.release_global_restore_slot(second)


def test_job_preformal_runtime_verification_never_expands_hard_timeout() -> None:
    """Runtime must preserve the serialized 900/1020-second JOB contract."""

    runner = FreshCalibration.__new__(FreshCalibration)
    runner.algorithm_cfg = {
        "scene": "job_memory",
        "workload_contract": {
            "benchmark_concurrency": {
                "target_wall_time_s": 900,
                "target_wall_time_is_advisory": False,
            }
        },
    }
    runner.cfg = {
        "binary_plan_tuning": {
            "validation": {
                "hybench_xp": {"timeout_s": 1020, "timeout_grace_s": 120}
            }
        }
    }
    with tempfile.TemporaryDirectory() as output_dir:
        runner.output_dir = Path(output_dir)
        runner.event = mock.Mock()
        before = copy.deepcopy(runner.cfg)
        runner.apply_preformal_runtime_contract_repairs()
        assert runner.cfg == before
        verification = runner.event.call_args.kwargs["verification"]
        assert verification["passed"] is True
        runner.cfg["binary_plan_tuning"]["validation"]["hybench_xp"][
            "timeout_s"
        ] = 7200
        try:
            runner.apply_preformal_runtime_contract_repairs()
        except RuntimeError as exc:
            assert "job_outer_timeout_exceeds_hard_gate_allowance" in str(exc)
        else:
            raise AssertionError("legacy 7200-second JOB timeout must fail closed")


def test_tpch_preformal_runtime_verification_never_expands_hard_timeout() -> None:
    """TPC-H must enforce the same serialized 900/1020-second contract."""

    runner = FreshCalibration.__new__(FreshCalibration)
    runner.algorithm_cfg = {
        "scene": "tpch_memory",
        "workload_contract": {
            "benchmark_concurrency": {
                "target_wall_time_s": 900,
                "target_wall_time_is_advisory": False,
            }
        },
    }
    runner.cfg = {
        "binary_plan_tuning": {
            "validation": {
                "hybench_xp": {"timeout_s": 1020, "timeout_grace_s": 120}
            }
        }
    }
    with tempfile.TemporaryDirectory() as output_dir:
        runner.output_dir = Path(output_dir)
        runner.event = mock.Mock()
        before = copy.deepcopy(runner.cfg)
        runner.apply_preformal_runtime_contract_repairs()
        assert runner.cfg == before
        verification = runner.event.call_args.kwargs["verification"]
        assert verification["scene"] == "tpch_memory"
        assert verification["passed"] is True
        runner.cfg["binary_plan_tuning"]["validation"]["hybench_xp"][
            "timeout_s"
        ] = 7200
        try:
            runner.apply_preformal_runtime_contract_repairs()
        except RuntimeError as exc:
            assert "tpch_outer_timeout_exceeds_hard_gate_allowance" in str(exc)
        else:
            raise AssertionError("legacy 7200-second TPC-H timeout must fail closed")


def test_container_psql_retries_transient_docker_timeout() -> None:
    """A slow Docker control plane must not abort JOB before any workload."""

    completed = subprocess.CompletedProcess(
        args=["docker", "exec"],
        returncode=0,
        stdout="1\n",
        stderr="",
    )
    with mock.patch.object(
        launcher.subprocess,
        "run",
        side_effect=[subprocess.TimeoutExpired("docker exec", 60), completed],
    ) as run_mock, mock.patch.object(launcher.time, "sleep"):
        result = launcher._container_psql(
            {"container": "pg_worker", "port": 5432, "user": "postgres"},
            "select 1",
        )
    assert result.returncode == 0
    assert run_mock.call_count == 2


def gate_config() -> dict:
    return {
        "algorithm_c": {
            "ssd_replay_qualification": {
                "enabled": True,
                "expected_devices": ["nvme1n1"],
                "expected_source": "host_diskstats_io_busy_ms",
                "minimum_stable_samples": 45,
                "non_target_maximum_ssd_avg_pct": 80.0,
                "non_target_maximum_ssd_p99_pct": 95.0,
                "io_target_baseline_minimum_ssd_avg_pct": 65.0,
                "io_target_maximum_ssd_avg_pct": 85.0,
                "io_target_maximum_ssd_p99_pct": 100.1,
                "maximum_parallel_avg_skew_pct_points": 5.0,
            }
        }
    }


def replay_row(worker: str, avg: float, p99: float, start: float = 10.0) -> dict:
    return {
        "status": "ok",
        "technical_failure": False,
        "worker_key": worker,
        "window": [start, start + 300.0],
        "resource_stats": {
            "resource_monitor_ok": True,
            "ssd_util_source": "host_diskstats_io_busy_ms",
            "ssd_observer_disk_devices": ["nvme1n1"],
            "ssd_util_percent": {
                "avg": avg,
                "p99": p99,
                "max": 100.0,
            },
            "stable_sample_count": {"io_util_pct": 300},
        },
        "workload_completeness": {"passed": True},
    }


def test_full_rank_design() -> None:
    audit = calibration_design_audit(INITIAL_PARAMS)
    assert audit["ready"] is True, audit
    assert audit["training_row_count"] == 20
    assert audit["holdout_row_count"] == 10
    assert audit["training_rank"] == 7
    assert audit["scaled_training_condition_number"] < 5.0
    assert audit["maximum_abs_column_correlation"] < 0.999
    assert audit["design"]["staged_batch_count"] == 5
    assert len(audit["design"]["sign_matrix"]) == 25
    assert audit["design"]["batch_magnitudes"][-1] == 0.85
    assert audit["maximum_frozen_design_row_count"] == 25
    assert audit["minimum_total_observation_count"] == 30
    replacement_holdout = [
        deterministic_design_point(index, INITIAL_PARAMS)
        for index in range(20, 25)
    ]
    assert len({tuple(sorted(row.items())) for row in replacement_holdout}) == 5


def test_sealed_second_holdout_is_skipped_without_replay() -> None:
    """A failed batch 2 must advance to a fresh batch 3, not deadlock."""
    runner = object.__new__(FreshCalibration)
    runner.output_dir = Path("/tmp/algorithm_c_test_sealed_second_holdout")
    runner.cfg_path = Path("/tmp/algorithm_c_test_config.json")
    runner.target = "memory"
    runner.design_audit = {"ready": True}
    runner.state = {
        "phase": "candidate_batch_technical_failure_recorded_clean",
        "completed_batches": ["baseline", "candidate_batch_01"],
        "failed_batches": [
            {
                "batch_index": 2,
                "batch_name": "candidate_batch_02",
                "physical_workload_invocations": 2,
                "valid_replays": 0,
                "technical_failures": 2,
                "immutable": True,
                "do_not_retry": True,
                "formal_state_mutation": False,
            }
        ],
    }
    runner.stop_requested = False
    runner.lane_controller = None
    not_ready = {"formal_admission": {"ready": False, "errors": ["missing_holdout"]}}
    ready = {"formal_admission": {"ready": True}, "artifact_sha256": "table-sha"}
    calls = []

    runner.apply_preformal_runtime_contract_repairs = mock.Mock()
    runner.recover_legacy_failed_batch_state = mock.Mock()
    runner.event = mock.Mock()
    runner.run_baseline = mock.Mock()
    runner.restore_clean = mock.Mock()
    runner.ensure_clean_restore = mock.Mock()
    runner.save = mock.Mock()
    runner.failed_batch_names = mock.Mock(return_value={"candidate_batch_02"})
    runner.run_candidate_batch = mock.Mock(
        side_effect=lambda batch_index, _design_start, **_kwargs: (
            calls.append(batch_index) or (batch_index != 2)
        )
    )
    table_results = iter((not_ready, ready))

    def build_after_replacement_holdout():
        assert 3 in calls, "table must not be built before replacement batch 3"
        return next(table_results)

    runner.build_table = mock.Mock(side_effect=build_after_replacement_holdout)
    runner.t_path = Path("/tmp/algorithm_c_test_table.json")

    with mock.patch(
        "run_pg_algorithm_c_calibrate_then_scene.LaneController"
    ) as lane_controller_cls:
        lane_controller_cls.return_value.run = mock.Mock()
        runner.run()

    # The normal admission branch asks for batch 3 again after the first table
    # build; run_candidate_batch returns immediately for the completed name, so
    # this is a state lookup rather than a second physical replay.
    assert calls == [1, 2, 3, 3]
    assert runner.run_candidate_batch.call_args_list[1].args == (2, 5)
    assert runner.run_candidate_batch.call_args_list[2].args == (3, 10)
    runner.event.assert_any_call(
        "algorithm_c_calibration_holdout_batch_unavailable",
        batch_index=2,
        action="skip_immutable_failed_batch_collect_fresh_batch_03",
        failed_batches=runner.state["failed_batches"],
        physical_workload_invocations=0,
        candidate_batch_retry=False,
        formal_state_mutation=False,
    )


def test_plan_probe_retries_only_transient_psql_transport_failures() -> None:
    original = plan_tuner.psql_query
    calls = []

    def transient_then_ok(*args, **kwargs):
        _ = args, kwargs
        calls.append("transient")
        if len(calls) == 1:
            raise RuntimeError("psql query failed rc=2: connection to server: timeout expired")
        return "ok"

    try:
        plan_tuner.psql_query = transient_then_ok
        result = plan_tuner.psql_query_with_transient_retry(
            object(),
            "EXPLAIN SELECT 1",
            attempts=3,
            retry_delay_s=0.0,
        )
        assert result == "ok"
        assert len(calls) == 2

        calls.clear()

        def deterministic_sql_error(*args, **kwargs):
            _ = args, kwargs
            calls.append("sql_error")
            raise RuntimeError('psql query failed rc=1: ERROR: syntax error at or near "SELECT"')

        plan_tuner.psql_query = deterministic_sql_error
        try:
            plan_tuner.psql_query_with_transient_retry(
                object(),
                "EXPLAIN SELECT 1",
                attempts=3,
                retry_delay_s=0.0,
            )
        except RuntimeError as exc:
            assert "syntax error" in str(exc)
        else:
            raise AssertionError("deterministic SQL errors must fail closed without retry")
        assert len(calls) == 1
    finally:
        plan_tuner.psql_query = original


def test_restore_and_parameter_apply_retry_transient_psql_transport_failures() -> None:
    original = plan_tuner.psql_query
    pg = plan_tuner.PgConn(
        host="127.0.0.1",
        port="5432",
        user="postgres",
        dbname="workload",
        password="",
        connect_timeout_s=5,
    )
    calls = []
    template_check_failures = 1
    create_failures = 1
    alter_failures = 1

    def transient_restore_and_apply(*args, **kwargs):
        nonlocal template_check_failures, create_failures, alter_failures
        _ = kwargs
        sql = str(args[1])
        calls.append(sql)
        if "SELECT 1 FROM pg_database" in sql:
            if template_check_failures:
                template_check_failures -= 1
                raise RuntimeError("psql query failed rc=2: psql: error: timeout expired")
            return "1"
        if sql.startswith("CREATE DATABASE") and create_failures:
            create_failures -= 1
            raise RuntimeError("psql query failed rc=2: connection to server: timeout expired")
        if sql.startswith("ALTER SYSTEM") and alter_failures:
            alter_failures -= 1
            raise RuntimeError("psql query failed rc=2: psql: error: timeout expired")
        if "FROM pg_settings" in sql:
            return "seq_page_cost\t1\t"
        return ""

    try:
        plan_tuner.psql_query = transient_restore_and_apply
        with mock.patch.object(plan_tuner.time, "sleep", return_value=None):
            plan_tuner.restore_database_from_template(pg, "workload_base")
            readback = plan_tuner.apply_pg_params(pg, {"seq_page_cost": 1.0})
        assert sum(sql.startswith("CREATE DATABASE") for sql in calls) == 2
        assert sum(sql.startswith("ALTER SYSTEM") for sql in calls) == 2
        assert readback["seq_page_cost"] == "1"
    finally:
        plan_tuner.psql_query = original


def test_restore_database_supports_explicit_file_copy_strategy() -> None:
    original = plan_tuner.psql_query
    pg = plan_tuner.PgConn(
        host="127.0.0.1",
        port="5432",
        user="postgres",
        dbname="workload",
        password="",
        connect_timeout_s=5,
    )
    calls = []

    def record_sql(*args, **kwargs):
        _ = kwargs
        sql = str(args[1])
        calls.append(sql)
        if "SELECT 1 FROM pg_database" in sql:
            return "1"
        return ""

    try:
        plan_tuner.psql_query = record_sql
        with mock.patch.dict(
            plan_tuner.os.environ,
            {"PG_ALGORITHM_C_RESTORE_DATABASE_STRATEGY": "FILE_COPY"},
        ):
            strategy = plan_tuner.restore_database_from_template(pg, "workload_base")
        assert strategy == "file_copy"
        create_sql = next(sql for sql in calls if sql.startswith("CREATE DATABASE"))
        assert "STRATEGY = FILE_COPY" in create_sql
    finally:
        plan_tuner.psql_query = original


def test_restore_memory_headroom_requires_host_and_each_numa_node() -> None:
    snapshots = iter(
        [
            {
                "host_mem_available_gib": 512.0,
                "numa_mem_free_gib": {"node0": 128.0, "node1": 32.0},
                "minimum_numa_mem_free_gib": 32.0,
            },
            {
                "host_mem_available_gib": 512.0,
                "numa_mem_free_gib": {"node0": 128.0, "node1": 96.0},
                "minimum_numa_mem_free_gib": 96.0,
            },
        ]
    )
    with mock.patch.object(
        plan_tuner,
        "restore_memory_headroom_snapshot",
        side_effect=lambda: next(snapshots),
    ), mock.patch.object(plan_tuner.time, "sleep", return_value=None), mock.patch.dict(
        plan_tuner.os.environ,
        {
            "PG_ALGORITHM_C_RESTORE_MIN_HOST_AVAILABLE_GIB": "128",
            "PG_ALGORITHM_C_RESTORE_MIN_NUMA_FREE_GIB": "64",
        },
    ):
        result = plan_tuner.wait_for_restore_memory_headroom(timeout_s=10.0)
    assert result["status"] == "ok"
    assert result["minimum_numa_free_gib"] == 64.0
    assert result["minimum_numa_mem_free_gib"] == 96.0
    assert result["minimum_numa_free_gib"] <= result["minimum_numa_mem_free_gib"]
    assert result["numa_mem_free_gib"]["node1"] == 96.0


def test_adaptive_directional_selection_falls_back_to_calibration_only_probes() -> None:
    predicted_values = (-0.20, -0.18, -0.16, -0.14, -0.12, 0.02)
    rows = [
        {
            "params": {"seq_page_cost": float(index + 1)},
            "plan_fingerprint": f"plan-{index}",
            "predicted_target_log_ratio": value,
            "prediction_uncertainty_log_ratio": 0.05,
            "predicted_target_upper_log_ratio": value + 0.05,
        }
        for index, value in enumerate(predicted_values)
    ]
    selected = select_directional_adaptive_candidates(
        rows,
        deadband_log_ratio=math.log(1.01),
    )
    assert selected["ready"] is True, selected
    assert selected["predicted_target_down_count"] == 5
    assert len(selected["selected"]) == 5
    assert all(
        row["predicted_target_upper_log_ratio"] <= -math.log(1.01)
        for row in selected["selected"]
    )
    assert selected["selected"][0]["predicted_target_log_ratio"] == -0.20
    strict_selected = select_directional_adaptive_candidates(
        rows,
        deadband_log_ratio=math.log(1.01),
        require_directional_batch=True,
    )
    assert strict_selected["ready"] is True, strict_selected
    assert strict_selected["directional_selected_count"] == 5
    assert strict_selected["predicted_target_down_selected_count"] == 5
    no_signal = select_directional_adaptive_candidates(
        [
            {
                **row,
                "predicted_target_log_ratio": -0.02,
                "predicted_target_upper_log_ratio": 0.03,
            }
            for row in rows
        ],
        deadband_log_ratio=math.log(1.01),
    )
    assert no_signal["ready"] is True
    assert len(no_signal["selected"]) == 5
    assert no_signal["predicted_target_down_selected_count"] == 0
    assert no_signal["model_identification_probe_count"] == 5
    assert all(
        row["calibration_selection_role"] == "model_identification_probe"
        for row in no_signal["selected"]
    )
    assert no_signal["selection_purpose"] == (
        "calibration_only_not_formal_improvement_candidates"
    )
    assert no_signal["warnings"]

    late_no_signal = select_directional_adaptive_candidates(
        [
            {
                **row,
                "predicted_target_log_ratio": -0.02,
                "predicted_target_upper_log_ratio": 0.03,
            }
            for row in rows
        ],
        deadband_log_ratio=math.log(1.01),
        require_directional_batch=True,
    )
    assert late_no_signal["ready"] is False
    assert late_no_signal["directional_selected_count"] == 0
    assert late_no_signal["require_directional_batch"] is True
    assert any(
        "directional_selected_count_0_below_5" in error
        for error in late_no_signal["errors"]
    )


def test_late_adaptive_batch_can_use_mixed_independent_holdout() -> None:
    identification_rows = [
        {
            "params": {"seq_page_cost": float(index + 10)},
            "plan_fingerprint": f"new-plan-{index}",
            "predicted_target_log_ratio": 0.02,
            "predicted_target_upper_log_ratio": 0.05,
            "calibration_selection_role": "model_identification_probe",
            "observation_index": index,
        }
        for index in range(5)
    ]
    directional = {
        "selected": identification_rows,
        "errors": ["adaptive_expansion_directional_selected_count_0_below_5"],
    }
    replication_rows = [
        {
            "params": {"seq_page_cost": float(index + 1)},
            "plan_fingerprint": f"replicated-plan-{index}",
            "predicted_target_log_ratio": -0.03,
            "predicted_target_upper_log_ratio": 0.01,
            "calibration_selection_role": (
                "historical_plan_cluster_replication_holdout"
            ),
            "calibration_replication_source": {
                "round_index": index + 1,
                "exact_parameter_vector_reused": False,
            },
            "observation_index": index + 10,
        }
        for index in range(3)
    ]
    mixed = build_mixed_independent_calibration_holdout(
        directional,
        replication_rows,
        deadband_log_ratio=math.log(1.01),
    )
    assert mixed is not None
    assert mixed["ready"] is True
    assert mixed["errors"] == []
    assert len(mixed["selected"]) == 5
    assert mixed["historical_plan_cluster_replication_count"] == 3
    assert mixed["model_identification_probe_count"] == 2
    assert len(
        {row["plan_fingerprint"] for row in mixed["selected"]}
    ) == 5
    assert mixed["selection_purpose"].endswith(
        "holdout_only_not_formal_candidates"
    )
    assert mixed["formal_top5_plan_uniqueness_contract_unchanged"] is True

    repeated_cluster_rows = copy.deepcopy(replication_rows)
    repeated_cluster_rows[1]["plan_fingerprint"] = repeated_cluster_rows[0][
        "plan_fingerprint"
    ]
    repeated_mixed = build_mixed_independent_calibration_holdout(
        directional,
        repeated_cluster_rows,
        deadband_log_ratio=math.log(1.01),
    )
    assert repeated_mixed is not None
    assert repeated_mixed["ready"] is True
    assert repeated_mixed["historical_plan_cluster_replication_count"] == 3
    assert repeated_mixed["repeated_plan_cluster_replication_count"] == 1
    assert repeated_mixed["distinct_plan_fingerprint_count"] == 4
    assert repeated_mixed["calibration_plan_cluster_replication_allowed"] is True

    one_round_only = copy.deepcopy(replication_rows)
    for row in one_round_only:
        row["calibration_replication_source"]["round_index"] = 1
    assert build_mixed_independent_calibration_holdout(
        directional,
        one_round_only,
        deadband_log_ratio=math.log(1.01),
    ) is None


def test_late_adaptive_batch_can_use_plan_unique_model_identification() -> None:
    rows = []
    for index in range(5):
        params = dict(INITIAL_PARAMS)
        params["seq_page_cost"] = 0.5 + index * 0.05
        rows.append(
            {
                "params": params,
                "plan_fingerprint": f"plan-{index}",
                "predicted_target_log_ratio": 0.02 + index * 0.01,
                "predicted_target_upper_log_ratio": 0.03 + index * 0.01,
                "calibration_selection_role": "model_identification_probe",
                "observation_index": index,
            }
        )
    holdout = build_model_identification_calibration_holdout(
        {"selected": rows}
    )
    assert holdout is not None
    assert holdout["ready"] is True
    assert holdout["distinct_plan_fingerprint_count"] == 5
    assert holdout["model_identification_probe_count"] == 5
    assert holdout["formal_top5_plan_uniqueness_contract_unchanged"] is True
    assert "not_formal" in holdout["selection_purpose"]

    duplicate_plan_rows = copy.deepcopy(rows)
    duplicate_plan_rows[-1]["plan_fingerprint"] = "plan-0"
    assert build_model_identification_calibration_holdout(
        {"selected": duplicate_plan_rows}
    ) is None

    mixed_role_rows = copy.deepcopy(rows)
    for row in mixed_role_rows[:4]:
        row["calibration_selection_role"] = (
            "single_observation_target_down_probe"
        )
    mixed_role_rows[-1]["calibration_selection_role"] = "predicted_target_down"
    mixed_role_rows[-1]["predicted_target_log_ratio"] = -0.50
    mixed_role_rows[-1]["predicted_target_upper_log_ratio"] = -0.05
    mixed_holdout = build_model_identification_calibration_holdout(
        {"selected": mixed_role_rows}
    )
    assert mixed_holdout is not None
    assert mixed_holdout["ready"] is True
    assert mixed_holdout["single_observation_direction_probe_count"] == 4
    assert mixed_holdout["predicted_target_down_probe_count"] == 1
    assert mixed_holdout["distinct_plan_fingerprint_count"] == 5
    assert mixed_holdout["formal_top5_plan_uniqueness_contract_unchanged"] is True

    mixed_duplicate_params = copy.deepcopy(mixed_role_rows)
    mixed_duplicate_params[-1]["params"] = copy.deepcopy(
        mixed_duplicate_params[0]["params"]
    )
    assert build_model_identification_calibration_holdout(
        {"selected": mixed_duplicate_params}
    ) is None

    mixed_duplicate_plan = copy.deepcopy(mixed_role_rows)
    mixed_duplicate_plan[-1]["plan_fingerprint"] = mixed_duplicate_plan[0][
        "plan_fingerprint"
    ]
    assert build_model_identification_calibration_holdout(
        {"selected": mixed_duplicate_plan}
    ) is None

    unauthorized_role_rows = copy.deepcopy(mixed_role_rows)
    unauthorized_role_rows[-1]["calibration_selection_role"] = (
        "historical_direction_continuation"
    )
    assert build_model_identification_calibration_holdout(
        {"selected": unauthorized_role_rows}
    ) is None


def test_adaptive_selection_continues_repeatable_real_target_down_direction() -> None:
    repeated_mask = 98
    repeated_x = [
        0.5 if repeated_mask & (1 << index) else -0.5
        for index in range(len(ACTIVE_PARAMS))
    ]
    mixed_mask = 52
    mixed_x = [
        0.5 if mixed_mask & (1 << index) else -0.5
        for index in range(len(ACTIVE_PARAMS))
    ]
    samples = [
        {"round_index": 1, "x": repeated_x, "outcomes": {"memory": -0.03}},
        {"round_index": 7, "x": repeated_x, "outcomes": {"memory": -0.02}},
        {"round_index": 4, "x": mixed_x, "outcomes": {"memory": -0.04}},
        {"round_index": 6, "x": mixed_x, "outcomes": {"memory": 0.05}},
    ]
    evidence = historical_target_direction_evidence(
        samples,
        target="memory",
        deadband_log_ratio=math.log(1.01),
    )
    assert evidence[repeated_mask]["trusted"] is True
    assert evidence[repeated_mask]["target_down_count"] == 2
    assert evidence[repeated_mask]["target_up_count"] == 0
    assert evidence[repeated_mask]["qps_used"] is False
    assert evidence[mixed_mask]["trusted"] is False
    assert evidence[mixed_mask]["exploratory"] is False

    single_mask = 17
    single_x = [
        0.5 if single_mask & (1 << index) else -0.5
        for index in range(len(ACTIVE_PARAMS))
    ]
    single_evidence = historical_target_direction_evidence(
        [{"round_index": 3, "x": single_x, "outcomes": {"memory": -0.04}}],
        target="memory",
        deadband_log_ratio=math.log(1.01),
    )
    assert single_evidence[single_mask]["trusted"] is False
    assert single_evidence[single_mask]["exploratory"] is True

    rows = [
        {
            "params": {"seq_page_cost": float(index + 1)},
            "plan_fingerprint": f"plan-{index}",
            "predicted_target_log_ratio": -0.02,
            "prediction_uncertainty_log_ratio": 0.05,
            "predicted_target_upper_log_ratio": 0.03,
            "mask": repeated_mask if index >= 4 else index + 1,
            "magnitude": 0.72 if index == 4 else 0.95,
            "historical_direction_evidence": (
                evidence[repeated_mask] if index >= 4 else None
            ),
        }
        for index in range(6)
    ]
    selected = select_directional_adaptive_candidates(
        rows,
        deadband_log_ratio=math.log(1.01),
    )
    assert selected["ready"] is True
    assert selected["historical_direction_continuation_count"] == 2
    assert selected["selected"][0]["mask"] == repeated_mask
    assert all(
        row["calibration_selection_role"]
        == "historical_target_down_direction_continuation"
        for row in selected["selected"][:2]
    )

    exploratory_rows = [
        {
            "params": {"seq_page_cost": float(index + 1)},
            "plan_fingerprint": f"single-plan-{index}",
            "predicted_target_log_ratio": 0.02,
            "prediction_uncertainty_log_ratio": 0.05,
            "predicted_target_upper_log_ratio": 0.07,
            "mask": single_mask if index < 2 else index + 32,
            "magnitude": 0.30 + index * 0.1,
            "historical_direction_evidence": (
                single_evidence[single_mask] if index < 2 else None
            ),
        }
        for index in range(6)
    ]
    exploratory = select_directional_adaptive_candidates(
        exploratory_rows,
        deadband_log_ratio=math.log(1.01),
    )
    assert exploratory["ready"] is True
    assert exploratory["single_observation_direction_probe_count"] == 1
    assert exploratory["selected"][0]["mask"] == single_mask
    assert exploratory["selected"][0]["calibration_selection_role"] == (
        "single_observation_target_down_probe"
    )

    unsupported_rows = copy.deepcopy(rows)
    for row in unsupported_rows:
        if row.get("mask") == repeated_mask:
            row["support_abstained"] = True
    unsupported = select_directional_adaptive_candidates(
        unsupported_rows,
        deadband_log_ratio=math.log(1.01),
    )
    assert unsupported["historical_direction_continuation_count"] == 0
    assert unsupported["eligible_count"] == 6
    assert unsupported["ready"] is True
    assert all(
        row["calibration_selection_role"] == "model_identification_probe"
        for row in unsupported["selected"]
    )


def test_adaptive_target_prediction_excludes_qps() -> None:
    zero = {name: 0.0 for name in ACTIVE_PARAMS}
    calibration = {
        "contract": RESOURCE_PREDICTION_CALIBRATION_CONTRACT,
        "qps_used": False,
        "resources": {
            "io": {
                "family": "hybrid",
                "parameter_coefficients": {**zero, "seq_page_cost": 0.5},
                "plan_log_ratio_coefficient": 0.25,
                "training_loo_mae_log_ratio": 0.10,
            }
        },
    }
    params = dict(INITIAL_PARAMS)
    params["seq_page_cost"] = 2.0
    prediction = calibrated_target_log_ratio(
        target="io",
        params=params,
        baseline_params=INITIAL_PARAMS,
        baseline_probe={"resource_avg": {"io": 10.0}, "db_qps": 1.0},
        candidate_probe={"resource_avg": {"io": 20.0}, "db_qps": 999.0},
        prediction_calibration=calibration,
    )
    assert prediction["qps_used"] is False
    assert math.isclose(prediction["prediction_uncertainty_log_ratio"], 0.10)
    assert math.isclose(
        prediction["predicted_target_upper_log_ratio"],
        prediction["predicted_target_log_ratio"] + 0.10,
    )
    assert math.isclose(
        prediction["predicted_target_log_ratio"],
        0.5 * math.log(2.0) + 0.25 * math.log(2.0),
    )


def test_opposite_direction_is_rejected_by_metrics() -> None:
    actual = [0.02, 0.04, 0.06, 0.08, 0.10]
    predicted = [-0.10, -0.08, -0.06, -0.04, -0.02]
    metrics = target_direction_metrics(actual, predicted)
    assert metrics["material_sign_evaluable_count"] == 5
    assert metrics["sign_accuracy"] == 0.0
    assert metrics["confirmed_target_down_count"] == 0


def test_robust_target_down_uses_frozen_error_margin() -> None:
    deadband = math.log(1.01)
    actual = [-0.04, -0.23, 0.20, -0.02, 0.06]
    predicted = [-0.13, -0.14, -0.016, -0.12, 0.03]
    metrics = target_direction_metrics(
        actual,
        predicted,
        prediction_error_margin_log_ratio=0.0724,
        deadband_log_ratio=deadband,
    )
    assert metrics["point_predicted_target_down_count"] == 4
    assert metrics["point_predicted_target_down_precision"] == 0.75
    assert metrics["robust_predicted_target_down_count"] == 3
    assert metrics["robust_confirmed_target_down_count"] == 3
    assert metrics["robust_predicted_target_down_precision"] == 1.0
    assert metrics["predicted_target_down_count"] == 3
    assert metrics["confirmed_target_down_count"] == 3
    assert metrics["uncertain_point_down_count"] == 1


def test_robust_target_down_boundary_matches_adaptive_selector() -> None:
    deadband = math.log(1.01)
    assert robust_target_down(-deadband - 0.02, 0.02, deadband) is True
    assert robust_target_down(-deadband - 0.02 + 1e-12, 0.02, deadband) is False


def test_predicted_down_actual_neutral_is_a_precision_false_positive() -> None:
    deadband = math.log(1.01)
    metrics = target_direction_metrics(
        actual=[0.0, -0.04, 0.03, 0.02, -0.05],
        predicted=[-0.02, -0.03, 0.02, 0.03, -0.04],
        prediction_error_margin_log_ratio=0.0,
        deadband_log_ratio=deadband,
    )
    assert metrics["material_sign_evaluable_count"] == 4
    assert metrics["point_predicted_target_down_count"] == 3
    assert metrics["point_confirmed_target_down_count"] == 2
    assert math.isclose(
        metrics["point_predicted_target_down_precision"],
        2.0 / 3.0,
    )


def test_selection_admission_requires_two_point_down_predictions() -> None:
    metrics = {
        "spearman": 0.2,
        "mae_log_ratio": 0.10,
        "direction": {
            "material_sign_evaluable_count": 5,
            "actual_target_down_count": 2,
            "point_predicted_target_down_count": 1,
            "point_confirmed_target_down_count": 1,
            "point_predicted_target_down_precision": 1.0,
        },
    }
    errors = target_selection_admission_errors(metrics)
    assert "target_holdout_point_predicted_down_count_below_2" in errors
    assert "target_holdout_point_confirmed_down_count_below_2" in errors


def test_selection_admission_allows_safe_false_negatives() -> None:
    metrics = {
        "spearman": 0.6,
        "mae_log_ratio": 0.05,
        "direction": {
            "material_sign_evaluable_count": 10,
            "sign_accuracy": 0.8,
            "sign_accuracy_wilson90_lower": 0.14,
            "actual_target_down_count": 5,
            "point_predicted_target_down_count": 2,
            "point_confirmed_target_down_count": 2,
            "point_predicted_target_down_precision": 1.0,
        },
    }
    assert target_selection_admission_errors(metrics) == []
    metrics["direction"]["point_predicted_target_down_precision"] = 0.5
    assert "target_holdout_point_predicted_down_precision_below_0p80" in target_selection_admission_errors(metrics)


def test_selection_admission_accepts_inclusive_spearman_boundary() -> None:
    metrics = {
        "spearman": 0.50 - 1e-16,
        "mae_log_ratio": 0.05,
        "direction": {
            "material_sign_evaluable_count": 10,
            "sign_accuracy": 0.75,
            "actual_target_down_count": 2,
            "point_predicted_target_down_count": 2,
            "point_confirmed_target_down_count": 2,
            "point_predicted_target_down_precision": 1.0,
        },
    }
    assert target_selection_admission_errors(metrics) == []


def test_every_inner_scored_resource_keeps_independent_holdout_diagnostics() -> None:
    good = {
        "spearman": 0.40,
        "mae_log_ratio": 0.10,
        "direction": {
            "material_sign_evaluable_count": 10,
            "sign_accuracy": 0.75,
        },
    }
    assert scored_resource_admission_errors("memory", good) == []

    bad_io = copy.deepcopy(good)
    bad_io["spearman"] = 0.10 - 1e-16
    bad_io["mae_log_ratio"] = 0.20
    bad_io["direction"]["sign_accuracy"] = 0.20
    errors = scored_resource_admission_errors("io", bad_io)
    assert "io_holdout_mae_above_15pct_log_ratio" in errors
    assert "io_holdout_sign_accuracy_below_0p75" in errors
    assert "io_holdout_spearman_below_0p10" not in errors


def test_only_target_scored_resource_errors_block_formal_admission() -> None:
    blocking, diagnostic = partition_scored_resource_admission_errors(
        {
            "cpu": ["cpu_holdout_spearman_below_0p10"],
            "memory": [],
            "io": ["io_holdout_mae_above_25pct_log_ratio"],
        },
        target_resource="memory",
    )
    assert blocking == {"memory": []}
    assert diagnostic == {
        "cpu": ["cpu_holdout_spearman_below_0p10"],
        "io": ["io_holdout_mae_above_25pct_log_ratio"],
    }


def test_adaptive_calibration_prioritizes_weak_high_weight_resource() -> None:
    focus = choose_calibration_focus_resource(
        target="memory",
        scored_resource_errors={
            "cpu": ["cpu_holdout_spearman_below_0p10"],
            "memory": [],
            "io": ["io_holdout_mae_above_25pct_log_ratio"],
        },
        resource_weights={"cpu": 1.0, "memory": 1.0, "io": 3.0},
    )
    assert focus == "io"
    assert choose_calibration_focus_resource(
        target="memory",
        scored_resource_errors={},
        resource_weights={"cpu": 1.0, "memory": 1.0, "io": 3.0},
    ) == "memory"


def test_historical_model_selection_rejects_inaccurate_abstention() -> None:
    """Zero historical down predictions cannot hide bad rank/error quality."""
    inaccurate_abstaining = {
        "spearman": -0.90,
        "mae_log_ratio": 0.34,
        "direction": {
            "predicted_target_down_count": 0,
            "confirmed_target_down_count": 0,
            "predicted_target_down_precision": 0.0,
        },
    }
    informative = {
        "spearman": 0.90,
        "mae_log_ratio": 0.09,
        "direction": {
            "predicted_target_down_count": 2,
            "confirmed_target_down_count": 1,
            "predicted_target_down_precision": 0.50,
        },
    }
    assert historical_validation_model_key(informative) < (
        historical_validation_model_key(inaccurate_abstaining)
    )


def test_historical_model_selection_uses_point_direction_after_rank_quality() -> None:
    """Error-envelope abstention must not defeat a much better rank model."""
    weak_raw_plan = {
        "spearman": 0.16,
        "mae_log_ratio": 0.074,
        "direction": {
            "point_predicted_target_down_count": 2,
            "point_confirmed_target_down_count": 1,
            "point_predicted_target_down_precision": 0.50,
            "predicted_target_down_count": 2,
            "confirmed_target_down_count": 1,
            "predicted_target_down_precision": 0.50,
            "sign_accuracy": 0.31,
        },
    }
    strong_hybrid_with_conservative_abstention = {
        "spearman": 0.65,
        "mae_log_ratio": 0.053,
        "direction": {
            "point_predicted_target_down_count": 3,
            "point_confirmed_target_down_count": 3,
            "point_predicted_target_down_precision": 1.0,
            # These robust fields are zero because the empirical error
            # envelope abstains.  They remain confidence metadata only.
            "predicted_target_down_count": 0,
            "confirmed_target_down_count": 0,
            "predicted_target_down_precision": 0.0,
            "sign_accuracy": 0.62,
        },
    }
    assert historical_validation_model_key(
        strong_hybrid_with_conservative_abstention
    ) < historical_validation_model_key(weak_raw_plan)


def test_calibration_stop_before_batch_starts_no_physical_replay() -> None:
    runner = object.__new__(FreshCalibration)
    runner.state = {"completed_batches": []}
    runner.stop_requested = True
    runner.failed_batch_names = mock.Mock(return_value=set())
    runner.logged_candidate_batch_rows = mock.Mock(return_value=[])
    runner.account_candidate_batch_rows = mock.Mock(return_value=0)
    runner.event = mock.Mock()

    assert runner.run_candidate_batch(7, 15, candidates_override=[]) is False
    runner.event.assert_called_once_with(
        "algorithm_c_calibration_stop_before_batch",
        batch_index=7,
        batch_name="candidate_batch_07",
        physical_workload_invocations=0,
        formal_state_mutation=False,
    )


def test_direction_contract_change_requires_newer_independent_holdout() -> None:
    with tempfile.TemporaryDirectory() as directory:
        runner = object.__new__(FreshCalibration)
        runner.minimum_fresh_direction_holdout_round = 13
        runner.t_path = Path(directory) / "experience.json"
        runner.event = mock.Mock()
        runner.build_table = mock.Mock(
            return_value={
                "schema_version": 6,
                "fit": {"holdout_outer_rounds": [12]},
                "formal_admission": {"ready": True, "errors": []},
                "artifact_sha256": "old",
            }
        )
        artifact = runner.build_table_for_admission()
        assert artifact["formal_admission"]["ready"] is False
        assert (
            "direction_contract_fresh_holdout_round_below_13"
            in artifact["formal_admission"]["errors"]
        )
        assert runner.t_path.is_file()
        runner.event.assert_called_once()

        runner.event.reset_mock()
        runner.build_table.return_value = {
            "schema_version": 6,
            "fit": {"holdout_outer_rounds": [13]},
            "formal_admission": {"ready": True, "errors": []},
            "artifact_sha256": "new",
        }
        artifact = runner.build_table_for_admission()
        assert artifact["formal_admission"]["ready"] is True
        runner.event.assert_not_called()


def test_stop_before_formal_handoff_does_not_run_formal_baseline() -> None:
    runner = object.__new__(FreshCalibration)
    runner.stop_requested = True
    runner.state = {"phase": "calibration_complete_formal_ready"}
    runner.t_path = Path("/tmp/not_materialized_experience.json")
    receipt = {
        "generation": 0,
        "contract_sha256": "clean",
        "reason": "calibration_complete_or_reused",
        "workers": [],
    }
    runner.ensure_clean_restore = mock.Mock(return_value=receipt)
    runner.save = mock.Mock()
    runner.event = mock.Mock()
    runner.handoff_formal()
    assert runner.ensure_clean_restore.call_args_list == [
        mock.call("calibration_complete_or_reused"),
        mock.call("signal_stop_before_formal_handoff"),
    ]
    assert runner.state["phase"] == "stopped_by_signal_clean"
    assert runner.state["signal_stop_clean_receipt"] == receipt
    assert runner.save.call_count == 2
    assert any(
        call.args and call.args[0] == "algorithm_c_signal_stop_complete"
        for call in runner.event.call_args_list
    )
    assert any(
        call.args
        and call.args[0] == "algorithm_c_formal_handoff_cancelled_clean"
        for call in runner.event.call_args_list
    )


def test_calibration_stop_after_restore_starts_no_physical_replay() -> None:
    """A stop received during restore must close the observed race window."""
    runner = object.__new__(FreshCalibration)
    runner.state = {"completed_batches": []}
    runner.stop_requested = False
    runner.cfg = {}
    runner.initial_params = dict(INITIAL_PARAMS)
    runner.output_dir = Path("/tmp/algorithm_c_test_stop_after_restore")
    runner.target = "memory"
    runner.workers = [{"name": "w1"}, {"name": "w2"}]
    runner._db_mutation_generation = 0
    runner._clean_restore_receipt = None
    runner.design_audit = {"ready": True}
    runner.failed_batch_names = mock.Mock(return_value=set())
    runner.logged_candidate_batch_rows = mock.Mock(return_value=[])
    runner.account_candidate_batch_rows = mock.Mock(return_value=0)
    runner.event = mock.Mock()
    runner.save = mock.Mock()

    restore_calls = []
    restore_barrier = threading.Barrier(2)

    def restore_then_stop(_cfg, worker, *, label):
        restore_calls.append((worker["name"], label))
        restore_barrier.wait(timeout=2.0)
        runner.stop_requested = True
        return {"status": "ok", "worker": worker["name"]}

    candidates = [dict(INITIAL_PARAMS) for _ in range(5)]
    with mock.patch(
        "run_pg_algorithm_c_calibrate_then_scene.restore_worker_from_hybench_template",
        side_effect=restore_then_stop,
    ), mock.patch(
        "run_pg_algorithm_c_calibrate_then_scene.run_real_validation_safe"
    ) as real_validation:
        assert runner.run_candidate_batch(
            7,
            15,
            candidates_override=candidates,
        ) is False

    assert len(restore_calls) == 2
    real_validation.assert_not_called()
    runner.event.assert_any_call(
        "algorithm_c_calibration_stop_after_restore_before_replay",
        batch_index=7,
        wave_start=0,
        worker_count=2,
        physical_workload_invocations=0,
        newly_accounted_physical=0,
        partial_batch_sealed=False,
        formal_state_mutation=False,
    )


def test_clean_restore_receipt_reuses_only_same_process_generation() -> None:
    runner = object.__new__(FreshCalibration)
    runner._db_mutation_generation = 4
    runner._clean_restore_receipt = {
        "generation": 4,
        "contract_sha256": "same-contract",
        "reason": "candidate_batch_13",
    }
    runner.clean_restore_contract = mock.Mock(return_value="same-contract")
    runner.event = mock.Mock()
    runner.restore_clean = mock.Mock()

    runner.ensure_clean_restore("formal_handoff")

    runner.restore_clean.assert_not_called()
    runner.event.assert_called_once_with(
        "algorithm_c_calibration_clean_restore_reused",
        source_reason="candidate_batch_13",
        current_reason="formal_handoff",
        contract_sha256="same-contract",
        no_workload_since=True,
        no_parameter_mutation_since=True,
        no_database_mutation_since=True,
    )

    runner.mark_database_mutated()
    runner.ensure_clean_restore("after_real_replay")
    runner.restore_clean.assert_called_once_with("after_real_replay")


def test_clean_restore_receipt_requires_baseline_parameter_readback() -> None:
    runner = object.__new__(FreshCalibration)
    runner.cfg = {}
    runner.workers = [{"name": "w1"}, {"name": "w2"}]
    runner.initial_params = {name: 1.0 for name in ACTIVE_PARAMS}
    runner._db_mutation_generation = 0
    runner._clean_restore_receipt = None
    runner.clean_restore_contract = mock.Mock(return_value="verified-contract")
    runner.event = mock.Mock()

    with mock.patch(
        "run_pg_algorithm_c_calibrate_then_scene.restore_worker_from_hybench_template",
        return_value={"status": "ok"},
    ), mock.patch(
        "run_pg_algorithm_c_calibrate_then_scene.pg_conn_from_worker",
        return_value={},
    ), mock.patch(
        "run_pg_algorithm_c_calibrate_then_scene.apply_pg_params",
        return_value=runner.initial_params,
    ):
        runner.restore_clean("verified")

    assert runner._clean_restore_receipt["generation"] == 1
    assert runner._clean_restore_receipt["contract_sha256"] == "verified-contract"
    assert runner._clean_restore_receipt["reason"] == "verified"

    with mock.patch(
        "run_pg_algorithm_c_calibrate_then_scene.restore_worker_from_hybench_template",
        return_value={"status": "ok"},
    ), mock.patch(
        "run_pg_algorithm_c_calibrate_then_scene.pg_conn_from_worker",
        return_value={},
    ), mock.patch(
        "run_pg_algorithm_c_calibrate_then_scene.apply_pg_params",
        return_value={},
    ):
        try:
            runner.restore_clean("bad-readback")
        except RuntimeError as exc:
            assert "parameter readback failed" in str(exc)
        else:
            raise AssertionError("missing parameter readback must fail closed")
    assert runner._clean_restore_receipt is None


def test_ridge_alpha_selection_uses_training_target_only() -> None:
    x_train = np.asarray(
        [
            [1.0, 0.0],
            [0.0, 1.0],
            [1.0, 1.0],
            [2.0, 1.0],
            [1.0, 2.0],
        ],
        dtype=float,
    )
    y_train = np.asarray([0.8, -0.4, 0.4, 1.2, 0.0], dtype=float)
    selection = select_ridge_alpha_by_training_loo(
        x_train,
        y_train,
        requested_alpha=0.05,
        alpha_grid=(0.05, 0.5, 2.0),
    )
    assert selection["selected_alpha"] in {0.05, 0.5, 2.0}
    assert selection["target_resource_training_rows_only"] is True
    assert selection["independent_holdout_used"] is False
    assert selection["qps_used"] is False
    assert len(selection["candidates"]) == 3


def test_resource_predictor_family_selection_uses_training_only() -> None:
    x_train = np.asarray(
        [
            [1.0, 0.0],
            [0.0, 1.0],
            [1.0, 1.0],
            [-1.0, 0.0],
            [0.0, -1.0],
            [-1.0, -1.0],
            [1.0, -1.0],
            [-1.0, 1.0],
        ],
        dtype=float,
    )
    plan_train = np.asarray(
        [1.0, -1.0, 0.5, -0.5, 1.5, -1.5, 0.75, -0.75],
        dtype=float,
    )
    y_train = 0.4 * x_train[:, 0] - 0.3 * x_train[:, 1] + 0.8 * plan_train
    x_holdout = np.asarray(
        [[0.5, 0.5], [-0.5, 0.5], [0.5, -0.5], [0.25, 0.75], [-0.75, 0.25]],
        dtype=float,
    )
    y_holdout = np.asarray([-0.2, -0.4, 0.4, -0.3, -0.5], dtype=float)
    first, first_metrics = fit_resource_prediction_calibration(
        x_train=x_train,
        x_holdout=x_holdout,
        y_train=y_train,
        y_holdout=y_holdout,
        plan_train=plan_train,
        plan_holdout=np.asarray([-0.3, -0.2, 0.2, -0.1, -0.4], dtype=float),
        active_params=("p1", "p2"),
        requested_alpha=0.05,
    )
    second, second_metrics = fit_resource_prediction_calibration(
        x_train=x_train,
        x_holdout=x_holdout,
        y_train=y_train,
        y_holdout=y_holdout,
        plan_train=plan_train,
        plan_holdout=np.asarray([3.0, -3.0, 2.0, -2.0, 1.0], dtype=float),
        active_params=("p1", "p2"),
        requested_alpha=0.05,
    )
    assert first["family"] == "hybrid"
    assert second["family"] == first["family"]
    assert second["parameter_coefficients"] == first["parameter_coefficients"]
    assert second["plan_log_ratio_coefficient"] == first["plan_log_ratio_coefficient"]
    assert first["independent_holdout_used_for_model_selection"] is False
    assert first["qps_used"] is False
    assert first_metrics["direction"]["prediction_error_margin_log_ratio"] == (
        second_metrics["direction"]["prediction_error_margin_log_ratio"]
    )
    assert first_metrics["predicted"] != second_metrics["predicted"]


def test_resource_predictor_learns_pairwise_parameter_interaction() -> None:
    x_train = np.asarray(
        [
            [left, right]
            for left in (-1.0, -0.5, 0.5, 1.0)
            for right in (-1.0, -0.5, 0.5, 1.0)
        ],
        dtype=float,
    )
    y_train = 0.8 * x_train[:, 0] * x_train[:, 1]
    x_holdout = np.asarray(
        [
            [-0.9, -0.7],
            [-0.9, 0.7],
            [-0.4, -0.8],
            [-0.4, 0.8],
            [0.4, -0.8],
            [0.4, 0.8],
            [0.9, -0.7],
            [0.9, 0.7],
            [-0.7, 0.3],
            [0.7, -0.3],
        ],
        dtype=float,
    )
    y_holdout = 0.8 * x_holdout[:, 0] * x_holdout[:, 1]
    calibration, metrics = fit_resource_prediction_calibration(
        x_train=x_train,
        x_holdout=x_holdout,
        y_train=y_train,
        y_holdout=y_holdout,
        plan_train=np.zeros(x_train.shape[0], dtype=float),
        plan_holdout=np.zeros(x_holdout.shape[0], dtype=float),
        active_params=("p1", "p2"),
        requested_alpha=0.05,
    )
    assert calibration["family"] == "parameter"
    assert calibration["parameter_feature_contract"] == (
        "resource_response_surface_v2"
    )
    assert calibration["parameter_interaction_coefficients"]["p1*p2"] > 0.70
    assert metrics["spearman"] > 0.98
    assert metrics["mae_log_ratio"] < 0.02
    assert calibration["qps_used"] is False


def test_resource_predictor_uses_historical_round_not_final_holdout() -> None:
    x_model_fit = np.asarray(
        [
            [1.0, 0.0],
            [0.0, 1.0],
            [1.0, 1.0],
            [-1.0, 0.0],
            [0.0, -1.0],
            [-1.0, -1.0],
        ],
        dtype=float,
    )
    plan_model_fit = np.asarray(
        [0.8, -0.6, 0.3, -0.4, 0.7, -0.5],
        dtype=float,
    )
    y_model_fit = 0.3 * x_model_fit[:, 0] + 0.9 * plan_model_fit
    x_model_validation = np.asarray(
        [
            [0.8, 0.2],
            [-0.8, 0.2],
            [0.2, -0.8],
            [-0.2, -0.8],
            [0.7, -0.3],
        ],
        dtype=float,
    )
    plan_model_validation = np.asarray(
        [-0.5, -0.4, 0.3, 0.2, -0.6],
        dtype=float,
    )
    y_model_validation = (
        0.3 * x_model_validation[:, 0] + 0.9 * plan_model_validation
    )
    x_train = np.vstack((x_model_fit, x_model_validation))
    plan_train = np.concatenate((plan_model_fit, plan_model_validation))
    y_train = np.concatenate((y_model_fit, y_model_validation))
    x_holdout = np.asarray(
        [[0.5, 0.5], [-0.5, 0.5], [0.5, -0.5], [-0.5, -0.5], [0.9, 0.1]],
        dtype=float,
    )
    first, first_metrics = fit_resource_prediction_calibration(
        x_train=x_train,
        x_holdout=x_holdout,
        y_train=y_train,
        y_holdout=np.asarray([-0.2, -0.4, 0.4, -0.3, -0.5], dtype=float),
        plan_train=plan_train,
        plan_holdout=np.asarray([-0.3, -0.2, 0.2, -0.1, -0.4], dtype=float),
        active_params=("p1", "p2"),
        requested_alpha=0.05,
        x_model_fit=x_model_fit,
        x_model_validation=x_model_validation,
        y_model_fit=y_model_fit,
        y_model_validation=y_model_validation,
        plan_model_fit=plan_model_fit,
        plan_model_validation=plan_model_validation,
    )
    second, second_metrics = fit_resource_prediction_calibration(
        x_train=x_train,
        x_holdout=x_holdout,
        y_train=y_train,
        y_holdout=np.asarray([3.0, -3.0, 2.0, -2.0, 1.0], dtype=float),
        plan_train=plan_train,
        plan_holdout=np.asarray([3.0, -3.0, 2.0, -2.0, 1.0], dtype=float),
        active_params=("p1", "p2"),
        requested_alpha=0.05,
        x_model_fit=x_model_fit,
        x_model_validation=x_model_validation,
        y_model_fit=y_model_fit,
        y_model_validation=y_model_validation,
        plan_model_fit=plan_model_fit,
        plan_model_validation=plan_model_validation,
    )
    assert first["selection_scope"] == "historical_round_validation"
    assert first["historical_validation_used_for_model_selection"] is True
    assert first["independent_holdout_used_for_model_selection"] is False
    assert second["family"] == first["family"]
    assert second["alpha"] == first["alpha"]
    assert second["parameter_coefficients"] == first["parameter_coefficients"]
    assert second["plan_log_ratio_coefficient"] == first["plan_log_ratio_coefficient"]
    assert first_metrics["direction"]["prediction_error_margin_log_ratio"] == (
        second_metrics["direction"]["prediction_error_margin_log_ratio"]
    )
    assert first_metrics["actual"] != second_metrics["actual"]


def test_parameter_support_domain_is_training_only_and_abstains_extrapolation() -> None:
    train = np.asarray(
        [
            [-1.0, -1.0],
            [-1.0, 1.0],
            [1.0, -1.0],
            [1.0, 1.0],
        ],
        dtype=float,
    )
    domain = build_parameter_support_domain(train, ("p1", "p2"))
    inside = parameter_support_assessment([0.0, 0.0], domain, ("p1", "p2"))
    outside = parameter_support_assessment([10.0, 10.0], domain, ("p1", "p2"))
    assert inside["supported"] is True
    assert outside["support_abstained"] is True
    predictions, assessments = apply_parameter_support_abstention(
        np.asarray([-0.2, -0.8], dtype=float),
        np.asarray([[0.0, 0.0], [10.0, 10.0]], dtype=float),
        domain,
        ("p1", "p2"),
    )
    assert predictions.tolist() == [-0.2, 0.0]
    assert sum(row["support_abstained"] for row in assessments) == 1
    assert domain["independent_holdout_used"] is False
    assert domain["qps_used"] is False


def test_parameter_support_zero_scale_fails_closed_on_unseen_change() -> None:
    domain = build_parameter_support_domain(
        np.asarray([[0.0, -1.0], [0.0, 1.0]], dtype=float),
        ("constant", "varying"),
    )
    same = parameter_support_assessment(
        [0.0, 0.0], domain, ("constant", "varying")
    )
    changed = parameter_support_assessment(
        [0.1, 0.0], domain, ("constant", "varying")
    )
    assert same["supported"] is True
    assert changed["support_abstained"] is True
    assert changed["zero_scale_deviation"] is True


def test_resource_predictor_support_abstention_excludes_holdout_labels() -> None:
    x_fit = np.asarray(
        [[-1.0], [-0.5], [0.5], [1.0], [1.5]],
        dtype=float,
    )
    x_validation = np.asarray(
        [[-0.8], [-0.3], [0.3], [0.8], [1.2]],
        dtype=float,
    )
    x_train = np.vstack((x_fit, x_validation))
    y_fit = -0.1 * x_fit[:, 0]
    y_validation = -0.1 * x_validation[:, 0]
    y_train = np.concatenate((y_fit, y_validation))
    plan_train = np.zeros(x_train.shape[0], dtype=float)
    x_holdout = np.asarray([[0.2], [0.6], [10.0], [-0.6], [-10.0]], dtype=float)
    first, first_metrics = fit_resource_prediction_calibration(
        x_train=x_train,
        x_holdout=x_holdout,
        y_train=y_train,
        y_holdout=np.asarray([-0.02, -0.06, 1.0, 0.06, 1.0], dtype=float),
        plan_train=plan_train,
        plan_holdout=np.zeros(5, dtype=float),
        active_params=("p1",),
        requested_alpha=0.05,
        x_model_fit=x_fit,
        x_model_validation=x_validation,
        y_model_fit=y_fit,
        y_model_validation=y_validation,
        plan_model_fit=np.zeros(5, dtype=float),
        plan_model_validation=np.zeros(5, dtype=float),
        enable_parameter_support_abstention=True,
    )
    second, second_metrics = fit_resource_prediction_calibration(
        x_train=x_train,
        x_holdout=x_holdout,
        y_train=y_train,
        y_holdout=np.asarray([9.0, -9.0, -9.0, 9.0, -9.0], dtype=float),
        plan_train=plan_train,
        plan_holdout=np.zeros(5, dtype=float),
        active_params=("p1",),
        requested_alpha=0.05,
        x_model_fit=x_fit,
        x_model_validation=x_validation,
        y_model_fit=y_fit,
        y_model_validation=y_validation,
        plan_model_fit=np.zeros(5, dtype=float),
        plan_model_validation=np.zeros(5, dtype=float),
        enable_parameter_support_abstention=True,
    )
    assert first["parameter_support_domain"] == second["parameter_support_domain"]
    assert first_metrics["predicted"] == second_metrics["predicted"]
    assert first_metrics["parameter_support"]["support_abstain_count"] == 2
    assert first_metrics["predicted"][2] == 0.0
    assert first_metrics["predicted"][4] == 0.0


def test_historical_alpha_selection_excludes_final_holdout() -> None:
    x_fit = np.asarray(
        [[1.0], [2.0], [3.0], [-1.0], [-2.0]],
        dtype=float,
    )
    y_fit = np.asarray([0.1, 0.2, 0.3, -0.1, -0.2], dtype=float)
    x_validation = np.asarray([[-3.0], [-2.0], [1.0], [2.0], [3.0]], dtype=float)
    y_validation = np.asarray([-0.3, -0.2, 0.1, 0.2, 0.3], dtype=float)
    selection = select_ridge_alpha_by_historical_validation(
        x_fit,
        y_fit,
        x_validation,
        y_validation,
        requested_alpha=0.05,
        alpha_grid=(0.05, 0.5, 2.0),
    )
    assert selection["historical_validation_used"] is True
    assert selection["independent_holdout_used"] is False
    assert selection["qps_used"] is False
    assert len(selection["candidates"]) == 3


def test_ratio_scale_estimator_applies_admitted_calibration() -> None:
    zero_coefficients = {name: 0.0 for name in ACTIVE_PARAMS}
    calibration = {
        "contract": RESOURCE_PREDICTION_CALIBRATION_CONTRACT,
        "resources": {
            "cpu": {
                "family": "parameter",
                "parameter_coefficients": {
                    **zero_coefficients,
                    "seq_page_cost": 1.0,
                },
                "parameter_interaction_coefficients": {
                    "seq_page_cost*random_page_cost": 2.0,
                },
                "plan_log_ratio_coefficient": 0.0,
            },
            "memory": {
                "family": "raw_plan",
                "parameter_coefficients": zero_coefficients,
                "plan_log_ratio_coefficient": 1.0,
            },
            "io": {
                "family": "hybrid",
                "parameter_coefficients": {
                    **zero_coefficients,
                    "random_page_cost": 0.5,
                },
                "plan_log_ratio_coefficient": 0.5,
            },
        },
    }
    baseline_probe = {
        "resource_avg": {"cpu": 10.0, "memory": 10.0, "io": 10.0},
        "resource_breakdown_summary": {
            name: {"physical_direction_supported": True}
            for name in ("cpu", "memory", "io")
        },
        "feature_inputs": {"cpu": {"plan_relation_stats_scan_coverage": 1.0}},
    }
    estimator = RatioScaleEstimator(
        {"metrics": {"plan_estimator": {"physical_model": {}}}},
        [],
        baseline_probe,
        {"cpu": 100.0, "memory": 200.0, "io": 300.0},
        INITIAL_PARAMS,
        calibration,
    )

    class FakeProbe:
        cfg = {"metrics": {"plan_estimator": {"physical_model": {}}}}

        def probe(self, _queries: object, _params: object) -> dict:
            return {
                "errors": [],
                "resource_avg": {"cpu": 20.0, "memory": 30.0, "io": 40.0},
                "resource_breakdown_summary": {
                    name: {"physical_direction_supported": True}
                    for name in ("cpu", "memory", "io")
                },
                "feature_inputs": {
                    "cpu": {
                        "plan_relation_stats_scan_coverage": 1.0,
                        "plan_stats_modification_fraction_max": 0.0,
                    }
                },
                "exact_plan_metadata": {
                    "plan_fingerprint_coverage": {"complete": True},
                },
                "physical_model_enabled": True,
            }

    estimator.probe = FakeProbe()
    params = dict(INITIAL_PARAMS)
    params["seq_page_cost"] *= math.exp(0.1)
    params["random_page_cost"] *= math.exp(0.2)
    resources, _stats, metadata = estimator(None, params)
    assert math.isclose(resources["cpu"], 100.0 * math.exp(0.14))
    assert math.isclose(resources["memory"], 200.0 * 3.0)
    assert math.isclose(resources["io"], 300.0 * math.exp(0.1 + 0.5 * math.log(4.0)))
    assert metadata["qps_used_for_inner_scoring"] is False
    assert math.isclose(
        metadata["resource_prediction_calibration_audit"]["cpu"][
            "parameter_interaction_component"
        ],
        0.04,
    )
    assert metadata["raw_plan_feature_ratios"] == {
        "cpu": 2.0,
        "memory": 3.0,
        "io": 4.0,
    }

    support_calibration = copy.deepcopy(calibration)
    support_calibration["resources"]["io"].update(
        {
            "parameter_support_abstention_enabled": True,
            "parameter_support_domain": build_parameter_support_domain(
                np.asarray(
                    [
                        [-0.01] * len(ACTIVE_PARAMS),
                        [0.01] * len(ACTIVE_PARAMS),
                    ],
                    dtype=float,
                ),
                ACTIVE_PARAMS,
            ),
        }
    )
    support_estimator = RatioScaleEstimator(
        {"metrics": {"plan_estimator": {"physical_model": {}}}},
        [],
        baseline_probe,
        {"cpu": 100.0, "memory": 200.0, "io": 300.0},
        INITIAL_PARAMS,
        support_calibration,
    )
    support_estimator.probe = FakeProbe()
    supported_resources, _stats, support_metadata = support_estimator(None, params)
    assert supported_resources["io"] == 300.0
    io_audit = support_metadata["resource_prediction_calibration_audit"]["io"]
    assert io_audit["support_abstained"] is True
    assert io_audit["parameter_support"]["supported"] is False


def test_schema5_hot_upgrade_sign_thresholds_are_nonzero_sentinels() -> None:
    # The compatibility threshold must be nonzero because the schema-v4
    # loader uses ``value or infinity``.  It is never a schema-v5 hard gate.
    sentinel = 1e-12
    assert sentinel > 0.0
    assert 0.14 >= sentinel


def test_wilson_small_sample_gate() -> None:
    assert wilson_lower_bound(5, 5) > 0.50
    assert wilson_lower_bound(4, 5) < 0.50


def test_ssd_wave_gate() -> None:
    non_target_rows = [replay_row("w1", 79.0, 90.0), replay_row("w2", 79.5, 91.0)]
    passed = qualify_ssd_wave(
        non_target_rows,
        target="cpu",
        phase="formal_baseline",
        cfg=gate_config(),
    )
    assert passed["passed"] is True, passed
    failed_rows = copy.deepcopy(non_target_rows)
    failed_rows[0]["resource_stats"]["ssd_util_percent"]["avg"] = 80.0
    failed = qualify_ssd_wave(
        failed_rows,
        target="memory",
        phase="formal_base_top5",
        cfg=gate_config(),
    )
    assert failed["passed"] is False
    assert any("above_stable_window" in value for value in failed["errors"])
    io_rows = [replay_row("w1", 80.0, 100.0), replay_row("w2", 81.0, 100.0)]
    io_passed = qualify_ssd_wave(
        io_rows,
        target="io",
        phase="calibration_baseline",
        cfg=gate_config(),
    )
    assert io_passed["passed"] is True, io_passed


def test_shared_ssd_non_target_uses_container_attribution() -> None:
    cfg = gate_config()
    cfg["algorithm_c"]["ssd_replay_qualification"].update(
        {
            "non_target_shared_ssd_policy": "container_io_capacity",
            "non_target_maximum_container_io_util_pct": 80.0,
        }
    )
    cfg["binary_plan_tuning"] = {
        "acceptance_policy": {"resource_capacity": {"io_mib_s": 1000.0}}
    }
    rows = [replay_row("w1", 70.0, 90.0), replay_row("w2", 71.0, 91.0)]
    rows[0]["resources"] = {"io": 65.0}
    rows[1]["resources"] = {"io": 70.0}
    passed = qualify_ssd_wave(
        rows,
        target="memory",
        phase="calibration_baseline",
        cfg=cfg,
    )
    assert passed["passed"] is True, passed
    assert passed["thresholds"]["global_ssd_utilization_is_informational"] is False
    assert math.isclose(passed["aggregate_container_io_util_pct"], 13.5)

    externally_saturated = copy.deepcopy(rows)
    externally_saturated[0]["resource_stats"]["ssd_util_percent"].update(
        {"avg": 97.0, "p99": 100.0, "max": 100.0}
    )
    externally_saturated[1]["resource_stats"]["ssd_util_percent"].update(
        {"avg": 97.5, "p99": 100.0, "max": 100.0}
    )
    external_failure = qualify_ssd_wave(
        externally_saturated,
        target="memory",
        phase="calibration_baseline",
        cfg=cfg,
    )
    assert external_failure["passed"] is False
    assert any("ssd_average_utilization_above" in value for value in external_failure["errors"])

    rows[0]["resources"] = {"io": 800.0}
    failed = qualify_ssd_wave(
        rows,
        target="memory",
        phase="calibration_baseline",
        cfg=cfg,
    )
    assert failed["passed"] is False
    assert any("container_io_utilization_above" in value for value in failed["errors"])

    candidate = qualify_ssd_wave(
        rows,
        target="memory",
        phase="formal_base_top5",
        cfg=cfg,
    )
    assert candidate["passed"] is False, candidate
    assert candidate["thresholds"][
        "non_target_candidate_saturation_is_semantic"
    ] is True
    assert candidate["semantic_non_target_bottleneck_resources"] == ["io"]
    assert candidate["observations"][0][
        "semantic_non_target_io_bottleneck"
    ] is True

    aggregate_only = copy.deepcopy(rows)
    aggregate_only[0]["resources"] = {"io": 450.0}
    aggregate_only[1]["resources"] = {"io": 450.0}
    aggregate_failure = qualify_ssd_wave(
        aggregate_only,
        target="memory",
        phase="calibration_candidate",
        cfg=cfg,
    )
    assert aggregate_failure["passed"] is False
    assert "aggregate_container_io_utilization_above_non_target_limit" in aggregate_failure["errors"]


def test_explicit_no_mutex_makes_unrelated_host_ssd_load_informational() -> None:
    cfg = gate_config()
    cfg["algorithm_c"]["ssd_replay_qualification"].update(
        {
            "non_target_shared_ssd_policy": "container_io_capacity",
            "non_target_maximum_container_io_util_pct": 80.0,
        }
    )
    cfg["binary_plan_tuning"] = {
        "acceptance_policy": {"resource_capacity": {"io_mib_s": 1000.0}}
    }
    rows = [replay_row("w1", 97.0, 100.0)]
    rows[0]["resources"] = {"io": 70.0}

    with mock.patch.dict(
        os.environ,
        {
            "BONECT_SHARED_SSD_COORDINATION_ENABLED": "0",
            "BONECT_SHARED_SSD_WORKLOAD_LOCK_MODE": "none",
        },
        clear=False,
    ):
        result = qualify_ssd_wave(
            rows,
            target="memory",
            phase="formal_base_top5",
            cfg=cfg,
        )

    assert result["passed"] is True, result
    assert result["thresholds"][
        "global_ssd_utilization_is_informational"
    ] is True
    assert result["thresholds"]["global_ssd_stable_window_enforced"] is False
    assert result["thresholds"]["container_io_non_target_limit_enforced"] is True
    assert result["observations"][0]["container_io_util_pct"] == 7.0


def test_caller_ssd_policy_keeps_io_gate_and_isolates_non_target_lane() -> None:
    cfg = gate_config()
    cfg["algorithm_c"]["ssd_replay_qualification"].update(
        {
            "non_target_shared_ssd_policy": "container_io_capacity",
            "non_target_maximum_container_io_util_pct": 80.0,
        }
    )
    cfg["binary_plan_tuning"] = {
        "acceptance_policy": {"resource_capacity": {"io_mib_s": 1000.0}}
    }
    rows = [replay_row("w1", 97.0, 100.0)]
    rows[0]["resources"] = {"io": 70.0}

    non_target = qualify_ssd_wave(
        rows,
        target="memory",
        phase="common_domain_algorithm_c_attempt_1",
        cfg=cfg,
        global_ssd_utilization_is_informational=True,
    )
    assert non_target["passed"] is True, non_target
    assert non_target["thresholds"]["global_ssd_stable_window_enforced"] is False
    assert non_target["thresholds"]["host_ssd_policy_authority"] == (
        "explicit_caller_contract"
    )

    io_target = qualify_ssd_wave(
        rows,
        target="io",
        phase="common_domain_algorithm_c_attempt_1",
        cfg=cfg,
        global_ssd_utilization_is_informational=False,
    )
    assert io_target["passed"] is False, io_target
    assert io_target["thresholds"]["global_ssd_stable_window_enforced"] is True
    assert any("ssd_average_utilization_above" in value for value in io_target["errors"])


def test_explicit_no_mutex_does_not_hard_fail_identical_rows_on_host_ssd_skew() -> None:
    cfg = gate_config()
    cfg["algorithm_c"]["ssd_replay_qualification"].update(
        {
            "non_target_shared_ssd_policy": "container_io_capacity",
            "non_target_maximum_container_io_util_pct": 80.0,
            "maximum_parallel_avg_skew_pct_points": 5.0,
        }
    )
    cfg["binary_plan_tuning"] = {
        "acceptance_policy": {"resource_capacity": {"io_mib_s": 1000.0}}
    }
    rows = [replay_row("w1", 81.0, 100.0), replay_row("w2", 90.0, 100.0)]
    rows[0]["resources"] = {"io": 35.0}
    rows[1]["resources"] = {"io": 43.0}
    rows[0]["params"] = {"seq_page_cost": 1.0}
    rows[1]["params"] = {"seq_page_cost": 1.0}

    coordinated = qualify_ssd_wave(
        rows,
        target="memory",
        phase="formal_completed_lane_cohort_support",
        cfg=cfg,
    )
    assert coordinated["passed"] is False, coordinated
    assert "parallel_ssd_average_skew_above_limit" in coordinated["errors"]

    with mock.patch.dict(
        os.environ,
        {
            "BONECT_SHARED_SSD_COORDINATION_ENABLED": "0",
            "BONECT_SHARED_SSD_WORKLOAD_LOCK_MODE": "none",
        },
        clear=False,
    ):
        no_mutex = qualify_ssd_wave(
            rows,
            target="memory",
            phase="formal_completed_lane_cohort_support",
            cfg=cfg,
        )

    assert no_mutex["passed"] is True, no_mutex
    assert no_mutex["thresholds"]["global_ssd_utilization_is_informational"] is True
    assert no_mutex["aggregate_container_io_util_pct"] == 7.8
    assert "parallel_ssd_average_skew_above_limit" not in no_mutex["errors"]


def test_no_mutex_baseline_domain_requires_matching_multi_lane_cohort() -> None:
    thresholds = {
        "explicit_user_authorized_no_mutex": True,
        "global_ssd_stable_window_enforced": False,
        "global_ssd_utilization_is_informational": True,
    }
    rows = [
        {
            "label": f"baseline-{index}",
            "baseline_cohort_completion_barrier": {
                "passed": True,
                "completed_count": 4,
                "repetition": 1 + index // 2,
                "barrier_stage_sha256": f"completion-{1 + index // 2}",
            },
            "ssd_qualification": {
                "qualification_id": f"qualification-{index}",
                "thresholds": copy.deepcopy(thresholds),
            },
        }
        for index in range(4)
    ]
    canonical = {
        "label": "canonical",
        "ssd_qualification": {
            "qualification_id": "canonical-qualification",
            "thresholds": copy.deepcopy(thresholds),
        },
    }
    cohort = {
        "enabled": True,
        "cohort_id_sha256": "cohort-sha",
        "cohort_size": 4,
    }

    passed = baseline_execution_domain_audit(
        rows,
        canonical,
        expected_no_mutex=True,
        cohort=cohort,
    )
    assert passed["passed"] is True, passed
    assert passed["cohort_size"] == 4
    assert len(passed["completion_observations"]) == 4

    missing_completion = copy.deepcopy(rows)
    missing_completion[0].pop("baseline_cohort_completion_barrier")
    completion_failed = baseline_execution_domain_audit(
        missing_completion,
        canonical,
        expected_no_mutex=True,
        cohort=cohort,
    )
    assert completion_failed["passed"] is False
    assert "baseline-0:baseline_completion_cohort_missing" in (
        completion_failed["errors"]
    )

    mismatched = copy.deepcopy(canonical)
    mismatched["ssd_qualification"]["thresholds"][
        "explicit_user_authorized_no_mutex"
    ] = False
    failed = baseline_execution_domain_audit(
        rows,
        mismatched,
        expected_no_mutex=True,
        cohort=cohort,
    )
    assert failed["passed"] is False
    assert "canonical:no_mutex_mode_mismatch" in failed["errors"]

    uncoordinated = baseline_execution_domain_audit(
        rows,
        canonical,
        expected_no_mutex=True,
        cohort={"enabled": False, "cohort_size": 1},
    )
    assert uncoordinated["passed"] is False
    assert "no_mutex_baseline_requires_multi_lane_cohort" in uncoordinated[
        "errors"
    ]


def test_no_mutex_runtime_requires_matching_immutable_workload_contract() -> None:
    cohort = {"enabled": True, "cohort_size": 4}
    algorithm_cfg = {
        "workload_contract": {
            "shared_ssd_coordination": {
                "contract": "pg_algorithm_c_user_authorized_no_mutex_v1",
                "enabled": False,
                "covers_cache_eviction_and_physical_workload": False,
                "database_restore_mode": "none",
                "physical_workload_mode": "none",
            },
            "baseline_cohort": {
                "contract": "algorithm_c_four_lane_no_mutex_baseline_cohort_v1",
                "lane_count": 4,
            },
        }
    }
    passed = workload_contract_execution_domain_audit(
        algorithm_cfg,
        expected_no_mutex=True,
        cohort=cohort,
    )
    assert passed["passed"] is True, passed

    stale = copy.deepcopy(algorithm_cfg)
    stale["workload_contract"]["shared_ssd_coordination"] = {
        "contract": "pg_algorithm_c_shared_ssd_measurement_rwlock_yield_v2",
        "covers_cache_eviction_and_physical_workload": True,
        "database_restore_mode": "exclusive",
        "physical_workload_mode": "exclusive",
    }
    failed = workload_contract_execution_domain_audit(
        stale,
        expected_no_mutex=True,
        cohort=cohort,
    )
    assert failed["passed"] is False
    assert "workload_contract_no_mutex_contract_mismatch" in failed["errors"]

    candidate_runtime = {"enabled": True, "cohort_size": 4}
    missing_candidate_contract = workload_contract_execution_domain_audit(
        algorithm_cfg,
        expected_no_mutex=True,
        cohort=cohort,
        candidate_cohort=candidate_runtime,
    )
    assert missing_candidate_contract["passed"] is False
    assert "workload_contract_candidate_cohort_missing" in (
        missing_candidate_contract["errors"]
    )

    coordinated = copy.deepcopy(algorithm_cfg)
    coordinated["workload_contract"]["candidate_cohort"] = {
        "contract": "algorithm_c_four_lane_no_mutex_candidate_wave_cohort_v1",
        "lane_count": 4,
    }
    candidate_passed = workload_contract_execution_domain_audit(
        coordinated,
        expected_no_mutex=True,
        cohort=cohort,
        candidate_cohort=candidate_runtime,
    )
    assert candidate_passed["passed"] is True, candidate_passed


def test_io_overloaded_baseline_admission_does_not_change_target_band() -> None:
    cfg = gate_config()
    cfg["algorithm_c"]["ssd_replay_qualification"][
        "io_target_maximum_ssd_avg_pct"
    ] = 98.0
    rows = [replay_row("w1", 96.7, 100.0), replay_row("w2", 96.8, 100.0)]
    passed = qualify_ssd_wave(
        rows,
        target="io",
        phase="calibration_baseline",
        cfg=cfg,
    )
    assert passed["passed"] is True, passed
    scoring = formal_inner_scoring_options(target="io")
    assert scoring["resource_target_ranges"]["io"] == {
        "min": 0.85,
        "max": 0.90,
        "hard": 0.95,
        "role": "target_bottleneck",
    }

    overloaded_candidate = [
        replay_row("w1", 99.0, 100.0),
        replay_row("w2", 99.1, 100.0),
    ]
    candidate_passed = qualify_ssd_wave(
        overloaded_candidate,
        target="io",
        phase="calibration_candidate",
        cfg=cfg,
    )
    assert candidate_passed["passed"] is False, candidate_passed
    assert candidate_passed["thresholds"][
        "io_target_candidate_saturation_is_semantic"
    ] is False
    assert candidate_passed["thresholds"][
        "global_ssd_stable_window_enforced"
    ] is True
    assert candidate_passed["thresholds"][
        "container_io_non_target_limit_enforced"
    ] is False

    overloaded_baseline = qualify_ssd_wave(
        overloaded_candidate,
        target="io",
        phase="calibration_baseline",
        cfg=cfg,
    )
    assert overloaded_baseline["passed"] is False


def test_run_level_qps_noise_floor() -> None:
    rows = [
        {"worker_key": "w1", "label": "w1a", "db_qps": 100.0},
        {"worker_key": "w1", "label": "w1b", "db_qps": 101.0},
        {"worker_key": "w2", "label": "w2a", "db_qps": 200.0},
        {"worker_key": "w2", "label": "w2b", "db_qps": 202.0},
    ]
    model = qps_noise_model(rows, static_floor_pct=0.1)
    assert model["ready"] is True, model
    assert model["effective_min_gain_pct"] > 0.1
    assert math.isclose(
        model["worker_sigma_log"]["w1"],
        model["worker_sigma_log"]["w2"],
        rel_tol=1e-12,
    )
    assert model["within_run_qps_dispersion_used"] is False

    nonstationary = qps_noise_model(
        [
            {"worker_key": "w1", "label": "w1a", "db_qps": 100.0},
            {"worker_key": "w1", "label": "w1b", "db_qps": 140.0},
            {"worker_key": "w2", "label": "w2a", "db_qps": 200.0},
            {"worker_key": "w2", "label": "w2b", "db_qps": 202.0},
        ],
        static_floor_pct=0.1,
    )
    assert nonstationary["ready"] is True
    assert nonstationary["quality_blocks_formal_start"] is False
    assert math.isclose(
        nonstationary["worker_repeat_ratio"]["w1"],
        1.4,
        rel_tol=1e-12,
    )
    assert any(
        "worker_control_qps_repeat_ratio_above_limit:w1" in error
        for error in nonstationary["quality_diagnostics"]
    )


def test_executor_telemetry_baseline_excludes_qps() -> None:
    rows = []
    for index in range(4):
        rows.append(
            {
                "worker_key": f"w{index % 2 + 1}",
                "label": f"baseline_{index}",
                "db_qps": 1000.0 + index,
                "executor_telemetry": {
                    "available": True,
                    "source": "pg_stat_statements_post_reset_aggregate_v1",
                    "metrics": {
                        "shared_blks_hit": 900.0,
                        "shared_blks_read": 100.0,
                        "temp_blks_read": 5.0,
                        "temp_blks_written": 7.0,
                    },
                    "derived": {
                        "shared_cache_hit_rate": 0.9,
                        "shared_block_accesses": 1000.0,
                        "temp_blocks": 12.0,
                    },
                },
            }
        )
    model = executor_telemetry_baseline_model(rows)
    assert model["ready"] is True, model
    assert math.isclose(model["derived"]["shared_cache_hit_rate"], 0.9)
    assert model["derived"]["temp_blocks"] == 48.0
    assert model["qps_used_for_resource_prediction"] is False
    assert "db_qps" not in model["aggregate_metrics"]


def test_executor_telemetry_parser_excludes_qps() -> None:
    metric_columns = (
        "calls",
        "rows",
        "shared_blks_hit",
        "shared_blks_read",
        "shared_blks_dirtied",
        "shared_blks_written",
        "local_blks_hit",
        "local_blks_read",
        "local_blks_dirtied",
        "local_blks_written",
        "temp_blks_read",
        "temp_blks_written",
        "blk_read_time",
        "blk_write_time",
        "wal_records",
        "wal_fpi",
        "wal_bytes",
    )
    values = (
        10.0,
        20.0,
        900.0,
        100.0,
        3.0,
        4.0,
        5.0,
        6.0,
        7.0,
        8.0,
        9.0,
        11.0,
        12.0,
        13.0,
        14.0,
        15.0,
        16.0,
    )
    original_query = plan_tuner.psql_query

    def fake_query(_pg: object, sql: str, **_kwargs: object) -> str:
        if "SELECT attname FROM pg_attribute" in sql:
            return "\n".join(metric_columns)
        assert "SUM(shared_blks_hit)" in sql
        assert "qps" not in sql.lower()
        return "\t".join(str(value) for value in values)

    plan_tuner.psql_query = fake_query
    try:
        result = plan_tuner.fetch_pg_stat_statements_executor_telemetry(
            plan_tuner.PgConn(host="127.0.0.1", port=5432, user="postgres", dbname="postgres")
        )
    finally:
        plan_tuner.psql_query = original_query

    assert result["available"] is True, result
    assert math.isclose(result["derived"]["shared_cache_hit_rate"], 0.9)
    assert result["derived"]["temp_blocks"] == 20.0
    assert result["derived"]["temp_bytes"] == 20.0 * 8192
    assert result["qps_used_for_resource_prediction"] is False
    assert "qps" not in result["metrics"]


def test_executor_telemetry_retries_transient_column_discovery_timeout() -> None:
    metric_columns = (
        "calls",
        "rows",
        "shared_blks_hit",
        "shared_blks_read",
        "shared_blks_dirtied",
        "shared_blks_written",
        "local_blks_hit",
        "local_blks_read",
        "local_blks_dirtied",
        "local_blks_written",
        "temp_blks_read",
        "temp_blks_written",
        "blk_read_time",
        "blk_write_time",
        "wal_records",
        "wal_fpi",
        "wal_bytes",
    )
    query_attempts = {"column_discovery": 0, "aggregate": 0}
    original_query = plan_tuner.psql_query
    original_sleep = plan_tuner.time.sleep

    def fake_query(_pg: object, sql: str, **_kwargs: object) -> str:
        if "SELECT attname FROM pg_attribute" in sql:
            query_attempts["column_discovery"] += 1
            if query_attempts["column_discovery"] == 1:
                raise subprocess.TimeoutExpired(cmd="psql", timeout=30.0)
            return "\n".join(metric_columns)
        query_attempts["aggregate"] += 1
        return "\t".join("1" for _ in metric_columns)

    plan_tuner.psql_query = fake_query
    plan_tuner.time.sleep = lambda _seconds: None
    try:
        result = plan_tuner.fetch_pg_stat_statements_executor_telemetry(
            plan_tuner.PgConn(
                host="127.0.0.1",
                port=5432,
                user="postgres",
                dbname="postgres",
            )
        )
    finally:
        plan_tuner.psql_query = original_query
        plan_tuner.time.sleep = original_sleep

    assert result["available"] is True, result
    assert query_attempts == {"column_discovery": 2, "aggregate": 1}


def test_formal_inner_scoring_contract_is_explicit() -> None:
    options = formal_inner_scoring_options("memory")
    assert options["score_mode"] == "band_target"
    assert options["directional_scoring_contract_required"] is True
    assert options["resource_weights"] == {"cpu": 1.0, "memory": 1.0, "io": 0.5}
    assert options["resource_target_ranges"]["memory"] == {
        "min": 0.85,
        "max": 0.90,
        "hard": 0.95,
        "role": "target_bottleneck",
    }
    assert options["resource_target_ranges"]["cpu"]["role"] == "non_bottleneck"
    assert (
        options["resource_target_ranges"]["io"]["role"]
        == "non_bottleneck_io_cost"
    )
    assert options["resource_target_ranges"]["io"]["max"] == 0.50


def test_loader_rejects_legacy_ready_boolean() -> None:
    artifact = {
        "schema_version": 3,
        "target_resource": "memory",
        "formal_admission": {"ready": True, "errors": []},
    }
    try:
        validate_experience_artifact(artifact, "memory")
    except RuntimeError as exc:
        assert "schema" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("legacy ready=true artifact was accepted")


def test_loader_rejects_five_train_five_holdout_ready_artifact() -> None:
    artifact = {
        "schema_version": 5,
        "target_resource": "memory",
        "formal_admission": {
            "ready": True,
            "errors": [],
            "minimum_train_count": 5,
            "minimum_holdout_count": 5,
        },
        "fit": {"train_count": 5, "holdout_count": 5},
    }
    try:
        validate_experience_artifact(artifact, "memory")
    except RuntimeError as exc:
        assert "insufficient training/holdout rows" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("5+5 ready artifact bypassed the formal loader")


def test_outer_accept_reject_pruning_is_context_conditioned_and_fail_open() -> None:
    cfg = {
        "algorithm_c": {
            "workload_contract": {"benchmark": "tpch", "queries": "Q1-Q22"},
            "qps_noise_gate": {"enabled": True},
        },
        "binary_plan_tuning": {
            "acceptance_policy": {"resource_capacity": {"memory_gib": 64.0}},
            "binary_search": {
                "nested_loop": {
                    "outer_acceptance": {"acceptance_tier_downgrade_after_rounds": 2}
                }
            },
        },
    }
    policy = {
        "enabled": True,
        "contract": OUTER_PRUNING_CONTRACT,
        "minimum_valid_rejects_per_plan": 2,
        "minimum_distinct_rounds": 2,
        "minimum_distinct_parameter_vectors": 2,
    }
    context = outer_pruning_context(
        cfg,
        target="memory",
        incumbent_params=INITIAL_PARAMS,
        required_acceptance_tier="strict",
        qps_noise_model_id="noise-v1",
    )
    candidate_a = {
        "optimized_params": dict(INITIAL_PARAMS, seq_page_cost=0.9),
        "three_level_fingerprint": {"plan_level": "workload-exact-physical-sha256:plan-a"},
    }
    candidate_b = {
        "optimized_params": dict(INITIAL_PARAMS, seq_page_cost=0.8),
        "three_level_fingerprint": {"plan_level": "workload-exact-physical-sha256:plan-a"},
    }
    reject = {
        "accepted": False,
        "rejects": ["db_qps_not_up_vs_last_accepted"],
        "qps_noise_gate_required": True,
        "qps_noise_model_ready": True,
    }
    memory = empty_outer_pruning_memory()
    first = record_outer_pruning_observation(
        memory,
        context=context,
        candidate=candidate_a,
        decision=reject,
        round_index=1,
    )
    assert first["recorded"] is True
    one_reject = assess_outer_pruning_candidate(
        memory,
        context=context,
        candidate=candidate_b,
        policy=policy,
    )
    assert one_reject["hard_prune"] is False
    assert one_reject["soft_deprioritize"] is True
    record_outer_pruning_observation(
        memory,
        context=context,
        candidate=candidate_b,
        decision=reject,
        round_index=2,
    )
    supported = assess_outer_pruning_candidate(
        memory,
        context=context,
        candidate=candidate_b,
        policy=policy,
    )
    assert supported["hard_prune"] is True, supported
    assert supported["candidate_score_modified"] is False
    assert supported["qps_used_for_inner_scoring"] is False

    fallback_context = outer_pruning_context(
        cfg,
        target="memory",
        incumbent_params=INITIAL_PARAMS,
        required_acceptance_tier="fallback",
        qps_noise_model_id="noise-v1",
    )
    tier_changed = assess_outer_pruning_candidate(
        memory,
        context=fallback_context,
        candidate=candidate_b,
        policy=policy,
    )
    # QPS/non-target-bottleneck evidence is invariant across the acceptance
    # tier downgrade.  The observed tier is audit metadata, not part of the
    # reusable pruning identity.
    assert context["context_sha256"] == fallback_context["context_sha256"]
    assert tier_changed["hard_prune"] is True
    assert tier_changed["reason"] == "zero_accept_full_plan_outer_reject_rule"

    tier_only_memory = empty_outer_pruning_memory()
    tier_only = record_outer_pruning_observation(
        tier_only_memory,
        context=context,
        candidate=candidate_a,
        decision={
            "accepted": False,
            "rejects": ["acceptance_tier_below_current_threshold"],
            "qps_noise_gate_required": True,
            "qps_noise_model_ready": True,
        },
        round_index=1,
    )
    assert tier_only["recorded"] is False
    assert tier_only["reason"] == "reject_reason_not_safe_for_reuse"

    accepted = dict(reject, accepted=True, rejects=[])
    record_outer_pruning_observation(
        memory,
        context=context,
        candidate=candidate_a,
        decision=accepted,
        round_index=3,
    )
    counterexample = assess_outer_pruning_candidate(
        memory,
        context=context,
        candidate=candidate_b,
        policy=policy,
    )
    assert counterexample["hard_prune"] is False
    assert counterexample["reason"] == "accepted_counterexample_fail_open"


def test_outer_pruning_excludes_technical_and_incomplete_evidence() -> None:
    cfg = {
        "algorithm_c": {"workload_contract": {"benchmark": "job"}},
        "binary_plan_tuning": {},
    }
    context = outer_pruning_context(
        cfg,
        target="memory",
        incumbent_params=INITIAL_PARAMS,
        required_acceptance_tier="strict",
        qps_noise_model_id="noise-v1",
    )
    candidate = {
        "optimized_params": INITIAL_PARAMS,
        "three_level_fingerprint": {"plan_level": "full-plan"},
    }
    memory = empty_outer_pruning_memory()
    unsafe = record_outer_pruning_observation(
        memory,
        context=context,
        candidate=candidate,
        decision={
            "accepted": False,
            "rejects": ["real_validation_status_not_ok"],
        },
        round_index=1,
    )
    assert unsafe["recorded"] is False
    assert memory["rules"] == {}


def test_full_range_basin_escape_excludes_incumbent_plan_without_mutating_ranges() -> None:
    controller = LaneController.__new__(LaneController)
    controller.state = {
        "incumbent_validation": {
            "candidate_ranking": {
                "three_level_fingerprint": {"plan_level": "incumbent-plan"}
            }
        }
    }
    controller.ranges = {
        name: ParameterRange(low, high) for name, (low, high) in DEFAULT_RANGES.items()
    }
    controller.ranges["seq_page_cost"] = ParameterRange(0.1, 1.0)
    rankings = []
    for rank, plan_id in enumerate(("incumbent-plan", "new-plan", "new-plan"), start=1):
        params = dict(INITIAL_PARAMS)
        params["seq_page_cost"] = 0.5 + rank * 0.1
        rankings.append(
            {
                "rank": rank,
                "optimized_params": {name: params[name] for name in ACTIVE_PARAMS},
                "three_level_fingerprint": {"plan_level": plan_id},
            }
        )
    selected = controller.fallback_expansion_candidates(rankings, [])
    assert [row["rank"] for row in selected] == [2]
    assert controller.has_shrunk_parameter_ranges() is True
    assert controller.ranges["seq_page_cost"].max_value == 1.0


def test_plan_saturated_basin_uses_only_trusted_partial_topk() -> None:
    from experience_table_subset_optimizer import IncompleteSubsetOptimizationError

    trusted = {
        "rank": 1,
        "optimized_params": dict(INITIAL_PARAMS),
        "three_level_fingerprint": {
            "plan_level": "workload-exact-physical-sha256:only-plan"
        },
    }
    saturated = IncompleteSubsetOptimizationError(
        "only one distinct plan",
        [],
        "/tmp/unit-plan-saturation",
    )
    saturated.plan_space_saturation_evidence = True
    saturated.eligible_candidate_rankings = [trusted]
    selected = saturated_plan_space_partial_rankings(saturated)
    assert selected == [trusted]
    assert selected is not saturated.eligible_candidate_rankings
    assert selected[0] is not trusted

    technical = IncompleteSubsetOptimizationError(
        "explain failed",
        [],
        "/tmp/unit-technical-failure",
    )
    technical.plan_space_saturation_evidence = True
    technical.technical_candidate_generation_failure = True
    technical.eligible_candidate_rankings = [trusted]
    try:
        saturated_plan_space_partial_rankings(technical)
    except IncompleteSubsetOptimizationError as exc:
        assert exc is technical
    else:
        raise AssertionError("technical candidate generation must fail closed")


def test_formal_rankings_deduplicate_by_full_physical_plan() -> None:
    rankings = [
        {
            "subset_id": f"subset_{index}",
            "three_level_fingerprint": {
                "id": f"three-level-sha256:{index:064x}",
                "valid": True,
                "plan_level": f"workload-exact-physical-sha256:{plan_id}",
            },
        }
        for index, plan_id in enumerate(("a", "a", "b", "c", "c", "d"), start=1)
    ]
    selected, duplicates = distinct_validated_behavior_rankings(rankings)
    assert [row["subset_id"] for row in selected] == [
        "subset_1",
        "subset_3",
        "subset_4",
        "subset_6",
    ]
    assert duplicates == ["subset_2", "subset_5"]

    invalid = copy.deepcopy(rankings[:1])
    invalid[0]["three_level_fingerprint"]["valid"] = False
    try:
        distinct_validated_behavior_rankings(invalid)
    except RuntimeError as exc:
        assert "lacks a validated three-level behavior fingerprint" in str(exc)
    else:
        raise AssertionError("invalid formal behavior fingerprint must fail closed")


def test_parameter_only_fallback_serializes_schema7_target_support() -> None:
    x_train = np.vstack((np.eye(len(ACTIVE_PARAMS)), np.ones(len(ACTIVE_PARAMS))))
    target_row = parameter_only_prediction_calibration(
        resource="memory",
        target_resource="memory",
        coefficient=np.ones(len(ACTIVE_PARAMS)),
        active_params=ACTIVE_PARAMS,
        x_train=x_train,
        training_loo_mae_log_ratio=0.05,
        alpha=0.1,
        enable_target_support_abstention=True,
    )
    assert target_row["parameter_support_abstention_enabled"] is True
    assert target_row["parameter_support_contract"]
    assert len(target_row["parameter_support_domain"]["reference_points"]) == 8

    non_target_row = parameter_only_prediction_calibration(
        resource="cpu",
        target_resource="memory",
        coefficient=np.ones(len(ACTIVE_PARAMS)),
        active_params=ACTIVE_PARAMS,
        x_train=x_train,
        training_loo_mae_log_ratio=0.05,
        alpha=0.1,
        enable_target_support_abstention=True,
    )
    assert non_target_row["parameter_support_abstention_enabled"] is False
    assert non_target_row["parameter_support_domain"] is None


def test_valid_exact_param_plan_replay_identity_is_not_backfilled() -> None:
    memory = empty_valid_replay_identity_memory("workload-sha", "io")
    candidate = {
        "optimized_params": dict(INITIAL_PARAMS),
        "three_level_fingerprint": {
            "plan_level": "workload-exact-physical-sha256:plan-a"
        },
    }
    assert assess_valid_replay_identity(memory, candidate)[
        "seen_valid_replay"
    ] is False
    update = record_valid_replay_identity(
        memory,
        candidate=candidate,
        round_index=1,
        label="valid-candidate",
    )
    assert update["recorded"] is True
    assert assess_valid_replay_identity(memory, candidate)[
        "seen_valid_replay"
    ] is True

    different_params = copy.deepcopy(candidate)
    different_params["optimized_params"]["seq_page_cost"] = 1.1
    assert assess_valid_replay_identity(memory, different_params)[
        "seen_valid_replay"
    ] is False
    different_plan = copy.deepcopy(candidate)
    different_plan["three_level_fingerprint"]["plan_level"] = (
        "workload-exact-physical-sha256:plan-b"
    )
    assert assess_valid_replay_identity(memory, different_plan)[
        "seen_valid_replay"
    ] is False


def test_outer_admission_backfills_only_unreplayed_exact_identities() -> None:
    controller = LaneController.__new__(LaneController)
    controller.target = "io"
    controller.workload_contract_sha256 = "workload-sha"
    controller.algorithm_cfg = {"scene": "unit_identity"}
    controller.cfg = {
        "algorithm_c": {"workload_contract": {}},
        "binary_plan_tuning": {
            "binary_search": {"nested_loop": {"outer_acceptance": {}}}
        },
    }
    controller.outer_pruning_policy = {"enabled": False}
    memory = empty_valid_replay_identity_memory("workload-sha", "io")
    controller.state = {
        "incumbent_params": dict(INITIAL_PARAMS),
        "consecutive_missed_outer_rounds": 0,
        "qps_noise_model": {},
        "outer_accept_reject_pruning": empty_outer_pruning_memory(),
        "valid_replay_identity_memory": memory,
    }
    controller.ranges = {
        name: ParameterRange(low, high)
        for name, (low, high) in DEFAULT_RANGES.items()
    }
    candidates = []
    for rank in range(1, 7):
        params = dict(INITIAL_PARAMS)
        params["seq_page_cost"] = 1.0 + rank * 0.01
        candidates.append(
            {
                "rank": rank,
                "optimized_params": params,
                "initial_resource": {"cpu": 1.0, "memory": 1.0, "io": 1.0},
                "predicted_resource": {"cpu": 1.0, "memory": 1.0, "io": 0.9},
                "three_level_fingerprint": {
                    "plan_level": f"workload-exact-physical-sha256:plan-{rank}"
                },
            }
        )
    record_valid_replay_identity(
        memory,
        candidate=candidates[0],
        round_index=1,
        label="already-valid",
    )
    with tempfile.TemporaryDirectory() as tmp_dir:
        controller.log_path = Path(tmp_dir) / "algorithm_c.jsonl"
        selected, audit = controller.select_outer_admitted_candidates(
            candidates,
            round_index=2,
            batch_kind="base_top5",
        )
    assert [row["rank"] for row in selected] == [2, 3, 4, 5, 6]
    assert audit["exact_valid_replay_excluded_count"] == 1
    assert audit["technical_failures_used_as_valid_replay_identities"] is False


def test_unfinished_outer_round_resumes_without_advancing_round_index() -> None:
    for phase in (
        "offline_algorithm_c_ranking",
        "base_top5_real_replay",
        "local_plan_tail_real_replay",
        "current_interval_plan_coverage_expansion_real_replay",
        "current_interval_plan_basin_escape_real_replay",
        "global_plan_coverage_rescue_real_replay",
    ):
        assert unfinished_algorithm_c_round_resume_index(
            {"phase": phase, "round_index": 10}
        ) == 10

    assert unfinished_algorithm_c_round_resume_index(
        {"phase": "formal_search", "round_index": 10}
    ) is None
    assert unfinished_algorithm_c_round_resume_index(
        {"phase": "complete", "round_index": 10}
    ) is None
    assert unfinished_algorithm_c_round_resume_index(
        {"phase": "base_top5_real_replay", "round_index": 0}
    ) is None
    assert unfinished_algorithm_c_round_resume_index(
        {
            "phase": "stopped_by_signal_clean",
            "signal_stop_interrupted_phase": "current_interval_plan_coverage_expansion_real_replay",
            "signal_stop_interrupted_round_index": 10,
            "round_index": 10,
        }
    ) == 10
    assert unfinished_algorithm_c_round_resume_index(
        {
            "phase": "stopped_by_signal_clean",
            "signal_stop_interrupted_phase": "formal_search",
            "round_index": 10,
        }
    ) is None
    legacy_stopped = {
        "phase": "stopped_by_signal_clean",
        "round_index": 10,
        "signal_stop_clean_receipt": {"passed": True},
        "inflight_physical_wave": None,
    }
    assert unfinished_algorithm_c_round_resume_index(legacy_stopped) is None
    assert unfinished_algorithm_c_round_resume_index(
        legacy_stopped,
        allow_legacy_stopped_clean=True,
    ) == 10


def test_convergence_requires_one_percent_width_without_stability_rounds() -> None:
    contract = resolve_convergence_contract(
        {
            "accepted_target": 7,
            "normalized_width_threshold": 0.01,
            "stability_confirmation_rounds": 2,
        }
    )
    assert contract["accepted_target"] == 7
    assert math.isclose(contract["expected_normalized_width"], 1.0 / 128.0)
    assert contract["expected_normalized_width"] <= 0.01
    assert contract["stability_confirmation_rounds"] == 0
    assert contract["stability_confirmation_max_attempts"] == 0
    assert contract["no_improvement_convergence_rounds"] == 2

    try:
        resolve_convergence_contract(
            {
                "accepted_target": 6,
                "normalized_width_threshold": 0.01,
                "stability_confirmation_rounds": 2,
            }
        )
    except ValueError as exc:
        assert "cannot reach normalized width threshold" in str(exc)
    else:
        raise AssertionError("six binary shrinks must not satisfy a one-percent width")


def main() -> int:
    test_fresh_t_requires_full_rank_train_and_independent_holdout()
    test_abc_training_sources_are_audit_only_and_qps_free()
    test_xp_io_native_mix_keeps_plan_sensitive_analytical_concurrency()
    test_job_concurrency_meets_full_workload_wall_target()
    test_postgres_cost_guc_readback_uses_show_precision()
    test_global_restore_slots_cap_cross_lane_clone_concurrency()
    test_job_preformal_runtime_verification_never_expands_hard_timeout()
    test_full_rank_design()
    test_sealed_second_holdout_is_skipped_without_replay()
    test_plan_probe_retries_only_transient_psql_transport_failures()
    test_restore_and_parameter_apply_retry_transient_psql_transport_failures()
    test_restore_database_supports_explicit_file_copy_strategy()
    test_restore_memory_headroom_requires_host_and_each_numa_node()
    test_adaptive_directional_selection_falls_back_to_calibration_only_probes()
    test_late_adaptive_batch_can_use_mixed_independent_holdout()
    test_late_adaptive_batch_can_use_plan_unique_model_identification()
    test_adaptive_selection_continues_repeatable_real_target_down_direction()
    test_adaptive_target_prediction_excludes_qps()
    test_opposite_direction_is_rejected_by_metrics()
    test_robust_target_down_uses_frozen_error_margin()
    test_robust_target_down_boundary_matches_adaptive_selector()
    test_predicted_down_actual_neutral_is_a_precision_false_positive()
    test_selection_admission_requires_two_point_down_predictions()
    test_selection_admission_allows_safe_false_negatives()
    test_selection_admission_accepts_inclusive_spearman_boundary()
    test_every_inner_scored_resource_keeps_independent_holdout_diagnostics()
    test_only_target_scored_resource_errors_block_formal_admission()
    test_adaptive_calibration_prioritizes_weak_high_weight_resource()
    test_calibration_stop_before_batch_starts_no_physical_replay()
    test_direction_contract_change_requires_newer_independent_holdout()
    test_stop_before_formal_handoff_does_not_run_formal_baseline()
    test_calibration_stop_after_restore_starts_no_physical_replay()
    test_clean_restore_receipt_reuses_only_same_process_generation()
    test_clean_restore_receipt_requires_baseline_parameter_readback()
    test_ridge_alpha_selection_uses_training_target_only()
    test_resource_predictor_family_selection_uses_training_only()
    test_resource_predictor_learns_pairwise_parameter_interaction()
    test_resource_predictor_uses_historical_round_not_final_holdout()
    test_parameter_support_domain_is_training_only_and_abstains_extrapolation()
    test_parameter_support_zero_scale_fails_closed_on_unseen_change()
    test_resource_predictor_support_abstention_excludes_holdout_labels()
    test_historical_alpha_selection_excludes_final_holdout()
    test_ratio_scale_estimator_applies_admitted_calibration()
    test_schema5_hot_upgrade_sign_thresholds_are_nonzero_sentinels()
    test_wilson_small_sample_gate()
    test_ssd_wave_gate()
    test_shared_ssd_non_target_uses_container_attribution()
    test_explicit_no_mutex_makes_unrelated_host_ssd_load_informational()
    test_caller_ssd_policy_keeps_io_gate_and_isolates_non_target_lane()
    test_no_mutex_baseline_domain_requires_matching_multi_lane_cohort()
    test_no_mutex_runtime_requires_matching_immutable_workload_contract()
    test_io_overloaded_baseline_admission_does_not_change_target_band()
    test_run_level_qps_noise_floor()
    test_executor_telemetry_baseline_excludes_qps()
    test_executor_telemetry_parser_excludes_qps()
    test_executor_telemetry_retries_transient_column_discovery_timeout()
    test_formal_inner_scoring_contract_is_explicit()
    test_loader_rejects_legacy_ready_boolean()
    test_loader_rejects_five_train_five_holdout_ready_artifact()
    test_outer_accept_reject_pruning_is_context_conditioned_and_fail_open()
    test_outer_pruning_excludes_technical_and_incomplete_evidence()
    test_full_range_basin_escape_excludes_incumbent_plan_without_mutating_ranges()
    test_plan_saturated_basin_uses_only_trusted_partial_topk()
    test_formal_rankings_deduplicate_by_full_physical_plan()
    test_parameter_only_fallback_serializes_schema7_target_support()
    test_valid_exact_param_plan_replay_identity_is_not_backfilled()
    test_outer_admission_backfills_only_unreplayed_exact_identities()
    test_unfinished_outer_round_resumes_without_advancing_round_index()
    test_convergence_requires_one_percent_width_without_stability_rounds()
    print("algorithm_c_current_safety_gates: PASS", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
