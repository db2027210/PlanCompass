from __future__ import annotations

from scripts import (
    run_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v20
    as subject,
)


def test_v20_source_is_clean_post_outer6_before_outer7_replay():
    state = subject.validate_source()
    assert state["stage"] == "ALGORITHM_B_OUTER_007_ESTIMATING"
    assert state["outer_attempt"] == 6
    assert state["accepted_rounds"] == {
        "job_memory": 1,
        "tpch_sf4_memory": 0,
        "hybench_xp_cpu": 1,
        "hybench_xp_io": 1,
    }
    assert len(subject.REUSED_BATCHES) == 30
    assert all(key in subject.REUSED_BATCHES for key in [(1, 1), (6, 5)])


def test_v20_clean_boundary_drain_covers_all_relational_workers():
    drain = subject._regular(subject.CLEAN_BOUNDARY_DRAIN)
    assert drain["all_workers_zero_sessions_after"] is True
    assert drain["all_tpch_revenue0_views_absent_after"] is True
    assert {row["worker_id"] for row in drain["workers"]} == {
        "job_w1", "job_w2", "tpch_w1", "tpch_w2",
    }


def test_v20_batched_q1_q22_validation_is_byte_identical_and_faster():
    validation = subject.validate_batched_plan_capture()
    assert validation["query_count"] == 22
    assert validation["byte_identical_plan_payloads"] is True
    assert validation["legacy_client_process_count"] == 22
    assert validation["batched_client_process_count"] == 1
    assert validation["speedup"] > 1.0


def test_v20_keeps_formal_timeout_and_convergence_contract():
    contract = subject.validate_contract()
    assert contract["workload_contract"]["maximum_candidate_seconds_per_scene"] == 900
    assert contract["convergence"]["accepted_shrinks_required_per_scene"] == 10
    assert contract["convergence"]["maximum_interval_ratio"] == 1.01
    assert contract["convergence"][
        "required_consecutive_stable_accepted_rounds_per_scene"
    ] == 2
    assert contract["tpch_plan_ranking"]["candidate_hard_pruning"] is False
    assert contract["tpch_plan_ranking"][
        "qps_used_for_resource_prediction_or_score"
    ] is False
