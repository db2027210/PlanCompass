#!/usr/bin/env python3
"""Module."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib
import math
import sys
import tempfile
import threading
import time


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common_outer_loop import binary_shrink_parameter_ranges  # noqa: E402
from gradient_descent_optimizer import ParameterRange  # noqa: E402
from parameter_optimizer import (  # noqa: E402
    _SharedPlanEstimateFutureCache,
    _bruteforce_first_stage_ranking_gate_applies,
    _baseline_workload_command,
    _build_empirical_resource_box_pruning_input,
    _dispatch_plan_estimator_bootstrap_monitor,
    evaluate_baseline_resource_band_gate,
    _gradient_advance_accepted_shrink_count,
    _gradient_expected_normalized_width,
    _gradient_mark_replayed_params,
    _initial_common_outer_hard_gate,
    _gradient_real_candidate_rank_key,
    _gradient_resume_skips_round0_subset_search,
    _gradient_resume_restores_accepted_candidate,
    _gradient_replay_stage_action,
    _build_empirical_resource_box_pruning_input,
    _resolve_partial_resume_rank_prefix,
    _gradient_take_unseen_rankings,
    _legacy_feedback_early_stop_allowed,
    _load_replicate_policy_callback,
    _normalize_degradation_summary_for_report,
)


def test_r0_profile_flag_is_added_only_to_baseline_command() -> None:
    candidate_cmd = "python3 candidate.py --target-resource io"
    protocol = {"r0_profile_enforcement_flag": "--enforce-r0-profile-band"}
    baseline_cmd = _baseline_workload_command(candidate_cmd, protocol)
    assert baseline_cmd.endswith(" --enforce-r0-profile-band")
    assert candidate_cmd == "python3 candidate.py --target-resource io"
    assert _baseline_workload_command(baseline_cmd, protocol) == baseline_cmd
    assert _baseline_workload_command(candidate_cmd, {}) == candidate_cmd


def test_baseline_resource_band_gate_matches_identify_bottleneck_threshold() -> None:
    gate = {
        "enabled": True,
        "hard_fail": True,
        "target_resource": "io",
        "ranges_pct": {
            "cpu": {"min_pct": 30.0, "max_pct": 50.0},
            "memory": {"min_pct": 30.0, "max_pct": 50.0},
            "io": {"min_pct": 80.0, "min_exclusive": True},
        },
    }
    low_edges = evaluate_baseline_resource_band_gate(
        utilization_pct={"cpu": 30.0, "memory": 30.0, "io": 80.001},
        gate_cfg=gate,
    )
    high_edges = evaluate_baseline_resource_band_gate(
        utilization_pct={"cpu": 50.0, "memory": 50.0, "io": 150.0},
        gate_cfg=gate,
    )
    assert low_edges["passed"] is True
    assert high_edges["passed"] is True
    assert high_edges["stage"] == "after_stable_baseline_before_retrain_and_candidate_search"
    assert high_edges["affects_outer_acceptance"] is False

    cpu_below = evaluate_baseline_resource_band_gate(
        utilization_pct={"cpu": 29.999, "memory": 40.0, "io": 85.0},
        gate_cfg=gate,
    )
    io_at_threshold = evaluate_baseline_resource_band_gate(
        utilization_pct={"cpu": 40.0, "memory": 40.0, "io": 80.0},
        gate_cfg=gate,
    )
    missing = evaluate_baseline_resource_band_gate(
        utilization_pct={"cpu": 40.0, "io": 85.0},
        gate_cfg=gate,
    )
    assert cpu_below["passed"] is False
    assert [item["resource"] for item in cpu_below["failures"]] == ["cpu"]
    assert io_at_threshold["passed"] is False
    assert [item["resource"] for item in io_at_threshold["failures"]] == ["io"]
    assert missing["passed"] is False
    assert [item["resource"] for item in missing["failures"]] == ["memory"]

    disabled = evaluate_baseline_resource_band_gate(
        utilization_pct={},
        gate_cfg={"enabled": False},
    )
    assert disabled["passed"] is True
    assert disabled["checks"] == []


def test_staged_5_then_10_then_scenario_cap() -> None:

    assert _gradient_replay_stage_action(
        validated_count=4,
        eligible_count=1,
        available_count=10,
    ) == "continue_initial"
    assert _gradient_replay_stage_action(
        validated_count=5,
        eligible_count=1,
        available_count=10,
    ) == "accept_current_batch"
    assert _gradient_replay_stage_action(
        validated_count=5,
        eligible_count=0,
        available_count=20,
        max_top_k=20,
    ) == "expand_second_batch"
    assert _gradient_replay_stage_action(
        validated_count=10,
        eligible_count=0,
        available_count=20,
        max_top_k=20,
    ) == "expand_final_batch"
    assert _gradient_replay_stage_action(
        validated_count=20,
        eligible_count=0,
        available_count=20,
        max_top_k=20,
    ) == "reject_exploration"
    assert _gradient_replay_stage_action(
        validated_count=10,
        eligible_count=1,
        available_count=10,
    ) == "accept_current_batch"
    assert _gradient_replay_stage_action(
        validated_count=3,
        eligible_count=1,
        available_count=3,
    ) == "accept_current_batch"
    assert _gradient_replay_stage_action(
        validated_count=3,
        eligible_count=0,
        available_count=3,
    ) == "reject_exploration"


def test_resource_pruning_box_uses_model_max_error_and_unit_conversion() -> None:
    def resource_log(resource: str, maximum: float, unit: str, window: float = 5.0):
        return {
            "resource_type": resource,
            "calibration_breakdown": {
                "learned_model": {
                    "artifact_path": f"/sealed/{resource}.joblib",
                    "prediction_window_seconds": window,
                    "target_unit": unit,
                    "metrics": {"test": {"abs_error_max": maximum, "sample_count": 50}},
                    "error_bound": {"value": maximum / 2.0, "unit": unit},
                }
            },
        }

    payload = _build_empirical_resource_box_pruning_input(
        {
            "final_score": 3.0,
            "final_resource": {"cpu": 4.0, "memory": 8.0, "io": 20.0},
            "final_estimation_metadata": {
                "resource_estimation_logs": [
                    resource_log("cpu", 10.0, "cpu_seconds_per_window"),
                    resource_log("memory", 1024.0, "MB"),
                    resource_log("io", 50.0, "MB/window"),
                ]
            },
        }
    )
    assert payload["calibration_ready"] is True
    assert payload["simultaneous_coverage_guaranteed"] is True
    assert payload["resource_error_bounds"]["cpu"]["error_upper"] == 2.0
    assert payload["resource_error_bounds"]["memory"]["error_upper"] == 1.0
    assert payload["resource_error_bounds"]["io"]["error_upper"] == 10.0

    fallback_logs = []
    for resource, maximum, unit in (
        ("cpu", 10.0, "cpu_seconds_per_window"),
        ("memory", 1024.0, "MB"),
        ("io", 50.0, "MB/window"),
    ):
        fallback_logs.append(
            {
                "resource_type": resource,
                "calibration_breakdown": {
                    "learned_model": {
                        "prediction_window_seconds": 5.0,
                        "target_unit": unit,
                        "error_bound": {"value": maximum / 2.0, "unit": unit},
                    },
                    "learned_model_fallback": {
                        "prediction_window_seconds": 5.0,
                        "target_unit": unit,
                        "metrics": {
                            "test": {"abs_error_max": maximum, "sample_count": 50}
                        },
                    },
                },
                "execution": {
                    "workload_context": {
                        "resource_model_retraining": {
                            "applied_model_paths": {
                                resource: f"/sealed/fallback-{resource}.joblib"
                            }
                        }
                    }
                },
            }
        )
    fallback_payload = _build_empirical_resource_box_pruning_input(
        {
            "final_score": 3.0,
            "final_resource": {"cpu": 4.0, "memory": 8.0, "io": 20.0},
            "final_estimation_metadata": {"resource_estimation_logs": fallback_logs},
        }
    )
    assert fallback_payload["calibration_ready"] is True
    assert (
        fallback_payload["resource_error_bounds"]["cpu"][
            "learned_model_evidence_source"
        ]
        == "learned_model_fallback"
    )


def test_reject_does_not_advance_and_accept_shrinks_every_active_dimension() -> None:
    count = _gradient_advance_accepted_shrink_count(0, accepted=False)
    assert count == 0

    ranges = {
        "seq_page_cost": ParameterRange(1.0, 9.0),
        "random_page_cost": ParameterRange(1.0, 9.0),
        "cpu_tuple_cost": ParameterRange(1.0, 9.0),
    }
    anchors = {
        "seq_page_cost": 3.0,
        "random_page_cost": 7.0,
        "cpu_tuple_cost": 3.0,
    }
    active = list(ranges)
    shrunk, updates = binary_shrink_parameter_ranges(ranges, anchors, active)
    assert set(updates) == set(active)
    for name in active:
        initial_width = math.log(ranges[name].max_value) - math.log(
            ranges[name].min_value
        )
        shrunk_width = math.log(shrunk[name].max_value) - math.log(
            shrunk[name].min_value
        )
        assert math.isclose(
            shrunk_width / initial_width,
            0.5,
            rel_tol=0.0,
            abs_tol=1e-12,
        )

    count = _gradient_advance_accepted_shrink_count(count, accepted=True)
    assert count == 1
    assert _gradient_expected_normalized_width(5) == 1.0 / 32.0
    assert _gradient_expected_normalized_width(10) == 1.0 / 1024.0


def test_accepted_and_sealed_partial_resumes_skip_round0_subset_search() -> None:
    assert _gradient_resume_skips_round0_subset_search(
        {"kind": "job_gradient_accepted_resume_checkpoint_v1"}
    )
    assert _gradient_resume_skips_round0_subset_search(
        {
            "kind": "gradient_partial_round_resume_checkpoint_v1",
            "resume_contract": {"do_not_rerun_round0": True},
        }
    )
    assert not _gradient_resume_skips_round0_subset_search(
        {
            "kind": "gradient_partial_round_resume_checkpoint_v1",
            "resume_contract": {"do_not_rerun_round0": False},
        }
    )
    assert not _gradient_resume_skips_round0_subset_search({})


def test_only_accepted_checkpoint_satisfies_restored_candidate_noise_gate() -> None:
    assert _gradient_resume_restores_accepted_candidate(
        {"kind": "job_gradient_accepted_resume_checkpoint_v1"}
    )
    assert not _gradient_resume_restores_accepted_candidate(
        {
            "kind": "gradient_partial_round_resume_checkpoint_v1",
            "resume_contract": {"do_not_rerun_round0": True},
        }
    )
    assert not _gradient_resume_restores_accepted_candidate({})


def test_seen_signatures_advance_to_rank_11() -> None:
    rankings = [
        {
            "rank": rank,
            "optimized_params": {"seq_page_cost": float(rank)},
        }
        for rank in range(1, 31)
    ]
    seen = set()
    first_5, first_5_ranks, _ = _gradient_take_unseen_rankings(
        rankings,
        seen,
        limit=5,
    )
    assert first_5_ranks == list(range(1, 6))
    assert seen == set()
    for record in first_5:
        _gradient_mark_replayed_params(record["optimized_params"], seen)
    assert len(seen) == 5

    # If the first five contain an eligible point, rank 6 was not executed and
    # remains the first unseen candidate for this incumbent/range.
    after_accepted_5, after_accepted_ranks, skipped_5 = (
        _gradient_take_unseen_rankings(rankings, seen, limit=5)
    )
    assert after_accepted_ranks == list(range(6, 11))
    assert skipped_5 == list(range(1, 6))
    assert len(seen) == 5

    # If the first five are rejected, only then is the second five replayed
    # and committed as evidence; the following exploration starts at rank 11.
    for record in after_accepted_5:
        _gradient_mark_replayed_params(record["optimized_params"], seen)
    second, second_ranks, skipped = _gradient_take_unseen_rankings(
        rankings,
        seen,
        limit=10,
    )
    assert len(second) == 10
    assert second_ranks == list(range(11, 21))
    assert skipped == list(range(1, 11))
    assert len(seen) == 10


def test_partial_resume_uses_exact_noncontiguous_representative_prefix() -> None:
    selected_ranks = [1, 3, 8, 11]
    replayed_params = [{"p": 1.0}, {"p": 3.0}]
    assert _resolve_partial_resume_rank_prefix(
        selected_ranks,
        replayed_params,
        [1, 3],
        8,
    ) == [1, 3]
    # Legacy checkpoints without an explicit rank vector inherit the selected prefix.
    assert _resolve_partial_resume_rank_prefix(
        selected_ranks,
        replayed_params,
        None,
        8,
    ) == [1, 3]
    for replayed_ranks, next_rank in (([1, 8], 8), ([1, 3], 11)):
        try:
            _resolve_partial_resume_rank_prefix(
                selected_ranks,
                replayed_params,
                replayed_ranks,
                next_rank,
            )
        except RuntimeError:
            pass
        else:
            raise AssertionError("non-prefix or skipped-next resume must fail closed")


def test_empirical_pruning_box_reads_fallback_metrics_and_context_model_path() -> None:
    def log_record(resource: str, *, fallback: bool = False):
        learned = {
            "metrics": {"test": {"abs_error_max": 5.0, "sample_count": 9}},
            "target_unit": (
                "cpu_seconds_per_5s_window"
                if resource == "cpu"
                else "MB_per_5s_window" if resource == "io" else "MB"
            ),
            "prediction_window_seconds": 5.0,
            "error_bound": {
                "value": 6.0,
                "unit": (
                    "cpu_seconds_per_5s_window"
                    if resource == "cpu"
                    else "MB_per_5s_window" if resource == "io" else "MB"
                ),
            },
        }
        calibration = {
            "learned_model": {} if fallback else learned,
            "learned_model_fallback": learned if fallback else {},
        }
        return {
            "resource_type": resource,
            "calibration_breakdown": calibration,
            "execution_breakdown": {
                "workload_context": {
                    "resource_model_retraining": {
                        "applied_model_paths": {resource: f"/{resource}.json"}
                    }
                }
            },
        }

    pruning_input = _build_empirical_resource_box_pruning_input(
        {
            "final_resource": {"cpu": 1.0, "memory": 2.0, "io": 3.0},
            "final_score": 4.0,
            "final_estimation_metadata": {
                "resource_estimation_logs": [
                    log_record("cpu", fallback=True),
                    log_record("memory"),
                    log_record("io"),
                ]
            },
        }
    )
    assert pruning_input["calibration_ready"] is True
    assert pruning_input["resource_error_bounds"]["cpu"][
        "learned_model_evidence_source"
    ] == "learned_model_fallback"
    assert pruning_input["resource_error_bounds"]["cpu"]["artifact_path"] == (
        "/cpu.json"
    )


def test_incumbent_signature_is_never_a_real_replay_candidate() -> None:
    incumbent = {"seq_page_cost": 1.0, "random_page_cost": 4.0}
    rankings = [
        {"rank": 1, "optimized_params": dict(incumbent)},
        {
            "rank": 2,
            "optimized_params": {
                "seq_page_cost": 1.2,
                "random_page_cost": 4.0,
            },
        },
    ]
    incumbent_signature = _SharedPlanEstimateFutureCache._signature(incumbent)
    seen = set()
    selected, selected_ranks, skipped_seen = _gradient_take_unseen_rankings(
        rankings,
        seen,
        limit=5,
        excluded_signatures={incumbent_signature},
    )
    assert selected_ranks == [2]
    assert selected == [rankings[1]]
    assert skipped_seen == []
    assert seen == set()


def test_gradient_requires_a_selected_non_incumbent_candidate() -> None:
    assert not _initial_common_outer_hard_gate(
        "gradient",
        performance_guard_ok=True,
        selected_gradient_candidate=False,
    )
    assert _initial_common_outer_hard_gate(
        "gradient",
        performance_guard_ok=True,
        selected_gradient_candidate=True,
    )
    assert _initial_common_outer_hard_gate(
        "bruteforce",
        performance_guard_ok=True,
        selected_gradient_candidate=False,
    )
    assert not _initial_common_outer_hard_gate(
        "gradient",
        performance_guard_ok=False,
        selected_gradient_candidate=True,
    )


def test_replicate_callback_loads_from_sealed_implementation_path() -> None:
    source = (
        b"from dataclasses import dataclass\n"
        b"@dataclass\n"
        b"class Payload:\n"
        b"    value: int\n"
        b"def run(**kwargs):\n"
        b"    return Payload(**kwargs)\n"
    )
    with tempfile.TemporaryDirectory() as temp_dir:
        implementation = Path(temp_dir) / "policy.py"
        implementation.write_bytes(source)
        callback = _load_replicate_policy_callback(
            "sealed_test_policy:run",
            {
                "implementation_path": str(implementation),
                "implementation_sha256": hashlib.sha256(source).hexdigest(),
            },
        )
        assert callback(value=3).value == 3


def test_gradient_disables_legacy_normal_early_stops() -> None:
    assert not _legacy_feedback_early_stop_allowed("gradient")
    assert _legacy_feedback_early_stop_allowed("bruteforce")


def test_incomplete_degradation_summary_is_safe_for_final_report() -> None:
    normalized = _normalize_degradation_summary_for_report(
        {"degraded_sql": [{"queryid": "7"}]}
    )
    assert normalized["threshold_pct"] == 10.0
    assert normalized["degraded_count"] == 1
    assert normalized["new_in_final_top_count"] == 0
    assert normalized["max_degradation_pct"] is None
    assert normalized["avg_degradation_pct"] is None


def test_top10_rejection_does_not_trigger_bruteforce_gate_for_sgd() -> None:
    rejected_summary = {"first_stage_ranking_validation_pass": False}
    assert not _bruteforce_first_stage_ranking_gate_applies(
        "gradient",
        True,
        rejected_summary,
    )
    assert _bruteforce_first_stage_ranking_gate_applies(
        "bruteforce",
        True,
        rejected_summary,
    )
    assert not _bruteforce_first_stage_ranking_gate_applies(
        "bruteforce",
        False,
        rejected_summary,
    )


def test_plan_estimator_docker_bootstrap_never_calls_vm_monitor() -> None:
    calls = {"docker": 0, "vm": 0}

    def docker_monitor():
        calls["docker"] += 1
        return {"cpu": 1.0}, {"source": "docker"}

    def vm_monitor():
        calls["vm"] += 1
        raise AssertionError("docker bootstrap must not call the VM/SSH monitor")

    result = _dispatch_plan_estimator_bootstrap_monitor(
        "docker-runtime",
        docker_monitor=docker_monitor,
        vm_monitor=vm_monitor,
    )
    assert result == ({"cpu": 1.0}, {"source": "docker"})
    assert calls == {"docker": 1, "vm": 0}

    try:
        _dispatch_plan_estimator_bootstrap_monitor(
            "unknown-runtime",
            docker_monitor=docker_monitor,
            vm_monitor=vm_monitor,
        )
    except ValueError as exc:
        assert "docker-runtime" in str(exc)
        assert "vm-runtime" in str(exc)
    else:
        raise AssertionError("unknown bootstrap runtime must fail closed")
    assert calls == {"docker": 1, "vm": 0}


def test_deterministic_eligible_order() -> None:
    candidates = [
        ({"db_qps": 11.0, "utilization_pct": {"cpu": 70.0}}, {"p": 2.0}),
        ({"db_qps": 12.0, "utilization_pct": {"cpu": 75.0}}, {"p": 3.0}),
        ({"db_qps": 12.0, "utilization_pct": {"cpu": 65.0}}, {"p": 4.0}),
        ({"db_qps": 12.0, "utilization_pct": {"cpu": 65.0}}, {"p": 1.0}),
    ]
    selected = max(
        candidates,
        key=lambda item: _gradient_real_candidate_rank_key(
            item[0],
            item[1],
            target_resource="cpu",
        ),
    )
    assert selected[1] == {"p": 1.0}

    manifest_order_candidates = [
        ({"db_qps": 12.0, "utilization_pct": {"cpu": 65.0}}, {"a": 1.0, "b": 2.0}),
        ({"db_qps": 12.0, "utilization_pct": {"cpu": 65.0}}, {"a": 2.0, "b": 1.0}),
    ]
    manifest_selected = max(
        manifest_order_candidates,
        key=lambda item: _gradient_real_candidate_rank_key(
            item[0],
            item[1],
            target_resource="cpu",
            active_param_order=["b", "a"],
        ),
    )
    assert manifest_selected[1] == {"a": 2.0, "b": 1.0}


def test_shared_future_coalesces_identical_params() -> None:
    cache = _SharedPlanEstimateFutureCache()
    lock = threading.Lock()
    compute_count = 0

    def compute():
        nonlocal compute_count
        with lock:
            compute_count += 1
        time.sleep(0.03)
        return {"cpu": 1.0}, {"cpu": {"avg": 1.0}}, {"source": "test"}

    def run_one(_index: int):
        return cache.get_or_compute({"p": 1.0}, compute)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(run_one, range(16)))

    assert compute_count == 1
    assert len(results) == 16
    stats = cache.stats()
    assert stats["request_count"] == 16
    assert stats["unique_parameter_count"] == 1
    assert stats["cache_hit_count"] == 15


def main() -> None:
    test_baseline_resource_band_gate_matches_identify_bottleneck_threshold()
    test_staged_5_then_10_then_scenario_cap()
    test_resource_pruning_box_uses_model_max_error_and_unit_conversion()
    test_reject_does_not_advance_and_accept_shrinks_every_active_dimension()
    test_seen_signatures_advance_to_rank_11()
    test_partial_resume_uses_exact_noncontiguous_representative_prefix()
    test_empirical_pruning_box_reads_fallback_metrics_and_context_model_path()
    test_incumbent_signature_is_never_a_real_replay_candidate()
    test_gradient_requires_a_selected_non_incumbent_candidate()
    test_replicate_callback_loads_from_sealed_implementation_path()
    test_gradient_disables_legacy_normal_early_stops()
    test_incomplete_degradation_summary_is_safe_for_final_report()
    test_top10_rejection_does_not_trigger_bruteforce_gate_for_sgd()
    test_plan_estimator_docker_bootstrap_never_calls_vm_monitor()
    test_deterministic_eligible_order()
    test_shared_future_coalesces_identical_params()
    print("parameter optimizer gradient subset outer tests passed", flush=True)


if __name__ == "__main__":
    main()
