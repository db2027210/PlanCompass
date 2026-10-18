#!/usr/bin/env python3
"""Fast semantic tests for the Algorithm A exhaustive candidate contract."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) in sys.path:
    sys.path.remove(str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT))

from shell import run_pg_algorithm_a_scene as algorithm_a
from shell import run_pg_algorithm_c_scene as framework
from shell import check_pg_algorithm_a_progress as progress
from shell import prepare_pg_algorithm_a_tpch_parallel8_r6 as tpch_parallel8
from shell import prepare_pg_algorithm_a_replay_farm_transition as replay_farm


def test_two_dimensional_product() -> None:
    ranges = {
        "x": framework.ParameterRange(1.0, 16.0),
        "y": framework.ParameterRange(0.0, 8.0),
    }
    rows = algorithm_a.full_factorial_parameter_vectors(
        ranges,
        parameter_names=("x", "y"),
    )
    assert len(rows) == 4
    assert [mask for mask, _params, _levels in rows] == [0, 1, 2, 3]
    assert len({tuple(params.values()) for _mask, params, _levels in rows}) == 4
    assert rows[0][2] == {"x": "L", "y": "L"}
    assert rows[-1][2] == {"x": "R", "y": "R"}


def test_lane_independent_algorithm_a_no_mutex_baseline_domain() -> None:
    qualification = {
        "qualification_id": "q-single-lane",
        "thresholds": {
            "explicit_user_authorized_no_mutex": True,
            "global_ssd_stable_window_enforced": False,
            "global_ssd_utilization_is_informational": True,
        },
    }
    row = {"label": "baseline", "ssd_qualification": qualification}
    cohort = {"enabled": False, "cohort_size": 1}
    algorithm_cfg = {
        "workload_contract": {
            "shared_ssd_coordination": {
                "contract": "pg_algorithm_c_user_authorized_no_mutex_v1",
                "enabled": False,
                "physical_workload_mode": "none",
                "database_restore_mode": "none",
            },
            "baseline_cohort": {
                "contract": "algorithm_c_four_lane_no_mutex_baseline_cohort_v1",
                "lane_count": 1,
            },
            "candidate_cohort": {
                "contract": "algorithm_a_lane_independent_candidate_waves_v1",
                "lane_count": 1,
                "synchronization": "none_between_lanes",
            },
        }
    }
    authorized = framework.algorithm_a_single_lane_no_mutex_authorized(
        algorithm_cfg,
        cohort,
    )
    assert authorized is True
    passed = framework.baseline_execution_domain_audit(
        [row],
        {"ssd_qualification": qualification},
        expected_no_mutex=True,
        cohort=cohort,
        allow_single_lane_no_mutex=authorized,
    )
    assert passed["passed"] is True, passed
    assert passed["single_lane_no_mutex_authorized"] is True

    stale = copy.deepcopy(algorithm_cfg)
    stale["workload_contract"]["candidate_cohort"]["lane_count"] = 4
    assert framework.algorithm_a_single_lane_no_mutex_authorized(
        stale,
        cohort,
    ) is False


def test_ssd_wave_does_not_spread_candidate_local_failure() -> None:
    def replay_row(worker: str, *, complete: bool, seq_page_cost: float) -> dict:
        return {
            "status": "ok",
            "technical_failure": False,
            "worker_key": worker,
            "window": [100.0, 200.0],
            "params": {"seq_page_cost": seq_page_cost},
            "resources": {"io": 25.0},
            "resource_stats": {
                "resource_monitor_ok": True,
                "ssd_util_percent": {"avg": 80.0, "p99": 100.0, "max": 100.0},
                "stable_sample_count": {"io_util_pct": 100},
                "ssd_observer_disk_devices": ["nvme1n1"],
                "ssd_util_source": "host_diskstats_io_busy_ms",
            },
            "workload_completeness": {
                "passed": complete,
                "errors": [] if complete else ["tpch_target_wall_time_exceeded"],
            },
        }

    failed = replay_row("w1", complete=False, seq_page_cost=1.0)
    valid = replay_row("w2", complete=True, seq_page_cost=2.0)
    cfg = {
        "algorithm_c": {
            "ssd_replay_qualification": {
                "enabled": True,
                "expected_devices": ["nvme1n1"],
                "expected_source": "host_diskstats_io_busy_ms",
                "minimum_stable_samples": 45,
                "non_target_shared_ssd_policy": "container_io_capacity",
                "non_target_maximum_container_io_util_pct": 80.0,
            }
        },
        "binary_plan_tuning": {
            "acceptance_policy": {"resource_capacity": {"io_mib_s": 1000.0}}
        },
    }
    with mock.patch.object(
        framework,
        "explicit_user_authorized_no_mutex_from_env",
        return_value=True,
    ):
        wave = framework.apply_ssd_wave_qualification(
            [failed, valid],
            target="memory",
            phase="formal_algorithm_a_full_factorial",
            cfg=cfg,
        )

    assert wave["passed"] is False
    assert wave["wave_errors"] == []
    assert failed["technical_failure"] is True
    assert failed["ssd_qualification"]["passed"] is False
    assert valid["technical_failure"] is False
    assert valid["ssd_qualification"]["passed"] is True
    assert framework.physical_valid(valid) is True


def test_inflight_wave_recovery_is_technical_and_idempotent() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        controller = algorithm_a.AlgorithmALaneController.__new__(
            algorithm_a.AlgorithmALaneController
        )
        controller.target = "memory"
        controller.workload_contract_sha256 = "contract"
        controller.log_path = Path(temp_dir) / "algorithm_a.jsonl"
        controller.log_path.write_text("", encoding="utf-8")
        controller.valid_replay_evidence_by_identity = {}
        controller.state = {
            "physical_workload_invocations": 10,
            "valid_replays": 8,
            "technical_failures": 2,
            "inflight_physical_wave": {
                "journal_id": "journal-1",
                "round_index": 3,
                "batch_kind": algorithm_a.ALGORITHM_A_BATCH_KIND,
                "entries": [
                    {
                        "physical_slot": 1,
                        "label": "candidate-1",
                        "worker_key": "w1",
                        "candidate": {
                            "optimized_params": {"seq_page_cost": 1.0},
                        },
                    },
                    {
                        "physical_slot": 2,
                        "label": "candidate-2",
                        "worker_key": "w2",
                        "candidate": {
                            "optimized_params": {"seq_page_cost": 2.0},
                        },
                    },
                ],
            },
        }
        events = []
        controller.log = lambda event, **fields: events.append((event, fields))
        controller.save_state = lambda: None
        controller.recover_inflight_physical_wave()
        assert controller.state["physical_workload_invocations"] == 12
        assert controller.state["valid_replays"] == 8
        assert controller.state["technical_failures"] == 4
        assert "inflight_physical_wave" not in controller.state
        assert sum(event == "algorithm_c_candidate_replay" for event, _ in events) == 2
        controller.recover_inflight_physical_wave()
        assert controller.state["physical_workload_invocations"] == 12


def test_baseline_domain_schema_upgrade_rejects_semantic_change() -> None:
    frozen = {
        "contract": "algorithm_c_baseline_execution_domain_v2",
        "passed": True,
        "errors": [],
        "cohort_size": 4,
        "observations": [{"qualification_id": "q1"}],
        "audit_sha256": "old",
    }
    current = {
        **copy.deepcopy(frozen),
        "single_lane_no_mutex_authorized": False,
        "audit_sha256": "new",
    }
    assert framework.baseline_execution_domain_schema_upgrade_only(
        frozen,
        current,
    ) is True
    changed = copy.deepcopy(current)
    changed["cohort_size"] = 1
    assert framework.baseline_execution_domain_schema_upgrade_only(
        frozen,
        changed,
    ) is False


def test_convergence_finalizes_without_stability_replay() -> None:
    controller = algorithm_a.AlgorithmALaneController.__new__(
        algorithm_a.AlgorithmALaneController
    )
    champion = {
        "params": {"random_page_cost": 2.0},
        "tier": "strict",
        "qps": 123.0,
    }
    controller.state = {
        "accepted": 7,
        "incumbent_params": {"random_page_cost": 2.5},
        "ranges": {"random_page_cost": {"low": 2.0, "high": 2.02}},
        "gradient_state": {"g_j": {}},
    }
    controller.accepted_target = 7
    controller.normalized_width_threshold = 0.01
    calls = []
    controller._ordered_feasible_champions = lambda: [copy.deepcopy(champion)]
    controller.save_state = lambda: calls.append(("save", None))
    controller.log = lambda event, **fields: calls.append((event, fields))
    controller.restore_clean_baseline = (
        lambda *, reason: calls.append(("restore", reason))
    )
    controller.write_authority_manifest = (
        lambda width: calls.append(("authority", width))
    )

    assert controller.finalize_without_post_convergence_stability(0.0078125)
    assert controller.state["phase"] == "complete"
    assert controller.state["final_champion"] == champion
    assert controller.state["post_convergence_stability_required"] is False
    assert controller.state["repeatability"] == {
        "required": False,
        "performed": False,
        "passed": None,
        "reason": "post_convergence_stability_is_engineering_only",
        "physical_workload_invocations": 0,
    }
    assert ("restore", "formal_completion_without_stability") in calls
    assert ("authority", 0.0078125) in calls
    assert all(name != "real_validation" for name, _payload in calls)


def test_formal_cardinality_and_lane_local_values() -> None:
    defaults = {
        name: framework.ParameterRange(*framework.DEFAULT_RANGES[name])
        for name in framework.ACTIVE_PARAMS
    }
    first = algorithm_a.full_factorial_parameter_vectors(defaults)
    assert len(first) == 128
    changed = dict(defaults)
    changed["random_page_cost"] = framework.ParameterRange(1.0, 4.0)
    second = algorithm_a.full_factorial_parameter_vectors(changed)
    assert len(second) == 128
    assert first[0][1] != second[0][1]
    assert {
        tuple(row[1][name] for name in framework.ACTIVE_PARAMS)
        for row in first
    }.isdisjoint(
        {
            tuple(row[1][name] for name in framework.ACTIVE_PARAMS)
            for row in second
        }
    )


def test_batch_limit_hook_isolated_from_algorithm_c() -> None:
    base = framework.LaneController.__new__(framework.LaneController)
    exhaustive = algorithm_a.AlgorithmALaneController.__new__(
        algorithm_a.AlgorithmALaneController
    )
    exhaustive.algorithm_a_candidates_per_round = 128
    assert base.physical_replay_batch_limit("base_top5") == 5
    assert exhaustive.physical_replay_batch_limit(
        algorithm_a.ALGORITHM_A_BATCH_KIND
    ) == 128
    assert exhaustive.physical_replay_batch_limit("stability") == 5


def test_algorithm_a_explicit_replay_farm_width() -> None:
    base = framework.LaneController.__new__(framework.LaneController)
    assert base.configured_replay_parallelism() == 2

    exhaustive = algorithm_a.AlgorithmALaneController.__new__(
        algorithm_a.AlgorithmALaneController
    )
    exhaustive.cfg = {
        "algorithm_a": {
            "method": algorithm_a.ALGORITHM_A_METHOD,
            "real_replay_parallelism": 4,
        }
    }
    assert exhaustive.configured_replay_parallelism() == 4

    exhaustive.cfg["algorithm_a"]["real_replay_parallelism"] = 17
    try:
        exhaustive.configured_replay_parallelism()
    except RuntimeError as exc:
        assert "between 2 and 16" in str(exc)
    else:
        raise AssertionError("oversized Algorithm A replay farm was accepted")


def test_four_worker_stability_assessment() -> None:
    attempts = []
    for attempt_index, scale in ((1, 1.0), (2, 1.01)):
        confirmations = [
            {
                "worker_key": f"w{worker_index}",
                "db_qps": 100.0 * scale,
                "resources": {
                    "cpu": 50.0 * scale,
                    "memory": 60.0 * scale,
                    "io": 40.0 * scale,
                },
            }
            for worker_index in range(1, 5)
        ]
        attempts.append(
            {
                "attempt": attempt_index,
                "passed": True,
                "technical_failure": False,
                "qps_mean": 100.0 * scale,
                "resource_means": {
                    "cpu": 50.0 * scale,
                    "memory": 60.0 * scale,
                    "io": 40.0 * scale,
                },
                "confirmations": confirmations,
                "confirmation_decisions": [
                    {"accepted": True, "acceptance_tier": "fallback"}
                    for _worker_index in range(4)
                ],
            }
        )
    assessment = framework.assess_stability_attempts(attempts)
    assert assessment["passed"] is True
    assert assessment["selected_attempts"] == [1, 2]
    canonical = framework.canonicalize_stability_attempt_outcomes(attempts)
    assert len(canonical) == 2
    assert all(row["passed"] is True for row in canonical)


def test_four_worker_algorithm_a_replay_wave() -> None:
    controller = algorithm_a.AlgorithmALaneController.__new__(
        algorithm_a.AlgorithmALaneController
    )
    controller.algorithm_a_candidates_per_round = 128
    controller.replay_parallelism = 4
    controller.validate_top5_audit_records = mock.Mock()
    controller.log = mock.Mock()
    controller.stop_requested = False
    controller.cfg = {}
    controller.output_dir = Path("/tmp/not-used-algorithm-a-four-worker")
    controller.workers = [{"name": f"w{index}"} for index in range(1, 5)]
    controller.target = "memory"
    controller.algorithm_cfg = {"scene": "job_memory"}
    controller.workload_contract_sha256 = "test-workload"
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
        {"rank": index, "optimized_params": {"seq_page_cost": float(index)}}
        for index in range(1, 5)
    ]
    replay_rows = [
        {
            "status": "ok",
            "technical_failure": False,
            "used_real_replay": True,
            "physical_workload_invocations": 1,
            "resource_stats": {"resource_monitor_ok": True},
            "workload_completeness": {"passed": True},
        }
        for _index in range(4)
    ]

    with mock.patch.object(
        framework,
        "restore_worker_from_hybench_template",
        return_value={"status": "ok"},
    ), mock.patch.object(
        framework,
        "run_real_validation_safe",
        side_effect=replay_rows,
    ), mock.patch.object(
        framework,
        "apply_ssd_wave_qualification",
        return_value={
            "passed": True,
            "errors": [],
            "thresholds": {"explicit_user_authorized_no_mutex": False},
        },
    ), mock.patch.object(
        framework,
        "wait_for_algorithm_c_candidate_cohort",
        return_value={"passed": True, "ready_count": 1},
    ), mock.patch.object(
        framework,
        "wait_for_algorithm_c_candidate_cohort_completion",
        return_value={"passed": True, "completed_count": 1},
    ), mock.patch.object(
        framework,
        "acquire_configured_shared_ssd_workload_coordination",
        return_value=(None, 0.0),
    ):
        rows = controller.replay_batch(
            candidates,
            round_index=1,
            batch_kind=algorithm_a.ALGORITHM_A_BATCH_KIND,
        )

    assert len(rows) == 4
    assert {row["worker_key"] for row in rows} == {"w1", "w2", "w3", "w4"}
    starts = [
        call
        for call in controller.log.call_args_list
        if call.args[0] == "algorithm_c_physical_batch_start"
    ]
    assert len(starts) == 1
    assert starts[0].kwargs["replay_parallelism"] == 4
    assert starts[0].kwargs["wave_shape"] == [4]


def test_worker_cohort_change_requires_audited_boundary_transition() -> None:
    controller = algorithm_a.AlgorithmALaneController.__new__(
        algorithm_a.AlgorithmALaneController
    )
    controller.replay_parallelism = 4
    controller.workers = [{"name": f"w{index}"} for index in range(1, 5)]
    controller.output_dir = Path("/tmp/not-used-worker-cohort-transition")
    controller.state = {"baseline_by_worker": {"w1": {}, "w2": {}}}
    try:
        controller.baseline()
    except RuntimeError as exc:
        assert "round-boundary execution-domain transition" in str(exc)
    else:
        raise AssertionError("worker cohort changed without a fresh baseline transition")


def test_legacy_matching_worker_cohort_is_frozen_without_replay() -> None:
    controller = algorithm_a.AlgorithmALaneController.__new__(
        algorithm_a.AlgorithmALaneController
    )
    controller.replay_parallelism = 2
    controller.workers = [{"name": "w1"}, {"name": "w2"}]
    controller.state = {"baseline_by_worker": {"w1": {}, "w2": {}}}
    controller.save_state = mock.Mock()
    controller.log = mock.Mock()
    controller.baseline()
    assert controller.state["worker_execution_cohort"]["worker_count"] == 2
    controller.save_state.assert_called_once_with()
    controller.log.assert_called_once()


def test_legacy_worker_cohort_enriches_proven_restore_parallelism() -> None:
    controller = algorithm_a.AlgorithmALaneController.__new__(
        algorithm_a.AlgorithmALaneController
    )
    controller.replay_parallelism = 2
    controller.workers = [{"name": "w1"}, {"name": "w2"}]
    with mock.patch.dict(
        algorithm_a.os.environ,
        {"PG_ALGORITHM_C_GLOBAL_RESTORE_PARALLELISM": "2"},
    ):
        current = controller.worker_execution_cohort_audit()
    legacy = copy.deepcopy(current)
    legacy.pop("cohort_sha256")
    legacy.pop("global_restore_parallelism")
    legacy["cohort_sha256"] = framework.canonical_sha256(legacy)
    controller.state = {
        "baseline_by_worker": {"w1": {}, "w2": {}},
        "baseline_replay_rows": [
            {"restore": {"global_restore_parallelism": 2}},
            {"restore": {"global_restore_parallelism": 2}},
        ],
        "worker_execution_cohort": legacy,
    }
    controller.save_state = mock.Mock()
    controller.log = mock.Mock()
    with mock.patch.dict(
        algorithm_a.os.environ,
        {"PG_ALGORITHM_C_GLOBAL_RESTORE_PARALLELISM": "2"},
    ):
        controller.baseline()
    assert (
        controller.state["worker_execution_cohort"]
        ["global_restore_parallelism"]
        == 2
    )
    assert controller.log.call_args.args[0] == (
        "algorithm_c_legacy_restore_parallelism_execution_cohort_enriched"
    )


def test_complete_baseline_waves_resume_without_physical_replay() -> None:
    controller = algorithm_a.AlgorithmALaneController.__new__(
        algorithm_a.AlgorithmALaneController
    )
    controller.replay_parallelism = 2
    controller.workers = [{"name": "w1"}, {"name": "w2"}]
    controller.workload_contract_sha256 = "unit-workload-contract"
    controller.target = "memory"
    controller.capacity = {"cpu": 8.0, "memory": 8.0, "io": 1000.0}
    controller.cfg = {}
    controller.algorithm_cfg = {"scene": "unit", "qps_noise_gate": {}}
    controller.stop_requested = False
    controller.save_state = mock.Mock()
    controller.log = mock.Mock()
    controller.worker_execution_cohort_audit = mock.Mock(
        return_value={"cohort_sha256": "unit-cohort", "worker_count": 2}
    )
    pending_rows = [
        {
            "status": "ok",
            "technical_failure": False,
            "used_real_replay": True,
            "valid_replay_count": 1,
            "worker_key": worker,
            "baseline_repetition": repetition,
            "db_qps": 100.0,
            "resources": {"cpu": 2.0, "memory": 4.0, "io": 10.0},
            "restore": {"global_restore_parallelism": 2},
        }
        for repetition in (1, 2)
        for worker in ("w1", "w2")
    ]
    controller.state = {
        "physical_workload_invocations": 4,
        "valid_replays": 4,
        "technical_failures": 0,
        "baseline_pending_rows": copy.deepcopy(pending_rows),
        "baseline_pending_context": {
            "contract": "algorithm_c_crash_resumable_parallel_baseline_v1",
            "worker_keys": ["w1", "w2"],
            "repetitions_per_worker": 2,
            "workload_contract_sha256": "unit-workload-contract",
            "global_restore_parallelism": 2,
        },
    }
    aggregate_worker = {
        "status": "ok",
        "db_qps": 100.0,
        "resources": {"cpu": 2.0, "memory": 4.0, "io": 10.0},
    }
    with mock.patch.object(
        framework,
        "aggregate_parallel_baseline_validation",
        return_value=copy.deepcopy(aggregate_worker),
    ), mock.patch.object(
        framework,
        "aggregate_baseline",
        return_value=copy.deepcopy(aggregate_worker),
    ), mock.patch.object(
        framework,
        "baseline_execution_domain_audit",
        return_value={"passed": True, "errors": []},
    ), mock.patch.object(
        framework,
        "qps_noise_model",
        return_value={"ready": True, "errors": []},
    ), mock.patch.object(
        framework,
        "executor_telemetry_baseline_model",
        return_value={"ready": True, "errors": []},
    ), mock.patch.object(
        framework,
        "configured_static_qps_floor_pct",
        return_value=1.0,
    ), mock.patch.object(
        controller,
        "prepare_parallel_baseline_workers",
    ) as prepare, mock.patch.object(
        framework,
        "run_real_validation_safe",
    ) as replay:
        with mock.patch.dict(
            algorithm_a.os.environ,
            {"PG_ALGORITHM_C_GLOBAL_RESTORE_PARALLELISM": "2"},
        ):
            controller.baseline()

    prepare.assert_not_called()
    replay.assert_not_called()
    assert controller.state["phase"] == "baseline_ready"
    assert controller.state["physical_workload_invocations"] == 4
    assert controller.state["valid_replays"] == 4
    assert "baseline_pending_rows" not in controller.state
    reused = [
        call
        for call in controller.log.call_args_list
        if call.args[0] == "algorithm_c_baseline_wave_reused_from_checkpoint"
    ]
    assert len(reused) == 2


def test_algorithm_a_continues_only_candidate_local_failed_wave() -> None:
    controller = algorithm_a.AlgorithmALaneController.__new__(
        algorithm_a.AlgorithmALaneController
    )
    candidate_failure = {
        "passed": False,
        "errors": [
            "w1:base_physical_validation_failed",
            "w2:base_physical_validation_failed",
        ],
        "semantic_non_target_bottleneck_resources": [],
    }
    assert controller.continue_after_candidate_terminal_qualification_failure(
        candidate_failure,
        batch_kind=algorithm_a.ALGORITHM_A_BATCH_KIND,
    )
    assert not controller.continue_after_candidate_terminal_qualification_failure(
        {**candidate_failure, "errors": ["w1:ssd_measurement_failed"]},
        batch_kind=algorithm_a.ALGORITHM_A_BATCH_KIND,
    )
    assert not controller.continue_after_candidate_terminal_qualification_failure(
        {
            **candidate_failure,
            "semantic_non_target_bottleneck_resources": ["io"],
        },
        batch_kind=algorithm_a.ALGORITHM_A_BATCH_KIND,
    )


def test_worker_cohort_transition_preserves_search_state_and_canonical_scale() -> None:
    controller = algorithm_a.AlgorithmALaneController.__new__(
        algorithm_a.AlgorithmALaneController
    )
    controller.replay_parallelism = 4
    controller.workers = [{"name": f"w{index}"} for index in range(1, 5)]
    controller.capacity = {"cpu": 20.0, "memory": 8.0, "io": 1000.0}
    controller.stop_requested = False
    controller.save_state = mock.Mock()
    controller.log = mock.Mock()
    old_canonical = {
        "status": "ok",
        "label": "old-canonical",
        "db_qps": 100.0,
        "resources": {"cpu": 10.0, "memory": 4.0, "io": 100.0},
    }
    old_incumbent = {
        "status": "ok",
        "label": "old-incumbent",
        "db_qps": 110.0,
        "resources": {"cpu": 9.0, "memory": 4.1, "io": 90.0},
    }
    params = {
        name: float(framework.DEFAULT_RANGES[name][0])
        for name in framework.ACTIVE_PARAMS
    }
    ranges = {
        name: {
            "min_value": float(bounds[0]),
            "max_value": float(bounds[1]),
        }
        for name, bounds in framework.DEFAULT_RANGES.items()
    }
    controller.state = {
        "phase": "stopped_by_signal_clean",
        "accepted": 3,
        "round_index": 3,
        "ranges": ranges,
        "incumbent_params": params,
        "best_feasible_champion": {"candidate_identity": ["champion"]},
        "accepted_candidate_archive": [{"accepted_index": 1}],
        "algorithm_a_round_checkpoint": None,
        "baseline_replay_rows": [
            {
                "restore": {
                    "global_restore_parallelism": 2,
                }
            }
        ],
        "baseline_by_worker": {"w1": {}, "w2": {}},
        "canonical_baseline": old_canonical,
        "incumbent_validation": old_incumbent,
        "qps_noise_model": {"ready": True},
        "executor_telemetry_baseline_model": {"ready": True},
        "baseline_execution_domain": {"passed": True},
        "worker_execution_cohort": {"cohort_sha256": "old"},
    }
    with tempfile.TemporaryDirectory() as temporary:
        controller.output_dir = Path(temporary)
        request = {
            "contract": algorithm_a.ALGORITHM_A_WORKER_COHORT_TRANSITION_CONTRACT,
            "authorized": True,
            "transition_id": "unit_2_to_4",
            "from_worker_keys": ["w1", "w2"],
            "to_worker_keys": ["w1", "w2", "w3", "w4"],
            "from_replay_parallelism": 2,
            "to_replay_parallelism": 4,
            "from_global_restore_parallelism": 2,
            "to_global_restore_parallelism": 2,
            "preserve_accepted": True,
            "preserve_ranges": True,
            "preserve_canonical_comparison_scale": True,
        }
        transition_path = (
            controller.output_dir
            / algorithm_a.ALGORITHM_A_WORKER_COHORT_TRANSITION_FILE
        )
        transition_path.write_text(json.dumps(request), encoding="utf-8")
        assert controller.prepare_worker_execution_cohort_transition(
            baseline_keys={"w1", "w2"},
            configured_keys={"w1", "w2", "w3", "w4"},
        ) is True
        assert controller.state["accepted"] == 3
        assert controller.state["ranges"] == ranges
        assert "baseline_by_worker" not in controller.state

        worker_baseline = {
            "status": "ok",
            "db_qps": 200.0,
            "resources": {"cpu": 12.0, "memory": 5.0, "io": 120.0},
        }
        controller.state["baseline_by_worker"] = {
            f"w{index}": copy.deepcopy(worker_baseline)
            for index in range(1, 5)
        }
        controller.state["canonical_baseline"] = {
            "status": "ok",
            "label": "new-measured-canonical",
            "db_qps": 200.0,
            "resources": {"cpu": 12.0, "memory": 5.0, "io": 120.0},
        }
        rows = [
            {
                "status": "ok",
                "technical_failure": False,
                "worker_key": f"w{index}",
                "db_qps": 220.0,
                "resources": {"cpu": 10.8, "memory": 5.125, "io": 108.0},
                "resource_stats": {"resource_monitor_ok": True},
                "workload_completeness": {"passed": True},
            }
            for index in range(1, 5)
        ]
        controller.replay_batch = mock.Mock(return_value=rows)
        with mock.patch.object(
            framework,
            "baseline_execution_domain_audit",
            return_value={"passed": True, "audit_sha256": "transition-domain"},
        ):
            assert (
                controller.complete_pending_worker_execution_cohort_transition()
                is True
            )

    assert controller.state["accepted"] == 3
    assert controller.state["ranges"] == ranges
    assert controller.state["canonical_baseline"]["label"] == "old-canonical"
    assert controller.state["incumbent_validation"]["db_qps"] == 110.0
    assert controller.state["execution_domain_transition"]["status"] == "complete"


def test_worker_cohort_transition_starts_new_evidence_epoch() -> None:
    controller = algorithm_a.AlgorithmALaneController.__new__(
        algorithm_a.AlgorithmALaneController
    )
    controller.workload_contract_sha256 = "new-workload-contract"
    controller.target = "memory"
    state = {
        "phase": "stopped_by_signal_clean",
        "algorithm_a_round_checkpoint": None,
        "outer_accept_reject_pruning": {"rules": {"old": {}}},
    }
    old_memory = framework.empty_valid_replay_identity_memory(
        "old-workload-contract",
        "memory",
    )
    old_memory["identities"] = {"old": {"labels": ["old"]}}
    with tempfile.TemporaryDirectory() as temporary:
        controller.output_dir = Path(temporary)
        request = {
            "contract": algorithm_a.ALGORITHM_A_WORKER_COHORT_TRANSITION_CONTRACT,
            "authorized": True,
            "transition_id": "unit_evidence_epoch",
            "preserve_accepted": True,
            "preserve_ranges": True,
            "preserve_canonical_comparison_scale": True,
        }
        path = (
            controller.output_dir
            / algorithm_a.ALGORITHM_A_WORKER_COHORT_TRANSITION_FILE
        )
        path.write_text(json.dumps(request), encoding="utf-8")
        assert controller.prepare_valid_replay_identity_context_transition(
            state,
            old_memory,
        ) is True
    memory = state["valid_replay_identity_memory"]
    assert memory["workload_contract_sha256"] == "new-workload-contract"
    assert memory["identities"] == {}
    assert state["outer_accept_reject_pruning"]["rules"] == {}
    assert state["valid_replay_identity_epoch_start_s"] > 0.0
    assert (
        state["pending_execution_domain_evidence_epoch_transition"]
        ["old_evidence_excluded_from_new_epoch"]
        is True
    )


def test_global_rank_labels_survive_missing_batch_restart() -> None:
    base = framework.LaneController.__new__(framework.LaneController)
    exhaustive = algorithm_a.AlgorithmALaneController.__new__(
        algorithm_a.AlgorithmALaneController
    )
    exhaustive.algorithm_a_candidates_per_round = 128
    exhaustive.state = {"formal_candidate_wave_sequence": 4}
    first = {"rank": 1}
    resumed = {"rank": 57}
    assert base.physical_replay_candidate_slot(
        first,
        local_position=0,
        batch_kind="base_top5",
    ) == 1
    assert base.physical_replay_candidate_slot_width("base_top5") == 2
    assert base.physical_replay_label_suffix(
        first,
        candidate_slot=1,
        batch_kind="base_top5",
    ) == ""
    assert exhaustive.physical_replay_candidate_slot(
        first,
        local_position=0,
        batch_kind=algorithm_a.ALGORITHM_A_BATCH_KIND,
    ) == 1
    assert exhaustive.physical_replay_candidate_slot(
        resumed,
        local_position=0,
        batch_kind=algorithm_a.ALGORITHM_A_BATCH_KIND,
    ) == 57
    assert exhaustive.physical_replay_candidate_slot_width(
        algorithm_a.ALGORITHM_A_BATCH_KIND
    ) == 3
    assert exhaustive.physical_replay_label_suffix(
        resumed,
        candidate_slot=57,
        batch_kind=algorithm_a.ALGORITHM_A_BATCH_KIND,
    ) == "_a0005"


def test_zero_accepted_checkpoint_is_resumable() -> None:
    controller = algorithm_a.AlgorithmALaneController.__new__(
        algorithm_a.AlgorithmALaneController
    )
    controller.state = {
        "accepted": 0,
        "algorithm_a_round_checkpoint": {
            "accepted_index": 0,
            "context_sha256": "context",
            "round_index": 7,
        },
    }
    assert controller._start_or_resume_round("context") == (7, True)


def test_round_boundary_stop_request_is_explicit_and_auditable() -> None:
    controller = algorithm_a.AlgorithmALaneController.__new__(
        algorithm_a.AlgorithmALaneController
    )
    with tempfile.TemporaryDirectory() as temporary:
        controller.output_dir = Path(temporary)
        assert controller.requested_round_boundary_stop() is None
        request = {
            "reason": "replay_farm_execution_domain_transition",
            "requested_parallelism": 4,
        }
        path = controller.output_dir / algorithm_a.ALGORITHM_A_ROUND_BOUNDARY_STOP_FILE
        path.write_text(json.dumps(request), encoding="utf-8")
        assert controller.requested_round_boundary_stop() == request

    targeted = {"target_completed_round": 4}
    controller.state = {
        "round_index": 4,
        "algorithm_a_round_checkpoint": {
            "round_index": 4,
            "status": "physical_replay",
        },
    }
    assert controller.round_boundary_stop_is_due(targeted) is False
    controller.state["algorithm_a_round_checkpoint"] = None
    assert controller.round_boundary_stop_is_due(targeted) is True
    controller.state["round_index"] = 3
    assert controller.round_boundary_stop_is_due(targeted) is False
    assert controller.round_boundary_stop_is_due({}) is True


def test_terminal_attempt_accounting_excludes_wave_companion() -> None:
    params = {
        name: float(framework.DEFAULT_RANGES[name][0])
        for name in framework.ACTIVE_PARAMS
    }
    second = dict(params)
    second["seq_page_cost"] = float(framework.DEFAULT_RANGES["seq_page_cost"][1])
    third = dict(params)
    third["random_page_cost"] = float(
        framework.DEFAULT_RANGES["random_page_cost"][1]
    )
    fourth = dict(params)
    fourth["cpu_tuple_cost"] = float(
        framework.DEFAULT_RANGES["cpu_tuple_cost"][1]
    )
    rankings = [
        {"optimized_params": params},
        {"optimized_params": second},
        {"optimized_params": third},
        {"optimized_params": fourth},
    ]
    events = [
        {
            "event": "algorithm_c_candidate_replay",
            "validation": {
                "status": "ok",
                "used_real_replay": True,
                "physical_workload_invocations": 1,
                "candidate_ranking": rankings[0],
                "unit_valid": True,
            },
        },
        {
            "event": "algorithm_c_candidate_replay",
            "validation": {
                "status": "failed",
                "used_real_replay": True,
                "physical_workload_invocations": 1,
                "candidate_ranking": rankings[1],
                "unit_valid": False,
            },
        },
        {
            "event": "algorithm_c_candidate_replay",
            "validation": {
                "status": "failed",
                "used_real_replay": True,
                "technical_failure": True,
                "candidate_ranking": rankings[3],
                "unit_valid": False,
                "error": "psql query failed: timeout expired",
            },
        },
        {
            "event": "algorithm_c_candidate_replay",
            "validation": {
                "status": "ok",
                "used_real_replay": True,
                "physical_workload_invocations": 1,
                "candidate_ranking": rankings[1],
                "unit_valid": True,
            },
        },
        {
            "event": "algorithm_c_candidate_replay",
            "validation": {
                "status": "ok",
                "used_real_replay": True,
                "physical_workload_invocations": 1,
                "candidate_ranking": rankings[2],
                "unit_valid": False,
            },
        },
    ]
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "algorithm_a.jsonl"
        path.write_text(
            "".join(json.dumps(event) + "\n" for event in events),
            encoding="utf-8",
        )
        controller = algorithm_a.AlgorithmALaneController.__new__(
            algorithm_a.AlgorithmALaneController
        )
        controller.log_path = path
        with mock.patch.object(
            algorithm_a.framework,
            "physical_valid",
            side_effect=lambda row: row.get("unit_valid") is True,
        ):
            attempted, counts = controller.attempted_parameter_signatures(
                rankings
            )
    assert len(attempted) == 4
    assert counts == {"valid": 1, "technical": 3}
    assert framework.exact_param_signature(third) in attempted
    assert framework.exact_param_signature(fourth) in attempted


def test_progress_checker_counts_pre_workload_technical_slot() -> None:
    params = {
        name: float(framework.DEFAULT_RANGES[name][0])
        for name in framework.ACTIVE_PARAMS
    }
    event = {
        "event": "algorithm_c_candidate_replay",
        "round_index": 7,
        "batch_kind": "algorithm_a_full_factorial",
        "validation": {
            "status": "failed",
            "used_real_replay": True,
            "technical_failure": True,
            "candidate_ranking": {"optimized_params": params},
            "resource_stats": {"resource_monitor_ok": False},
            "error": "psql query failed: timeout expired",
        },
    }
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "algorithm_a.jsonl"
        path.write_text(json.dumps(event) + "\n", encoding="utf-8")
        counts = progress.round_replay_counts(path, 7)
    assert counts == (1, 0, 1, 0)


def test_tpch_parallel8_repair_contract() -> None:
    source = {
        "algorithm_c": {
            "workload_contract": {
                "benchmark": "tpch",
                "benchmark_concurrency": {},
            }
        },
        "algorithm_a": {
            "accepted_target": 7,
            "candidates_per_round": 128,
            "real_replay_parallelism": 2,
        },
        "binary_plan_tuning": {
            "experiment_meta": {},
            "validation": {"hybench_xp": {"timeout_s": 1020}},
            "workload_context": {},
            "workload": {"plan_coverage_gate": {"expected_query_count": 22}},
        },
    }
    tpch_parallel8.apply_parallel8_contract(source)
    tpch_parallel8.validate_parallel8_config(source)
    concurrency = source["algorithm_c"]["workload_contract"][
        "benchmark_concurrency"
    ]
    assert concurrency["client_count"] == 8
    assert concurrency["query_execution"] == "native_Q1_Q22_exactly_once"
    assert source["binary_plan_tuning"]["workload_context"]["concurrency"] == 8


def test_replay_farm_worker_isolation_and_wave_shape() -> None:
    workers = [
        {
            "name": "w1",
            "container": "pg_w1",
            "port": 60001,
            "data_dir": "/disk1/pg/w1",
            "dbname": "bench",
            "cpuset_cpus": "1-2",
            "cpus": "2",
            "memory": "8g",
        },
        {
            "name": "w2",
            "container": "pg_w2",
            "port": 60002,
            "data_dir": "/disk1/pg/w2",
            "dbname": "bench",
            "cpuset_cpus": "3-4",
            "cpus": "2",
            "memory": "8g",
        },
    ]
    replay_farm.validate_workers(workers, workers)
    assert replay_farm.wave_shape(128, 4) == [4] * 32
    assert replay_farm.wave_shape(128, 6) == [6] * 21 + [2]
    assert replay_farm.wave_shape(128, 8) == [8] * 16
    source_config = {
        "algorithm_a": {
            "method": replay_farm.ALGORITHM_A_METHOD,
            "candidates_per_round": 128,
            "real_replay_parallelism": 2,
        },
        "algorithm_c": {
            "scene": "tpch_memory",
            "target_resource": "memory",
            "real_replay_parallelism": 2,
            "workload_contract": {
                "full_factorial_replay_parallelism": 2,
                "full_factorial_wave_count": 64,
                "wave_shape": [2] * 64,
                "baseline_cohort": {"lane_count": 1, "lanes": ["tpch_memory"]},
                "database": {"workers": [{"name": "stale"}]},
            },
        },
        "binary_plan_tuning": {
            "validation": {
                "docker_workers": {
                    "workers": workers,
                    "cpu_memory_parallelism": 2,
                },
                "parallel_baseline": {"parallelism": 2},
            },
            "experiment_meta": {},
        },
    }
    expanded_workers = workers + [
        {
            "name": "w3",
            "container": "pg_w3",
            "port": 60003,
            "data_dir": "/disk1/pg/w3",
            "dbname": "bench",
            "cpuset_cpus": "5-6",
            "cpus": "2",
            "memory": "8g",
        },
        {
            "name": "w4",
            "container": "pg_w4",
            "port": 60004,
            "data_dir": "/disk1/pg/w4",
            "dbname": "bench",
            "cpuset_cpus": "7-8",
            "cpus": "2",
            "memory": "8g",
        },
    ]
    expanded = replay_farm.build_transition_config(
        source_config,
        expanded_workers,
        transition_id="unit_2_to_4",
        baseline_cohort_lanes=["tpch_memory"],
        global_restore_parallelism=4,
    )
    contract = expanded["algorithm_c"]["workload_contract"]
    assert contract["full_factorial_wave_count"] == 32
    assert contract["wave_shape"] == [4] * 32
    assert [row["name"] for row in contract["database"]["workers"]] == [
        "w1", "w2", "w3", "w4"
    ]
    overlapping = copy.deepcopy(workers)
    overlapping.append(
        {
            "name": "w3",
            "container": "pg_w3",
            "port": 60003,
            "data_dir": "/disk1/pg/w3",
            "dbname": "bench",
            "cpuset_cpus": "2,5",
            "cpus": "2",
            "memory": "8g",
        }
    )
    try:
        replay_farm.validate_workers(workers, overlapping)
    except ValueError as exc:
        assert "CPU overlap" in str(exc)
    else:
        raise AssertionError("overlapping replay-farm CPU sets were accepted")


def test_tpch_replay_farm_uses_quiescent_clone_chain_and_source_gate() -> None:
    provision_path = PROJECT_ROOT / "shell" / "provision_pg_algorithm_a_replay_farm_worker.sh"
    source = provision_path.read_text(encoding="utf-8")

    def case_block(worker_index: int) -> str:
        start = source.index(f"    tpch:{worker_index})")
        end = source.index("        ;;", start)
        return source[start:end]

    assert "source_port=59521" in case_block(3)
    assert "source_port=59522" in case_block(4)
    assert "source_port=59527" in case_block(5)
    assert "source_port=59528" in case_block(6)
    assert "source_port=59527" in case_block(7)
    assert "source_port=59528" in case_block(8)
    assert "source workload/template database gate failed" in source
    assert "status=ready_existing" in source
    assert "target worker artifacts exist but do not match" in source

    activation_path = PROJECT_ROOT / "shell" / "activate_pg_algorithm_a_tpch8_at_r4_boundary.sh"
    activation = activation_path.read_text(encoding="utf-8")
    assert "for _attempt in $(seq 1 600)" in activation
    assert "event=old_controller_clean_exit_wait" in activation
    assert "event=old_controller_term" in activation

    cron_path = PROJECT_ROOT / "shell" / "cron_watch_pg_algorithm_a_r5.sh"
    cron_source = cron_path.read_text(encoding="utf-8")
    assert ".algorithm_a_watchdog_pause" in cron_source
    assert "[[ -f ${pause_path} ]] && return 0" in cron_source

    supervisor_path = PROJECT_ROOT / "shell" / "supervise_pg_algorithm_a_r5_lane.sh"
    supervisor = supervisor_path.read_text(encoding="utf-8")
    assert "trap request_stop TERM INT" in supervisor
    assert "supervisor_stop_requested_after_controller_exit" in supervisor


def main() -> int:
    test_two_dimensional_product()
    test_lane_independent_algorithm_a_no_mutex_baseline_domain()
    test_ssd_wave_does_not_spread_candidate_local_failure()
    test_inflight_wave_recovery_is_technical_and_idempotent()
    test_baseline_domain_schema_upgrade_rejects_semantic_change()
    test_convergence_finalizes_without_stability_replay()
    test_formal_cardinality_and_lane_local_values()
    test_batch_limit_hook_isolated_from_algorithm_c()
    test_algorithm_a_explicit_replay_farm_width()
    test_four_worker_stability_assessment()
    test_four_worker_algorithm_a_replay_wave()
    test_worker_cohort_change_requires_audited_boundary_transition()
    test_legacy_matching_worker_cohort_is_frozen_without_replay()
    test_legacy_worker_cohort_enriches_proven_restore_parallelism()
    test_complete_baseline_waves_resume_without_physical_replay()
    test_algorithm_a_continues_only_candidate_local_failed_wave()
    test_worker_cohort_transition_preserves_search_state_and_canonical_scale()
    test_worker_cohort_transition_starts_new_evidence_epoch()
    test_global_rank_labels_survive_missing_batch_restart()
    test_zero_accepted_checkpoint_is_resumable()
    test_round_boundary_stop_request_is_explicit_and_auditable()
    test_terminal_attempt_accounting_excludes_wave_companion()
    test_progress_checker_counts_pre_workload_technical_slot()
    test_tpch_parallel8_repair_contract()
    test_replay_farm_worker_isolation_and_wave_shape()
    test_tpch_replay_farm_uses_quiescent_clone_chain_and_source_gate()
    print("Algorithm A full-factorial semantic tests passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
