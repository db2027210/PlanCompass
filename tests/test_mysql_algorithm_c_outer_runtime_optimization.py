from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260815_v46"
    / "optimization_contract.json"
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_runtime_optimization_preserves_formal_measurement_contract() -> None:
    value = json.loads(CONTRACT.read_text(encoding="utf-8"))
    unchanged = value["unchanged_contracts"]
    optimized = value["optimized_reset"]
    ranking = value["optimized_plan_ranking"]
    assert value["formal_scope"] == "mysql_only"
    assert value["cross_engine_comparison_prohibited"] is True
    assert unchanged["fixed_top_k"] == 5
    assert unchanged["candidate_rank_order"] == [1, 2, 3, 4, 5]
    assert unchanged["job_full_query_count"] == 113
    assert unchanged["tpch_scale_factor"] == 4
    assert unchanged["tpch_full_query_count"] == 22
    assert unchanged["xp_measurement_seconds"] == 600
    assert unchanged["xp_at_mix_percent"] == [35, 25, 15, 15, 7, 3]
    assert unchanged["xp_xap_xtp_ratio"] == [1, 1]
    assert unchanged["maximum_interval_ratio"] == 1.01
    assert unchanged["required_consecutive_stable_accepted_rounds"] == 2
    assert optimized["measurement_concurrency_changed"] is True
    serialized = value["serialized_candidate_scheduling"]
    assert serialized["successor_batches"] == [[1], [2], [3], [4], [5]]
    assert serialized["candidate_set_and_order_unchanged"] is True
    assert serialized["within_candidate_four_scene_concurrency_unchanged"] is True
    assert serialized["formal_acceptance_and_convergence_unchanged"] is True
    assert optimized["fresh_template_copy_required"] is True
    assert optimized["bootstrap_required"] is False
    assert optimized["credential_initialization"] == "mysqld_init_file"
    assert optimized["post_start_resource_readback_required"] is True
    assert ranking["maximum_concurrency"] == 4
    assert ranking["candidate_generation_unchanged"] is False
    assert ranking["boundary_collapsed_in_range_supplementation"] is True
    assert ranking["duplicate_parameter_points_explained_once"] is True
    assert ranking["candidate_scoring_unchanged"] is True
    assert ranking["fixed_top_k_unchanged"] is True
    assert ranking["real_replay_unchanged"] is True
    assert ranking["measurement_concurrency_changed"] is False
    gate = value["non_target_bottleneck_hard_gate"]
    assert gate["threshold"] == 0.8
    assert gate["future_violation_effect"] == "hard_gate_failed"
    assert gate["prior_credited_acceptance_audit_status"] == "PASS"
    assert gate["prior_credited_acceptance_mutation"] is False


def test_runtime_optimization_source_bindings_and_activation_fail_closed() -> None:
    value = json.loads(CONTRACT.read_text(encoding="utf-8"))
    launcher = (ROOT / "scripts/launch_mysql_algorithm_c_boundary_resume_v2.py").read_text(
        encoding="utf-8"
    )
    assert "runtime_external_interference_watchdog.jsonl" in launcher
    assert '"runtime_watchdog_unit": watchdog_unit' in launcher
    assert '"--interval-seconds", "1"' in launcher
    for label in (
        "resource_freeze", "reset_source", "algorithm_source",
        "resume_controller", "discard_reset_pilot_source",
        "runtime_validation_pilot", "runtime_validation_reset_binding",
        "partial_reset_pilot_source", "partial_reset_pilot",
        "partial_reset_binding", "session_drain_test",
        "session_drain_timeout_evidence", "session_drain_recovery_binding",
        "session_drain_resume_completion", "bounded_prestate_test",
        "bounded_prestate_failure_evidence",
        "run_scoped_reset_collision_evidence", "bounded_gate_pass_evidence",
        "formal_partial_source_state", "formal_partial_baseline",
        "formal_partial_rankings", "formal_partial_blocked_gate",
        "formal_partial_batch_1", "formal_partial_batch_2",
        "formal_partial_batch_3", "formal_partial_batch_4",
        "non_target_bottleneck_audit_source", "non_target_bottleneck_audit",
        "non_target_bottleneck_release_builder", "non_target_bottleneck_test",
    ):
        path = ROOT / value["bindings"][f"{label}_path"]
        assert path.is_file() and not path.is_symlink()
        assert _sha(path) == value["bindings"][f"{label}_sha256"]
    activation = value["activation"]
    assert activation["hot_reload_into_active_formal_controller_prohibited"] is True
    assert activation["activate_only_from_verified_outer_boundary_checkpoint"] is True
    assert activation["fresh_rolling_noninterference_preflight_required"] is True
    assert activation["discard_fast_shutdown_runtime_validated"] is True
    assert activation["stopped_wrapper_reuse_runtime_validated"] is True
    assert activation["single_candidate_partial_reset_runtime_validated"] is True
    assert activation["runtime_validated"] is True

    drain = value["bounded_pre_reset_session_drain"]
    assert drain["total_timeout_seconds"] == 60
    assert drain["probe_concurrency"] == 4
    assert drain["per_probe_timeout_cap_seconds"] == 15
    assert drain["transient_probe_failure_action"] == "retry_within_total_bound"
    assert drain["all_workers_explicit_zero_required"] is True
    assert drain["timeout_action"] == "fail_closed_without_candidate_state_mutation"

    prestate = value["bounded_pre_measurement_coordination"]
    assert prestate["lease_acquisition_timeout_seconds"] == 3
    assert prestate["required_quiet_seconds"] == 15
    assert prestate["maximum_quiet_wait_seconds"] == 20
    assert prestate["maximum_gate_attempts_per_wave"] == 1
    assert prestate["persistent_d_state_required_consecutive_samples"] == 3
    assert prestate["single_sample_d_state_action"] == "record_without_resetting_clean_window"
    checkpoint_builder = ROOT / value["bindings"]["outer_checkpoint_builder_path"]
    assert checkpoint_builder.is_file() and not checkpoint_builder.is_symlink()
    assert _sha(checkpoint_builder) == value["bindings"]["outer_checkpoint_builder_sha256"]
    pause_guard = ROOT / value["bindings"]["external_pg_pause_guard_path"]
    assert pause_guard.is_file() and not pause_guard.is_symlink()
    assert _sha(pause_guard) == value["bindings"]["external_pg_pause_guard_sha256"]
    assert value["run_scoped_reset_evidence"]["global_history_reset_path_prohibited"] is True
    assert value["run_scoped_reset_evidence"]["formal_incumbent_baseline_run_private"] is True
    partial = value["formal_partial_outer_continuation"]
    assert partial["completed_ranks_reused"] == [1, 2, 3, 4]
    assert partial["first_new_physical_rank"] == 5
    assert partial["source_evidence_sha256_required"] is True
    assert partial["candidate_state_mutation_before_continuation"] is False
    assert partial["all_five_ranks_required_before_decision"] is True


