from copy import deepcopy
import json
from pathlib import Path

import pytest

from scripts import write_mysql_algorithm_c_continued_final_completion_audit_v2 as audit


def _manifest(pid: int = 1234, run_id: str = "formal_scene_run") -> dict:
    return {
        "launches": {
            "job_memory": {
                "pid": pid,
                "run_id": run_id,
            }
        }
    }


def _write_cmdline(proc_root: Path, pid: int, arguments: list[str]) -> None:
    process = proc_root / str(pid)
    process.mkdir(parents=True)
    (process / "cmdline").write_bytes(
        b"\0".join(value.encode("utf-8") for value in arguments) + b"\0"
    )


def test_manual_snapshot_accepts_absent_controller_without_polling(tmp_path: Path) -> None:
    snapshot = audit.controller_process_snapshot(_manifest(), proc_root=tmp_path)

    assert snapshot["observation_mode"] == "single_manual_read"
    assert snapshot["continuous_polling"] is False
    assert snapshot["all_expected_controllers_exited"] is True
    assert snapshot["scenes"]["job_memory"]["process_exists_at_pid"] is False


def test_manual_snapshot_rejects_matching_live_controller(tmp_path: Path) -> None:
    _write_cmdline(
        tmp_path,
        1234,
        ["python", "controller.py", "--run-id", "formal_scene_run"],
    )

    with pytest.raises(RuntimeError, match="formal controller is still alive"):
        audit.controller_process_snapshot(_manifest(), proc_root=tmp_path)


def test_manual_snapshot_distinguishes_reused_pid(tmp_path: Path) -> None:
    _write_cmdline(tmp_path, 1234, ["unrelated-process", "--serve"])

    snapshot = audit.controller_process_snapshot(_manifest(), proc_root=tmp_path)
    row = snapshot["scenes"]["job_memory"]
    assert row["process_exists_at_pid"] is True
    assert row["run_id_bound_controller_alive"] is False
    assert row["observed_argv"] == ["unrelated-process", "--serve"]


def test_terminal_snapshot_atomically_replaces_unsealed_prior_file(
    tmp_path: Path,
) -> None:
    path = tmp_path / "manual_terminal_snapshot.json"
    path.write_text('{"stale": true}\n', encoding="utf-8")
    expected = {"status": "terminal", "continuous_polling": False}

    receipt = audit.write_terminal_snapshot_atomic(path, expected)

    assert audit.load(path) == expected
    assert receipt["replaced_unsealed_prior_snapshot"] is True
    assert receipt["sha256"] == audit.base.sha(path)
    assert not list(tmp_path.glob(".manual_terminal_snapshot.json.*.tmp"))


def test_terminal_snapshot_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text('{}\n', encoding="utf-8")
    path = tmp_path / "manual_terminal_snapshot.json"
    path.symlink_to(target)

    with pytest.raises(RuntimeError, match="must not be a symlink"):
        audit.write_terminal_snapshot_atomic(path, {"status": "terminal"})


def test_final_audit_seal_overwrites_retryable_orphan_checksum(
    tmp_path: Path,
) -> None:
    output = tmp_path / "final_completion_audit.json"
    checksum = output.with_suffix(output.suffix + ".sha256")
    checksum.write_text("stale  final_completion_audit.json\n", encoding="utf-8")
    document = {"status": "PASS_COMPLETE", "all_four_scenes_complete": True}

    receipt = audit.seal_final_audit_atomic(output, document)

    assert audit.load(output) == document
    assert receipt["sha256"] == audit.base.sha(output)
    assert checksum.read_text(encoding="utf-8") == (
        f'{receipt["sha256"]}  {output.name}\n'
    )
    assert not list(tmp_path.glob(".*.tmp"))


