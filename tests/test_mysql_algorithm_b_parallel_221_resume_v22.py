from scripts import (
    run_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v22
    as subject,
)


def test_v22_contract_binds_online_resource_model_correction():
    contract = subject.validate_contract()
    update = contract["online_resource_model_update"]

    assert contract["release_version"] == "v22"
    assert contract["candidate_batches"] == [[1, 2], [3, 4], [5]]
    assert contract["candidate_parallelism"] == 2
    assert update["effective_from_outer"] == 11
    assert update["accepted_and_rejected_valid_measurements_used"] is True
    assert update["qps_used_as_model_input_feature"] is False
    assert update["qps_used_for_resource_target"] is False
    assert update["qps_used_for_resource_prediction_gradient_or_score"] is False
    assert update["inner_candidate_score_modified"] is False


def test_v22_keeps_algorithm_c_independent_and_historical_credit_immutable():
    document = subject.state_document_v22("test_v22_document")
    update = document["online_resource_model_update_release"]

    assert document["independence_contract"]["algorithm_c_checkpoint_read"] is False
    assert document["independence_contract"]["algorithm_c_measurement_read"] is False
    assert document["technical_continuation"]["accepted_round_credit_changed"] is False
    assert update["algorithm_c_state_read"] is False
    assert update["effective_from_outer"] == 11


def test_historical_top5_is_restored_from_sealed_source_without_recomputation():
    restored = subject.rank_scene_resume(
        round_index=7,
        scene="job_memory",
    )
    source = subject._regular(
        subject.SOURCE / "outer_007/predicted_rankings.json"
    )

    assert restored["selected_top5"] == source["selected_top_k"]["job_memory"]
    assert restored["historical_ranking_restored_verified"] is True
    assert restored["historical_ranking_source_sha256"] == subject.alg.sha(
        subject.SOURCE / "outer_007/predicted_rankings.json"
    )
