from scripts import (
    run_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v23
    as subject,
)


def test_v23_contract_binds_outer11_and_tpch_global_local_top5():
    contract = subject.validate_contract()
    continuation = contract["continuation"]
    plan = contract["tpch_plan_ranking"]

    assert contract["release_version"] == "v23"
    assert contract["candidate_batches"] == [[1, 2], [3, 4], [5]]
    assert continuation["completed_outer_decisions_reconstructed"] == list(
        range(1, 12)
    )
    assert continuation["source_partial_outer12_measurement_credit"] is False
    assert plan["candidate_pool"] == (
        "global_resource_grid_plus_incumbent_local_probes"
    )
    assert plan["top5_global_quota"] == 3
    assert plan["top5_incumbent_local_quota"] == 2
    assert plan["qps_used_for_probe_generation_or_ranking"] is False
    assert plan["candidate_hard_pruning"] is False


def test_v23_source_has_outer11_credit_and_clean_eight_worker_drain():
    state = subject.validate_source()

    assert state["outer_attempt"] == 11
    assert state["accepted_rounds"] == {
        "job_memory": 4,
        "tpch_sf4_memory": 2,
        "hybench_xp_cpu": 3,
        "hybench_xp_io": 3,
    }
    drain = subject.predecessor._regular(subject.CLEAN_BOUNDARY_DRAIN)
    assert drain["all_workers_zero_sessions_after"] is True
    assert len(drain["workers"]) == 8


def test_v23_state_keeps_qps_out_and_partial_outer12_out_of_credit():
    document = subject.state_document_v23("test_v23_document")
    release = document["tpch_full_suite_plan_release"]
    continuation = document["technical_continuation"]

    assert release["top5_global_quota"] == 3
    assert release["top5_incumbent_local_quota"] == 2
    assert release["qps_used_for_probe_generation_or_ranking"] is False
    assert release["inner_candidate_score_modified"] is False
    assert continuation["source_partial_outer12_measurement_credit"] is False
    assert continuation["accepted_round_credit_changed"] is False
