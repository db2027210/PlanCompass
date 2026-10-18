from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from scripts import build_mysql_algorithm_c_outer_checkpoint_v2 as checkpoint_builder
from scripts import build_mysql_algorithm_c_outer_checkpoint_v3 as checkpoint_builder_v3
from scripts import build_mysql_algorithm_c_outer_checkpoint_v4 as checkpoint_builder_v4
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v2 as controller
from scripts import watch_mysql_formal_external_postgres_interference_v1 as watchdog


ROOT = Path(__file__).resolve().parents[1]


def assignments() -> dict[str, dict[str, object]]:
    return {
        "job_w1": {},
        "tpch_w1": {},
        "xp_cpu_w1": {},
        "xp_io_w1": {},
    }


def timeout_batch() -> dict[str, object]:
    return {
        "status": "FAILED_CLOSED",
        "measurements": {worker_id: {} for worker_id in assignments()},
        "failures": [
            {"task": "pair_1", "error": "TimeoutExpired: bounded at 900s"},
            {"task": "xp_cpu_w1", "error": "TimeoutExpired: bounded at 900s"},
            {"task": "job_w1_plan_capture", "error": "skipped_after_invalid_workload"},
            {"task": "tpch_w1_plan_capture", "error": "skipped_after_invalid_workload"},
        ],
    }


def test_candidate_non_target_bottleneck_is_a_hard_gate() -> None:
    passed, violations = controller.candidate_non_target_bottleneck_gate(
        {"resource_means": {"cpu": 0.79, "memory": 0.95, "io": 0.01}},
        target_resource="memory",
    )
    assert passed is True
    assert violations == []

    passed, violations = controller.candidate_non_target_bottleneck_gate(
        {"resource_means": {"cpu": 0.8, "memory": 0.70, "io": 0.01}},
        target_resource="memory",
    )
    assert passed is False
    assert violations == ["cpu"]


def test_candidate_non_target_bottleneck_gate_fails_closed_on_missing_value() -> None:
    passed, violations = controller.candidate_non_target_bottleneck_gate(
        {"resource_means": {"cpu": 0.70, "memory": 0.70}},
        target_resource="memory",
    )
    assert passed is False
    assert violations == ["io"]


def test_pure_workload_timeout_wave_is_excluded_without_state_credit() -> None:
    result = controller.classify_technical_timeout_batch(
        timeout_batch(), assignments()
    )
    assert result is not None
    assert result["classification"] == "TECHNICAL_TIMEOUT_EXCLUDED"
    assert result["candidate_state_mutation"] is False
    assert result["accepted_state_credit_increment"] == 0
    assert result["rejected_outer_attempt_increment"] == 0
    assert result["acceptance_tier_mutation"] is False
    assert result["convergence_mutation"] is False
    assert result["replay_history_mutation"] is False
    assert result["pruning_evidence_eligible"] is False


def test_non_timeout_hard_failure_still_fails_closed() -> None:
    document = timeout_batch()
    document["failures"].append(
        {"task": "xp_io_w1_plan_capture", "error": "RuntimeError: missing plan"}
    )
    assert controller.classify_technical_timeout_batch(
        document, assignments()
    ) is None


def test_timeout_classifier_requires_exact_measurement_worker_set() -> None:
    document = timeout_batch()
    del document["measurements"]["job_w1"]
    assert controller.classify_technical_timeout_batch(
        document, assignments()
    ) is None


def test_derived_plan_skip_without_timeout_is_not_excluded() -> None:
    document = timeout_batch()
    document["failures"] = [
        {"task": "job_w1_plan_capture", "error": "skipped_after_invalid_workload"}
    ]
    assert controller.classify_technical_timeout_batch(
        document, assignments()
    ) is None


