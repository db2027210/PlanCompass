#!/usr/bin/env python3
"""Focused integration tests for Algorithm C adaptive controller evidence."""

from __future__ import annotations

import copy
import inspect
import math
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from algorithm_c_adaptive_search import AdaptiveSearchPolicy, adaptive_search_decision
from shell import run_pg_algorithm_c_scene as base
from shell.run_pg_algorithm_c_scene import GLOBAL_PLAN_COVERAGE_CONTRACT
from shell.run_pg_algorithm_c_scene import partition_baseline_controls
from shell.run_pg_algorithm_c_budgeted_scene import (
    BudgetedLaneController,
    OUTER_BOUNDARY_REFINEMENT_CONTRACT,
    PROGRESSIVE_NUMERIC_REFINEMENT_CONTRACT,
    external_baseline_information_paths,
    interpolate_parameter_vectors,
    normalized_parameter_distance,
    paper_abc_runtime_config_violations,
    radical_inverse,
    validate_paper_abc_runtime_model,
)


def main() -> int:
    baseline_rows = [
        {"baseline_repetition": repetition, "worker_key": worker, "db_qps": 100.0 if repetition == 1 else 50.0}
        for repetition in (1, 2, 3)
        for worker in ("w1", "w2")
    ]
    measured, burn_in = partition_baseline_controls(
        baseline_rows,
        worker_keys=("w1", "w2"),
        repetitions_per_worker=3,
        burn_in_repetitions=1,
    )
    assert len(measured) == 4 and {row["baseline_repetition"] for row in measured} == {2, 3}
    assert len(burn_in) == 2 and {row["baseline_repetition"] for row in burn_in} == {1}
    try:
        partition_baseline_controls(
            baseline_rows[:-1], worker_keys=("w1", "w2"),
            repetitions_per_worker=3, burn_in_repetitions=1,
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("incomplete baseline controls were admitted")

    # A burn-in wave remains audited, but the frozen scoring domain must use
    # exactly the rows that verify_baseline_execution_domain later reads.
    domain_rows = [
        {
            "label": f"baseline_{worker}_{repetition}",
            "baseline_repetition": repetition,
            "worker_key": worker,
            "ssd_qualification": {
                "qualification_id": f"qualification_{worker}_{repetition}",
                "thresholds": {
                    "explicit_user_authorized_no_mutex": False,
                    "global_ssd_stable_window_enforced": True,
                    "global_ssd_utilization_is_informational": False,
                },
            },
        }
        for repetition in (1, 2, 3)
        for worker in ("w1", "w2")
    ]
    scored_rows, _ = partition_baseline_controls(
        domain_rows, worker_keys=("w1", "w2"),
        repetitions_per_worker=3, burn_in_repetitions=1,
    )
    canonical_domain_row = copy.deepcopy(scored_rows[0])
    cohort = {"enabled": False, "cohort_size": 1}
    all_domain = base.baseline_execution_domain_audit(
        domain_rows, canonical_domain_row, expected_no_mutex=False, cohort=cohort,
    )
    scored_domain = base.baseline_execution_domain_audit(
        scored_rows, canonical_domain_row, expected_no_mutex=False, cohort=cohort,
    )
    assert all_domain["passed"] and scored_domain["passed"]
    assert all_domain["audit_sha256"] != scored_domain["audit_sha256"]
    domain_controller = base.LaneController.__new__(base.LaneController)
    domain_controller.algorithm_cfg = {}
    domain_controller.state = {
        "baseline_replay_rows": scored_rows,
        "canonical_baseline": canonical_domain_row,
        "baseline_execution_domain": scored_domain,
        "baseline_all_replay_execution_domain": all_domain,
    }
    domain_controller.save_state = lambda: None
    domain_controller.log = lambda *_args, **_kwargs: None
    with mock.patch.object(base, "explicit_user_authorized_no_mutex_from_env", return_value=False), \
            mock.patch.object(base, "algorithm_c_cohort_config_from_env", return_value=cohort):
        assert domain_controller.verify_baseline_execution_domain()["passed"] is True
    baseline_source = inspect.getsource(base.LaneController.baseline)
    assert "all_replay_execution_domain = baseline_execution_domain_audit(\n            valid_rows," in baseline_source
    assert "execution_domain = baseline_execution_domain_audit(\n            measured_rows," in baseline_source

    warmup = BudgetedLaneController.__new__(BudgetedLaneController)
    warmup.policy = {"startup_warmup": {
        "contract": "algorithm_c_full_replay_startup_warmup_v1", "enabled": True,
    }}
    warmup.state = {}
    warmup.trial = {}
    warmup.workers = [{"name": "w1"}, {"name": "w2"}]
    warmup.replay_parallelism = 2
    warmup.algorithm_cfg = {"scene": "job_memory"}
    warmup.target = "memory"
    warmup.cfg = {}
    warmup.initial_params = {"seq_page_cost": 1.0}
    warmup.output_dir = Path("/tmp/algorithm_c_startup_warmup_test")
    warmup.stop_requested = False
    warmup.save_state = lambda: None
    warmup.log = lambda *_args, **_kwargs: None
    warmup.prepare_parallel_baseline_workers = lambda _rep: [
        (worker, {"status": "ok"}) for worker in warmup.workers
    ]
    with mock.patch.object(base, "wait_for_algorithm_c_baseline_cohort", return_value={"passed": True}), \
            mock.patch.object(base, "wait_for_algorithm_c_baseline_cohort_completion", return_value={"passed": True}), \
            mock.patch.object(base, "apply_ssd_wave_qualification", return_value={"passed": True}), \
            mock.patch.object(base, "run_real_validation_safe", return_value={
                "status": "ok", "workload_completeness": {"passed": True}, "db_qps": 1.0,
            }) as replay:
        warmup_audit = warmup.run_startup_warmup()
    assert warmup_audit["completed"] is True
    assert warmup_audit["worker_count"] == 2
    assert warmup_audit["qps_used_for_baseline_or_candidate_scoring"] is False
    assert warmup.state["physical_workload_invocations"] == 2
    assert replay.call_count == 2

    repeatability = BudgetedLaneController.__new__(BudgetedLaneController)
    repeatability.policy = {"baseline_repeatability_gate": {
        "enabled": True, "maximum_worker_repeat_ratio": 1.10,
    }}
    repeatability.workers = [{"container": "w1"}, {"container": "w2"}]
    repeatability.state = {"qps_noise_model": {
        "ready": True, "worker_repeat_ratio": {"w1": 1.01, "w2": 1.08},
    }}
    repeatability.save_state = lambda: None
    restored = []
    repeatability.restore_clean_baseline = lambda *, reason: restored.append(reason)
    assert repeatability.verify_baseline_repeatability()["passed"] is True
    repeatability.state["qps_noise_model"]["worker_repeat_ratio"]["w2"] = 1.20
    try:
        repeatability.verify_baseline_repeatability()
    except RuntimeError as exc:
        assert "unstable_worker:w2" in str(exc)
    else:
        raise AssertionError("unstable baseline entered formal search")
    assert repeatability.state["phase"] == "baseline_repeatability_failed_clean"
    assert restored == ["baseline_repeatability_failed"]

    repeatability.policy["baseline_repeatability_gate"]["mode"] = "advisory"
    repeatability.state.pop("phase")
    repeatability.log = lambda *_args, **_kwargs: None
    audit = repeatability.verify_baseline_repeatability()
    assert audit["passed"] is False
    assert audit["search_allowed"] is True
    assert audit["worker_repeat_ratio"]["w2"] == 1.20
    assert "phase" not in repeatability.state
    assert restored == ["baseline_repeatability_failed"]

    anchor_controller = BudgetedLaneController.__new__(BudgetedLaneController)
    anchor_controller.inner_resource_anchor = None
    measured = {"cpu": 3.0, "memory": 4.0, "io": 5.0}
    assert anchor_controller.inner_scoring_reference_resources(measured) == measured
    anchor_controller.inner_resource_anchor = {
        "reference_resources": {"cpu": 8.0, "memory": 16.0, "io": 12.0}
    }
    assert anchor_controller.inner_scoring_reference_resources(measured) == {
        "cpu": 8.0,
        "memory": 16.0,
        "io": 12.0,
    }

    controller = BudgetedLaneController.__new__(BudgetedLaneController)
    controller.adaptive_policy = AdaptiveSearchPolicy()
    controller.target = "cpu"
    controller.normalized_width_threshold = 0.01
    controller.ranges = {name: base.ParameterRange(*limits) for name, limits in base.DEFAULT_RANGES.items()}
    controller.trial = {
        "started_at_epoch_s": time.time(),
        "active_session_started_at_epoch_s": time.time(),
        "completed_search_waves": 0,
        "seen_search_plan_fingerprints": [],
        "region_queue": [],
        "search_replays": 2,
        "search_valid_replays": 2,
        "search_technical_failures": 0,
        "consecutive_empty_admissions": 0,
        "feasible_archive": {},
        "pending_outer_confirmation": None,
        "completed_outer_confirmation_count": 0,
        "consecutive_outer_non_accept_waves": 0,
    }
    controller.state = {
        "canonical_baseline": {"resources": {"cpu": 4.0}},
        "incumbent_validation": None,
    }
    controller.log = lambda *_args, **_kwargs: None
    controller.save_state = lambda: None
    controller.budget = None
    assert controller.budget_stop() is None
    candidates = [
        {
            "three_level_fingerprint": {"plan_level": "plan-1"},
            "confidence": {"p": 0.9},
        },
        {
            "three_level_fingerprint": {"plan_level": "plan-2"},
            "confidence": {"p": 0.8},
        },
    ]
    rows = [
        {"status": "ok", "candidate_ranking": candidate}
        for candidate in candidates
    ]
    selected = {
        "normalized": {"resources": {"cpu": 3.6}},
        "raw": rows[0],
    }
    controller.record_adaptive_batch(candidates, rows, selected)
    evidence = controller.adaptive_evidence()
    assert evidence.completed_waves == 1
    assert evidence.latest_valid_search_replays == 2
    assert evidence.novel_plan_fingerprints == 2
    assert abs(evidence.normalized_resource_gain - 0.1) < 1e-12
    assert abs(evidence.maximum_model_uncertainty - 0.2) < 1e-12
    assert evidence.outer_qps_accepted is True
    assert evidence.consecutive_outer_non_accept_waves == 0
    assert evidence.has_feasible_finalist is False

    controller.record_adaptive_batch(candidates, rows, None)
    evidence = controller.adaptive_evidence()
    assert evidence.completed_waves == 2
    assert evidence.novel_plan_fingerprints == 0
    assert evidence.duplicate_plan_fraction == 1.0
    assert evidence.consecutive_no_progress_waves == 1
    assert evidence.consecutive_outer_non_accept_waves == 1

    controller.record_adaptive_batch([], [], None)
    evidence = controller.adaptive_evidence()
    assert evidence.completed_waves == 2
    assert evidence.consecutive_no_progress_waves == 1
    assert evidence.consecutive_rejected_batches == 1
    assert evidence.consecutive_empty_admissions == 1
    assert evidence.consecutive_outer_non_accept_waves == 1
    assert evidence.latest_valid_search_replays == 0

    controller.trial.update({
        "consecutive_empty_admissions": 0,
        "consecutive_no_progress_waves": 3,
        "latest_duplicate_plan_fraction": 1.0,
        "latest_maximum_model_uncertainty": 0.5,
        "latest_valid_search_replays": 2,
        "recovery_stage": 3,
        "stage_rejected_batches": 2,
        "consecutive_rejected_batches": 2,
        "consecutive_no_progress_batches": 2,
    })
    exhausted = controller.next_adaptive_decision()
    assert exhausted.action == "shrink_incumbent"
    assert exhausted.converged is False

    controller.trial["feasible_archive"] = {
        "candidate": {
            "params": {"p": 1.0},
            "observations": [{"label": "physical", "qps": 2.0}],
        }
    }
    exhausted_with_finalist = controller.next_adaptive_decision()
    assert exhausted_with_finalist.action == "shrink_incumbent"
    assert exhausted_with_finalist.converged is False

    controller.trial.update({
        "feasible_archive": {},
        "pending_outer_confirmation": None,
        "completed_outer_confirmation_count": 0,
        "outer_confirmation_exhausted_identities": [],
    })
    controller.workers = [{"name": "w1"}, {"name": "w2"}]
    near_gate_ranking = {
        "optimized_params": {"p": 1.0},
        "active_subset": ["p"],
    }
    near_gate_evaluation = {
        "raw": {
            "status": "ok",
            "label": "near-gate-w1",
            "worker_key": "w1",
            "db_qps": 104.0,
            "candidate_ranking": near_gate_ranking,
        },
        "normalized": {"db_qps": 104.0},
    }
    near_gate_decision = {
        "accepted": False,
        "non_target_safe": True,
        "qps_noise_model_ready": True,
        "qps_gate_gain": 0.04,
        "static_min_db_qps_gain_pct": 1.0,
        "min_db_qps_gain_pct": 8.0,
        "rejects": ["db_qps_not_up_vs_canonical_baseline"],
    }
    assert controller.enqueue_outer_confirmation(
        near_gate_evaluation,
        near_gate_decision,
        4,
    ) is False
    queued_decision = controller.next_adaptive_decision()
    assert queued_decision.action == "shrink_incumbent"
    assert controller.trial["pending_outer_confirmation"] is None

    support_probe = {
        "global_plan_coverage_rescue": True,
        "global_plan_coverage_contract": GLOBAL_PLAN_COVERAGE_CONTRACT,
    }
    supported_distance = {
        "support_abstained": True,
        "distance_to_threshold_ratio": 1.97,
    }
    assert controller.allow_target_support_expansion_probe(
        support_probe,
        supported_distance,
    ) is True
    assert controller.allow_target_support_expansion_probe(
        support_probe,
        {**supported_distance, "distance_to_threshold_ratio": 2.51},
    ) is False
    assert controller.allow_target_support_expansion_probe(
        {},
        supported_distance,
    ) is False

    boundary = BudgetedLaneController.__new__(BudgetedLaneController)
    from algorithm_c_execution_role import FORMAL
    boundary.execution_role = FORMAL
    boundary.state = {}
    boundary.select_outer_admitted_candidates = lambda rows, **kwargs: (rows, {})
    boundary.adaptive_policy = AdaptiveSearchPolicy()
    boundary.target = "io"
    boundary.ranges = {
        name: base.ParameterRange(*limits)
        for name, limits in base.DEFAULT_RANGES.items()
    }
    boundary.trial = {
        "outer_boundary_observations": [],
        "outer_boundary_generated_identities": [],
        "progressive_numeric_refinement_cursor": 1,
        "progressive_numeric_refinement_window": 0,
    }
    boundary.log = lambda *_args, **_kwargs: None
    boundary.save_state = lambda: None
    safe_params = {
        "seq_page_cost": 1.0,
        "random_page_cost": 4.0,
        "cpu_tuple_cost": 0.01,
        "cpu_index_tuple_cost": 0.005,
        "cpu_operator_cost": 0.0025,
        "parallel_setup_cost": 1000.0,
        "parallel_tuple_cost": 0.1,
    }
    unsafe_params = copy.deepcopy(safe_params)
    unsafe_params.update({
        "random_page_cost": 1.0,
        "cpu_tuple_cost": 0.04,
        "parallel_setup_cost": 5000.0,
    })
    boundary.state["incumbent_params"] = copy.deepcopy(safe_params)
    safe_evaluation = {
        "raw": {
            "label": "safe-qps-shortfall",
            "candidate_ranking": {
                "optimized_params": safe_params,
                "three_level_fingerprint": {"plan_level": "safe-plan"},
            },
        }
    }
    unsafe_evaluation = {
        "raw": {
            "label": "qps-pass-resource-unsafe",
            "candidate_ranking": {
                "optimized_params": unsafe_params,
                "three_level_fingerprint": {"plan_level": "unsafe-plan"},
            },
        }
    }
    common_decision = {
        "qps_noise_model_ready": True,
        "min_db_qps_gain_pct": 1.8,
        "qps_noise_model_id": "c-only-noise",
        "non_target_bottleneck_threshold_pct": 80.0,
    }
    safe_observation = boundary.record_outer_boundary_observation(
        safe_evaluation,
        {
            **common_decision,
            "qps_gate_gain": -0.009,
            "qps_up": False,
            "non_target_safe": True,
            "target_down": True,
            "non_target_utilization_pct": {"cpu": 79.0, "memory": 20.0},
        },
        round_index=1,
    )
    unsafe_observation = boundary.record_outer_boundary_observation(
        unsafe_evaluation,
        {
            **common_decision,
            "qps_gate_gain": 0.015,
            "qps_up": False,
            "non_target_safe": False,
            "target_down": False,
            "non_target_utilization_pct": {"cpu": 84.0, "memory": 20.0},
        },
        round_index=2,
    )
    assert safe_observation["category"] == "resource_feasible_qps_below_gate"
    assert unsafe_observation["category"] == "positive_qps_resource_infeasible"
    pair = boundary.outer_boundary_pair()
    assert pair is not None
    assert pair["safe"]["label"] == "safe-qps-shortfall"
    assert pair["unsafe"]["label"] == "qps-pass-resource-unsafe"
    assert pair["normalized_parameter_distance"] > 0.0
    midpoint = interpolate_parameter_vectors(safe_params, unsafe_params, 0.5)
    assert math.isclose(midpoint["random_page_cost"], 2.0)
    assert math.isclose(
        normalized_parameter_distance(
            safe_params,
            midpoint,
            boundary.ranges,
        ),
        pair["normalized_parameter_distance"] / 2.0,
    )
    assert 0.0 < radical_inverse(7, 2) < 1.0
    boundary_vectors, boundary_audit = boundary.outer_boundary_refinement_vectors(2)
    assert len(boundary_vectors) == 2
    assert boundary_audit["contract"] == OUTER_BOUNDARY_REFINEMENT_CONTRACT
    assert boundary_audit["qps_used_for_inner_scoring"] is False
    assert boundary_audit["qps_used_for_outer_interval_control"] is True
    # CPU crosses 80% one fifth of the way from 79% to 84%.
    assert math.isclose(
        boundary_audit["vectors"][0]["fraction_from_safe_to_unsafe"], 0.1,
    )
    assert all(
        base.parameter_vector_range_audit(row, boundary.ranges)["passed"]
        for row in boundary_vectors
    )
    next_boundary_vectors, _ = boundary.outer_boundary_refinement_vectors(2)
    assert len(next_boundary_vectors) == 2
    assert {
        base.exact_param_signature(row) for row in boundary_vectors
    }.isdisjoint({
        base.exact_param_signature(row) for row in next_boundary_vectors
    })
    progressive_one, progressive_audit_one = (
        boundary.progressive_numeric_refinement_vectors(4)
    )
    progressive_two, progressive_audit_two = (
        boundary.progressive_numeric_refinement_vectors(4)
    )
    assert progressive_audit_one["contract"] == (
        PROGRESSIVE_NUMERIC_REFINEMENT_CONTRACT
    )
    assert progressive_audit_one["cursor_end_exclusive"] == (
        progressive_audit_two["cursor_start"]
    )
    assert progressive_audit_one["normalized_radius"] == 0.05
    assert progressive_audit_two["normalized_radius"] == 0.10
    assert progressive_audit_one["center"] == progressive_audit_two["center"]
    assert {
        base.exact_param_signature(row) for row in progressive_one
    }.isdisjoint({
        base.exact_param_signature(row) for row in progressive_two
    })

    def dynamic_rankings(round_index, *, allow_numeric_refinement=False):
        assert round_index == 3
        assert allow_numeric_refinement is True
        assert boundary._adaptive_coverage_override
        return [
            {
                "optimized_params": copy.deepcopy(params),
                "numeric_refinement": True,
                "plan_space_saturation_required": True,
                "qps_used_for_inner_scoring": False,
            }
            for params in boundary._adaptive_coverage_override
        ]

    boundary.generate_global_plan_coverage_rankings = dynamic_rankings
    dynamic = boundary.generate_dynamic_numeric_refinement_rankings(3)
    assert len(dynamic) == 5
    assert all(row["outer_boundary_refinement"] is True for row in dynamic)
    assert all(row["qps_used_for_inner_scoring"] is False for row in dynamic)
    assert all(
        row["qps_used_for_outer_interval_control"] is True for row in dynamic
    )
    accepted_safe = boundary.record_outer_boundary_observation(
        safe_evaluation,
        {
            **common_decision,
            "qps_gate_gain": 0.005,
            "qps_up": True,
            "non_target_safe": True,
            "target_down": True,
            "non_target_utilization_pct": {"cpu": 79.0, "memory": 20.0},
        },
        round_index=3,
    )
    assert accepted_safe["category"] == "resource_feasible_qps_passed"
    prior_observations = boundary.trial["outer_boundary_observations"]
    boundary.trial["outer_boundary_observations"] = [accepted_safe, unsafe_observation]
    assert boundary.outer_boundary_pair()["safe"]["qps_passed"] is True
    boundary.trial["outer_boundary_observations"] = prior_observations

    fallback_events = []
    boundary.log = lambda event, **values: fallback_events.append(
        {"event": event, **values}
    )
    fallback_calls = 0

    def boundary_then_progressive(round_index, *, allow_numeric_refinement=False):
        nonlocal fallback_calls
        assert round_index == 4
        assert allow_numeric_refinement is True
        fallback_calls += 1
        if fallback_calls == 1:
            return []
        return [
            {
                "optimized_params": copy.deepcopy(params),
                "numeric_refinement": True,
                "plan_space_saturation_required": True,
                "qps_used_for_inner_scoring": False,
            }
            for params in boundary._adaptive_coverage_override
        ]

    boundary.generate_global_plan_coverage_rankings = boundary_then_progressive
    boundary.trial["numeric_pending_rankings"] = []
    fallback_rankings = boundary.generate_dynamic_numeric_refinement_rankings(4)
    assert fallback_calls == 2
    assert fallback_rankings
    assert all(
        row["dynamic_numeric_refinement_mode"]
        == "progressive_low_discrepancy"
        for row in fallback_rankings
    )
    fallback_event = fallback_events[-1]
    assert fallback_event["event"] == "algorithm_c_dynamic_numeric_refinement"
    assert fallback_event["boundary_fallback"]["contract"] == (
        OUTER_BOUNDARY_REFINEMENT_CONTRACT
    )

    with tempfile.TemporaryDirectory() as temporary:
        coverage = BudgetedLaneController.__new__(BudgetedLaneController)
        coverage.adaptive_policy = AdaptiveSearchPolicy()
        coverage.target = "memory"
        coverage.output_dir = Path(temporary)
        coverage.capacity = {"cpu": 16.0, "memory": 32.0, "io": 100.0}
        coverage.ranges = {
            name: base.ParameterRange(*limits)
            for name, limits in base.DEFAULT_RANGES.items()
        }
        incumbent = {
            "seq_page_cost": 1.0,
            "random_page_cost": 4.0,
            "cpu_tuple_cost": 0.01,
            "cpu_index_tuple_cost": 0.005,
            "cpu_operator_cost": 0.0025,
            "parallel_setup_cost": 1000.0,
            "parallel_tuple_cost": 0.1,
        }
        coverage.state = {
            "incumbent_params": incumbent,
            "incumbent_validation": None,
            "canonical_baseline": {
                "resources": {"cpu": 4.0, "memory": 8.0, "io": 20.0}
            },
        }
        coverage.experience_artifact = {
            "resource_prediction_calibration": {
                "resources": {
                    "memory": {"parameter_support_abstention_enabled": True}
                }
            }
        }
        vectors = []
        for index in range(4):
            params = copy.deepcopy(incumbent)
            params["seq_page_cost"] = 1.2 + index
            vectors.append(params)
        coverage.coverage_vectors = lambda _incumbent: copy.deepcopy(vectors)
        coverage.queries_and_probe = lambda _params: ([], {}, {})
        coverage.validate_top5_audit_records = lambda _rows: None
        logged = []
        coverage.log = lambda event, **values: logged.append({"event": event, **values})

        support = {
            "enabled": True,
            "supported": False,
            "support_abstained": True,
            "distance_to_threshold_ratio": 1.97,
        }

        def estimator(_sql_set, _params):
            return (
                {"cpu": 3.8, "memory": 8.0, "io": 19.0},
                {},
                {
                    "resource_prediction_calibration_audit": {
                        "memory": {"parameter_support": copy.deepcopy(support)}
                    },
                    "plan_model_confidence": {"memory": 0.0},
                },
            )

        def fingerprint(row, _capacity):
            result = copy.deepcopy(row)
            suffix = int(round(float(result["optimized_params"]["seq_page_cost"]) * 10))
            result["three_level_fingerprint"] = {
                "plan_level": f"workload-exact-physical-sha256:plan-{suffix}"
            }
            return result

        with mock.patch.object(base, "RatioScaleEstimator", return_value=estimator), mock.patch.object(
            base,
            "attach_three_level_fingerprint",
            side_effect=fingerprint,
        ):
            rankings = coverage.generate_global_plan_coverage_rankings(1)
        assert len(rankings) == 4
        assert all(row["target_support_expansion_probe"] is True for row in rankings)
        assert all(
            row["target_support_prediction_used_for_candidate_score"] is False
            for row in rankings
        )
        assert logged[-1]["target_support_expansion_probe_count"] == 4
        assert logged[-1]["target_support_excluded_count"] == 0

        # Once the distinct-plan rescue is exhausted, the controller must be
        # able to expose unseen exact parameter identities from a saturated
        # plan cluster. This remains deterministic and QPS-blind.
        def saturated_fingerprint(row, _capacity):
            result = copy.deepcopy(row)
            result["three_level_fingerprint"] = {
                "valid": True,
                "id": "behavior-saturated",
                "plan_level": "workload-exact-physical-sha256:one-plan",
            }
            return result

        with mock.patch.object(base, "RatioScaleEstimator", return_value=estimator), mock.patch.object(
            base,
            "attach_three_level_fingerprint",
            side_effect=saturated_fingerprint,
        ):
            distinct_only = coverage.generate_global_plan_coverage_rankings(2)
            numeric = coverage.generate_global_plan_coverage_rankings(
                3,
                allow_numeric_refinement=True,
            )
        assert len(distinct_only) == 1
        assert len(numeric) == len(vectors)
        assert all(row["numeric_refinement"] is True for row in numeric)
        assert all(row["qps_used_for_inner_scoring"] is False for row in numeric)

        coverage.workload_contract_sha256 = "adaptive-unit-workload"
        coverage.outer_pruning_policy = {"enabled": False}
        identity_memory = base.empty_valid_replay_identity_memory(
            coverage.workload_contract_sha256,
            coverage.target,
        )
        for position, candidate in enumerate(numeric[:2], start=1):
            recorded = base.record_valid_replay_identity(
                identity_memory,
                candidate=candidate,
                round_index=position,
                label=f"already-replayed-{position}",
            )
            assert recorded["recorded"] is True
        coverage.state.update({
            "valid_replay_identity_memory": identity_memory,
            "outer_accept_reject_pruning": base.empty_outer_pruning_memory(),
        })
        coverage.current_outer_pruning_context = lambda: {
            "required_acceptance_tier": "fallback",
            "context_sha256": "adaptive-unit-context",
        }
        selected, admission = coverage.select_outer_admitted_candidates(
            numeric,
            round_index=4,
            batch_kind="budgeted_formal_search",
            limit=2,
        )
        assert len(selected) == 2
        assert admission["exact_valid_replay_excluded_count"] == 2
        assert admission["selected_count"] == 2
        assert selected == numeric[2:4]

        # A plan-level reject rule must not erase an unseen numeric point:
        # PostgreSQL costs can change runtime/resource behavior without
        # changing the physical plan. Exact parameter+plan replay identity
        # remains the hard de-duplication boundary.
        ordinary = copy.deepcopy(numeric[2])
        ordinary.pop("numeric_refinement", None)
        ordinary.pop("numeric_refinement_reason", None)
        ordinary.pop("plan_space_saturation_required", None)
        refinement = copy.deepcopy(numeric[3])
        context = coverage.current_outer_pruning_context()
        rule_key = base.outer_pruning_rule_key(context, refinement)
        coverage.outer_pruning_policy = {
            "enabled": True,
            "minimum_valid_rejects_per_plan": 2,
            "minimum_distinct_rounds": 2,
            "minimum_distinct_parameter_vectors": 2,
        }
        coverage.state["valid_replay_identity_memory"] = (
            base.empty_valid_replay_identity_memory(
                coverage.workload_contract_sha256,
                coverage.target,
            )
        )
        coverage.state["outer_accept_reject_pruning"] = {
            "contract": base.OUTER_PRUNING_CONTRACT,
            "rules": {
                rule_key: {
                    "accepted_count": 0,
                    "rejected_count": 2,
                    "rounds": [1, 2],
                    "parameter_signatures": ["p1", "p2"],
                    "reject_reason_counts": {
                        "db_qps_not_up_vs_last_accepted": 2,
                    },
                }
            },
        }
        selected, admission = coverage.select_outer_admitted_candidates(
            [ordinary, refinement],
            round_index=5,
            batch_kind="budgeted_formal_search",
            limit=2,
        )
        assert selected == [refinement]
        assert admission["hard_pruned_count"] == 1
        assert admission[
            "numeric_refinement_hard_prune_bypassed_count"
        ] == 1
        assert admission["selected"][0]["assessment"][
            "plan_level_hard_prune_bypassed_for_numeric_refinement"
        ] is True

    assert external_baseline_information_paths({"training": ["algorithm_a"]}) == [
        "$.training[0]"
    ]
    assert external_baseline_information_paths({"training": ["algorithm_c"]}) == []
    paper_model = {
        "qps_used_for_inner_scoring": False,
        "training_boundary": {
            "allowed_training_source_algorithms": [
                "algorithm_a", "algorithm_b", "algorithm_c",
            ],
            "accept_reject_labels_used_for_resource_model": False,
            "historical_parameter_identity_used_as_model_feature": False,
            "historical_plan_fingerprint_used_as_model_feature": False,
            "qps_present_in_resource_training_rows": False,
            "qps_used_as_model_feature_or_target": False,
            "source_algorithm_used_as_model_feature": False,
        },
        "holdout": {
            "holdout_source_algorithm": "algorithm_c",
            "independent_from_fit": True,
            "qps_used_for_selection_prediction": False,
        },
        "runtime_candidate_prior_firewall": {
            "qps_used_as_model_feature_or_target": False,
            "qps_used_for_candidate_ranking": False,
            "qps_used_for_candidate_score": False,
            "qps_used_for_gradient": False,
            "source_algorithm_used_as_model_feature": False,
            "training_source_accept_reject_used_as_runtime_candidates": False,
            "training_source_parameters_used_as_runtime_candidates": False,
            "training_source_plan_fingerprints_used_as_runtime_candidates": False,
            "training_source_rankings_used_as_runtime_candidates": False,
        },
    }
    validate_paper_abc_runtime_model(paper_model)
    paper_config = {
        "algorithm_c": {
            "offline_resource_training_audit": {
                "contract": "algorithm_c_abc_resource_training_c_holdout_audit_v1",
                "training_sources": ["algorithm_a", "algorithm_b", "algorithm_c"],
                "qps_used": False,
            },
            "workload_contract": {
                "training_runtime_boundary": {
                    "offline_abc_resource_training_only": True,
                }
            },
        }
    }
    assert paper_abc_runtime_config_violations(paper_config) == []
    bad_paper_config = copy.deepcopy(paper_config)
    bad_paper_config["algorithm_c"]["runtime_candidate"] = "algorithm_b"
    assert paper_abc_runtime_config_violations(bad_paper_config) == [
        "$.algorithm_c.runtime_candidate"
    ]
    bad_paper_model = copy.deepcopy(paper_model)
    bad_paper_model["training_boundary"]["qps_used_as_model_feature_or_target"] = True
    try:
        validate_paper_abc_runtime_model(bad_paper_model)
    except RuntimeError:
        pass
    else:
        raise AssertionError("paper ABC model using QPS passed the runtime firewall")

    source = inspect.getsource(BudgetedLaneController.record_adaptive_batch).lower()
    assert "candidate_score" not in source
    assert "algorithm_a" not in source and "algorithm_b" not in source
    for inner_generator in (
        BudgetedLaneController.generate_rankings,
        BudgetedLaneController.generate_global_plan_coverage_rankings,
        BudgetedLaneController.generate_dynamic_numeric_refinement_rankings,
    ):
        generator_source = inspect.getsource(inner_generator).lower()
        assert "db_qps" not in generator_source
        assert "xp_qps" not in generator_source
        assert "algorithm_a" not in generator_source
        assert "algorithm_b" not in generator_source
    assert "resource_estimation_factory" in inspect.getsource(
        BudgetedLaneController.generate_rankings
    )
    assert "calculate_utility_score" in inspect.getsource(
        BudgetedLaneController.generate_global_plan_coverage_rankings
    )
    decision_source = inspect.getsource(adaptive_search_decision).lower()
    assert "db_qps" not in decision_source and "xp_qps" not in decision_source
    assert "algorithm_a" not in decision_source and "algorithm_b" not in decision_source
    assert "consecutive_rejected_batches" in decision_source
    run_source = inspect.getsource(BudgetedLaneController.run)
    assert "technical_incomplete_replay_batch" in run_source
    assert "frontier_diversification" in run_source
    assert "frontier_numeric_refinement" in run_source
    assert "outer_near_gate_confirmation" in run_source
    assert "self.effective_outer_round_limit()" in run_source
    assert "min(self.max_rounds, self.effective_outer_round_limit())" in run_source
    assert "self.adaptive_policy is not None or" not in run_source
    print("PASS: Algorithm C adaptive controller evidence", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