def test_final_audit_seal_rejects_checksum_symlink(tmp_path: Path) -> None:
    output = tmp_path / "final_completion_audit.json"
    target = tmp_path / "target.sha256"
    target.write_text("untouched\n", encoding="utf-8")
    output.with_suffix(output.suffix + ".sha256").symlink_to(target)

    with pytest.raises(RuntimeError, match="checksum must not be a symlink"):
        audit.seal_final_audit_atomic(output, {"status": "PASS_COMPLETE"})
    assert target.read_text(encoding="utf-8") == "untouched\n"


def test_final_writer_has_no_completion_monitor_dependency() -> None:
    source = Path(audit.__file__).read_text(encoding="utf-8")
    assert "completion_monitor_summary.json" not in source
    assert '"manual_one_off_only"' in source


def test_sealed_four_scene_stable_round_is_bound_and_verifiable() -> None:
    manifest_path = (
        audit.HISTORY
        / "c_v212_continued_four_scene_20260929"
        / "launch_manifest.json"
    )
    receipt = audit.stable_round_receipt(audit.load(manifest_path))

    assert receipt["all_four_scenes_complete_outer_001"] is True
    assert receipt["physical_wave_shape"] == [2, 2, 1]
    assert set(receipt["scenes"]) == set(audit.base.SCENES.values())
    assert all(
        row["coverage_measurements"] == 5
        for row in receipt["scenes"].values()
    )
    assert receipt["read_only_observation"]["continuous_monitoring_now_enabled"] is False


def test_terminal_state_binds_the_same_passing_paper_baseline_gate() -> None:
    run = audit.HISTORY / "c_v221_job_dynamic_gate_recovery_20260929_job"
    state = audit.load(run / "run_state.json")

    receipt = audit.terminal_baseline_binding_receipt(
        state,
        run,
        "job_memory",
    )

    assert receipt["status"] == "PASS"
    assert receipt["terminal_state_binding_verified"] is True
    assert receipt["checksum_sidecar_verified"] is True
    assert all(
        abs(value) <= 1.0
        for value in receipt["delta_percentage_points"].values()
    )


def test_terminal_baseline_binding_rejects_changed_digest() -> None:
    run = audit.HISTORY / "c_v221_job_dynamic_gate_recovery_20260929_job"
    state = deepcopy(audit.load(run / "run_state.json"))
    state["paper_baseline_resource_gate"]["sha256"] = "0" * 64

    with pytest.raises(RuntimeError, match="SHA-256 differs"):
        audit.terminal_baseline_binding_receipt(state, run, "job_memory")


def test_original_four_scene_launch_is_sealed_concurrent_and_cpu_disjoint() -> None:
    manifest = audit.load(
        audit.HISTORY
        / "c_v212_continued_four_scene_20260929"
        / "launch_manifest.json"
    )

    receipt = audit.original_full_parallel_launch_receipt(manifest)

    assert receipt["scene_count"] == 4
    assert receipt["maximum_start_skew_seconds"] < 0.02
    assert receipt["controller_cpu_sets_disjoint"] is True
    assert receipt["four_scene_round_barrier"] is False
    assert receipt["sibling_scene_wait"] is False


def test_original_launch_rejects_overlapping_controller_cpu_sets(
    tmp_path: Path,
) -> None:
    manifest = audit.load(
        audit.HISTORY
        / "c_v212_continued_four_scene_20260929"
        / "launch_manifest.json"
    )
    source_path = Path(manifest["original_full_parallel_launch"]["path"])
    launch = audit.load(source_path)
    launch["launches"]["hybench_xp_io"]["controller_cpuset"] = launch[
        "launches"
    ]["hybench_xp_cpu"]["controller_cpuset"]
    path = tmp_path / "launch_manifest.json"
    path.write_text(json.dumps(launch) + "\n", encoding="utf-8")
    path.with_suffix(path.suffix + ".sha256").write_text(
        f"{audit.base.sha(path)}  {path.name}\n",
        encoding="utf-8",
    )
    changed = deepcopy(manifest)
    changed["original_full_parallel_launch"] = {
        "path": str(path),
        "sha256": audit.base.sha(path),
    }

    with pytest.raises(RuntimeError, match="CPU sets overlap"):
        audit.original_full_parallel_launch_receipt(changed)