def test_xp_post_measurement_drain_timeout_is_candidate_local(
    tmp_path: Path,
) -> None:
    batch_path = tmp_path / "batch_result.json"
    worker = tmp_path / "xp_io_w1"
    worker.mkdir()
    (worker / "stderr.log").write_text(
        "java.lang.IllegalStateException: client operation drain timed out\n",
        encoding="utf-8",
    )
    (worker / "stdout.log").write_text(
        'HYBENCH_FORMAL_METRICS_JSON={"workload_error":"IllegalStateException",'
        '"stop_requested":true,"forced_boundary_stop":false}\n',
        encoding="utf-8",
    )
    (worker / "resource_stability.json").write_text(
        json.dumps({
            "schema_version": "mysql_xp_adaptive_stop_v1",
            "stable": True,
            "stop_request_published": True,
            "selected_measurement_seconds": 510,
            "maximum_measurement_seconds": 900,
        }) + "\n",
        encoding="utf-8",
    )
    document = {
        "status": "FAILED_CLOSED",
        "measurements": {
            "xp_io_w1": {
                "hard_gate_passed": False, "coverage_complete": False,
            },
            "xp_io_w2": {
                "hard_gate_passed": True, "coverage_complete": True,
            },
        },
        "failures": [
            {
                "task": "xp_io_w1",
                "error": f"RuntimeError: HyBench exited 2; see {worker}",
            },
            {
                "task": "xp_io_w1_plan_capture",
                "error": "skipped_after_invalid_workload",
            },
        ],
    }
    result = controller.classify_technical_timeout_batch(
        document,
        {"xp_io_w1": {"rank": 1}, "xp_io_w2": {"rank": 2}},
        batch_result_path=batch_path,
    )
    assert result is not None
    assert result["classification"] == "TECHNICAL_CANDIDATE_TERMINAL_EXCLUDED"
    assert result["direct_failed_worker_ids"] == ["xp_io_w1"]
    assert result["failed_worker_ids"] == ["xp_io_w1"]
    assert result["derived_unavailable_worker_ids"] == []


def test_generic_hybench_exit_two_remains_fail_closed(tmp_path: Path) -> None:
    worker = tmp_path / "xp_io_w1"
    worker.mkdir()
    (worker / "stderr.log").write_text(
        "java.sql.SQLException: candidate query failed\n", encoding="utf-8",
    )
    (worker / "stdout.log").write_text("", encoding="utf-8")
    (worker / "resource_stability.json").write_text("{}\n", encoding="utf-8")
    document = {
        "status": "FAILED_CLOSED",
        "measurements": {
            "xp_io_w1": {
                "hard_gate_passed": False, "coverage_complete": False,
            },
        },
        "failures": [{
            "task": "xp_io_w1",
            "error": f"RuntimeError: HyBench exited 2; see {worker}",
        }],
    }
    assert controller.classify_technical_timeout_batch(
        document, {"xp_io_w1": {"rank": 1}},
        batch_result_path=tmp_path / "batch_result.json",
    ) is None


def pre_reset_timeout() -> subprocess.TimeoutExpired:
    return subprocess.TimeoutExpired(
        [
            "mysql", "--defaults-file=/tmp/selected.cnf", "--host=127.0.0.1",
            "--port=65341", "--connect-timeout=10", "-NBe",
            "SELECT COUNT(*) FROM information_schema.processlist "
            "WHERE ID<>CONNECTION_ID() AND COMMAND<>'Daemon' "
            "AND USER<>'event_scheduler'",
        ],
        timeout=15,
    )


def test_premeasurement_session_probe_timeout_is_technically_excluded(
    tmp_path: Path,
) -> None:
    result = controller.classify_premeasurement_reset_timeout(
        pre_reset_timeout(), batch_result_path=tmp_path / "batch_result.json",
    )
    assert result is not None
    assert result["classification"] == (
        "TECHNICAL_PREMEASUREMENT_RESET_TIMEOUT_EXCLUDED"
    )
    assert result["physical_workload_started"] is False
    assert result["candidate_state_mutation"] is False


def test_premeasurement_timeout_does_not_mask_other_reset_failures(
    tmp_path: Path,
) -> None:
    error = subprocess.TimeoutExpired(
        ["docker", "inspect", "ra_mysql_disk2_algc_xp_io_w1_20260809"],
        timeout=15,
    )
    assert controller.classify_premeasurement_reset_timeout(
        error, batch_result_path=tmp_path / "batch_result.json",
    ) is None


def test_existing_batch_result_cannot_be_reclassified_as_reset_timeout(
    tmp_path: Path,
) -> None:
    result = tmp_path / "batch_result.json"
    result.write_text("{}\n", encoding="utf-8")
    assert controller.classify_premeasurement_reset_timeout(
        pre_reset_timeout(), batch_result_path=result,
    ) is None


