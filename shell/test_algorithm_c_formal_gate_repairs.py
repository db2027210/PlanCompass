#!/usr/bin/env python3
"""Targeted regression tests for Algorithm C formal-gate repairs."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import inspect
import sys
import tempfile
import threading
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from binary_plan_resource_tuner import outer_semantic_acceptance_details
from gradient_descent_optimizer import ParameterRange
from experience_table_subset_optimizer import IncompleteSubsetOptimizationError
from run_pg_algorithm_c_calibrate_then_scene import (
    FreshCalibration,
    deterministic_design_point,
)
from run_pg_algorithm_c_scene import (
    ACTIVE_PARAMS,
    BEHAVIOR_CLUSTER_GRADIENT_PROBE_STEP_PCT,
    DEFAULT_RANGES,
    GLOBAL_PLAN_COVERAGE_CONTRACT,
    GLOBAL_PLAN_COVERAGE_REAL_REPLAY_LIMIT,
    HISTORICAL_VALID_REPLAY_SEED_CONTRACT,
    INCUMBENT_CONVERGENCE_CONTRACT,
    LOCAL_PLAN_CANDIDATE_REAL_REPLAY_LIMIT,
    LaneController,
    _validated_cohort_rows,
    account_new_physical_rows,
    algorithm_c_tmp_space_preflight,
    assess_outer_pruning_candidate,
    assess_stability_attempts,
    baseline_parameter_readback_audit,
    canonical_sha256,
    deterministic_global_coverage_vectors,
    empty_outer_pruning_memory,
    exact_param_signature,
    formal_outer_qps_progress_contract,
    unfinished_algorithm_c_round_resume_index,
    outer_pruning_context,
    parameter_vector_range_audit,
    partition_formal_range_candidates,
    physical_batch_counts,
    physical_replay_artifact_identity_token,
    predicted_target_direction_audit,
    record_outer_pruning_observation,
    record_valid_replay_identity,
    should_escape_cached_qps_stagnation,
    validate_zero_gradient_delta,
    wait_for_algorithm_c_baseline_cohort_completion,
    wait_for_algorithm_c_candidate_cohort,
    wait_for_algorithm_c_candidate_cohort_completion,
    write_json,
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


def valid_physical_row(**extra):
    return {
        "status": "ok",
        "technical_failure": False,
        "resource_stats": {"resource_monitor_ok": True},
        "workload_completeness": {"passed": True},
        **extra,
    }


def test_physical_replay_artifact_identity_prevents_resume_label_collision() -> None:
    first = {
        "optimized_params": copy.deepcopy(INITIAL_PARAMS),
        "three_level_fingerprint": {"plan_level": "full-plan-a"},
    }
    same = copy.deepcopy(first)
    different_params = copy.deepcopy(first)
    different_params["optimized_params"]["seq_page_cost"] = 1.01
    different_plan = copy.deepcopy(first)
    different_plan["three_level_fingerprint"]["plan_level"] = "full-plan-b"

    first_token = physical_replay_artifact_identity_token(first)
    assert len(first_token) == 12
    assert physical_replay_artifact_identity_token(same) == first_token
    assert physical_replay_artifact_identity_token(different_params) != first_token
    assert physical_replay_artifact_identity_token(different_plan) != first_token


def stability_attempt(attempt, passed, qps, cpu, memory, io):
    return {
        "attempt": attempt,
        "passed": passed,
        "technical_failure": False,
        "qps_mean": qps,
        "resource_means": {"cpu": cpu, "memory": memory, "io": io},
        "confirmations": [
            {
                "worker_key": worker_key,
                "db_qps": qps,
                "resources": {"cpu": cpu, "memory": memory, "io": io},
            }
            for worker_key in ("w1", "w2")
        ],
    }


def test_stability_assessment_allows_noise_before_consecutive_pair() -> None:
    attempts = [
        stability_attempt(1, False, 60.0, 16.0, 28.0, 9.0),
        stability_attempt(2, True, 101.0, 10.1, 20.1, 5.1),
        stability_attempt(3, True, 100.0, 10.0, 20.0, 5.0),
        stability_attempt(4, False, 75.0, 14.0, 28.0, 9.0),
    ]
    result = assess_stability_attempts(attempts)
    assert result["passed"] is True
    assert result["selected_attempts"] == [2, 3]


def test_stability_qps_temporal_gap_is_diagnostic_after_two_accepts() -> None:
    attempts = [
        stability_attempt(1, True, 100.0, 10.0, 20.0, 5.0),
        stability_attempt(2, True, 130.0, 10.0, 20.0, 5.0),
        stability_attempt(3, False, 170.0, 17.0, 28.0, 8.0),
        stability_attempt(4, True, 100.0, 10.0, 20.0, 5.0),
    ]
    result = assess_stability_attempts(attempts)
    assert result["passed"] is True
    assert result["selected_attempts"] == [1, 2]
    assert result["temporal_metric_diagnostics_passed"] is False


def test_stability_assessment_allows_fixed_cross_worker_offset() -> None:
    attempts = [
        stability_attempt(1, True, 100.0, 10.0, 20.0, 5.0),
        stability_attempt(2, True, 101.0, 10.1, 20.1, 5.1),
    ]
    for attempt in attempts:
        attempt["confirmations"][0]["db_qps"] *= 1.25
        attempt["confirmations"][1]["db_qps"] *= 0.75
        attempt["confirmations"][0]["resources"]["io"] *= 1.4
        attempt["confirmations"][1]["resources"]["io"] *= 0.6
    result = assess_stability_attempts(attempts)
    assert result["passed"] is True
    assert result["selected_attempts"] == [1, 2]
    assert set(result["same_worker_consecutive_relative_gaps"]) == {"w1", "w2"}


def test_baseline_pre_wave_stop_accounts_completed_rows_exactly_once() -> None:
    state = {
        "physical_workload_invocations": 0,
        "valid_replays": 0,
        "technical_failures": 0,
    }
    rows = [valid_physical_row(label="w1"), valid_physical_row(label="w2")]
    cursor, first = account_new_physical_rows(state, rows, 0)
    assert cursor == 2
    assert first == {
        "physical_workload_invocations": 2,
        "valid_replays": 2,
        "technical_failures": 0,
    }
    cursor, repeated = account_new_physical_rows(state, rows, cursor)
    assert cursor == 2
    assert repeated == {
        "physical_workload_invocations": 0,
        "valid_replays": 0,
        "technical_failures": 0,
    }
    assert state == {
        "physical_workload_invocations": 2,
        "valid_replays": 2,
        "technical_failures": 0,
    }


def test_four_lane_completion_barriers_block_next_restore_and_propagate_failure() -> None:
    scenes = ("job_memory", "tpch_memory", "xp_cpu", "xp_io")
    with tempfile.TemporaryDirectory(prefix="algorithm-c-completion-") as temp_dir:
        cohort_suffix = Path(temp_dir).name
        env = {
            "BONECT_ALGORITHM_C_BASELINE_COHORT_ID": f"baseline-{cohort_suffix}",
            "BONECT_ALGORITHM_C_BASELINE_COHORT_SIZE": "4",
            "BONECT_ALGORITHM_C_BASELINE_COHORT_TIMEOUT_S": "5",
            "BONECT_ALGORITHM_C_CANDIDATE_COHORT_ID": f"candidate-{cohort_suffix}",
            "BONECT_ALGORITHM_C_CANDIDATE_COHORT_SIZE": "4",
            "BONECT_ALGORITHM_C_CANDIDATE_COHORT_TIMEOUT_S": "5",
            "BONECT_ALGORITHM_C_MIN_TMP_AVAILABLE_BYTES": "0",
        }
        baseline_results = []
        baseline_errors = []

        def baseline_worker(scene: str) -> None:
            try:
                baseline_results.append(
                    wait_for_algorithm_c_baseline_cohort_completion(
                        scene=scene,
                        repetition=1,
                        wave_valid=True,
                    )
                )
            except Exception as exc:
                baseline_errors.append(exc)

        with mock.patch.dict(os.environ, env, clear=False):
            threads = [
                threading.Thread(target=baseline_worker, args=(scene,))
                for scene in scenes
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10.0)

        assert not baseline_errors
        assert len(baseline_results) == 4
        assert all(row["passed"] is True for row in baseline_results)
        assert all(row["completed_count"] == 4 for row in baseline_results)

        candidate_results = []
        candidate_errors = []

        def candidate_worker(scene: str) -> None:
            try:
                candidate_results.append(
                    wait_for_algorithm_c_candidate_cohort_completion(
                        scene=scene,
                        wave_sequence=1,
                        round_index=1,
                        batch_kind="base_top5",
                        wave_start=0,
                        wave_valid=scene != "xp_io",
                    )
                )
            except Exception as exc:
                candidate_errors.append(exc)

        with mock.patch.dict(os.environ, env, clear=False):
            threads = [
                threading.Thread(target=candidate_worker, args=(scene,))
                for scene in scenes
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10.0)

        assert not candidate_errors
        assert len(candidate_results) == 4
        assert all(row["passed"] is False for row in candidate_results)
        assert all(row["all_wave_valid"] is False for row in candidate_results)


def test_candidate_cohort_allows_mixed_local_rounds_on_one_global_wave() -> None:
    """A global physical wave must not require equal per-lane outer rounds."""

    contexts = {
        "job_memory": (4, "base_top5", 0),
        "tpch_memory": (4, "base_top5_empty_wave_control", 0),
        "xp_cpu": (4, "base_top5", 0),
        "xp_io": (3, "current_interval_plan_coverage_001", 0),
    }
    with tempfile.TemporaryDirectory(prefix="algorithm-c-mixed-round-") as temp_dir:
        cohort_suffix = Path(temp_dir).name
        env = {
            "BONECT_ALGORITHM_C_CANDIDATE_COHORT_ID": (
                f"candidate-mixed-{cohort_suffix}"
            ),
            "BONECT_ALGORITHM_C_CANDIDATE_COHORT_SIZE": "4",
            "BONECT_ALGORITHM_C_CANDIDATE_COHORT_TIMEOUT_S": "5",
            "BONECT_ALGORITHM_C_MIN_TMP_AVAILABLE_BYTES": "0",
        }
        ready_results = []
        completion_results = []
        errors = []

        def worker(scene: str) -> None:
            round_index, batch_kind, wave_start = contexts[scene]
            try:
                ready_results.append(
                    wait_for_algorithm_c_candidate_cohort(
                        scene=scene,
                        wave_sequence=10,
                        round_index=round_index,
                        batch_kind=batch_kind,
                        wave_start=wave_start,
                        stop_requested=lambda: False,
                    )
                )
                completion_results.append(
                    wait_for_algorithm_c_candidate_cohort_completion(
                        scene=scene,
                        wave_sequence=10,
                        round_index=round_index,
                        batch_kind=batch_kind,
                        wave_start=wave_start,
                        wave_valid=True,
                    )
                )
            except Exception as exc:
                errors.append(exc)

        with mock.patch.dict(os.environ, env, clear=False):
            threads = [
                threading.Thread(target=worker, args=(scene,))
                for scene in contexts
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10.0)

        assert not errors
        assert not any(thread.is_alive() for thread in threads)
        assert len(ready_results) == 4
        assert len(completion_results) == 4
        assert all(row["ready_count"] == 4 for row in ready_results)
        assert all(row["completed_count"] == 4 for row in completion_results)
        assert all(row["passed"] is True for row in completion_results)
        assert all(
            row["participant_round_indices"] == [3, 4]
            for row in ready_results + completion_results
        )
        assert all(
            row["local_context_is_not_barrier_identity"] is True
            for row in ready_results + completion_results
        )


def test_cohort_receipts_are_atomic_and_corrupt_files_never_count() -> None:
    with tempfile.TemporaryDirectory(prefix="algorithm-c-atomic-cohort-") as temp_dir:
        barrier_dir = Path(temp_dir)
        identity = {"wave_sequence": 7}
        expected = {"wave_sequence": 7, "round_index": 3, "wave_start": 0}
        job_name = canonical_sha256({"scene": "job_memory", **identity}) + ".ready"
        write_json(
            barrier_dir / job_name,
            {
                "scene": "job_memory",
                **expected,
                "batch_kind": "base_top5",
            },
        )
        xp_name = canonical_sha256({"scene": "xp_cpu", **identity}) + ".ready"
        (barrier_dir / xp_name).write_bytes(b"")
        tpch_name = canonical_sha256({"scene": "tpch_memory", **identity}) + ".ready"
        write_json(
            barrier_dir / tpch_name,
            {
                "scene": "tpch_memory",
                "wave_sequence": 7,
                "round_index": 2,
                "wave_start": 0,
                "batch_kind": "base_top5_empty_wave_control",
            },
        )

        rows, invalid = _validated_cohort_rows(
            barrier_dir,
            suffix=".ready",
            participant_identity_fields=identity,
            expected_fields=expected,
        )

        assert [row["scene"] for row in rows] == ["job_memory"]
        assert sorted(invalid) == sorted([xp_name, tpch_name])

        durable_target = barrier_dir / "durable.done"
        write_json(durable_target, {"scene": "job_memory", "wave_valid": True})
        durable_before = durable_target.read_bytes()
        with mock.patch.object(Path, "open", side_effect=OSError(28, "No space")):
            try:
                write_json(
                    durable_target,
                    {"scene": "job_memory", "wave_valid": False},
                )
            except OSError as exc:
                assert exc.errno == 28
            else:
                raise AssertionError("ENOSPC must fail the atomic receipt write")
        assert durable_target.read_bytes() == durable_before
        assert not list(barrier_dir.glob("durable.done.tmp.*"))


def test_tmp_space_preflight_fails_before_cohort_or_workload() -> None:
    stat = mock.Mock(f_bavail=1, f_bfree=2, f_frsize=4096)
    with mock.patch("run_pg_algorithm_c_scene.os.statvfs", return_value=stat):
        try:
            algorithm_c_tmp_space_preflight(
                path=Path("/tmp"),
                minimum_available_bytes=8192,
            )
        except RuntimeError as exc:
            assert "tmp space preflight failed" in str(exc)
            assert "available=4096" in str(exc)
        else:
            raise AssertionError("insufficient /tmp capacity must fail closed")


def test_predicted_target_direction_is_ranking_only_for_outer_tiers() -> None:
    controller = LaneController.__new__(LaneController)
    controller.target = "cpu"
    controller.ranges = {
        name: ParameterRange(*DEFAULT_RANGES[name]) for name in ACTIVE_PARAMS
    }
    controller.outer_pruning_policy = {"enabled": False}
    controller.state = {"outer_accept_reject_pruning": empty_outer_pruning_memory()}
    controller.log = lambda *_args, **_kwargs: None

    candidates = []
    for rank, ratio in enumerate((0.90, 0.95, 0.99, 1.01, 1.05, 1.10), start=1):
        params = dict(INITIAL_PARAMS)
        params["seq_page_cost"] = 1.0 + rank * 0.1
        candidates.append(
            {
                "rank": rank,
                "optimized_params": params,
                "initial_resource": {"cpu": 4.0},
                "predicted_resource": {"cpu": 4.0 * ratio},
            }
        )

    controller.current_outer_pruning_context = lambda: {
        "required_acceptance_tier": "strict"
    }
    selected, audit = controller.select_outer_admitted_candidates(
        candidates,
        round_index=1,
        batch_kind="base_top5",
    )
    assert len(selected) == 5
    assert audit["target_direction_gate_required"] is False
    assert audit["target_direction_excluded_count"] == 0
    assert audit["target_direction_ranking_preference_enabled"] is True
    assert audit["target_direction_deprioritized_count"] == 3
    assert [row["rank"] for row in selected] == [1, 2, 3, 4, 5]
    assert sum(
        not predicted_target_direction_audit(row, "cpu")[
            "predicted_target_down"
        ]
        for row in selected
    ) == 2

    controller.current_outer_pruning_context = lambda: {
        "required_acceptance_tier": "fallback"
    }
    fallback, fallback_audit = controller.select_outer_admitted_candidates(
        candidates,
        round_index=5,
        batch_kind="base_top5",
    )
    assert len(fallback) == 5
    assert fallback_audit["target_direction_gate_required"] is False
    assert fallback_audit["target_direction_excluded_count"] == 0
    assert fallback_audit["target_direction_ranking_preference_enabled"] is True
    assert fallback_audit["target_direction_deprioritized_count"] == 3


def test_valid_physical_replay_is_cached_and_redecided_without_accounting() -> None:
    controller = LaneController.__new__(LaneController)
    controller.target = "cpu"
    controller.workload_contract_sha256 = "same-campaign-workload"
    controller.ranges = {
        name: ParameterRange(*DEFAULT_RANGES[name]) for name in ACTIVE_PARAMS
    }
    controller.capacity = {"cpu": 100.0, "memory": 100.0, "io": 100.0}
    controller.cfg = {}
    controller.outer_pruning_policy = {"enabled": False}
    controller.valid_replay_evidence_by_identity = {}
    controller.log = mock.Mock()
    controller.save_state = mock.Mock()
    candidate = {
        "rank": 1,
        "active_subset": ["seq_page_cost"],
        "initial_params": dict(INITIAL_PARAMS),
        "optimized_params": {**INITIAL_PARAMS, "seq_page_cost": 1.1},
        "three_level_fingerprint": {"plan_level": "complete-plan-a"},
    }
    memory = {
        "contract": "algorithm_c_valid_exact_param_full_plan_identity_v1",
        "workload_contract_sha256": controller.workload_contract_sha256,
        "target": controller.target,
        "technical_failures_excluded": True,
        "identities": {},
    }
    record_valid_replay_identity(
        memory,
        candidate=candidate,
        round_index=1,
        label="physical-r1",
    )
    canonical = {
        "label": "canonical",
        "db_qps": 100.0,
        "resources": {"cpu": 50.0, "memory": 30.0, "io": 20.0},
    }
    controller.state = {
        "valid_replay_identity_memory": memory,
        "baseline_by_worker": {"worker-a": copy.deepcopy(canonical)},
        "canonical_baseline": copy.deepcopy(canonical),
        "incumbent_validation": None,
        "qps_noise_model": {},
        "consecutive_missed_outer_rounds": 4,
        "physical_workload_invocations": 9,
        "valid_replays": 9,
        "technical_failures": 0,
    }
    physical_row = valid_physical_row(
        label="physical-r1",
        worker_key="worker-a",
        db_qps=120.0,
        resources={"cpu": 55.0, "memory": 30.0, "io": 20.0},
        candidate_ranking=copy.deepcopy(candidate),
    )
    cached = controller.cache_valid_replay_evidence(
        physical_row,
        round_index=1,
    )
    assert cached["cached"] is True
    technical = copy.deepcopy(physical_row)
    technical["technical_failure"] = True
    assert controller.cache_valid_replay_evidence(
        technical,
        round_index=2,
    )["cached"] is False

    rows = controller.cached_valid_replay_rows(
        [candidate],
        round_index=5,
    )
    assert len(rows) == 1
    assert rows[0]["cached_valid_replay_redecision"][
        "physical_workload_invocations"
    ] == 0
    counters_before = {
        key: controller.state[key]
        for key in (
            "physical_workload_invocations",
            "valid_replays",
            "technical_failures",
        )
    }
    with mock.patch(
        "run_pg_algorithm_c_scene.outer_semantic_acceptance_details",
        return_value={
            "accepted": True,
            "accepted_escape": True,
            "acceptance_tier": "fallback",
            "target_drop_vs_reference": -0.1,
            "qps_gate_gain": 0.2,
        },
    ):
        accepted, decision_count = controller.evaluate_batch(
            rows,
            round_index=5,
            batch_kind="cached_valid_replay_redecision",
            evidence_source="cached_valid_replay",
        )
    assert accepted is not None
    assert decision_count == 1
    assert counters_before == {
        key: controller.state[key]
        for key in counters_before
    }
    decision_logs = [
        call.kwargs
        for call in controller.log.call_args_list
        if call.args and call.args[0] == "algorithm_c_outer_candidate_decision"
    ]
    assert decision_logs[-1]["physical_workload_invocations"] == 0
    assert decision_logs[-1]["valid_replays_added"] == 0
    assert decision_logs[-1]["qps_outer_only"] is True
    assert decision_logs[-1]["outer_pruning_evidence_update"]["recorded"] is False


def test_one_strict_candidate_accepts_the_same_top5_round() -> None:
    """A strict Top-5 point is accepted immediately; peers need not pass."""
    controller = LaneController.__new__(LaneController)
    controller.target = "memory"
    controller.state = {
        "baseline_by_worker": {
            "worker-a": {
                "label": "worker-baseline",
                "db_qps": 100.0,
                "resources": {"cpu": 20.0, "memory": 80.0, "io": 20.0},
            }
        },
        "canonical_baseline": {
            "label": "canonical-baseline",
            "db_qps": 100.0,
            "resources": {"cpu": 20.0, "memory": 80.0, "io": 20.0},
        },
        "incumbent_validation": None,
        "qps_noise_model": {},
        "consecutive_missed_outer_rounds": 0,
        "outer_accept_reject_pruning": empty_outer_pruning_memory(),
    }
    controller.capacity = {"cpu": 100.0, "memory": 100.0, "io": 100.0}
    controller.cfg = {
        "binary_plan_tuning": {
            "binary_search": {
                "nested_loop": {
                    "outer_acceptance": {
                        "min_db_qps_gain_pct": 0.1,
                        "min_target_drop_pct": 0.1,
                        "min_other_up_pct": 0.0,
                        "acceptance_tier_downgrade_after_rounds": 2,
                        "non_target_bottleneck_threshold_pct": 80.0,
                    }
                }
            }
        }
    }
    controller.outer_pruning_policy = {"enabled": False}
    controller.log = mock.Mock()

    def row(label: str, qps: float, memory: float, cpu: float) -> dict:
        return valid_physical_row(
            label=label,
            worker_key="worker-a",
            db_qps=qps,
            resources={"cpu": cpu, "memory": memory, "io": 20.0},
            candidate_ranking={"rank": int(label[-1])},
        )

    rows = [
        row("candidate-1", 99.0, 79.0, 21.0),
        row("candidate-2", 102.0, 81.0, 21.0),
        # This one point simultaneously has QPS up, target memory down, and
        # non-target CPU up without creating a new non-target bottleneck.
        row("candidate-3", 102.0, 79.0, 21.0),
        row("candidate-4", 99.0, 81.0, 19.0),
        row("candidate-5", 101.0, 81.0, 19.0),
    ]

    accepted, valid_count = controller.evaluate_batch(
        rows,
        round_index=1,
        batch_kind="base_top5",
    )

    assert valid_count == 5
    assert accepted is not None
    assert accepted["raw"]["label"] == "candidate-3"
    assert accepted["decision"]["acceptance_tier"] == "strict"
    assert accepted["decision"]["required_acceptance_tier"] == "fallback"
    assert accepted["decision"]["preferred_acceptance_tier"] == "strict"


def test_same_round_fallback_is_accepted_when_no_stricter_candidate_exists() -> None:
    controller = LaneController.__new__(LaneController)
    controller.target = "memory"
    canonical = {
        "label": "canonical-baseline",
        "status": "ok",
        "db_qps": 100.0,
        "resources": {"cpu": 20.0, "memory": 80.0, "io": 20.0},
    }
    controller.state = {
        "baseline_by_worker": {"worker-a": copy.deepcopy(canonical)},
        "canonical_baseline": copy.deepcopy(canonical),
        "incumbent_validation": None,
        "qps_noise_model": {},
        "consecutive_missed_outer_rounds": 0,
    }
    controller.capacity = {"cpu": 100.0, "memory": 100.0, "io": 100.0}
    controller.cfg = {
        "algorithm_c": {"qps_noise_gate": {"enabled": False}},
        "binary_plan_tuning": {
            "acceptance_policy": {
                "resource_capacity": {
                    "cpu_cores": 100.0,
                    "memory_gib": 100.0,
                    "io_mib_s": 100.0,
                }
            },
            "binary_search": {
                "nested_loop": {
                    "outer_acceptance": {
                        "qps_reference_mode": "canonical_baseline",
                        "qps_threshold_mode": "static_floor",
                        "min_db_qps_gain_pct": 0.1,
                        "min_target_drop_pct": 0.1,
                        "non_target_bottleneck_threshold_pct": 80.0,
                    }
                }
            },
        },
    }
    controller.outer_pruning_policy = {"enabled": False}
    controller.log = mock.Mock()
    fallback = valid_physical_row(
        label="fallback-only",
        worker_key="worker-a",
        db_qps=120.0,
        resources={"cpu": 21.0, "memory": 81.0, "io": 21.0},
        candidate_ranking={"rank": 1},
    )

    accepted, valid_count = controller.evaluate_batch(
        [fallback], round_index=1, batch_kind="base_top5"
    )

    assert valid_count == 1
    assert accepted is not None
    assert accepted["decision"]["acceptance_tier"] == "fallback"
    assert accepted["decision"]["required_acceptance_tier"] == "fallback"
    assert accepted["decision"]["preferred_acceptance_tier"] == "strict"
    assert accepted["decision"]["accepted"] is True


def test_formal_qps_contract_requires_previous_accepted_reference() -> None:
    def cfg(reference_mode: str) -> dict:
        return {
            "algorithm_c": {"qps_noise_gate": {"enabled": False}},
            "binary_plan_tuning": {
                "acceptance_policy": {"min_db_qps_gain_pct": 0.1},
                "binary_search": {
                    "nested_loop": {
                        "outer_acceptance": {
                            "qps_reference_mode": reference_mode,
                            "qps_threshold_mode": "static_floor",
                            "min_db_qps_gain_pct": 0.1,
                        }
                    }
                },
            },
        }

    legacy = formal_outer_qps_progress_contract(cfg("canonical_baseline"))
    assert legacy["passed"] is False
    assert legacy["errors"] == [
        "qps_reference_mode_must_be_last_accepted"
    ]
    current = formal_outer_qps_progress_contract(cfg("last_accepted"))
    assert current["passed"] is True
    assert current["first_round_reference"] == "canonical_baseline"
    assert current["subsequent_round_reference"] == "previous_accepted_round"
    assert current["rejected_round_updates_reference"] is False


def test_cached_qps_stagnation_reopens_full_range_plan_escape() -> None:
    """Lower-QPS cached evidence must not suppress new-plan exploration."""

    assert should_escape_cached_qps_stagnation(
        required_tier="fallback",
        decision_evidence_count=5,
        physical_rows_this_round=0,
    )
    assert not should_escape_cached_qps_stagnation(
        required_tier="strict",
        decision_evidence_count=5,
        physical_rows_this_round=0,
    )
    assert not should_escape_cached_qps_stagnation(
        required_tier="fallback",
        decision_evidence_count=0,
        physical_rows_this_round=0,
    )
    assert not should_escape_cached_qps_stagnation(
        required_tier="fallback",
        decision_evidence_count=5,
        physical_rows_this_round=1,
    )


def test_evaluate_batch_rejects_qps_below_previous_accepted_round() -> None:
    """A point above r0 but below the accepted incumbent cannot advance B/C."""

    controller = LaneController.__new__(LaneController)
    controller.target = "memory"
    canonical = {
        "label": "canonical-baseline",
        "status": "ok",
        "db_qps": 100.0,
        "resources": {"cpu": 20.0, "memory": 80.0, "io": 20.0},
    }
    controller.state = {
        "baseline_by_worker": {"worker-a": copy.deepcopy(canonical)},
        "canonical_baseline": copy.deepcopy(canonical),
        "incumbent_validation": {
            "label": "accepted-round-1",
            "status": "ok",
            "db_qps": 110.0,
            "resources": {"cpu": 21.0, "memory": 79.0, "io": 20.0},
        },
        "qps_noise_model": {},
        "consecutive_missed_outer_rounds": 0,
        "outer_accept_reject_pruning": empty_outer_pruning_memory(),
    }
    controller.capacity = {"cpu": 100.0, "memory": 100.0, "io": 100.0}
    controller.cfg = {
        "algorithm_c": {"qps_noise_gate": {"enabled": False}},
        "binary_plan_tuning": {
            "acceptance_policy": {
                "resource_capacity": {
                    "cpu_cores": 100.0,
                    "memory_gib": 100.0,
                    "io_mib_s": 100.0,
                }
            },
            "binary_search": {
                "nested_loop": {
                    "outer_acceptance": {
                        "qps_reference_mode": "last_accepted",
                        "qps_threshold_mode": "static_floor",
                        "min_db_qps_gain_pct": 0.1,
                        "min_target_drop_pct": 0.1,
                        "min_other_up_pct": 0.0,
                        "non_target_bottleneck_threshold_pct": 80.0,
                    }
                }
            },
        },
    }
    controller.outer_pruning_policy = {"enabled": False}
    controller.log = mock.Mock()

    above_r0_below_previous = valid_physical_row(
        label="above-r0-below-r1",
        worker_key="worker-a",
        db_qps=105.0,
        resources={"cpu": 22.0, "memory": 78.0, "io": 21.0},
        candidate_ranking={"rank": 1},
    )
    rejected, valid_count = controller.evaluate_batch(
        [above_r0_below_previous],
        round_index=2,
        batch_kind="base_top5",
    )
    assert valid_count == 1
    assert rejected is None
    decision_log = controller.log.call_args_list[-1].kwargs["decision"]
    assert decision_log["qps_up"] is False
    assert decision_log["reference_db_qps"] == 110.0
    assert decision_log["baseline_db_qps"] == 100.0
    assert decision_log["qps_gate_reference_role"] == "previous_accepted_round"
    assert decision_log["rejects"] == ["db_qps_not_up_vs_last_accepted"]

    qps_progress_fallback = valid_physical_row(
        label="above-r1-fallback",
        worker_key="worker-a",
        db_qps=111.0,
        resources={"cpu": 20.0, "memory": 81.0, "io": 20.0},
        candidate_ranking={"rank": 2},
    )
    accepted, valid_count = controller.evaluate_batch(
        [qps_progress_fallback],
        round_index=2,
        batch_kind="base_top5",
    )
    assert valid_count == 1
    assert accepted is not None
    assert accepted["decision"]["acceptance_tier"] == "fallback"
    assert accepted["decision"]["reference_db_qps"] == 110.0
    assert accepted["decision"]["qps_gate_reference_role"] == (
        "previous_accepted_round"
    )


def test_historical_valid_pool_redecides_never_accepted_in_range_points() -> None:
    controller = LaneController.__new__(LaneController)
    controller.target = "memory"
    controller.workload_contract_sha256 = "workload-sha"
    controller.ranges = {
        name: ParameterRange(*DEFAULT_RANGES[name]) for name in ACTIVE_PARAMS
    }
    canonical = {
        "label": "canonical-baseline",
        "db_qps": 100.0,
        "resources": {"cpu": 20.0, "memory": 80.0, "io": 20.0},
    }
    controller.state = {
        "incumbent_params": copy.deepcopy(INITIAL_PARAMS),
        "incumbent_validation": None,
        "baseline_by_worker": {"worker-a": copy.deepcopy(canonical)},
        "canonical_baseline": copy.deepcopy(canonical),
        "qps_noise_model": {},
        "consecutive_missed_outer_rounds": 0,
    }
    controller.capacity = {"cpu": 100.0, "memory": 100.0, "io": 100.0}
    controller.cfg = {
        "binary_plan_tuning": {
            "binary_search": {
                "nested_loop": {
                    "outer_acceptance": {
                        "min_db_qps_gain_pct": 0.1,
                        "min_target_drop_pct": 0.1,
                        "min_other_up_pct": 0.0,
                        "acceptance_tier_downgrade_after_rounds": 2,
                        "non_target_bottleneck_threshold_pct": 80.0,
                    }
                }
            }
        }
    }
    controller.log = mock.Mock()
    controller.log_path = Path("/does/not/exist")
    controller.accepted_parameter_signatures = mock.Mock(
        return_value={exact_param_signature(INITIAL_PARAMS)}
    )

    def ranking(value: float, plan: str, active_subset=None) -> dict:
        params = copy.deepcopy(INITIAL_PARAMS)
        params["seq_page_cost"] = value
        return {
            "optimized_params": params,
            "active_subset": active_subset or ["seq_page_cost"],
            "three_level_fingerprint": {
                "valid": True,
                "id": f"three-level-{plan}-{value}",
                "plan_level": plan,
            },
        }

    def cached(
        label: str,
        candidate: dict,
        *,
        qps: float = 102.0,
        memory: float = 79.0,
    ) -> dict:
        return valid_physical_row(
            label=label,
            worker_key="worker-a",
            db_qps=qps,
            resources={"cpu": 21.0, "memory": memory, "io": 20.0},
            candidate_ranking=candidate,
        )

    accepted_before = ranking(1.10, "plan-accepted")
    accepted_signature = exact_param_signature(accepted_before["optimized_params"])
    controller.accepted_parameter_signatures.return_value.add(accepted_signature)
    outside = ranking(20.0, "plan-outside")
    incomplete_transition = ranking(
        1.20,
        "plan-incomplete-transition",
        active_subset=["random_page_cost"],
    )
    eligible = ranking(1.30, "plan-eligible")
    rejected = ranking(1.40, "plan-rejected")
    evidence_rows = [
        ("accepted", cached("accepted", accepted_before)),
        ("outside", cached("outside", outside)),
        ("transition", cached("transition", incomplete_transition)),
        ("eligible", cached("eligible", eligible)),
        ("rejected", cached("rejected", rejected, qps=99.0)),
    ]
    controller.valid_replay_evidence_by_identity = {
        key: {
            "round_index": index,
            "label": row["label"],
            "validation": row,
        }
        for index, (key, row) in enumerate(evidence_rows, start=1)
    }

    selected = controller.cached_historical_valid_replay_rows(round_index=7)

    assert len(selected) == 1
    assert selected[0]["candidate_ranking"]["optimized_params"][
        "seq_page_cost"
    ] == 1.30
    audit = next(
        call.kwargs
        for call in controller.log.call_args_list
        if call.args
        and call.args[0] == "algorithm_c_historical_valid_replay_pool_admission"
    )
    assert audit["physical_workload_invocations"] == 0
    assert audit["valid_replays_added"] == 0
    assert audit["technical_failures_excluded"] is True
    assert audit["previously_accepted_parameter_vectors_excluded"] is True
    assert audit["exclusion_counts"]["already_accepted_parameter_vector"] == 1
    assert audit["exclusion_counts"]["outside_current_range"] == 1
    assert audit["exclusion_counts"][
        "transition_not_covered_by_active_subset"
    ] == 1
    assert audit["exclusion_counts"]["current_gate_not_passed"] == 1


def test_cross_campaign_seed_projects_after_fresh_baseline_and_rehydrates() -> None:
    with tempfile.TemporaryDirectory() as temporary_dir:
        root = Path(temporary_dir)
        artifact_path = root / "experience.json"
        artifact_path.write_text('{"table": {}}\n', encoding="utf-8")
        candidate = {
            "optimized_params": copy.deepcopy(INITIAL_PARAMS),
            "active_subset": ["seq_page_cost"],
            "three_level_fingerprint": {
                "valid": True,
                "id": "three-level-seed",
                "plan_level": "plan-seed",
            },
        }
        validation = valid_physical_row(
            label="source-r3-c2",
            worker_key="worker-a",
            db_qps=110.0,
            resources={"cpu": 45.0, "memory": 72.0, "io": 18.0},
            candidate_ranking=candidate,
        )
        source_worker = {
            "label": "source-worker-a",
            "db_qps": 100.0,
            "resources": {"cpu": 50.0, "memory": 80.0, "io": 20.0},
        }
        source_canonical = {
            "label": "source-canonical",
            "db_qps": 120.0,
            "resources": {"cpu": 60.0, "memory": 100.0, "io": 25.0},
        }
        workload_contract = {"benchmark": "test", "query_count": 1}
        seed = {
            "contract": HISTORICAL_VALID_REPLAY_SEED_CONTRACT,
            "source_campaign": "r61",
            "source_algorithm": "algorithm_c",
            "target": "memory",
            "workload_contract_sha256": canonical_sha256(workload_contract),
            "experience_table_sha256": hashlib.sha256(
                artifact_path.read_bytes()
            ).hexdigest(),
            "technical_failures_excluded": True,
            "previously_accepted_parameter_vectors_excluded": True,
            "qps_outer_only": True,
            "rows": [{
                "source_round_index": 3,
                "source_label": "source-r3-c2",
                "validation": validation,
                "source_worker_baseline": source_worker,
                "source_canonical_baseline": source_canonical,
            }],
        }
        seed["payload_sha256"] = canonical_sha256(seed)
        seed_path = root / "seed.json"
        seed_path.write_text(json.dumps(seed), encoding="utf-8")

        controller = LaneController.__new__(LaneController)
        controller.algorithm_cfg = {
            "historical_valid_replay_seed_path": str(seed_path),
        }
        controller.workload_contract_sha256 = canonical_sha256(workload_contract)
        controller.target = "memory"
        controller.t_artifact_path = artifact_path
        controller.capacity = {"cpu": 100.0, "memory": 100.0, "io": 100.0}
        controller.valid_replay_evidence_by_identity = {}
        controller.state = {
            "physical_workload_invocations": 12,
            "valid_replays": 12,
            "technical_failures": 0,
            "baseline_by_worker": {
                "worker-a": {
                    "label": "fresh-worker-a",
                    "db_qps": 90.0,
                    "resources": {"cpu": 40.0, "memory": 60.0, "io": 16.0},
                },
            },
            "valid_replay_identity_memory": {
                "workload_contract_sha256": canonical_sha256(workload_contract),
                "target": "memory",
                "identities": {},
            },
        }
        controller.save_state = mock.Mock()
        controller.log = mock.Mock()

        receipt = controller.import_historical_valid_replay_seed()
        assert receipt["imported_count"] == 1
        projected = next(iter(controller.valid_replay_evidence_by_identity.values()))[
            "validation"
        ]
        assert math.isclose(projected["resources"]["memory"], 54.0)
        assert math.isclose(projected["resources"]["cpu"], 36.0)
        assert math.isclose(projected["resources"]["io"], 14.4)
        assert math.isclose(projected["db_qps"], 99.0)
        assert controller.state["physical_workload_invocations"] == 12
        assert controller.state["valid_replays"] == 12
        assert controller.state["technical_failures"] == 0

        # A restarted controller has an empty in-memory evidence cache.  The
        # same immutable seed must rehydrate it without changing counters.
        controller.valid_replay_evidence_by_identity = {}
        second = controller.import_historical_valid_replay_seed()
        assert second["imported_count"] == 1
        assert len(controller.valid_replay_evidence_by_identity) == 1
        assert controller.state["physical_workload_invocations"] == 12
        assert controller.state["valid_replays"] == 12


def _gradient_backfill_candidate(
    *,
    seq_page_cost: float,
    plan_fingerprint: str,
    candidate_origin: str = "gradient_probe",
) -> dict:
    params = dict(INITIAL_PARAMS)
    params["seq_page_cost"] = seq_page_cost
    return {
        "candidate_origin": candidate_origin,
        "probe_kind": "gradient_relative_step_probe",
        "used_for_gradient": True,
        "boundary_plateau": False,
        "active_subset": ["seq_page_cost"],
        "initial_params": dict(INITIAL_PARAMS),
        "optimized_params": params,
        "dScore/dU": {"cpu": -0.5},
        "T": {"seq_page_cost": 1.0},
        "confidence": {"seq_page_cost": 0.8},
        "g_j": {"seq_page_cost": -0.4},
        "three_level_fingerprint": {"plan_level": plan_fingerprint},
    }


def test_valid_identity_backfill_uses_only_new_trusted_gradient_plans() -> None:
    controller = LaneController.__new__(LaneController)
    controller.target = "cpu"
    controller.ranges = {
        name: ParameterRange(*DEFAULT_RANGES[name]) for name in ACTIVE_PARAMS
    }
    controller.state = {
        "incumbent_params": dict(INITIAL_PARAMS),
        "incumbent_validation": {
            "candidate_ranking": {
                "three_level_fingerprint": {"plan_level": "plan-incumbent"}
            }
        },
    }
    controller.log = mock.Mock()
    controller.validate_top5_audit_records = mock.Mock()
    trusted = _gradient_backfill_candidate(
        seq_page_cost=1.1,
        plan_fingerprint="plan-replayed",
    )
    expanded = [
        _gradient_backfill_candidate(
            seq_page_cost=1.2,
            plan_fingerprint="plan-incumbent",
        ),
        _gradient_backfill_candidate(
            seq_page_cost=1.3,
            plan_fingerprint="plan-random",
            candidate_origin="random_probe",
        ),
        _gradient_backfill_candidate(
            seq_page_cost=1.4,
            plan_fingerprint="plan-new-1",
        ),
        _gradient_backfill_candidate(
            seq_page_cost=1.5,
            plan_fingerprint="plan-new-1",
        ),
        *[
            _gradient_backfill_candidate(
                seq_page_cost=1.5 + index / 10,
                plan_fingerprint=f"plan-new-{index}",
            )
            for index in range(2, 6)
        ],
    ]
    controller.generate_rankings = mock.Mock(return_value=expanded)

    def select(rows, *, limit, **_kwargs):
        return list(rows[:limit]), {"admitted_count": min(limit, len(rows))}

    controller.select_outer_admitted_candidates = mock.Mock(side_effect=select)

    selected, audit = controller.backfill_replayed_identity_top5(
        [trusted],
        [],
        {"exact_valid_replay_excluded_count": 5},
        round_index=4,
    )

    assert len(selected) == 5
    assert [row["three_level_fingerprint"]["plan_level"] for row in selected] == [
        "plan-new-1",
        "plan-new-2",
        "plan-new-3",
        "plan-new-4",
        "plan-new-5",
    ]
    assert audit["exclusion_counts"]["incumbent_full_plan"] == 1
    assert audit["exclusion_counts"]["untrusted_gradient_lineage"] == 1
    assert audit["exclusion_counts"]["duplicate_full_plan"] == 1
    assert audit["random_direction_used"] is False
    assert audit["physical_workload_invocations"] == 0
    controller.validate_top5_audit_records.assert_called_once_with(selected)


def test_valid_identity_backfill_uses_directional_bisection_at_full_range() -> None:
    controller = LaneController.__new__(LaneController)
    controller.output_dir = Path("/tmp/algorithm-c-full-range-backfill-test")
    controller.target = "cpu"
    controller.ranges = {
        name: ParameterRange(*DEFAULT_RANGES[name]) for name in ACTIVE_PARAMS
    }
    controller.state = {
        "incumbent_params": dict(INITIAL_PARAMS),
        "incumbent_validation": {},
    }
    controller.log = mock.Mock()
    controller.validate_top5_audit_records = mock.Mock()
    trusted = _gradient_backfill_candidate(
        seq_page_cost=1.1,
        plan_fingerprint="plan-replayed",
    )
    controller.generate_rankings = mock.Mock(
        return_value=[
            _gradient_backfill_candidate(
                seq_page_cost=1.2 + index / 10,
                plan_fingerprint=f"plan-new-{index}",
            )
            for index in range(2)
        ]
    )
    controller.select_outer_admitted_candidates = mock.Mock(
        side_effect=lambda rows, *, limit, **_kwargs: (
            list(rows[:limit]),
            {"admitted_count": min(limit, len(rows))},
        )
    )
    boundary = [
        {
            **_gradient_backfill_candidate(
                seq_page_cost=2.0 + index / 10,
                plan_fingerprint=f"plan-boundary-{index}",
            ),
            "candidate_origin": (
                "formal_range_gradient_direction_bisection_probe"
            ),
            "probe_kind": "gradient_direction_bisection_probe",
            "used_for_gradient": False,
            "gradient_state_unchanged": True,
        }
        for index in range(3)
    ]
    controller.boundary_inward_bisection_rankings = mock.Mock(
        return_value=boundary
    )

    selected, audit = controller.backfill_replayed_identity_top5(
        [trusted],
        [],
        {"exact_valid_replay_excluded_count": 5},
        round_index=4,
    )

    assert len(selected) == 5
    assert audit["final_candidate_count"] == 5
    assert audit["boundary_inward_attempted"] is True
    assert audit["boundary_inward_backfill_admitted_count"] == 3
    assert audit["full_range_directional_bisection_allowed"] is True
    controller.boundary_inward_bisection_rankings.assert_called_once()
    boundary_kwargs = (
        controller.boundary_inward_bisection_rankings.call_args.kwargs
    )
    assert boundary_kwargs["required_admissible_plan_count"] == 3
    assert audit["physical_workload_invocations"] == 0
    controller.validate_top5_audit_records.assert_called_once_with(selected)


def test_valid_identity_backfill_uses_partial_expansion_exception_rows() -> None:
    controller = LaneController.__new__(LaneController)
    controller.target = "io"
    controller.ranges = {
        name: ParameterRange(*DEFAULT_RANGES[name]) for name in ACTIVE_PARAMS
    }
    controller.state = {
        "incumbent_params": dict(INITIAL_PARAMS),
        "incumbent_validation": {},
    }
    controller.log = mock.Mock()
    controller.validate_top5_audit_records = mock.Mock()
    ordinary = _gradient_backfill_candidate(
        seq_page_cost=1.1,
        plan_fingerprint="plan-ordinary-new",
    )
    partial = [
        _gradient_backfill_candidate(
            seq_page_cost=1.2 + index / 10,
            plan_fingerprint=f"plan-expanded-{index}",
        )
        for index in range(4)
    ]
    error = IncompleteSubsetOptimizationError(
        "only four clusters",
        [],
        "test-partial-expansion",
    )
    error.candidate_rankings = partial
    # Duplicate storage on the exception must not duplicate plan clusters.
    error.all_candidate_rankings = copy.deepcopy(partial)
    controller.generate_rankings = mock.Mock(side_effect=error)

    def select(rows, *, limit, **_kwargs):
        return list(rows[:limit]), {"admitted_count": min(limit, len(rows))}

    controller.select_outer_admitted_candidates = mock.Mock(side_effect=select)

    selected, audit = controller.backfill_replayed_identity_top5(
        [ordinary],
        [ordinary],
        {"exact_valid_replay_excluded_count": 4},
        round_index=3,
    )

    assert len(selected) == 5
    assert audit["initial_admitted_count"] == 1
    assert audit["backfill_admitted_count"] == 4
    assert audit["expansion_error"] == "only four clusters"
    assert audit["exclusion_counts"]["duplicate_full_plan"] == 4
    controller.validate_top5_audit_records.assert_called_once_with(selected)


def test_valid_identity_backfill_excludes_unsupported_partial_rows() -> None:
    controller = LaneController.__new__(LaneController)
    controller.target = "io"
    controller.ranges = {
        name: ParameterRange(*DEFAULT_RANGES[name]) for name in ACTIVE_PARAMS
    }
    controller.state = {
        "incumbent_params": dict(INITIAL_PARAMS),
        "incumbent_validation": {},
    }
    controller.experience_artifact = {
        "resource_prediction_calibration": {
            "resources": {
                "io": {"parameter_support_abstention_enabled": True}
            }
        }
    }
    controller.log = mock.Mock()
    controller.validate_top5_audit_records = mock.Mock()
    trusted = _gradient_backfill_candidate(
        seq_page_cost=1.1,
        plan_fingerprint="plan-replayed",
    )
    expanded = []
    for index in range(6):
        candidate = _gradient_backfill_candidate(
            seq_page_cost=1.2 + index / 10,
            plan_fingerprint=f"plan-support-{index}",
        )
        candidate["final_estimation_metadata"] = {
            "resource_prediction_calibration_audit": {
                "io": {
                    "parameter_support": {
                        "supported": index != 0,
                        "support_abstained": index == 0,
                    }
                }
            }
        }
        expanded.append(candidate)
    error = IncompleteSubsetOptimizationError(
        "partial rows include unsupported target points",
        [],
        "test-support-filter",
    )
    error.candidate_rankings = expanded
    error.all_candidate_rankings = []
    controller.generate_rankings = mock.Mock(side_effect=error)
    controller.select_outer_admitted_candidates = mock.Mock(
        side_effect=lambda rows, *, limit, **_kwargs: (
            list(rows[:limit]),
            {"admitted_count": min(limit, len(rows))},
        )
    )

    selected, audit = controller.backfill_replayed_identity_top5(
        [trusted],
        [],
        {"exact_valid_replay_excluded_count": 5},
        round_index=6,
    )

    assert len(selected) == 5
    assert all(
        row["three_level_fingerprint"]["plan_level"] != "plan-support-0"
        for row in selected
    )
    assert (
        audit["exclusion_counts"]["unsupported_target_predictor_domain"]
        == 1
    )
    controller.validate_top5_audit_records.assert_called_once_with(selected)


def test_qps_only_fallback_is_a_real_third_tier_accept() -> None:
    cfg = {
        "algorithm_c": {"qps_noise_gate": {"enabled": False}},
        "binary_plan_tuning": {
            "acceptance_policy": {
                "resource_capacity": {
                    "cpu_cores": 4.0,
                    "memory_gib": 10.0,
                    "io_mib_s": 100.0,
                }
            },
            "binary_search": {
                "nested_loop": {
                    "outer_acceptance": {
                        "min_db_qps_gain_pct": 0.1,
                        "min_target_drop_pct": 0.1,
                        "min_other_up_pct": 0.1,
                        "non_target_bottleneck_threshold_pct": 80.0,
                        "acceptance_tier_downgrade_after_rounds": 2,
                        "qps_reference_mode": "canonical_baseline",
                        "qps_threshold_mode": "static_floor",
                    }
                }
            },
        },
    }
    baseline = {
        "label": "baseline",
        "status": "ok",
        "db_qps": 100.0,
        "resources": {"cpu": 1.0, "memory": 5.0, "io": 20.0},
    }
    candidate = {
        "label": "qps-only",
        "status": "ok",
        "db_qps": 101.0,
        "resources": {"cpu": 1.0, "memory": 5.1, "io": 20.0},
        "actual_utilization_pct": {"cpu": 25.0, "memory": 51.0, "io": 20.0},
        "resource_stats": {"resource_monitor_ok": True},
    }
    decision = outer_semantic_acceptance_details(
        candidate,
        baseline,
        baseline,
        target="memory",
        cfg=cfg,
        consecutive_missed_outer_rounds=4,
    )
    assert decision["required_acceptance_tier"] == "fallback"
    assert decision["acceptance_tier"] == "fallback"
    assert decision["accepted"] is True
    assert decision["accepted_shrink"] is True
    assert decision["formal_state_mutation_allowed"] is True
    assert decision["accepted_escape"] is False


def test_range_invariant_rejects_full_range_escape_outside_current_interval() -> None:
    ranges = {
        name: ParameterRange(*bounds)
        for name, bounds in {
            "seq_page_cost": (0.5, 1.5),
            "random_page_cost": (2.0, 6.0),
            "cpu_tuple_cost": (0.005, 0.02),
            "cpu_index_tuple_cost": (0.002, 0.01),
            "cpu_operator_cost": (0.001, 0.005),
            "parallel_setup_cost": (500.0, 1500.0),
            "parallel_tuple_cost": (0.05, 0.2),
        }.items()
    }
    outside = dict(INITIAL_PARAMS, seq_page_cost=2.0)
    audit = parameter_vector_range_audit(outside, ranges)
    assert audit["passed"] is False
    assert audit["violations"]["seq_page_cost"]["reason"] == "outside_formal_range"

    controller = LaneController.__new__(LaneController)
    controller.ranges = copy.deepcopy(ranges)
    controller.state = {"accepted": 0}
    evaluation = {
        "raw": {"candidate_ranking": {"optimized_params": outside}},
        "normalized": {},
        "decision": {"accepted": True},
    }
    try:
        controller.accept(evaluation, 1)
    except RuntimeError as exc:
        assert "outside the current formal ranges" in str(exc)
    else:
        raise AssertionError("out-of-range full-basin candidate mutated formal state")
    assert controller.state["accepted"] == 0


def test_formal_range_filter_runs_before_topk_and_keeps_late_backfill() -> None:
    ranges = {
        name: ParameterRange(*DEFAULT_RANGES[name]) for name in ACTIVE_PARAMS
    }
    ranges["seq_page_cost"] = ParameterRange(1.0, 5.0)
    candidates = []
    for index, value in enumerate((9.0, 8.0, 1.1, 1.2, 1.3, 1.4, 1.5)):
        params = copy.deepcopy(INITIAL_PARAMS)
        params["seq_page_cost"] = value
        candidates.append({"rank": index + 1, "optimized_params": params})

    eligible, excluded = partition_formal_range_candidates(candidates, ranges)

    assert [row["rank"] for row in eligible] == [3, 4, 5, 6, 7]
    assert len(excluded) == 2
    assert all(
        row["range_audit"]["reason"] == "outside_formal_ranges"
        for row in excluded
    )


def test_base_top5_replay_accepts_partial_topk_and_emits_empty_wave_controls() -> None:
    controller = LaneController.__new__(LaneController)
    controller.validate_top5_audit_records = mock.Mock()
    controller.log = mock.Mock()
    controller.stop_requested = False
    controller.cfg = {}
    controller.output_dir = Path("/tmp/not-used-partial-topk")
    controller.workers = [{"name": "w1"}, {"name": "w2"}]
    controller.target = "cpu"
    controller.algorithm_cfg = {"scene": "xp_cpu"}
    controller.workload_contract_sha256 = "test-workload"
    controller.ranges = {
        name: ParameterRange(*DEFAULT_RANGES[name]) for name in ACTIVE_PARAMS
    }
    controller.initial_params = dict(INITIAL_PARAMS)
    controller.state = {
        "physical_workload_invocations": 0,
        "valid_replays": 0,
        "technical_failures": 0,
        "formal_candidate_wave_sequence": 0,
        "baseline_execution_domain": {"expected_no_mutex": False},
    }
    controller.save_state = mock.Mock()
    controller.restore_clean_baseline = mock.Mock(return_value={"passed": True})
    candidates = [
        {"rank": index + 1, "optimized_params": copy.deepcopy(INITIAL_PARAMS)}
        for index in range(3)
    ]
    replay_rows = [valid_physical_row(label=f"c{index + 1}") for index in range(3)]

    with mock.patch(
        "run_pg_algorithm_c_scene.restore_worker_from_hybench_template",
        return_value={"status": "ok"},
    ), mock.patch(
        "run_pg_algorithm_c_scene.run_real_validation_safe",
        side_effect=replay_rows,
    ), mock.patch(
        "run_pg_algorithm_c_scene.apply_ssd_wave_qualification",
        return_value={
            "passed": True,
            "errors": [],
            "thresholds": {"explicit_user_authorized_no_mutex": False},
        },
    ), mock.patch(
        "run_pg_algorithm_c_scene.wait_for_algorithm_c_candidate_cohort",
        return_value={"passed": True, "ready_count": 4},
    ) as ready, mock.patch(
        "run_pg_algorithm_c_scene.wait_for_algorithm_c_candidate_cohort_completion",
        return_value={"passed": True, "completed_count": 4},
    ) as done, mock.patch(
        "run_pg_algorithm_c_scene.acquire_configured_shared_ssd_workload_coordination",
        return_value=(None, 0.0),
    ):
        rows = controller.replay_batch(
            candidates,
            round_index=2,
            batch_kind="base_top5",
        )

    assert len(rows) == 3
    assert ready.call_count == 3
    assert done.call_count == 3
    assert controller.state["formal_candidate_wave_sequence"] == 3
    assert controller.state["physical_workload_invocations"] == 3
    empty_controls = [
        call
        for call in controller.log.call_args_list
        if call.args[0] == "algorithm_c_partial_topk_empty_wave_control"
    ]
    assert len(empty_controls) == 1
    assert empty_controls[0].kwargs["wave_start"] == 4
    assert empty_controls[0].kwargs["workload_executed"] is False
    assert empty_controls[0].kwargs["physical_workload_invocations"] == 0
    controller.validate_top5_audit_records.assert_called_once_with(candidates)


def test_zero_topk_emits_all_empty_waves_without_physical_state_mutation() -> None:
    controller = LaneController.__new__(LaneController)
    controller.validate_top5_audit_records = mock.Mock()
    controller.log = mock.Mock()
    controller.stop_requested = False
    controller.cfg = {}
    controller.output_dir = Path("/tmp/not-used-zero-topk")
    controller.workers = [{"name": "w1"}, {"name": "w2"}]
    controller.target = "memory"
    controller.algorithm_cfg = {"scene": "job_memory"}
    controller.workload_contract_sha256 = "test-workload"
    controller.state = {
        "physical_workload_invocations": 4,
        "valid_replays": 4,
        "technical_failures": 0,
        "formal_candidate_wave_sequence": 0,
    }
    controller.save_state = mock.Mock()
    controller.restore_clean_baseline = mock.Mock(return_value={"passed": True})

    with mock.patch(
        "run_pg_algorithm_c_scene.wait_for_algorithm_c_candidate_cohort",
        return_value={"passed": True, "ready_count": 4},
    ) as ready, mock.patch(
        "run_pg_algorithm_c_scene.wait_for_algorithm_c_candidate_cohort_completion",
        return_value={"passed": True, "completed_count": 4},
    ) as done:
        rows = controller.replay_batch(
            [],
            round_index=6,
            batch_kind="base_top5",
        )

    assert rows == []
    assert ready.call_count == 3
    assert done.call_count == 3
    assert controller.state["formal_candidate_wave_sequence"] == 3
    assert controller.state["physical_workload_invocations"] == 4
    assert controller.state["valid_replays"] == 4
    assert controller.state["technical_failures"] == 0
    assert sum(
        call.args[0] == "algorithm_c_partial_topk_empty_wave_control"
        for call in controller.log.call_args_list
    ) == 3


def test_exhausted_partial_topk_uses_only_eligible_current_range_plans() -> None:
    controller = LaneController.__new__(LaneController)
    controller.target = "memory"
    controller.ranges = {
        name: ParameterRange(*DEFAULT_RANGES[name]) for name in ACTIVE_PARAMS
    }
    controller.state = {"incumbent_params": copy.deepcopy(INITIAL_PARAMS)}
    controller.validate_top5_audit_records = mock.Mock()
    controller.log = mock.Mock()

    def candidate(index: int, plan: str, score: float) -> dict:
        params = copy.deepcopy(INITIAL_PARAMS)
        params["random_page_cost"] = 4.0 + index
        return {
            "rank": index,
            "subset_id": f"eligible-{index}",
            "optimized_params": params,
            "candidate_score": score,
            "three_level_fingerprint": {
                "valid": True,
                "id": f"three-level-{index}",
                "plan_level": plan,
            },
        }

    first = IncompleteSubsetOptimizationError("ordinary incomplete", [], "/tmp/a")
    first.eligible_candidate_rankings = [
        candidate(1, "plan-a", 2.0),
        candidate(2, "plan-b", 1.5),
    ]
    # Raw rows are deliberately not trusted by the merge path.
    first.candidate_rankings = [candidate(9, "raw-plan-must-not-enter", 99.0)]
    second = IncompleteSubsetOptimizationError("boundary incomplete", [], "/tmp/b")
    second.eligible_candidate_rankings = [
        candidate(3, "plan-a", 1.0),
        candidate(4, "plan-c", 1.25),
    ]

    selected = controller.merge_exhausted_partial_topk(
        [first, second],
        round_index=4,
    )

    assert [row["three_level_fingerprint"]["plan_level"] for row in selected] == [
        "plan-a",
        "plan-b",
        "plan-c",
    ]
    assert all(
        row["three_level_fingerprint"]["plan_level"] != "raw-plan-must-not-enter"
        for row in selected
    )
    controller.validate_top5_audit_records.assert_called_once_with(selected)


def test_plan_plateau_is_clean_exhaustion_not_fake_acceptance() -> None:
    controller = LaneController.__new__(LaneController)
    controller.accepted_target = 7
    controller.normalized_width_threshold = 0.01
    controller.state = {
        "accepted": 3,
        "normalized_width": 0.125,
        "consecutive_missed_outer_rounds": 2,
        "consecutive_plan_plateau_rounds": 0,
        "incumbent_params": copy.deepcopy(INITIAL_PARAMS),
    }
    controller.save_state = mock.Mock()
    controller.log = mock.Mock()

    controller.finish_plan_space_exhaustion(9)

    assert controller.state["accepted"] == 3
    assert controller.state["normalized_width"] == 0.125
    assert controller.state["incumbent_params"] == INITIAL_PARAMS
    assert controller.state["phase"] == (
        "candidate_space_exhausted_without_accepted_target_clean"
    )
    evidence = controller.state["candidate_space_exhaustion"]
    assert evidence["technical_failure"] is False
    assert evidence["accepted_unchanged"] is True
    assert evidence["ranges_unchanged"] is True
    assert evidence["outside_current_formal_range_reintroduction_allowed"] is False


def test_failed_boundary_probe_is_technical_not_plan_saturation() -> None:
    controller = LaneController.__new__(LaneController)
    controller.state = {
        "incumbent_params": copy.deepcopy(INITIAL_PARAMS),
        "incumbent_validation": {
            "resources": {"cpu": 10.0, "memory": 10.0, "io": 10.0}
        },
        "canonical_baseline": None,
        "valid_replay_identity_memory": {},
    }
    controller.ranges = {
        name: ParameterRange(*DEFAULT_RANGES[name]) for name in ACTIVE_PARAMS
    }
    controller.capacity = {"cpu": 100.0, "memory": 100.0, "io": 100.0}
    controller.target = "memory"
    controller.experience_artifact = {
        "resource_prediction_calibration": {"contract": "test"}
    }
    controller.log = mock.Mock()
    controller.queries_and_probe = mock.Mock(
        return_value=(
            [],
            {
                "exact_plan_metadata": {
                    "workload_plan_fingerprint": "incumbent-plan"
                }
            },
            {},
        )
    )
    gradient = {
        "active_subset": ["seq_page_cost"],
        "trajectory_index": 1,
        "g_j": {"seq_page_cost": 1.0},
        "dScore/dU": {
            "seq_page_cost": {"cpu": 0.0, "memory": 1.0, "io": 0.0}
        },
        "T": {"seq_page_cost": {"cpu": 0.0, "memory": 1.0, "io": 0.0}},
        "confidence": {"seq_page_cost": 1.0},
        "gradient_components": {},
    }
    error = IncompleteSubsetOptimizationError("large eta incomplete", [], "/tmp")
    error.all_candidate_rankings = [gradient]
    error.eligible_candidate_rankings = []

    class FailingEstimator:
        def __call__(self, _sql_set, _params):
            raise RuntimeError("EXPLAIN transport failed")

    with mock.patch(
        "run_pg_algorithm_c_scene.RatioScaleEstimator",
        return_value=FailingEstimator(),
    ):
        try:
            controller.boundary_inward_bisection_rankings(3, error)
        except IncompleteSubsetOptimizationError as exc:
            assert getattr(exc, "technical_candidate_generation_failure", False)
            assert getattr(exc, "plan_space_saturation_evidence", False) is False
        else:
            raise AssertionError("failed boundary probes were misclassified as saturation")


def test_stop_after_restore_prevents_physical_replay() -> None:
    controller = LaneController.__new__(LaneController)
    controller.stop_requested = True
    controller.cfg = {}
    controller.output_dir = Path("/tmp/not-used")
    controller.workers = [{"name": "w1"}, {"name": "w2"}]
    controller.target = "cpu"
    controller.validate_top5_audit_records = mock.Mock()
    controller.log = mock.Mock()
    candidates = [
        {"rank": index + 1, "optimized_params": copy.deepcopy(INITIAL_PARAMS)}
        for index in range(5)
    ]
    with mock.patch(
        "run_pg_algorithm_c_scene.restore_worker_from_hybench_template",
        return_value={"status": "ok"},
    ), mock.patch(
        "run_pg_algorithm_c_scene.run_real_validation_safe"
    ) as replay:
        rows = controller.replay_batch(
            candidates,
            round_index=2,
            batch_kind="base_top5",
        )
    assert rows == []
    replay.assert_not_called()
    assert any(
        call.args[0] == "algorithm_c_stop_after_restore_before_physical_replay"
        for call in controller.log.call_args_list
    )


def test_formal_wave_restores_two_workers_concurrently() -> None:
    """Two-point replay waves must not serialize independent DB clones."""
    controller = LaneController.__new__(LaneController)
    controller.stop_requested = True
    controller.cfg = {}
    controller.output_dir = Path("/tmp/not-used")
    controller.workers = [{"name": "w1"}, {"name": "w2"}]
    controller.target = "cpu"
    controller.validate_top5_audit_records = mock.Mock()
    controller.log = mock.Mock()
    barrier = threading.Barrier(2)

    def concurrent_restore(_cfg, worker, *, label):
        barrier.wait(timeout=2.0)
        return {"status": "ok", "worker": worker["name"], "label": label}

    candidates = [
        {"rank": index + 1, "optimized_params": copy.deepcopy(INITIAL_PARAMS)}
        for index in range(5)
    ]
    with mock.patch(
        "run_pg_algorithm_c_scene.restore_worker_from_hybench_template",
        side_effect=concurrent_restore,
    ), mock.patch(
        "run_pg_algorithm_c_scene.run_real_validation_safe"
    ) as replay:
        rows = controller.replay_batch(
            candidates,
            round_index=1,
            batch_kind="base_top5",
        )
    assert rows == []
    replay.assert_not_called()


def test_formal_baseline_reuses_clean_handoff_once_then_restores_in_parallel() -> None:
    controller = LaneController.__new__(LaneController)
    controller.cfg = {}
    controller.workers = [{"name": "w1"}, {"name": "w2"}]
    controller.initial_params = copy.deepcopy(INITIAL_PARAMS)
    controller.log = mock.Mock()
    good_readback = {name: format(value, ".17g") for name, value in INITIAL_PARAMS.items()}
    controller.prepared_clean_handoff_receipt = {
        "reason": "calibration_complete_or_reused",
        "completed_at_epoch_s": 123.0,
        "workers": [
            {
                "worker": name,
                "restore": {"status": "ok", "worker": name},
                "baseline_parameter_readback": {
                    "passed": True,
                    "readback": good_readback,
                },
            }
            for name in ("w1", "w2")
        ],
    }

    with mock.patch(
        "run_pg_algorithm_c_scene.restore_worker_from_hybench_template"
    ) as restore:
        prepared = controller.prepare_parallel_baseline_workers(1)
    assert len(prepared) == 2
    restore.assert_not_called()
    assert controller.prepared_clean_handoff_receipt is None

    barrier = threading.Barrier(2)

    def concurrent_restore(_cfg, worker, *, label):
        barrier.wait(timeout=2.0)
        return {"status": "ok", "worker": worker["name"], "label": label}

    with mock.patch(
        "run_pg_algorithm_c_scene.restore_worker_from_hybench_template",
        side_effect=concurrent_restore,
    ) as restore, mock.patch(
        "run_pg_algorithm_c_scene.pg_conn_from_worker",
        return_value=object(),
    ), mock.patch(
        "run_pg_algorithm_c_scene.apply_pg_params",
        return_value=good_readback,
    ):
        prepared = controller.prepare_parallel_baseline_workers(2)
    assert len(prepared) == 2
    assert restore.call_count == 2


def test_max_rounds_stops_clean_without_semantic_mutation() -> None:
    controller = LaneController.__new__(LaneController)
    with tempfile.TemporaryDirectory() as tmp:
        controller.output_dir = Path(tmp)
        controller.cfg_path = Path(tmp) / "cfg.json"
        controller.t_artifact_path = Path(tmp) / "table.json"
        controller.algorithm_cfg = {"scene": "test", "workload_contract": {}}
        controller.workload_runtime_execution_domain = {"passed": True}
        controller.accepted_target = 7
        controller.max_rounds = 2
        controller.outer_round_limit = 2
        controller.no_improvement_convergence_rounds = 2
        controller.normalized_width_threshold = 0.01
        controller.stability_confirmation_rounds = 0
        controller.convergence_contract = {"expected_normalized_width": 1.0 / 128.0}
        controller.stop_requested = False
        controller.state = {
            "accepted": 0,
            "accepted_target": 7,
            "round_index": 2,
            "consecutive_no_improvement_rounds": 0,
            "formal_started": True,
            "incumbent_params": copy.deepcopy(INITIAL_PARAMS),
            "ranges": {},
        }
        incumbent_before = copy.deepcopy(controller.state["incumbent_params"])
        controller.baseline = mock.Mock()
        controller.verify_baseline_execution_domain = mock.Mock()
        controller.restore_clean_baseline = mock.Mock()
        controller.save_state = mock.Mock()
        controller.log = mock.Mock()
        controller.run()
        assert controller.state["phase"] == "nonconvergence_max_rounds_clean"
        assert controller.state["accepted"] == 0
        assert controller.state["incumbent_params"] == incumbent_before
        assert controller.restore_clean_baseline.call_args_list[-1] == mock.call(
            reason="formal_outer_round_limit_nonconvergence"
        )


def test_fine_interval_has_no_stability_confirmation_grace() -> None:
    controller = LaneController.__new__(LaneController)
    controller.outer_round_limit = 13
    controller.accepted_target = 7
    controller.no_improvement_convergence_rounds = 2
    controller.normalized_width_threshold = 0.01
    controller.state = {
        "accepted": 3,
        "round_index": 13,
        "normalized_width": 1.0 / 128.0,
        "consecutive_no_improvement_rounds": 1,
        "incumbent_centered_stagnation_refinement_count": 4,
    }
    assert controller.effective_outer_round_limit() == 13
    controller.state["consecutive_no_improvement_rounds"] = 0
    assert controller.effective_outer_round_limit() == 13
    controller.state["normalized_width"] = 1.0 / 64.0
    assert controller.effective_outer_round_limit() == 13


def test_clean_max_round_stop_does_not_reopen_for_confirmation_grace() -> None:
    controller = LaneController.__new__(LaneController)
    controller.outer_round_limit = 13
    controller.accepted_target = 7
    controller.no_improvement_convergence_rounds = 2
    controller.normalized_width_threshold = 0.01
    controller.state = {
        "phase": "nonconvergence_max_rounds_clean",
        "accepted": 3,
        "round_index": 13,
        "normalized_width": 1.0 / 128.0,
        "consecutive_no_improvement_rounds": 1,
        "incumbent_centered_stagnation_refinement_count": 4,
        "incumbent_params": copy.deepcopy(INITIAL_PARAMS),
        "ranges": {"seq_page_cost": {"min": 0.9, "max": 1.1}},
        "valid_replays": 106,
        "physical_workload_invocations": 110,
        "technical_failures": 4,
        "inflight_physical_wave": None,
        "nonconvergence": {"reason": "budget"},
    }
    before = copy.deepcopy(controller.state)
    controller.save_state = mock.Mock()
    controller.log = mock.Mock()
    assert controller.reopen_truncated_fine_interval_convergence() is False
    assert controller.state == before
    controller.save_state.assert_not_called()
    controller.log.assert_not_called()


def test_accept_shrinks_only_moved_active_subset_parameters() -> None:
    """A one-parameter accepted probe must not invent six shrink decisions."""
    controller = LaneController.__new__(LaneController)
    controller.ranges = {
        name: ParameterRange(*DEFAULT_RANGES[name]) for name in ACTIVE_PARAMS
    }
    controller.state = {
        "accepted": 0,
        "incumbent_params": copy.deepcopy(INITIAL_PARAMS),
        "incumbent_validation": None,
        "consecutive_missed_outer_rounds": 1,
        "phase": "formal_search",
    }
    controller.accepted_target = 7
    controller.max_rounds = 64
    controller.normalized_width_threshold = 0.01
    controller.save_state = mock.Mock()
    controller.log = mock.Mock()
    candidate_params = copy.deepcopy(INITIAL_PARAMS)
    candidate_params["seq_page_cost"] = 5.0
    evaluation = {
        "raw": {
            "candidate_ranking": {
                "active_subset": ["seq_page_cost"],
                "optimized_params": candidate_params,
            }
        },
        "normalized": {"resources": {"cpu": 1.0, "memory": 1.0, "io": 1.0}},
        "decision": {"accepted": True},
    }
    before = copy.deepcopy(controller.ranges)

    controller.accept(evaluation, round_index=1)

    assert controller.state["accepted"] == 1
    assert controller.ranges["seq_page_cost"] != before["seq_page_cost"]
    for name in ACTIVE_PARAMS:
        if name != "seq_page_cost":
            assert controller.ranges[name] == before[name]
    event = controller.log.call_args.kwargs
    assert event["range_shrink_parameter_subset"] == ["seq_page_cost"]
    assert event["inactive_parameter_ranges_preserved"] is True


def test_global_coverage_vectors_are_deterministic_and_external_evidence_free() -> None:
    current_ranges = {
        name: ParameterRange(*DEFAULT_RANGES[name]) for name in ACTIVE_PARAMS
    }
    current_ranges["seq_page_cost"] = ParameterRange(0.5, 1.5)
    current_ranges["random_page_cost"] = ParameterRange(3.0, 5.0)
    first = deterministic_global_coverage_vectors(INITIAL_PARAMS, current_ranges)
    second = deterministic_global_coverage_vectors(INITIAL_PARAMS, current_ranges)

    assert first == second
    assert len(first) == 256
    assert len({exact_param_signature(row) for row in first}) == 256
    assert all(exact_param_signature(row) != exact_param_signature(INITIAL_PARAMS) for row in first)
    for row in first:
        assert set(row) == set(ACTIVE_PARAMS)
        for name, value in row.items():
            low = current_ranges[name].min_value
            high = current_ranges[name].max_value
            assert low <= float(value) <= high
    source = inspect.getsource(deterministic_global_coverage_vectors).lower()
    assert "algorithm_a" not in source
    assert tuple(inspect.signature(deterministic_global_coverage_vectors).parameters) == (
        "incumbent_params",
        "parameter_ranges",
        "sample_count",
    )
    assert "read_text" not in source
    assert "open(" not in source


def test_global_coverage_accept_stays_inside_current_parameter_domain() -> None:
    controller = LaneController.__new__(LaneController)
    controller.ranges = {
        name: ParameterRange(*DEFAULT_RANGES[name]) for name in ACTIVE_PARAMS
    }
    controller.ranges["seq_page_cost"] = ParameterRange(0.5, 1.5)
    controller.ranges["random_page_cost"] = ParameterRange(3.0, 5.0)
    controller.state = {
        "accepted": 0,
        "incumbent_params": copy.deepcopy(INITIAL_PARAMS),
        "incumbent_validation": None,
        "consecutive_missed_outer_rounds": 1,
        "phase": "formal_search",
    }
    controller.accepted_target = 7
    controller.max_rounds = 7
    controller.normalized_width_threshold = 0.01
    controller.save_state = mock.Mock()
    controller.log = mock.Mock()
    candidate_params = copy.deepcopy(INITIAL_PARAMS)
    candidate_params["seq_page_cost"] = 1.4
    evaluation = {
        "raw": {
            "candidate_ranking": {
                "active_subset": ["seq_page_cost"],
                "optimized_params": candidate_params,
                "global_plan_coverage_rescue": True,
                "global_plan_coverage_contract": GLOBAL_PLAN_COVERAGE_CONTRACT,
            }
        },
        "normalized": {"resources": {"cpu": 1.0, "memory": 1.0, "io": 1.0}},
        "decision": {"accepted": True},
    }

    controller.accept(evaluation, round_index=1)

    assert controller.state["accepted"] == 1
    assert controller.ranges["seq_page_cost"] == ParameterRange(
        math.sqrt(0.5 * 1.5),
        1.5,
    )
    assert controller.ranges["random_page_cost"] == ParameterRange(3.0, 5.0)
    event = controller.log.call_args.kwargs
    assert event["global_plan_coverage_rescue"] is True
    assert event["formal_range_reopened_from_default_bounds"] is False
    assert event["global_coverage_limited_to_current_formal_interval"] is True
    assert event["external_algorithm_evidence_used"] is False


def test_incumbent_interval_is_refined_after_two_complete_misses() -> None:
    controller = LaneController.__new__(LaneController)
    controller.accepted_target = 7
    controller.no_improvement_convergence_rounds = 2
    controller.ranges = {
        name: ParameterRange(*DEFAULT_RANGES[name])
        for name in ACTIVE_PARAMS
    }
    controller.state = {
        "accepted": 1,
        "accepted_target": 7,
        "normalized_width": 0.5,
        "consecutive_missed_outer_rounds": 0,
        "consecutive_no_improvement_rounds": 0,
        "incumbent_params": copy.deepcopy(INITIAL_PARAMS),
        "incumbent_validation": {"db_qps": 100.0},
    }
    controller.save_state = mock.Mock()
    controller.log = mock.Mock()
    controller.finalize_on_current_incumbent = mock.Mock()

    first = controller.finish_no_improvement_round(
        round_index=3,
        reason="first_complete_exhaustive_miss",
        exhaustive_current_interval=True,
    )
    assert first is False
    assert controller.state["consecutive_no_improvement_rounds"] == 1
    controller.finalize_on_current_incumbent.assert_not_called()

    terminal = controller.finish_no_improvement_round(
        round_index=4,
        reason="second_complete_exhaustive_miss",
        exhaustive_current_interval=True,
    )
    assert terminal is False
    assert controller.state["accepted"] == 1
    assert controller.state["consecutive_no_improvement_rounds"] == 0
    assert controller.state[
        "incumbent_centered_stagnation_refinement_count"
    ] == 1
    assert controller.state["effective_binary_shrink_count"] == 2
    assert math.isclose(controller.state["accepted_normalized_width"], 0.5)
    assert math.isclose(controller.state["normalized_width"], 0.25)
    assert controller.state["incumbent_params"] == INITIAL_PARAMS
    controller.finalize_on_current_incumbent.assert_not_called()
    shrink_event = controller.log.call_args.kwargs
    assert shrink_event["rejected_candidate_used_as_anchor"] is False
    assert shrink_event["accepted_unchanged"] is True
    assert shrink_event["next_round_uses_refined_ranges"] is True
    assert shrink_event["normalized_width_unchanged"] is False


def test_effective_one_percent_interval_finishes_without_more_replays() -> None:
    controller = LaneController.__new__(LaneController)
    controller.accepted_target = 7
    controller.no_improvement_convergence_rounds = 2
    controller.normalized_width_threshold = 0.01
    controller.ranges = {
        name: ParameterRange(*DEFAULT_RANGES[name])
        for name in ACTIVE_PARAMS
    }
    controller.state = {
        "accepted": 3,
        "accepted_target": 7,
        "incumbent_centered_stagnation_refinement_count": 4,
        "effective_binary_shrink_count": 7,
        "normalized_width": 1.0 / 128.0,
        "consecutive_missed_outer_rounds": 1,
        "consecutive_no_improvement_rounds": 1,
        "incumbent_params": copy.deepcopy(INITIAL_PARAMS),
        "incumbent_validation": {"db_qps": 100.0},
    }
    controller.save_state = mock.Mock()
    controller.log = mock.Mock()
    controller.finalize_on_current_incumbent = mock.Mock()

    terminal = controller.finalize_precision_terminal_without_stability_confirmation()

    assert terminal is True
    assert controller.state["accepted"] == 3
    controller.finalize_on_current_incumbent.assert_called_once()


def test_refinement_crossing_one_percent_finishes_immediately() -> None:
    controller = LaneController.__new__(LaneController)
    controller.accepted_target = 7
    controller.no_improvement_convergence_rounds = 2
    controller.normalized_width_threshold = 0.01
    controller.ranges = {
        name: ParameterRange(*DEFAULT_RANGES[name])
        for name in ACTIVE_PARAMS
    }
    controller.state = {
        "accepted": 3,
        "accepted_target": 7,
        "incumbent_centered_stagnation_refinement_count": 3,
        "effective_binary_shrink_count": 6,
        "normalized_width": 1.0 / 64.0,
        "consecutive_missed_outer_rounds": 1,
        "consecutive_no_improvement_rounds": 1,
        "incumbent_params": copy.deepcopy(INITIAL_PARAMS),
        "incumbent_validation": {"db_qps": 100.0},
    }
    controller.save_state = mock.Mock()
    controller.log = mock.Mock()
    controller.finalize_on_current_incumbent = mock.Mock()

    terminal = controller.finish_no_improvement_round(
        round_index=12,
        reason="second_complete_miss_crosses_precision",
        exhaustive_current_interval=True,
    )

    assert terminal is True
    assert math.isclose(controller.state["normalized_width"], 1.0 / 128.0)
    assert controller.state["accepted"] == 3
    controller.finalize_on_current_incumbent.assert_called_once_with(
        reason=(
            "effective_interval_reached_threshold_without_"
            "stability_confirmation"
        ),
        round_index=12,
        exhaustive_global_coverage=True,
    )


def test_incumbent_convergence_logs_restore_once_and_writes_authority() -> None:
    controller = LaneController.__new__(LaneController)
    controller.outer_round_limit = 14
    controller.normalized_width_threshold = 0.01
    controller.no_improvement_convergence_rounds = 2
    controller.state = {
        "accepted": 3,
        "accepted_candidate_archive": [{"accepted_index": 3}],
        "incumbent_centered_stagnation_refinement_count": 4,
        "consecutive_no_improvement_rounds": 2,
        "incumbent_params": copy.deepcopy(INITIAL_PARAMS),
        "incumbent_validation": {"db_qps": 100.0},
    }
    restore_receipt = {"passed": True, "reason": "formal_incumbent_convergence"}
    controller.save_state = mock.Mock()
    controller.log = mock.Mock()
    controller.restore_clean_baseline = mock.Mock(return_value=restore_receipt)
    controller.write_authority_manifest = mock.Mock()

    controller.finalize_on_current_incumbent(
        reason="precision_reached_without_stability_confirmation",
        round_index=14,
        exhaustive_global_coverage=True,
    )

    assert controller.state["phase"] == "converged_on_incumbent_clean"
    assert controller.state["accepted"] == 3
    assert controller.state["incumbent_convergence"][
        "clean_restore_receipt"
    ] == restore_receipt
    converged_event = controller.log.call_args_list[-1]
    assert converged_event.args == ("algorithm_c_converged_on_current_incumbent",)
    assert converged_event.kwargs["clean_restore_receipt"] == restore_receipt
    controller.write_authority_manifest.assert_called_once_with(1.0 / 128.0)


def test_fine_interval_incumbent_terminal_is_not_reopened_on_restart() -> None:
    controller = LaneController.__new__(LaneController)
    controller.accepted_target = 7
    controller.normalized_width_threshold = 0.01
    controller.state = {
        "accepted": 3,
        "phase": "converged_on_incumbent_clean",
        "completion_contract": INCUMBENT_CONVERGENCE_CONTRACT,
        "normalized_width": 1.0 / 128.0,
        "incumbent_convergence": {
            "effective_normalized_width": 1.0 / 128.0,
            "clean_restore_receipt": {"passed": True},
        },
    }

    assert controller.reopen_under_target_incumbent_convergence() is False
    assert controller.state["phase"] == "converged_on_incumbent_clean"
    assert controller.state["accepted"] == 3


def test_pending_signal_stop_restore_recovers_original_round() -> None:
    controller = LaneController.__new__(LaneController)
    controller.state = {
        "phase": "signal_stop_pending_clean_restore",
        "round_index": 12,
        "signal_stop_interrupted_phase": "global_plan_coverage_rescue_real_replay",
        "signal_stop_interrupted_round_index": 12,
        "accepted": 3,
    }
    receipt = {"passed": True, "reason": "signal_stop_restart_recovery"}
    controller.restore_clean_baseline = mock.Mock(return_value=receipt)
    controller.save_state = mock.Mock()
    controller.log = mock.Mock()

    recovered = controller.recover_pending_signal_stop_restore()

    assert recovered is True
    assert controller.state["phase"] == "stopped_by_signal_clean"
    assert controller.state["round_index"] == 12
    assert controller.state["accepted"] == 3
    assert unfinished_algorithm_c_round_resume_index(controller.state) == 12
    controller.restore_clean_baseline.assert_called_once_with(
        reason="signal_stop_restart_recovery"
    )


def test_current_interval_coverage_starts_after_local_plan_pool_exhaustion() -> None:
    source = inspect.getsource(LaneController.run)
    local_marker = "algorithm_c_local_plan_tail_expansion"
    exhaustion_marker = "if not expansion:"
    coverage_marker = "generate_global_plan_coverage_rankings"
    assert local_marker in source
    assert exhaustion_marker in source
    assert coverage_marker in source
    assert source.index(local_marker) < source.index(exhaustion_marker)
    assert source.index(exhaustion_marker) < source.index(coverage_marker)
    assert "ordinary_local_scored_top10_completed_without_" in source
    assert "physical_batch_limit=BASE_TOP_K" in source
    assert LOCAL_PLAN_CANDIDATE_REAL_REPLAY_LIMIT == 10
    assert (
        "local_replay_count < LOCAL_PLAN_CANDIDATE_REAL_REPLAY_LIMIT"
        in source
    )
    assert GLOBAL_PLAN_COVERAGE_REAL_REPLAY_LIMIT == 10
    assert (
        "coverage_replay_count < GLOBAL_PLAN_COVERAGE_REAL_REPLAY_LIMIT"
        in source
    )
    assert "candidate_score_ranking_used=True" in source


def test_global_coverage_excludes_surplus_probe_errors_without_killing_top5() -> None:
    source = inspect.getsource(
        LaneController.generate_global_plan_coverage_rankings
    )
    assert "offline_probe_error_count=len(probe_errors)" in source
    assert "failed_offline_probes_excluded=True" in source
    assert "failed_offline_probes_used_for_candidates=False" in source
    assert "if probe_errors and len(rankings) < BASE_TOP_K:" in source
    assert (
        'if probe_errors:\n            raise RuntimeError(\n'
        '                "global plan coverage has failed offline probes: "'
        not in source
    )


def test_technical_candidate_waves_continue_within_bounded_budget() -> None:
    run_source = inspect.getsource(LaneController.run)
    resume_source = inspect.getsource(
        LaneController.resume_current_interval_coverage_after_local_budget
    )

    assert run_source.count("algorithm_c_technical_wave_excluded_continue") >= 3
    assert "continue_within_physical_budget=True" in run_source
    assert "if base_rows:" in run_source
    assert "ordinary Top-5 contains a technical failure" not in run_source
    assert "local plan-tail expansion contains a technical failure" not in run_source
    assert "current-interval coverage contains a technical failure" not in run_source
    assert "algorithm_c_technical_wave_excluded_continue" in resume_source
    assert "continue_within_physical_budget=True" in resume_source
    assert "resumed current-interval coverage contains a technical" not in resume_source

def test_restart_local_top10_overrun_is_quarantined_not_technical() -> None:
    controller = LaneController.__new__(LaneController)
    with tempfile.TemporaryDirectory() as tmp:
        controller.log_path = Path(tmp) / "algorithm_c.jsonl"
        controller.state = {
            "round_index": 1,
            "valid_replays": 15,
            "physical_workload_invocations": 15,
            "technical_failures": 0,
            "invalidated_replay_labels": [],
            "valid_replay_identity_memory": {
                "identities": {
                    "late-identity": {"labels": ["late-local-row"]},
                }
            },
        }
        rows = [
            {
                "event": "algorithm_c_local_candidate_budget_complete",
                "time": 10.0,
                "round_index": 1,
                "physical_replay_count": 10,
            },
            {
                "event": "algorithm_c_candidate_replay",
                "time": 11.0,
                "round_index": 1,
                "batch_kind": "base_top5",
                "validation": valid_physical_row(label="late-local-row"),
            },
        ]
        controller.log_path.write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )
        controller.save_state = mock.Mock()
        controller.log = mock.Mock()

        controller.repair_restart_local_candidate_budget_overrun()

        assert controller.state["valid_replays"] == 14
        assert controller.state["physical_workload_invocations"] == 15
        assert controller.state["technical_failures"] == 0
        assert controller.state["invalidated_replay_labels"] == [
            "late-local-row"
        ]
        assert (
            controller.state["valid_replay_identity_memory"]["identities"]
            == {}
        )
        repair = controller.state[
            "restart_local_budget_overrun_quarantine"
        ]
        assert repair["physical_workload_invocations_retained"] is True
        assert repair["technical_failures_unchanged"] is True


def test_restart_after_local_top10_resumes_current_interval_coverage() -> None:
    source = inspect.getsource(
        LaneController.resume_current_interval_coverage_after_local_budget
    )
    run_source = inspect.getsource(LaneController.run)

    assert "local_candidate_budget_checkpoint" in source
    assert "ordinary_candidate_generation_skipped=True" in source
    assert "ordinary_physical_replay_skipped=True" in source
    assert "generate_global_plan_coverage_rankings" in source
    assert "invalidated_replay_labels_excluded=True" in source
    assert "resume_current_interval_coverage_after_local_budget" in run_source
    assert run_source.index(
        "resume_current_interval_coverage_after_local_budget"
    ) < run_source.index("self.generate_rankings(round_index)")


def test_canonical_qps_reject_is_learnable_outer_evidence() -> None:
    cfg = {
        "algorithm_c": {
            "workload_contract": {"benchmark": "job"},
            "qps_noise_gate": {"enabled": True},
        },
        "binary_plan_tuning": {
            "acceptance_policy": {},
            "binary_search": {"nested_loop": {"outer_acceptance": {}}},
        },
    }
    context = outer_pruning_context(
        cfg,
        target="memory",
        incumbent_params=INITIAL_PARAMS,
        required_acceptance_tier="strict",
        qps_noise_model_id="noise-v1",
    )
    candidate = {
        "optimized_params": dict(INITIAL_PARAMS, seq_page_cost=0.9),
        "three_level_fingerprint": {"plan_level": "full-plan-a"},
    }
    result = record_outer_pruning_observation(
        empty_outer_pruning_memory(),
        context=context,
        candidate=candidate,
        decision={
            "accepted": False,
            "rejects": ["db_qps_not_up_vs_canonical_baseline"],
            "qps_noise_gate_required": True,
            "qps_noise_model_ready": True,
        },
        round_index=1,
    )
    assert result["recorded"] is True


def test_outer_qps_reject_evidence_is_reused_across_acceptance_tiers() -> None:
    cfg = {
        "algorithm_c": {
            "workload_contract": {"benchmark": "job"},
            "qps_noise_gate": {"enabled": True},
        },
        "binary_plan_tuning": {
            "acceptance_policy": {},
            "binary_search": {"nested_loop": {"outer_acceptance": {}}},
        },
    }
    strict_context = outer_pruning_context(
        cfg,
        target="memory",
        incumbent_params=INITIAL_PARAMS,
        required_acceptance_tier="strict",
        qps_noise_model_id="noise-v1",
    )
    secondary_context = outer_pruning_context(
        cfg,
        target="memory",
        incumbent_params=INITIAL_PARAMS,
        required_acceptance_tier="secondary",
        qps_noise_model_id="noise-v1",
    )
    assert strict_context["context_sha256"] == secondary_context["context_sha256"]
    assert strict_context["required_acceptance_tier_observed"] == "strict"
    assert secondary_context["required_acceptance_tier_observed"] == "secondary"

    memory = empty_outer_pruning_memory()
    first = {
        "optimized_params": dict(INITIAL_PARAMS, seq_page_cost=0.9),
        "three_level_fingerprint": {"plan_level": "full-plan-a"},
    }
    second = {
        "optimized_params": dict(INITIAL_PARAMS, seq_page_cost=0.8),
        "three_level_fingerprint": {"plan_level": "full-plan-a"},
    }
    decision = {
        "accepted": False,
        "rejects": ["db_qps_not_up_vs_canonical_baseline"],
        "qps_noise_gate_required": True,
        "qps_noise_model_ready": True,
    }
    record_outer_pruning_observation(
        memory,
        context=strict_context,
        candidate=first,
        decision=decision,
        round_index=1,
    )
    record_outer_pruning_observation(
        memory,
        context=secondary_context,
        candidate=second,
        decision=decision,
        round_index=2,
    )
    assessment = assess_outer_pruning_candidate(
        memory,
        context=secondary_context,
        candidate=second,
        policy={
            "enabled": True,
            "minimum_valid_rejects_per_plan": 2,
            "minimum_distinct_rounds": 2,
            "minimum_distinct_parameter_vectors": 2,
        },
    )
    assert assessment["hard_prune"] is True
    assert assessment["rejected_count"] == 2


def test_tier_only_reject_is_not_learnable_outer_evidence() -> None:
    cfg = {
        "algorithm_c": {"workload_contract": {"benchmark": "job"}},
        "binary_plan_tuning": {
            "acceptance_policy": {},
            "binary_search": {"nested_loop": {"outer_acceptance": {}}},
        },
    }
    context = outer_pruning_context(
        cfg,
        target="memory",
        incumbent_params=INITIAL_PARAMS,
        required_acceptance_tier="strict",
        qps_noise_model_id="noise-v1",
    )
    candidate = {
        "optimized_params": dict(INITIAL_PARAMS, seq_page_cost=0.9),
        "three_level_fingerprint": {"plan_level": "full-plan-a"},
    }
    result = record_outer_pruning_observation(
        empty_outer_pruning_memory(),
        context=context,
        candidate=candidate,
        decision={
            "accepted": False,
            "rejects": ["acceptance_tier_below_current_threshold"],
            "qps_noise_gate_required": True,
            "qps_noise_model_ready": True,
        },
        round_index=1,
    )
    assert result["recorded"] is False
    assert result["reason"] == "reject_reason_not_safe_for_reuse"


def test_technical_failure_is_counted_per_invocation() -> None:
    valid = valid_physical_row(label="valid")
    failed = valid_physical_row(label="failed", technical_failure=True)
    counts = physical_batch_counts([valid, failed])
    assert counts == {
        "physical_workload_invocations": 2,
        "valid_replays": 1,
        "technical_failures": 1,
    }
    assert valid["technical_failure"] is False


def test_failed_completion_domain_is_excluded_not_counted_as_lane_failure() -> None:
    row = valid_physical_row(
        candidate_cohort_completion_barrier={
            "enabled": True,
            "passed": False,
        },
        ssd_qualification={"passed": True},
    )
    assert physical_batch_counts([row]) == {
        "physical_workload_invocations": 1,
        "valid_replays": 0,
        "technical_failures": 0,
        "out_of_band_invalid_replays": 1,
    }


def test_legacy_over_wall_row_is_accounted_as_technical_not_valid() -> None:
    row = valid_physical_row(label="legacy-over-wall")
    row["workload_completeness"] = {"passed": True}
    row["hybench"] = {
        "benchmark": {
            "completed_within_target_wall_time": False,
            "target_wall_time_s": 900,
            "wall_time_s": 1119.0,
        }
    }
    assert physical_batch_counts([row]) == {
        "physical_workload_invocations": 1,
        "valid_replays": 0,
        "technical_failures": 1,
    }


def test_legacy_over_wall_durable_state_is_reclassified_once() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        row = valid_physical_row(
            event="candidate_validation",
            round=4,
            label="calibration_r04_c02_w2",
        )
        row["workload_completeness"] = {"passed": True}
        row["hybench"] = {
            "benchmark": {
                "completed_within_target_wall_time": False,
                "target_wall_time_s": 900,
                "wall_time_s": 1119.0,
            }
        }
        runner = FreshCalibration.__new__(FreshCalibration)
        runner.log_path = Path(tmp) / "calibration_replays.jsonl"
        runner.log_path.write_text(
            json.dumps(row, sort_keys=True) + "\n", encoding="utf-8"
        )
        runner.state = {
            "physical_workload_invocations": 1,
            "valid_replays": 1,
            "technical_failures": 0,
            "completed_batches": ["candidate_batch_04"],
            "failed_batches": [],
            "candidate_batch_progress": {},
        }
        runner.save = mock.Mock()
        runner.event = mock.Mock()

        runner.reclassify_accounted_legacy_over_wall_rows()
        runner.reclassify_accounted_legacy_over_wall_rows()

        assert runner.state["physical_workload_invocations"] == 1
        assert runner.state["valid_replays"] == 0
        assert runner.state["technical_failures"] == 1
        assert runner.state["completed_batches"] == []
        assert len(runner.state["failed_batches"]) == 1
        assert runner.state["failed_batches"][0]["valid_replays"] == 0
        assert runner.state["failed_batches"][0]["technical_failures"] == 1
        assert runner.save.call_count == 1


def test_all_zero_gradient_with_nonzero_delta_fails_closed() -> None:
    candidate = {
        "g_j": {name: 0.0 for name in ACTIVE_PARAMS},
        "delta_param": {name: 0.0 for name in ACTIVE_PARAMS},
    }
    validate_zero_gradient_delta(candidate, 1)
    candidate["delta_param"]["seq_page_cost"] = 0.1
    try:
        validate_zero_gradient_delta(candidate, 1)
    except RuntimeError as exc:
        assert "all-zero g_j but nonzero delta_param" in str(exc)
    else:
        raise AssertionError("zero-gradient movement passed formal validation")


def test_behavior_cluster_expansion_reaches_full_gradient_boundary() -> None:
    steps = BEHAVIOR_CLUSTER_GRADIENT_PROBE_STEP_PCT
    assert tuple(sorted(set(steps))) == steps
    assert steps[:3] == (10.0, 20.0, 35.0)
    # A positive gradient can reach a 10x upper bound from the incumbent.
    assert steps[-1] == 900.0


def test_current_range_candidate_exhaustion_uses_gradient_step_expansion() -> None:
    controller = LaneController.__new__(LaneController)
    controller.ranges = {
        name: ParameterRange(*DEFAULT_RANGES[name]) for name in ACTIVE_PARAMS
    }
    controller.log = mock.Mock()
    controller.generate_rankings = mock.Mock(return_value=[{"rank": 1}])
    incumbent_error = IncompleteSubsetOptimizationError(
        "only one plan cluster",
        [],
        "/tmp/subset_logs",
    )

    rankings = controller.expand_behavior_cluster_rankings(1, incumbent_error)

    assert rankings == [{"rank": 1}]
    assert controller.generate_rankings.call_count == 1
    kwargs = controller.generate_rankings.call_args.kwargs
    assert kwargs["ranking_scope"] == (
        "current_range_gradient_step_behavior_cluster_expansion"
    )
    assert kwargs["parameter_ranges"] is controller.ranges
    assert tuple(kwargs["relative_step_probe_pct"]) == (
        BEHAVIOR_CLUSTER_GRADIENT_PROBE_STEP_PCT
    )
    controller.log.assert_called_once()
    event_name = controller.log.call_args.args[0]
    event = controller.log.call_args.kwargs
    assert event_name == "algorithm_c_behavior_cluster_gradient_step_expansion"
    assert event["gradient_direction_preserved"] is True
    assert event["random_direction_used"] is False
    assert event["zero_confidence_parameter_movement_allowed"] is False
    assert event["uncertain_probe_used"] is False
    assert event["baseline_padding_allowed"] is False
    assert event["physical_workload_invocations"] == 0


def test_shrunk_boundary_exhaustion_uses_inward_bisection_fallback() -> None:
    controller = LaneController.__new__(LaneController)
    controller.ranges = {
        name: ParameterRange(*DEFAULT_RANGES[name]) for name in ACTIVE_PARAMS
    }
    controller.log = mock.Mock()
    incumbent_error = IncompleteSubsetOptimizationError(
        "incumbent range incomplete",
        [],
        "/tmp/subset_logs",
    )
    gradient_error = IncompleteSubsetOptimizationError(
        "large eta incomplete",
        [],
        "/tmp/subset_logs",
    )
    expected = [{"rank": index} for index in range(1, 6)]
    controller.generate_rankings = mock.Mock(side_effect=gradient_error)
    controller.boundary_inward_bisection_rankings = mock.Mock(
        return_value=expected
    )

    rankings = controller.expand_behavior_cluster_rankings(2, incumbent_error)

    assert rankings == expected
    assert controller.generate_rankings.call_count == 1
    assert controller.generate_rankings.call_args.kwargs["parameter_ranges"] is (
        controller.ranges
    )
    controller.boundary_inward_bisection_rankings.assert_called_once_with(
        2,
        gradient_error,
    )


def test_boundary_inward_bisection_keeps_g_and_moves_inside_log_interval() -> None:
    controller = LaneController.__new__(LaneController)
    controller.state = {
        "incumbent_params": {
            **INITIAL_PARAMS,
            "seq_page_cost": 10.0,
        },
        "incumbent_validation": {
            "resources": {"cpu": 10.0, "memory": 10.0, "io": 10.0}
        },
        "canonical_baseline": None,
    }
    controller.ranges = {
        name: ParameterRange(*DEFAULT_RANGES[name]) for name in ACTIVE_PARAMS
    }
    controller.ranges["seq_page_cost"] = ParameterRange(1.0, 10.0)
    controller.capacity = {"cpu": 100.0, "memory": 100.0, "io": 100.0}
    controller.target = "cpu"
    controller.experience_artifact = {
        "resource_prediction_calibration": {"contract": "test"}
    }
    controller.log = mock.Mock()
    controller.validate_top5_audit_records = mock.Mock()
    controller.queries_and_probe = mock.Mock(
        return_value=(
            [],
            {
                "exact_plan_metadata": {
                    "workload_plan_fingerprint": "incumbent-plan"
                }
            },
            {},
        )
    )
    full_gradient = {
        "active_subset": list(ACTIVE_PARAMS),
        "trajectory_index": 1,
        "g_j": {
            name: (1.0 if name == "seq_page_cost" else 0.0)
            for name in ACTIVE_PARAMS
        },
        "dScore/dU": {
            name: {"cpu": 1.0, "memory": 0.0, "io": 0.0}
            for name in ACTIVE_PARAMS
        },
        "T": {
            name: {"cpu": 1.0, "memory": 0.0, "io": 0.0}
            for name in ACTIVE_PARAMS
        },
        "confidence": {name: 1.0 for name in ACTIVE_PARAMS},
        "gradient_components": {},
    }
    error = IncompleteSubsetOptimizationError(
        "large eta incomplete",
        [],
        "/tmp/subset_logs",
    )
    error.all_candidate_rankings = [full_gradient]
    probed_params = []

    class FakeEstimator:
        def __call__(self, _sql_set, params):
            probed_params.append(copy.deepcopy(params))
            value = float(params["seq_page_cost"])
            resources = {"cpu": 10.0 + value / 100.0, "memory": 10.0, "io": 10.0}
            metadata = {
                "feature_ratios": {
                    "cpu": resources["cpu"] / 10.0,
                    "memory": 1.0,
                    "io": 1.0,
                }
            }
            return resources, {}, metadata

    def fake_attach(ranking, capability):
        _ = capability
        enriched = copy.deepcopy(ranking)
        value = float(enriched["optimized_params"]["seq_page_cost"])
        enriched.update(
            {
                "three_level_fingerprint": {
                    "valid": True,
                    "id": f"three-{value:.12g}",
                    "plan_level": f"plan-{value:.12g}",
                },
                "feature_ratio": copy.deepcopy(
                    enriched["final_estimation_metadata"]["feature_ratios"]
                ),
                "predicted_resource": copy.deepcopy(enriched["final_resource"]),
                "utilization": {
                    name: float(enriched["final_resource"][name]) / 100.0
                    for name in ("cpu", "memory", "io")
                },
            }
        )
        return enriched

    with mock.patch(
        "run_pg_algorithm_c_scene.RatioScaleEstimator",
        return_value=FakeEstimator(),
    ), mock.patch(
        "run_pg_algorithm_c_scene.attach_three_level_fingerprint",
        side_effect=fake_attach,
    ):
        rankings = controller.boundary_inward_bisection_rankings(2, error)

    assert len(rankings) >= 5
    assert math.isclose(
        probed_params[0]["seq_page_cost"],
        math.sqrt(10.0),
        rel_tol=1e-12,
    )
    assert all(
        1.0 < row["optimized_params"]["seq_page_cost"] < 10.0
        for row in rankings
    )
    assert all(row["g_j"]["seq_page_cost"] > 0.0 for row in rankings)
    assert all(
        row["delta_param"]["seq_page_cost"]["absolute"] < 0.0
        for row in rankings
    )
    assert all(row["used_for_gradient"] is False for row in rankings)
    controller.validate_top5_audit_records.assert_called_once()

    # An interior incumbent must follow the trusted non-zero gradient toward
    # its retained bound even when the available frozen-gradient row covers
    # only that active subset.  Missing/zero-gradient coordinates stay fixed.
    controller.state["incumbent_params"]["seq_page_cost"] = 4.0
    probed_params.clear()
    partial_gradient = {
        "active_subset": ["seq_page_cost"],
        "trajectory_index": 1,
        "g_j": {"seq_page_cost": 1.0},
        "dScore/dU": {
            "seq_page_cost": {"cpu": 1.0, "memory": 0.0, "io": 0.0}
        },
        "T": {
            "seq_page_cost": {"cpu": 1.0, "memory": 0.0, "io": 0.0}
        },
        "confidence": {"seq_page_cost": 1.0},
        "gradient_components": {},
    }
    partial_error = IncompleteSubsetOptimizationError(
        "identity exhausted with partial gradient row",
        [],
        "/tmp/subset_logs",
    )
    partial_error.all_candidate_rankings = [partial_gradient]

    with mock.patch(
        "run_pg_algorithm_c_scene.RatioScaleEstimator",
        return_value=FakeEstimator(),
    ), mock.patch(
        "run_pg_algorithm_c_scene.attach_three_level_fingerprint",
        side_effect=fake_attach,
    ):
        interior_rankings = controller.boundary_inward_bisection_rankings(
            3,
            partial_error,
        )

    assert len(interior_rankings) >= 5
    assert math.isclose(
        probed_params[0]["seq_page_cost"],
        math.sqrt(40.0),
        rel_tol=1e-12,
    )
    assert all(
        4.0 < row["optimized_params"]["seq_page_cost"] < 10.0
        for row in interior_rankings
    )
    assert all(
        row["delta_param"]["seq_page_cost"]["absolute"] > 0.0
        for row in interior_rankings
    )
    assert all(
        row["optimized_params"]["random_page_cost"]
        == INITIAL_PARAMS["random_page_cost"]
        for row in interior_rankings
    )

    # Identity-backfill mode must not stop merely because eight total plan
    # clusters were found.  If the first eight are already-valid replay
    # identities, keep the bounded dyadic search until five unseen plans are
    # present (or the existing 96-probe cap is reached).
    controller.state["incumbent_params"]["seq_page_cost"] = 10.0
    controller.state["valid_replay_identity_memory"] = {
        "workload_contract_sha256": "test-workload",
        "target": "cpu",
        "identities": {},
    }
    probed_params.clear()
    identity_assessment_calls = 0

    def fake_identity_assessment(_memory, _candidate):
        nonlocal identity_assessment_calls
        identity_assessment_calls += 1
        return {
            "seen_valid_replay": identity_assessment_calls <= 8,
            "identity_key": f"identity-{identity_assessment_calls}",
        }

    with mock.patch(
        "run_pg_algorithm_c_scene.RatioScaleEstimator",
        return_value=FakeEstimator(),
    ), mock.patch(
        "run_pg_algorithm_c_scene.attach_three_level_fingerprint",
        side_effect=fake_attach,
    ), mock.patch(
        "run_pg_algorithm_c_scene.assess_valid_replay_identity",
        side_effect=fake_identity_assessment,
    ):
        identity_rankings = controller.boundary_inward_bisection_rankings(
            4,
            error,
            required_admissible_plan_count=5,
            excluded_batch_plan_fingerprints=set(),
        )

    assert len(identity_rankings) >= 13
    assert len(probed_params) == 13
    identity_log = controller.log.call_args.kwargs
    assert identity_log["early_stop_mode"] == (
        "unseen_valid_replay_identity_and_batch_plan"
    )
    assert identity_log["required_admissible_plan_count"] == 5
    assert identity_log["admissible_candidate_plan_count"] == 5


def test_restore_requires_ok_status_and_exact_parameter_readback() -> None:
    good_readback = {name: format(value, ".17g") for name, value in INITIAL_PARAMS.items()}
    assert baseline_parameter_readback_audit(INITIAL_PARAMS, good_readback)["passed"] is True
    bad_readback = dict(good_readback, seq_page_cost="2")
    assert baseline_parameter_readback_audit(INITIAL_PARAMS, bad_readback)["passed"] is False

    controller = LaneController.__new__(LaneController)
    controller.cfg = {}
    controller.workers = [{"name": "w1"}, {"name": "w2"}]
    controller.initial_params = copy.deepcopy(INITIAL_PARAMS)
    controller.state = {"phase": "formal_completion", "restore_technical_failures": 0}
    controller.log = mock.Mock()
    controller.save_state = mock.Mock()
    with mock.patch(
        "run_pg_algorithm_c_scene.restore_worker_from_hybench_template",
        side_effect=[{"status": "ok"}, {"status": "error", "error": "clone failed"}],
    ), mock.patch(
        "run_pg_algorithm_c_scene.pg_conn_from_worker", return_value=object()
    ), mock.patch(
        "run_pg_algorithm_c_scene.apply_pg_params", return_value=good_readback
    ):
        try:
            controller.restore_clean_baseline(reason="test")
        except RuntimeError as exc:
            assert "restore/readback failed" in str(exc)
        else:
            raise AssertionError("failed restore was reported as clean")
    assert controller.state["phase"] == "clean_restore_technical_failure"
    assert controller.state["restore_technical_failures"] == 1


def test_calibration_restore_retries_only_transient_missing_database_readback() -> None:
    """A completed clone gets a bounded retry for catalog visibility only."""
    good_readback = {
        name: format(value, ".17g") for name, value in INITIAL_PARAMS.items()
    }
    runner = FreshCalibration.__new__(FreshCalibration)
    runner.cfg = {}
    runner.workers = [{"name": "w1"}, {"name": "w2"}]
    runner.initial_params = copy.deepcopy(INITIAL_PARAMS)
    runner.target = "memory"
    runner.event = mock.Mock()
    runner.clean_restore_contract = mock.Mock(return_value="contract-sha")

    with mock.patch(
        "run_pg_algorithm_c_calibrate_then_scene.restore_worker_from_hybench_template",
        side_effect=[
            {"status": "ok", "worker": "w1"},
            {"status": "ok", "worker": "w2"},
        ],
    ), mock.patch(
        "run_pg_algorithm_c_calibrate_then_scene.pg_conn_from_worker",
        return_value=object(),
    ), mock.patch(
        "run_pg_algorithm_c_calibrate_then_scene.apply_pg_params",
        side_effect=[
            RuntimeError('FATAL: database "fullgrid_job" does not exist'),
            good_readback,
            good_readback,
        ],
    ) as apply_params, mock.patch(
        "run_pg_algorithm_c_calibrate_then_scene.time.sleep"
    ):
        runner.restore_clean("test_transient_visibility")

    assert apply_params.call_count == 3
    retry_events = [
        call
        for call in runner.event.call_args_list
        if call.args[0]
        == "algorithm_c_calibration_post_restore_readback_retry"
    ]
    assert len(retry_events) == 1
    assert retry_events[0].kwargs["formal_state_mutation"] is False
    assert retry_events[0].kwargs["physical_workload_invocations"] == 0


def calibration_row(batch_index: int, position: int, design_start: int, params):
    return valid_physical_row(
        event="candidate_validation",
        round=batch_index,
        candidate_index=design_start + position - 1,
        label=f"calibration_r{batch_index:02d}_c{position:02d}_w{(position - 1) % 2 + 1}",
        worker_key=f"w{(position - 1) % 2 + 1}",
        params=copy.deepcopy(params),
    )


def test_partial_calibration_resume_accounts_c1_c4_once_and_runs_only_c5() -> None:
    batch_index = 4
    design_start = 15
    candidates = [
        deterministic_design_point(design_start + offset, INITIAL_PARAMS)
        for offset in range(5)
    ]
    with tempfile.TemporaryDirectory() as tmp:
        output_dir = Path(tmp)
        log_path = output_dir / "calibration_replays.jsonl"
        original_rows = [
            calibration_row(batch_index, position, design_start, candidates[position - 1])
            for position in range(1, 5)
        ]
        original_prefix = "".join(
            json.dumps(row, sort_keys=True) + "\n" for row in original_rows
        )
        log_path.write_text(original_prefix, encoding="utf-8")
        (output_dir / "calibration_audit.jsonl").write_text(
            json.dumps(
                {
                    "event": "algorithm_c_stop_requested",
                    "active_phase": "candidate_batch_04",
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        runner = FreshCalibration.__new__(FreshCalibration)
        runner.output_dir = output_dir
        runner.log_path = log_path
        runner.cfg = {}
        runner.target = "memory"
        runner.initial_params = copy.deepcopy(INITIAL_PARAMS)
        runner.workers = [{"name": "w1"}, {"name": "w2"}]
        runner.stop_requested = False
        runner.design_audit = {"ready": True}
        runner.state = {
            "phase": "stopped_by_signal_clean",
            "physical_workload_invocations": 0,
            "valid_replays": 0,
            "technical_failures": 0,
            "completed_batches": ["baseline", "candidate_batch_01", "candidate_batch_02", "candidate_batch_03"],
            "failed_batches": [],
        }
        runner.save = mock.Mock()
        runner.event = mock.Mock()
        runner.restore_clean = mock.Mock()

        runner.recover_legacy_failed_batch_state()
        assert runner.state["physical_workload_invocations"] == 4
        assert runner.state["valid_replays"] == 4
        assert runner.failed_batch_names() == set()
        resumable_events = [
            call
            for call in runner.event.call_args_list
            if call.args[0]
            == "algorithm_c_calibration_clean_stop_partial_batch_resumable"
        ]
        assert len(resumable_events) == 1

        replay_calls = []

        def replay(_cfg, params, **kwargs):
            replay_calls.append((copy.deepcopy(params), kwargs["label"]))
            return valid_physical_row(params=copy.deepcopy(params), label=kwargs["label"])

        with mock.patch(
            "run_pg_algorithm_c_calibrate_then_scene.restore_worker_from_hybench_template",
            return_value={"status": "ok"},
        ), mock.patch(
            "run_pg_algorithm_c_calibrate_then_scene.run_real_validation_safe",
            side_effect=replay,
        ), mock.patch(
            "run_pg_algorithm_c_calibrate_then_scene.apply_ssd_wave_qualification",
            return_value={"passed": True},
        ):
            completed = runner.run_candidate_batch(batch_index, design_start)

        assert completed is True
        assert len(replay_calls) == 1
        assert replay_calls[0][0] == candidates[4]
        assert "_c05_" in replay_calls[0][1]
        assert runner.state["physical_workload_invocations"] == 5
        assert runner.state["valid_replays"] == 5
        assert runner.state["technical_failures"] == 0
        assert "candidate_batch_04" in runner.state["completed_batches"]
        assert runner.failed_batch_names() == set()
        assert log_path.read_text(encoding="utf-8").startswith(original_prefix)
        assert len(log_path.read_text(encoding="utf-8").splitlines()) == 5


def test_out_of_band_partial_calibration_batch_is_sealed_without_resume() -> None:
    """An explicitly aborted SSD wave must never be mixed with a later c5."""
    batch_index = 4
    design_start = 15
    candidates = [
        deterministic_design_point(design_start + offset, INITIAL_PARAMS)
        for offset in range(5)
    ]
    with tempfile.TemporaryDirectory() as tmp:
        output_dir = Path(tmp)
        log_path = output_dir / "calibration_replays.jsonl"
        rows = [
            calibration_row(batch_index, position, design_start, candidates[position - 1])
            for position in range(1, 5)
        ]
        for row in rows[2:]:
            row["status"] = "failed"
            row["technical_failure"] = True
        log_path.write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
            encoding="utf-8",
        )
        (output_dir / "calibration_audit.jsonl").write_text(
            json.dumps(
                {
                    "event": "algorithm_c_calibration_batch_aborted_out_of_band",
                    "batch_index": batch_index,
                    "executed_slots": 4,
                    "remaining_slots": 1,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        runner = FreshCalibration.__new__(FreshCalibration)
        runner.output_dir = output_dir
        runner.log_path = log_path
        runner.target = "memory"
        runner.state = {
            "phase": "candidate_batch_partial_accounted_clean",
            "physical_workload_invocations": 0,
            "valid_replays": 0,
            "technical_failures": 0,
            "completed_batches": ["baseline"],
            "failed_batches": [],
        }
        runner.save = mock.Mock()
        runner.event = mock.Mock()

        runner.recover_legacy_failed_batch_state()

        assert runner.state["physical_workload_invocations"] == 4
        assert runner.state["valid_replays"] == 2
        assert runner.state["technical_failures"] == 2
        assert runner.failed_batch_names() == {"candidate_batch_04"}
        assert runner.state.get("candidate_batch_progress") == {}
        failed = runner.state["failed_batches"][0]
        assert failed["source"] == "ssd_out_of_band_partial_batch_do_not_resume"
        assert failed["do_not_retry"] is True


def test_failed_replacement_holdout_continues_to_table_build() -> None:
    """Failed immutable batches 2 and 3 must advance instead of killing calibration."""
    with tempfile.TemporaryDirectory() as tmp:
        runner = FreshCalibration.__new__(FreshCalibration)
        runner.output_dir = Path(tmp)
        runner.target = "memory"
        runner.design_audit = {"ready": True}
        runner.stop_requested = False
        runner.calibration_release_enabled = False
        runner.t_path = Path(tmp) / "experience_table.json"
        runner.state = {
            "phase": "candidate_batch_technical_failure_recorded_clean",
            "completed_batches": ["baseline", "candidate_batch_01"],
            "failed_batches": [
                {"batch_name": "candidate_batch_02", "batch_index": 2},
                {"batch_name": "candidate_batch_03", "batch_index": 3},
            ],
            "physical_workload_invocations": 15,
        }
        runner.event = mock.Mock()
        runner.apply_preformal_runtime_contract_repairs = mock.Mock()
        runner.recover_legacy_failed_batch_state = mock.Mock()
        runner.try_reuse_calibration_release = mock.Mock(return_value=False)
        runner.run_baseline = mock.Mock()
        runner.run_candidate_batch = mock.Mock(
            side_effect=lambda batch_index, *_args, **_kwargs: batch_index == 1
        )
        ready_artifact = {
            "artifact_sha256": "artifact-sha",
            "formal_admission": {"ready": True},
        }
        runner.build_table_for_admission = mock.Mock(return_value=ready_artifact)
        runner.save = mock.Mock()
        runner.restore_clean = mock.Mock()
        runner.handoff_formal = mock.Mock()

        runner.run()

        assert runner.run_candidate_batch.call_args_list[:3] == [
            mock.call(1, 0),
            mock.call(2, 5),
            mock.call(3, 10),
        ]
        runner.build_table_for_admission.assert_called_once_with()
        runner.handoff_formal.assert_called_once_with()
        runner.event.assert_any_call(
            "algorithm_c_calibration_replacement_holdout_unavailable",
            batch_index=3,
            action="continue_with_distinct_plan_change_only_batch",
            failed_batches=copy.deepcopy(runner.state["failed_batches"]),
            physical_workload_invocations=0,
            formal_state_mutation=False,
        )


def test_failed_training_batch_one_advances_without_retry() -> None:
    """A sealed technical batch 1 must not permanently deadlock calibration."""
    with tempfile.TemporaryDirectory() as tmp:
        runner = FreshCalibration.__new__(FreshCalibration)
        runner.output_dir = Path(tmp)
        runner.target = "memory"
        runner.design_audit = {"ready": True}
        runner.stop_requested = False
        runner.calibration_release_enabled = False
        runner.t_path = Path(tmp) / "experience_table.json"
        runner.state = {
            "phase": "candidate_batch_technical_failure_recorded_clean",
            "completed_batches": ["baseline"],
            "failed_batches": [
                {"batch_name": "candidate_batch_01", "batch_index": 1},
            ],
            "physical_workload_invocations": 4,
        }
        runner.event = mock.Mock()
        runner.apply_preformal_runtime_contract_repairs = mock.Mock()
        runner.recover_legacy_failed_batch_state = mock.Mock()
        runner.try_reuse_calibration_release = mock.Mock(return_value=False)
        runner.run_baseline = mock.Mock()
        runner.run_candidate_batch = mock.Mock(
            side_effect=lambda batch_index, *_args, **_kwargs: batch_index != 1
        )
        runner.build_table_for_admission = mock.Mock(
            return_value={
                "artifact_sha256": "artifact-sha",
                "formal_admission": {"ready": True},
            }
        )
        runner.save = mock.Mock()
        runner.restore_clean = mock.Mock()
        runner.handoff_formal = mock.Mock()

        runner.run()

        assert runner.run_candidate_batch.call_args_list[:2] == [
            mock.call(1, 0),
            mock.call(2, 5),
        ]
        runner.event.assert_any_call(
            "algorithm_c_calibration_training_batch_unavailable",
            batch_index=1,
            action="advance_to_distinct_batch_02_without_retry",
            failed_batches=copy.deepcopy(runner.state["failed_batches"]),
            physical_workload_invocations=0,
            candidate_batch_retry=False,
            technical_failures_used_for_model=False,
            formal_state_mutation=False,
        )
        runner.handoff_formal.assert_called_once_with()


def test_insufficient_valid_vectors_is_non_ready_not_controller_failure() -> None:
    runner = FreshCalibration.__new__(FreshCalibration)
    runner.build_table = mock.Mock(
        side_effect=RuntimeError(
            "experience-table builder failed: current-scope T requires at least "
            "30 valid physical observations, got 7"
        )
    )
    runner.event = mock.Mock()

    artifact = runner.build_table_for_admission()

    assert artifact["formal_admission"]["ready"] is False
    assert artifact["formal_admission"][
        "observed_valid_distinct_parameter_vectors"
    ] == 7
    runner.event.assert_called_once_with(
        "algorithm_c_calibration_table_insufficient_valid_vectors",
        observed_valid_distinct_parameter_vectors=7,
        minimum_valid_physical_resource_observations=30,
        errors=["valid_physical_resource_observation_count_below_30"],
        action="collect_fresh_independent_plan_change_batch",
        qps_used=False,
        physical_workload_invocations=0,
        formal_state_mutation=False,
    )


def main() -> int:
    test_physical_replay_artifact_identity_prevents_resume_label_collision()
    test_stability_assessment_allows_noise_before_consecutive_pair()
    test_stability_qps_temporal_gap_is_diagnostic_after_two_accepts()
    test_stability_assessment_allows_fixed_cross_worker_offset()
    test_four_lane_completion_barriers_block_next_restore_and_propagate_failure()
    test_cohort_receipts_are_atomic_and_corrupt_files_never_count()
    test_tmp_space_preflight_fails_before_cohort_or_workload()
    test_valid_physical_replay_is_cached_and_redecided_without_accounting()
    test_one_strict_candidate_accepts_the_same_top5_round()
    test_same_round_fallback_is_accepted_when_no_stricter_candidate_exists()
    test_formal_qps_contract_requires_previous_accepted_reference()
    test_evaluate_batch_rejects_qps_below_previous_accepted_round()
    test_historical_valid_pool_redecides_never_accepted_in_range_points()
    test_cross_campaign_seed_projects_after_fresh_baseline_and_rehydrates()
    test_valid_identity_backfill_uses_only_new_trusted_gradient_plans()
    test_valid_identity_backfill_uses_directional_bisection_at_full_range()
    test_valid_identity_backfill_uses_partial_expansion_exception_rows()
    test_valid_identity_backfill_excludes_unsupported_partial_rows()
    test_qps_only_fallback_is_a_real_third_tier_accept()
    test_range_invariant_rejects_full_range_escape_outside_current_interval()
    test_base_top5_replay_accepts_partial_topk_and_emits_empty_wave_controls()
    test_zero_topk_emits_all_empty_waves_without_physical_state_mutation()
    test_exhausted_partial_topk_uses_only_eligible_current_range_plans()
    test_plan_plateau_is_clean_exhaustion_not_fake_acceptance()
    test_failed_boundary_probe_is_technical_not_plan_saturation()
    test_stop_after_restore_prevents_physical_replay()
    test_formal_wave_restores_two_workers_concurrently()
    test_formal_baseline_reuses_clean_handoff_once_then_restores_in_parallel()
    test_max_rounds_stops_clean_without_semantic_mutation()
    test_fine_interval_has_no_stability_confirmation_grace()
    test_clean_max_round_stop_does_not_reopen_for_confirmation_grace()
    test_accept_shrinks_only_moved_active_subset_parameters()
    test_global_coverage_vectors_are_deterministic_and_external_evidence_free()
    test_global_coverage_accept_stays_inside_current_parameter_domain()
    test_incumbent_interval_is_refined_after_two_complete_misses()
    test_effective_one_percent_interval_finishes_without_more_replays()
    test_refinement_crossing_one_percent_finishes_immediately()
    test_incumbent_convergence_logs_restore_once_and_writes_authority()
    test_fine_interval_incumbent_terminal_is_not_reopened_on_restart()
    test_pending_signal_stop_restore_recovers_original_round()
    test_current_interval_coverage_starts_after_local_plan_pool_exhaustion()
    test_global_coverage_excludes_surplus_probe_errors_without_killing_top5()
    test_technical_candidate_waves_continue_within_bounded_budget()
    test_restart_local_top10_overrun_is_quarantined_not_technical()
    test_restart_after_local_top10_resumes_current_interval_coverage()
    test_canonical_qps_reject_is_learnable_outer_evidence()
    test_outer_qps_reject_evidence_is_reused_across_acceptance_tiers()
    test_tier_only_reject_is_not_learnable_outer_evidence()
    test_technical_failure_is_counted_per_invocation()
    test_failed_completion_domain_is_excluded_not_counted_as_lane_failure()
    test_legacy_over_wall_row_is_accounted_as_technical_not_valid()
    test_legacy_over_wall_durable_state_is_reclassified_once()
    test_all_zero_gradient_with_nonzero_delta_fails_closed()
    test_behavior_cluster_expansion_reaches_full_gradient_boundary()
    test_current_range_candidate_exhaustion_uses_gradient_step_expansion()
    test_shrunk_boundary_exhaustion_uses_inward_bisection_fallback()
    test_boundary_inward_bisection_keeps_g_and_moves_inside_log_interval()
    test_restore_requires_ok_status_and_exact_parameter_readback()
    test_calibration_restore_retries_only_transient_missing_database_readback()
    test_partial_calibration_resume_accounts_c1_c4_once_and_runs_only_c5()
    test_out_of_band_partial_calibration_batch_is_sealed_without_resume()
    test_failed_replacement_holdout_continues_to_table_build()
    test_failed_training_batch_one_advances_without_retry()
    test_insufficient_valid_vectors_is_non_ready_not_controller_failure()
    print("algorithm_c_formal_gate_repairs: PASS", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