def test_failed_recovery_attempts_have_zero_credit_and_no_extra_decisions() -> None:
    manifest = audit.load(
        audit.HISTORY
        / "c_v212_continued_four_scene_20260929"
        / "launch_manifest.json"
    )

    receipt = audit.failed_recovery_attempts_receipt(manifest)

    assert receipt["attempt_count"] == 21
    assert receipt["preserved_authoritative_decision_count"] == 6
    assert receipt["non_authoritative_decision_count"] == 0
    assert receipt["all_technical_failures_received_zero_semantic_credit"] is True


def test_failed_recovery_attempt_rejects_nonzero_semantic_credit() -> None:
    manifest = audit.load(
        audit.HISTORY
        / "c_v212_continued_four_scene_20260929"
        / "launch_manifest.json"
    )
    changed = deepcopy(manifest)
    changed["failed_recovery_attempts"][0]["completed_outer_credit"] = 1

    with pytest.raises(RuntimeError, match="technical failure received"):
        audit.failed_recovery_attempts_receipt(changed)


def _first_xp_measurement() -> tuple[dict, dict]:
    run = audit.HISTORY / "c_v211f_pg_dynamic_gate_four_scene_20260929_xp_cpu"
    state = audit.load(run / "run_state.json")
    batch = audit.load(run / "outer_001/top5_batch_1/batch_result.json")
    measurements = batch["measurements"]
    measurement = next(iter(measurements.values()))
    return state, measurement


def _first_measurement(run_id: str) -> dict:
    batch = audit.load(
        audit.HISTORY / run_id / "outer_001/top5_batch_1/batch_result.json"
    )
    measurements = batch["measurements"]
    return next(iter(measurements.values()))


def test_native_xp_measurement_matches_frozen_mix_and_runtime() -> None:
    state, measurement = _first_xp_measurement()
    contract = audit.native_xp_contract_receipt(state, "hybench_xp_cpu")

    receipt = audit.coverage_receipt("hybench_xp_cpu", measurement, contract)
    native = receipt["native_workload_contract"]
    assert native["iq_selection"] == "uniform_random_IQ1_IQ7"
    assert native["at_mix_percent"] == [35, 25, 15, 15, 7, 3]
    assert native["xap_xtp_clients"] == [8, 8]
    assert native["forbidden_overrides_absent"] is True


def test_decision_receipt_verifies_all_physical_waves_beyond_semantic_prefix() -> None:
    run = audit.HISTORY / "c_v211f_pg_dynamic_gate_four_scene_20260929_xp_cpu"
    state = audit.load(run / "run_state.json")
    contract = audit.native_xp_contract_receipt(state, "hybench_xp_cpu")

    receipt, semantic_count = audit.decision_receipt(
        run / "outer_001/hybench_xp_cpu.decision.json",
        "hybench_xp_cpu",
        1,
        contract,
    )

    assert semantic_count == 4
    assert receipt["physical_wave_shape"] == [2, 2, 1]
    assert receipt["physical_measurement_count"] == 5
    assert receipt["physical_full_workload_coverage_verified"] is True
    assert receipt["semantic_evaluations_are_physical_measurements"] is True