def test_watchdog_marks_stale_running_state_failed_closed(tmp_path: Path) -> None:
    state_path = tmp_path / "run_state.json"
    state_path.write_text(
        json.dumps({"status": "RUNNING", "stage": "OUTER_006_BATCH_3_MEASURING"}),
        encoding="utf-8",
    )
    changed = watchdog.mark_run_state_failed_closed(
        state_path,
        event="runtime_interference_detected_under_exclusive_wave_lease",
        evidence={"blockers": [{"pid": 123}]},
        terminal_event="formal_forced_stopped_after_sigint_timeout",
    )
    value = json.loads(state_path.read_text(encoding="utf-8"))
    assert changed is True
    assert value["status"] == "FAILED_CLOSED"
    assert value["stage"] == "FAILED"
    assert value["watchdog_interruption"]["runtime_stage"] == (
        "OUTER_006_BATCH_3_MEASURING"
    )
    assert value["watchdog_interruption"]["formal_state_credit_increment"] == 0
    assert state_path.with_name("run_state.json.sha256").is_file()


def test_watchdog_does_not_misclassify_release_transition_as_lease_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        watchdog, "load_run_state",
        lambda run_id: {
            "stage": "OUTER_009_BATCH_2_CROSS_PROJECT_LEASE_RELEASED",
            "cross_project_wave_coordination": {"phase": "released"},
        },
    )
    monkeypatch.setattr(
        watchdog.coordination, "lock_held_exclusively_by",
        lambda pid: False,
    )
    assert watchdog.confirmed_lease_loss("formal_release_race", 123) is None


def test_watchdog_requires_fresh_held_phase_and_second_missing_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {
        "stage": "OUTER_009_BATCH_3_MEASURING",
        "cross_project_wave_coordination": {"phase": "measuring"},
    }
    monkeypatch.setattr(watchdog, "load_run_state", lambda run_id: state)
    monkeypatch.setattr(
        watchdog.coordination, "lock_held_exclusively_by",
        lambda pid: False,
    )
    assert watchdog.confirmed_lease_loss("formal_real_loss", 123) == state