def test_runtime_budget_is_near_45_minutes_without_shortening_measurement() -> None:
    value = json.loads(CONTRACT.read_text(encoding="utf-8"))
    historical = value["historical_runtime_basis_seconds"]
    target = value["target_runtime_seconds"]
    assert target["measurement_total_unchanged"] == historical["outer_002_measurement_total"]
    assert 40 <= target["estimated_outer_minutes"] <= 48
    assert target["observed_expected_reset_total"] <= target["conservative_reset_total"]
    assert target["estimated_outer_total"] == round(
        target["observed_expected_reset_total"]
        + target["observed_expected_measurement_total"]
        + target["observed_plan_ranking"]
        + target["decision_overhead"]
    )
    assert target["conservative_outer_total"] == sum(
        target[name]
        for name in (
            "conservative_reset_total",
            "measurement_total_unchanged",
            "conservative_plan_ranking",
            "decision_overhead",
        )
    )


def test_boundary_resume_parallelizes_only_independent_plan_ranking() -> None:
    source = (
        ROOT / "scripts/run_mysql_four_scene_algorithm_c_boundary_resume_v2.py"
    ).read_text(encoding="utf-8")
    assert 'thread_name_prefix="formal-plan-ranking"' in source
    assert "max_workers=min(4, len(active_scenes))" in source
    assert '"plan_ranking_scene_audit"' in source
    assert "permanently_invalid_param_keys=permanently_invalid[scene]" in source
    assert "alg.measure_batch(" in source
    assert 'batch_document.get("status") != "PASS"' in source
    assert "failed closed;" in source


def test_single_candidate_batch_resets_only_assigned_xp_workers() -> None:
    source = (ROOT / "scripts/run_mysql_four_scene_algorithm_c_v2.py").read_text(
        encoding="utf-8"
    )
    assert "worker_ids: set[str] | None = None" in source
    assert "requested.issubset(XP_IDS)" in source
    assert '"reset_worker_ids": sorted(requested)' in source
    assert "worker_ids=set(assignments).intersection(XP_IDS)" in source
    assert "reset.purge_completed_discard_quarantine(" in source


def test_completed_reset_quarantine_is_zero_retention() -> None:
    source = (ROOT / "scripts/reset_mysql_xp_data_planes_v5.py").read_text(
        encoding="utf-8"
    )
    assert '"policy": "zero_retention_after_successful_reset_binding"' in source
    assert '"interrupted_reset_quarantine_retained_until_recovery_complete": True' in source
    assert '["sudo", "-n", "rm", "-rf", "--", str(target)]' in source
    assert '"live_mounts_deleted": False' in source


def test_failed_outer_recovery_is_boundary_bound_and_tiered() -> None:
    checkpoint = (
        ROOT / "scripts/build_mysql_algorithm_c_outer_checkpoint_v2.py"
    ).read_text(encoding="utf-8")
    pilot = (
        ROOT / "scripts/pilot_mysql_algorithm_c_discard_reset_v2.py"
    ).read_text(encoding="utf-8")
    replay = (
        ROOT / "scripts/build_mysql_algorithm_c_replay_quarantine_v5.py"
    ).read_text(encoding="utf-8")
    assert '"FAILED_CLOSED"' in checkpoint
    assert 'any(failed_root.glob("*.decision.json"))' in checkpoint
    assert '"source_state": _bound(state_path' in checkpoint
    assert 'r"outer (\\d+) batch ([123]) failed closed"' in pilot
    assert "failed_path.parent not in reset_path.parents" in pilot
    assert 'not in {"FAILED_CLOSED", "PILOT_FAILED_CLOSED"}' in pilot
    assert 'failed.get("formal_evidence_eligible") is not False' in pilot
    assert 'failed.get("accepted_state_credit_prohibited") is not True' in pilot
    assert '"FORMAL_WALL_TIMEOUT_INVALID_MEASUREMENT"' in replay
    assert '"VALID_REPLAY_OUTER_ABORTED_BEFORE_DECISION"' in replay
    assert "timeout_scenes != PERMANENT_TIMEOUT_SCENES" in replay
    assert "attempted_positions = expected_pass_ranks | expected_failed_ranks" in replay