def test_native_xp_measurement_rejects_nonuniform_override(tmp_path: Path) -> None:
    state, source = _first_xp_measurement()
    contract = audit.native_xp_contract_receipt(state, "hybench_xp_cpu")
    measurement = deepcopy(source)
    properties = tmp_path / "hybench.properties"
    properties.write_text(
        "\n".join(
            [
                "dist=zipfian",
                "at1_percent=35",
                "at2_percent=25",
                "at3_percent=15",
                "at4_percent=15",
                "at5_percent=7",
                "at6_percent=3",
                "apclient=8",
                "tpclient=8",
                "xapclient=8",
                "xtpclient=8",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    measurement["workload_result"]["properties"] = str(properties)

    with pytest.raises(RuntimeError, match="distribution is not uniform"):
        audit.coverage_receipt("hybench_xp_cpu", measurement, contract)


def test_full_job_measurement_enforces_schedule_coverage_and_wall_time() -> None:
    measurement = _first_measurement(
        "c_v211f_pg_dynamic_gate_four_scene_20260929_job"
    )

    receipt = audit.coverage_receipt("job_memory", measurement)
    assert receipt["completed"] == 113
    assert receipt["concurrency"] == 16
    assert receipt["maximum_concurrent_heavy_queries"] <= 4
    assert receipt["wall_time_seconds"] <= 900
    assert receipt["each_sql_executed_exactly_once"] is True


def test_full_job_measurement_rejects_wall_time_above_900_seconds() -> None:
    measurement = deepcopy(
        _first_measurement("c_v211f_pg_dynamic_gate_four_scene_20260929_job")
    )
    measurement["workload_result"]["independent_wall_time_s"] = 900.01

    with pytest.raises(RuntimeError, match="wall time"):
        audit.coverage_receipt("job_memory", measurement)


def test_tpch_sf4_measurement_enforces_five_lanes_and_exact_q1_q22() -> None:
    run = audit.HISTORY / "c_v211f_pg_dynamic_gate_four_scene_20260929_tpch"
    state = audit.load(run / "run_state.json")
    contract = audit.tpch_sf4_contract_receipt(state, "tpch_sf4_memory")
    measurement = _first_measurement(
        "c_v211f_pg_dynamic_gate_four_scene_20260929_tpch"
    )

    receipt = audit.coverage_receipt("tpch_sf4_memory", measurement)
    assert contract["scale_factor"] == 4
    assert receipt["completed"] == 22
    assert receipt["lane_count"] == 5
    assert receipt["wall_time_seconds"] <= 900
    assert receipt["each_query_executed_exactly_once"] is True


def test_tpch_measurement_rejects_duplicate_query_across_lanes() -> None:
    measurement = deepcopy(
        _first_measurement("c_v211f_pg_dynamic_gate_four_scene_20260929_tpch")
    )
    lane = measurement["workload_result"]["lanes"][0]["query_coverage"]
    lane["completed_queries"].append("Q1")
    lane["required_queries"].append("Q1")

    with pytest.raises(RuntimeError, match="exactly 22 queries"):
        audit.coverage_receipt("tpch_sf4_memory", measurement)


def _first_job_dynamic_gate() -> dict:
    path = (
        audit.HISTORY
        / "c_v211f_pg_dynamic_gate_four_scene_20260929_job"
        / "outer_001/top5_batch_1_dynamic_noninterference_gate.json"
    )
    return audit.load(path)


def test_dynamic_gate_verifies_all_resource_and_noninterference_readbacks() -> None:
    receipt = audit._validate_dynamic_gate(
        _first_job_dynamic_gate(),
        "job_memory",
        "test gate",
    )

    assert receipt["cpu_numa_memory_swap_io_process_readbacks_passed"] is True
    assert receipt["blocking_external_workloads_absent"] is True
    assert receipt["process_control_performed"] is False
    assert receipt["prohibited_locks_and_barriers_absent"] is True
    assert receipt["observed_seconds"] >= receipt["required_clean_seconds"]


def test_dynamic_gate_rejects_whole_round_resource_lease() -> None:
    gate = deepcopy(_first_job_dynamic_gate())
    gate["whole_round_resource_lease"] = True

    with pytest.raises(RuntimeError, match="whole-round lease"):
        audit._validate_dynamic_gate(gate, "job_memory", "test gate")


def test_dynamic_gate_accepts_transient_reset_followed_by_full_clean_window() -> None:
    path = (
        audit.HISTORY
        / "c_v211f_pg_dynamic_gate_four_scene_20260929_job"
        / "outer_009/top5_batch_1_dynamic_noninterference_gate.json"
    )

    receipt = audit.dynamic_gate_receipt(path, "job_memory", 9, 1)
    assert receipt["transient_clean_window_resets"] == 1
    assert (
        receipt["final_contiguous_clean_seconds"]
        >= receipt["required_clean_seconds"]
    )


def test_dynamic_gate_rejects_dirty_final_sample() -> None:
    gate = deepcopy(_first_job_dynamic_gate())
    final_sample = gate["samples"][-1]
    final_sample["errors"] = ["persistent_target_overlapping_uninterruptible_sleep"]
    final_sample["persistent_blocking_d_state_pids"] = [1234]
    final_sample["clean_elapsed_seconds"] = 0

    with pytest.raises(RuntimeError, match="final sample has errors"):
        audit._validate_dynamic_gate(gate, "job_memory", "test gate")


def test_pg_mysql_algorithm_c_logic_correspondence_is_bound() -> None:
    manifest = audit.load(
        audit.HISTORY
        / "c_v212_continued_four_scene_20260929"
        / "launch_manifest.json"
    )
    receipt = audit.pg_logic_correspondence_receipt(manifest)
    job_state = audit.load(
        audit.HISTORY
        / "c_v221_job_dynamic_gate_recovery_20260929_job"
        / "run_state.json"
    )

    assert receipt["semantic_check_count"] == 15
    assert receipt["all_semantic_checks_passed"] is True
    assert receipt["mysql_regression_tests_passed"] >= 99
    assert receipt["mysql_direct_state_machine_tests_passed"] >= 2
    assert job_state["controller_source_sha256"] in receipt["audited_runtime_sha256"]
    assert receipt["historical_progress_snapshot_used_for_completion"] is False


def test_tpch_confirmed_cleanup_recovery_is_content_bound() -> None:
    manifest = audit.load(
        audit.HISTORY
        / "c_v212_continued_four_scene_20260929"
        / "launch_manifest.json"
    )

    receipt = audit.tpch_confirmed_cleanup_recovery_receipt(manifest)

    assert receipt["release_version"] == "v243-tpch-confirmed-cleanup-recovery"
    assert receipt["attempt_limit"] == 3
    assert receipt["lock_wait_timeout_seconds"] == 5
    assert receipt["server_readback_after_client_error"] is True
    assert receipt["incomplete_outer_credit"] == 0


def test_tpch_confirmed_cleanup_recovery_rejects_controller_hash_change() -> None:
    manifest = audit.load(
        audit.HISTORY
        / "c_v212_continued_four_scene_20260929"
        / "launch_manifest.json"
    )
    changed = deepcopy(manifest)
    changed["launches"]["tpch_sf4_memory"]["controller_sha256"] = "0" * 64

    with pytest.raises(RuntimeError, match="controller: SHA-256 differs"):
        audit.tpch_confirmed_cleanup_recovery_receipt(changed)


def _xp_io_first_four_decisions() -> list[dict]:
    run = audit.HISTORY / "c_v211f_pg_dynamic_gate_four_scene_20260929_xp_io"
    return [
        audit.load(run / f"outer_{outer:03d}/hybench_xp_io.decision.json")
        for outer in range(1, 5)
    ]


def _complete_job_decisions() -> list[dict]:
    segments = [
        ("c_v211f_pg_dynamic_gate_four_scene_20260929_job", 1, 15),
        ("c_v213b_job_legacy_cap_recovery_20260929_job", 16, 19),
        ("c_v221_job_dynamic_gate_recovery_20260929_job", 20, 33),
    ]
    return [
        audit.load(
            audit.HISTORY
            / run_id
            / f"outer_{outer:03d}"
            / "job_memory.decision.json"
        )
        for run_id, first, last in segments
        for outer in range(first, last + 1)
    ]


def test_decision_trajectory_enforces_first_expand_second_half_shrink() -> None:
    receipt = audit.validate_decision_trajectory(
        _xp_io_first_four_decisions(),
        expected_accepted=1,
        expected_rejected=3,
        scene="hybench_xp_io",
    )

    assert receipt["first_valid_miss_expansions"] == 2
    assert receipt["second_valid_miss_half_shrinks"] == 1
    assert receipt["accepted_and_rejected_counters_match_run_state"] is True


def test_decision_trajectory_rejects_nonhalf_second_miss_shrink() -> None:
    decisions = deepcopy(_xp_io_first_four_decisions())
    changes = decisions[-1]["rejection_cycle"]["range_changes"]
    next(iter(changes.values()))["log_width_ratio"] = 0.6

    with pytest.raises(RuntimeError, match="shrink is not one half"):
        audit.validate_decision_trajectory(
            decisions,
            expected_accepted=1,
            expected_rejected=3,
            scene="hybench_xp_io",
        )


def test_complete_job_trajectory_binds_legacy_confirmations_to_resume_boundary() -> None:
    receipt = audit.validate_decision_trajectory(
        _complete_job_decisions(),
        expected_accepted=31,
        expected_rejected=2,
        scene="job_memory",
        continuation_boundaries={16, 20},
    )

    assert receipt["zero_credit_legacy_confirmations"] == 4
    assert receipt["legacy_confirmation_credit_restorations"] == 1
    assert receipt["restored_legacy_confirmation_credits"] == 4
    assert receipt["accepted_and_rejected_counters_match_run_state"] is True


def test_job_terminal_width_is_independently_recomputed_from_frozen_ranges() -> None:
    run = audit.HISTORY / "c_v221_job_dynamic_gate_recovery_20260929_job"
    state = audit.load(run / "run_state.json")
    decision = audit.load(run / "outer_033/job_memory.decision.json")

    receipt = audit.independent_terminal_convergence_receipt(
        state,
        {"normalized_width": decision["interval_convergence"]["normalized_width"]},
        "job_memory",
    )

    assert receipt["maximum_normalized_log_width"] == pytest.approx(0.0078125)
    assert receipt["recorded_normalized_width"] == pytest.approx(0.0078125)
    assert receipt["all_active_parameters_at_most_0_01"] is True
    assert len(receipt["normalized_log_width_by_parameter"]) == 4


def test_independent_terminal_width_rejects_wide_active_parameter() -> None:
    run = audit.HISTORY / "c_v221_job_dynamic_gate_recovery_20260929_job"
    state = deepcopy(audit.load(run / "run_state.json"))
    state["ranges_by_scene"]["job_memory"]["io_block_read_cost"]["upper"] = 10.0

    with pytest.raises(RuntimeError, match="exceeds 0.01"):
        audit.independent_terminal_convergence_receipt(
            state,
            {"normalized_width": 0.0078125},
            "job_memory",
        )


def test_job_trajectory_rejects_unproven_zero_credit_acceptance() -> None:
    decisions = _complete_job_decisions()
    decisions[11] = deepcopy(decisions[11])
    decisions[11].pop("post_target_stability_confirmation")

    with pytest.raises(RuntimeError, match="lacks legacy confirmation proof"):
        audit.validate_decision_trajectory(
            decisions,
            expected_accepted=31,
            expected_rejected=2,
            scene="job_memory",
            continuation_boundaries={16, 20},
        )


def test_job_trajectory_rejects_legacy_credit_restoration_off_resume_boundary() -> None:
    with pytest.raises(RuntimeError, match="outside a continuation boundary"):
        audit.validate_decision_trajectory(
            _complete_job_decisions(),
            expected_accepted=31,
            expected_rejected=2,
            scene="job_memory",
            continuation_boundaries={20},
        )