def test_watchdog_ignores_transient_external_affinity_startup_mask(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = {
        "passed": False,
        "workloads": [{
            "pid": 501,
            "errors": [
                "external_cpu_not_in_frozen_union",
                "external_cpu_overlaps_mysql",
            ],
        }],
    }
    monkeypatch.setattr(watchdog.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        watchdog.coordination,
        "external_process_inventory",
        lambda run_id, mems: {"workloads": [{"pid": 501}]},
    )
    monkeypatch.setattr(
        watchdog.coordination,
        "evaluate_co_resident_pg_isolation",
        lambda workloads: {
            "passed": True,
            "workloads": [{"pid": 501, "errors": []}],
        },
    )
    assert watchdog.confirmed_workload_isolation_failure(
        "affinity_transition", first, target_mems={0, 1},
    ) is None


def test_watchdog_fails_closed_on_persistent_external_cpu_overlap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = {
        "passed": False,
        "workloads": [{
            "pid": 502,
            "errors": ["external_cpu_overlaps_mysql"],
        }],
    }
    rechecked = {
        "passed": False,
        "workloads": [{
            "pid": 502,
            "errors": ["external_cpu_overlaps_mysql"],
        }],
    }
    monkeypatch.setattr(watchdog.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        watchdog.coordination,
        "external_process_inventory",
        lambda run_id, mems: {"workloads": [{"pid": 502}]},
    )
    monkeypatch.setattr(
        watchdog.coordination,
        "evaluate_co_resident_pg_isolation",
        lambda workloads: rechecked,
    )
    result = watchdog.confirmed_workload_isolation_failure(
        "persistent_overlap", first, target_mems={0, 1},
    )
    assert result is not None
    assert result["isolation"] is rechecked
    assert result["persistent_error_identities"] == [{
        "pid": 502, "error": "external_cpu_overlaps_mysql",
    }]


def test_checkpoint_v3_accepts_only_unmutated_reset_watchdog_boundary(
    tmp_path: Path,
) -> None:
    outer = tmp_path / "outer_009"
    reset = tmp_path / "xp_resets/outer_009_batch_1"
    outer.mkdir(parents=True)
    reset.mkdir(parents=True)
    (reset / "pre_reset_session_drain.json").write_text("{}\n", encoding="utf-8")
    state = {
        "status": "FAILED_CLOSED",
        "stage": "FAILED",
        "error": checkpoint_builder_v3._WATCHDOG_ERROR,
        "watchdog_interruption": {
            "event": "runtime_interference_detected_under_exclusive_wave_lease",
            "runtime_stage": "OUTER_009_BATCH_1_RESETTING_XP",
            "formal_state_credit_increment": 0,
            "interrupted_candidate_replay_prohibited": True,
        },
    }
    assert checkpoint_builder_v3.clean_premeasurement_watchdog_boundary(
        tmp_path, state, 8,
    ) is True

    (reset / "reset_binding.json").write_text("{}\n", encoding="utf-8")
    assert checkpoint_builder_v3.clean_premeasurement_watchdog_boundary(
        tmp_path, state, 8,
    ) is False


def test_checkpoint_v4_accepts_only_uncredited_measurement_boundary(
    tmp_path: Path,
) -> None:
    outer = tmp_path / "outer_010"
    for rank in (1, 2, 3):
        (outer / f"top5_batch_{rank}").mkdir(parents=True)
    for rank in (1, 2):
        (outer / f"top5_batch_{rank}/batch_result.json").write_text("{}\n")
    state = {
        "status": "FAILED_CLOSED",
        "stage": "FAILED",
        "error": checkpoint_builder_v3._WATCHDOG_ERROR,
        "watchdog_interruption": {
            "event": "runtime_interference_detected_under_exclusive_wave_lease",
            "runtime_stage": "OUTER_010_BATCH_3_MEASURING",
            "formal_state_credit_increment": 0,
            "interrupted_candidate_replay_prohibited": True,
        },
    }
    assert checkpoint_builder_v4.clean_measurement_watchdog_boundary(
        tmp_path, state, 9,
    ) is True
    (outer / "top5_batch_3/batch_result.json").write_text("{}\n")
    assert checkpoint_builder_v4.clean_measurement_watchdog_boundary(
        tmp_path, state, 9,
    ) is False


def test_xp_candidate_selects_frozen_reset_target_before_sql_liveness(
    monkeypatch,
) -> None:
    workers = {
        "xp_cpu_w1": {"scene": "hybench_xp_cpu"},
        "xp_cpu_w2": {"scene": "hybench_xp_cpu"},
    }
    monkeypatch.setattr(
        controller.alg, "mysql_server_liveness",
        lambda worker: (_ for _ in ()).throw(AssertionError("unexpected SQL probe")),
    )
    assert controller._single_candidate_live_representative(
        scene="hybench_xp_cpu", workers=workers,
    ) == "xp_cpu_w1"


def test_non_xp_candidate_still_requires_live_sql_lane(monkeypatch) -> None:
    workers = {
        "job_w1": {"scene": "job_memory", "worker_id": "job_w1"},
        "job_w2": {"scene": "job_memory", "worker_id": "job_w2"},
    }
    monkeypatch.setattr(
        controller.alg, "mysql_server_liveness",
        lambda worker: worker["worker_id"] == "job_w2",
    )
    assert controller._single_candidate_live_representative(
        scene="job_memory", workers=workers,
    ) == "job_w2"


def test_technical_rank3_timeout_is_excluded_and_continuation_starts_rank4() -> None:
    checkpoint_path = (
        ROOT / "history/mysql_four_scene_20260809/"
        "algorithm_c_v38_outer9_checkpoint_v11_20260815T1228p0800/"
        "prepared_binding.json"
    )
    source_root = (
        ROOT / "history/mysql_four_scene_20260809/"
        "v41_formal_outer10_rank3_retry2_20260815T1314p0800"
    )
    checkpoint = controller.checkpointmod.verify_checkpoint(checkpoint_path)
    result = controller._validate_formal_partial_continuation(
        source_root=source_root,
        checkpoint=checkpoint,
        reconstructed=checkpoint["reconstructed_state"],
        start_rank=4,
    )
    assert result["continuation_reason"] == (
        "technical_timeout_batch_then_empty_next_reset_failure"
    )
    assert result["start_rank"] == 4
    assert [row["rank"] for row in result["completed_batches"]] == [1, 2, 3]
    assert result["completed_batches"][-1]["technical_timeout_excluded"] is True
    assert len(result["technical_batch_exclusions"]) == 1
    assert result["technical_batch_exclusions"][0]["candidate_state_mutation"] is False
    assert all(len(rows) == 2 for rows in result["evaluations"].values())


def test_formal_partial_continuation_reuses_only_four_bound_passed_ranks() -> None:
    checkpoint_path = (
        ROOT / "history/mysql_four_scene_20260809/"
        "algorithm_c_v18_pruning_v2_checkpoint_v5_20260813T1920p0800/"
        "prepared_binding.json"
    )
    source_root = (
        ROOT / "history/mysql_four_scene_20260809/"
        "v18_formal_bounded_prestate_v12_20260814T1023p0800_algorithm_c_v2"
    )
    checkpoint = controller.checkpointmod.verify_checkpoint(checkpoint_path)
    result = controller._validate_formal_partial_continuation(
        source_root=source_root,
        checkpoint=checkpoint,
        reconstructed=checkpoint["reconstructed_state"],
        start_rank=5,
    )
    assert result["outer_attempt"] == 5
    assert result["start_rank"] == 5
    assert [row["rank"] for row in result["completed_batches"]] == [1, 2, 3, 4]
    assert all(len(rows) == 4 for rows in result["evaluations"].values())
    assert result["candidate_state_mutation_before_continuation"] is False
    assert result["accepted_state_credit_before_continuation"] == 0


def test_completed_rank5_recovery_reuses_clean_pass_without_measurement() -> None:
    checkpoint_path = (
        ROOT / "history/mysql_four_scene_20260809/"
        "algorithm_c_v18_pruning_v2_checkpoint_v5_20260813T1920p0800/"
        "prepared_binding.json"
    )
    source_root = (
        ROOT / "history/mysql_four_scene_20260809/"
        "v18_formal_bounded_prestate_v12_20260814T1023p0800_algorithm_c_v2"
    )
    completed_root = (
        ROOT / "history/mysql_four_scene_20260809/"
        "v18_formal_outer5_rank5_continuation_v14_20260814T1617p0800"
    )
    checkpoint = controller.checkpointmod.verify_checkpoint(checkpoint_path)
    result = controller._validate_formal_partial_continuation(
        source_root=source_root,
        checkpoint=checkpoint,
        reconstructed=checkpoint["reconstructed_state"],
        start_rank=5,
        completed_rank5_root=completed_root,
    )
    assert result["start_rank"] == 6
    assert [row["rank"] for row in result["completed_batches"]] == [1, 2, 3, 4, 5]
    assert all(len(rows) == 5 for rows in result["evaluations"].values())
    assert result["completed_rank5_recovery"]["measurement_replayed_without_rerun"] is True
    assert result["completed_rank5_recovery"]["watchdog"]["interference_detected"] is False


def test_runtime_interference_continuation_reuses_only_completed_rank1_and_rank2() -> None:
    checkpoint_path = (
        ROOT / "history/mysql_four_scene_20260809/"
        "algorithm_c_v18_outer5_checkpoint_v7_20260814T1745p0800/"
        "prepared_binding.json"
    )
    source_root = (
        ROOT / "history/mysql_four_scene_20260809/"
        "v18_formal_outer5_decision_recovery_v15_20260814T1640p0800"
    )
    checkpoint = controller.checkpointmod.verify_checkpoint(checkpoint_path)
    result = controller._validate_formal_partial_continuation(
        source_root=source_root,
        checkpoint=checkpoint,
        reconstructed=checkpoint["reconstructed_state"],
        start_rank=3,
    )
    assert result["outer_attempt"] == 6
    assert result["start_rank"] == 3
    assert [row["rank"] for row in result["completed_batches"]] == [1, 2]
    assert all(len(rows) == 2 for rows in result["evaluations"].values())
    assert result["continuation_reason"] == (
        "runtime_external_interference_watchdog_stop"
    )
    assert result["runtime_interruption_watchdog"]["sha256"]


def test_runtime_interference_continuation_preserves_inherited_technical_timeout() -> None:
    """A second continuation must retain an earlier zero-credit exclusion."""
    checkpoint_path = (
        ROOT / "history/mysql_four_scene_20260809/"
        "algorithm_c_v38_outer9_checkpoint_v11_20260815T1228p0800/"
        "prepared_binding.json"
    )
    source_root = (
        ROOT / "history/mysql_four_scene_20260809/"
        "v43_formal_outer10_rank4_auto_20260815_attempt001"
    )
    checkpoint = controller.checkpointmod.verify_checkpoint(checkpoint_path)

    result = controller._validate_formal_partial_continuation(
        source_root=source_root,
        checkpoint=checkpoint,
        reconstructed=checkpoint["reconstructed_state"],
        start_rank=4,
    )

    assert result["outer_attempt"] == 10
    assert result["start_rank"] == 4
    assert [row["rank"] for row in result["completed_batches"]] == [1, 2, 3]
    assert result["completed_batches"][2]["technical_timeout_excluded"] is True
    assert len(result["technical_batch_exclusions"]) == 1
    exclusion = result["technical_batch_exclusions"][0]
    assert exclusion["batch_index"] == 3
    assert exclusion["candidate_state_mutation"] is False
    assert exclusion["accepted_state_credit_increment"] == 0
    assert exclusion["rejected_outer_attempt_increment"] == 0
    assert exclusion["pruning_evidence_eligible"] is False
    assert all(len(rows) == 2 for rows in result["evaluations"].values())
    assert result["continuation_reason"] == (
        "runtime_external_interference_watchdog_stop"
    )


def test_release_race_continuation_reuses_only_completed_rank1_and_rank2() -> None:
    checkpoint_path = (
        ROOT / "history/mysql_four_scene_20260809/"
        "algorithm_c_v27_outer8_checkpoint_v10_20260815T0232p0800/"
        "prepared_binding.json"
    )
    source_root = (
        ROOT / "history/mysql_four_scene_20260809/"
        "v33_formal_outer9_resume_20260815T0853p0800"
    )
    checkpoint = controller.checkpointmod.verify_checkpoint(checkpoint_path)
    result = controller._validate_formal_partial_continuation(
        source_root=source_root,
        checkpoint=checkpoint,
        reconstructed=checkpoint["reconstructed_state"],
        start_rank=3,
    )
    assert result["outer_attempt"] == 9
    assert result["start_rank"] == 3
    assert [row["rank"] for row in result["completed_batches"]] == [1, 2]
    assert all(len(rows) == 2 for rows in result["evaluations"].values())
    assert result["continuation_reason"] == (
        "completed_rank_release_watchdog_toctou_recovery"
    )
    assert result["completed_rank_release_race"] is True
    assert result["candidate_state_mutation_before_continuation"] is False


def test_reset_stage_interruption_before_mutation_reuses_completed_rank3_and_rank4() -> None:
    checkpoint_path = (
        ROOT / "history/mysql_four_scene_20260809/"
        "algorithm_c_v18_outer5_checkpoint_v7_20260814T1745p0800/"
        "prepared_binding.json"
    )
    source_root = (
        ROOT / "history/mysql_four_scene_20260809/"
        "v20_formal_outer6_rank3_runtime_resume_retry5_20260814T2226p0800"
    )
    reset_root = source_root / "xp_resets/outer_006_batch_5"
    assert (reset_root / "pre_reset_session_drain.json").is_file()
    assert not (reset_root / "reset_binding.json").exists()
    assert not list(reset_root.glob("*/reset_worker.json"))
    checkpoint = controller.checkpointmod.verify_checkpoint(checkpoint_path)
    result = controller._validate_formal_partial_continuation(
        source_root=source_root,
        checkpoint=checkpoint,
        reconstructed=checkpoint["reconstructed_state"],
        start_rank=5,
    )
    assert result["outer_attempt"] == 6
    assert result["start_rank"] == 5
    assert [row["rank"] for row in result["completed_batches"]] == [1, 2, 3, 4]
    assert "v18_formal_outer5_decision_recovery_v15" in (
        result["completed_batches"][0]["path"]
    )
    assert "v20_formal_outer6_rank3_runtime_resume_retry5" in (
        result["completed_batches"][2]["path"]
    )
    assert all(len(rows) == 4 for rows in result["evaluations"].values())
    assert result["candidate_state_mutation_before_continuation"] is False


def test_checkpoint_reconstructs_outer6_with_rank5_technical_timeout_excluded() -> None:
    run_root = (
        ROOT / "history/mysql_four_scene_20260809/"
        "v24_formal_outer6_rank5_runtime_resume_retry7_20260814T2304p0800"
    )
    checkpoint = checkpoint_builder.build_checkpoint(run_root)
    reconstructed = checkpoint["reconstructed_state"]
    assert checkpoint["completed_outer_attempts"] == 6
    assert reconstructed["accepted_rounds"] == {
        "job_memory": 2,
        "tpch_sf4_memory": 2,
        "hybench_xp_cpu": 1,
        "hybench_xp_io": 0,
    }
    batches = checkpoint["evidence"]["outers"]["6"]["batches"]
    assert [row["rank"] for row in batches] == [1, 2, 3, 4, 5]
    assert [row["status"] for row in batches] == [
        "PASS", "PASS", "PASS", "PASS", "TECHNICAL_TIMEOUT_EXCLUDED",
    ]


def test_split_outer_checkpoint_binds_reused_ranks_and_local_rank5(
    tmp_path: Path,
) -> None:
    source_outer = (
        ROOT / "history/mysql_four_scene_20260809/"
        "v18_formal_bounded_prestate_v12_20260814T1023p0800_algorithm_c_v2/"
        "outer_005"
    )
    completed = []
    for rank in range(1, 5):
        path = source_outer / f"top5_batch_{rank}/batch_result.json"
        completed.append({
            "rank": rank,
            "path": str(path),
            "sha256": controller.alg.sha(path),
        })

    local_outer = tmp_path / "outer_005"
    local_rank5 = local_outer / "top5_batch_5/batch_result.json"
    local_rank5.parent.mkdir(parents=True)
    document = json.loads(
        (source_outer / "top5_batch_4/batch_result.json").read_text(
            encoding="utf-8"
        )
    )
    for row in document["measurements"].values():
        row["rank"] = 5
    local_rank5.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    state = {
        "formal_partial_continuation": {
            "outer_attempt": 5,
            "start_rank": 5,
            "completed_batches": completed,
        }
    }
    rows = checkpoint_builder._serialized_outer_batches(
        state=state, outer_root=local_outer, outer=5,
    )
    assert [row["rank"] for row in rows] == [1, 2, 3, 4, 5]
    assert [row["origin"] for row in rows] == [
        "formal_partial_replay_source",
        "formal_partial_replay_source",
        "formal_partial_replay_source",
        "formal_partial_replay_source",
        "source_run_root",
    ]

    state["formal_partial_continuation"]["completed_batches"][0]["sha256"] = "0" * 64
    with pytest.raises(RuntimeError, match="rank 1 binding differs"):
        checkpoint_builder._serialized_outer_batches(
            state=state, outer_root=local_outer, outer=5,
        )


def test_split_outer_checkpoint_accepts_five_bound_recovered_ranks() -> None:
    source_outer = (
        ROOT / "history/mysql_four_scene_20260809/"
        "v18_formal_bounded_prestate_v12_20260814T1023p0800_algorithm_c_v2/"
        "outer_005"
    )
    recovered = (
        ROOT / "history/mysql_four_scene_20260809/"
        "v18_formal_outer5_rank5_continuation_v14_20260814T1617p0800/"
        "outer_005/top5_batch_5/batch_result.json"
    )
    completed = []
    for rank in range(1, 6):
        path = recovered if rank == 5 else source_outer / f"top5_batch_{rank}/batch_result.json"
        completed.append({"rank": rank, "path": str(path), "sha256": controller.alg.sha(path)})
    state = {
        "formal_partial_continuation": {
            "outer_attempt": 5,
            "start_rank": 6,
            "completed_batches": completed,
        }
    }
    rows = checkpoint_builder._serialized_outer_batches(
        state=state, outer_root=ROOT / "does-not-need-local-batches", outer=5,
    )
    assert [row["rank"] for row in rows] == [1, 2, 3, 4, 5]
    assert all(row["origin"] == "formal_partial_replay_source" for row in rows)


def test_outer5_checkpoint_reconstructs_plan_replay_history() -> None:
    run_root = (
        ROOT / "history/mysql_four_scene_20260809/"
        "v18_formal_outer5_decision_recovery_v15_20260814T1640p0800"
    )
    state, reconstruction = checkpoint_builder.reconstruct_completed_state(run_root)
    rebuilt = reconstruction["state"]["full_plan_replay_history_v2"]
    assert rebuilt == state["full_plan_replay_history_v2"]
    assert set(rebuilt) == set(controller.SCENES)
    assert all(len(rebuilt[scene]) == 5 for scene in controller.SCENES)
