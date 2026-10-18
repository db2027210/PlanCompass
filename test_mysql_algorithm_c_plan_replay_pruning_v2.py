from mysql_algorithm_c_plan_replay_pruning import (
    classify_plan, rank_and_prune_candidates, record_outcomes,
)


PLAN = "a" * 64


def evaluation(qps, params, *, accepted=False, reason="qps_below_threshold",
               required_rank=0, satisfied_rank=0):
    return {
        "db_qps": qps, "params": params, "hard_gate_passed": True,
        "coverage_complete": True,
        "three_level_fingerprint": {"l1_plan_tree_sha256": PLAN},
        "candidate_physical_plan_sha256": PLAN,
        "outer_decision": {
            "accepted": accepted, "reason": reason,
            "qps_strictly_increased": qps > 100,
            "required_acceptance_tier_rank": required_rank,
            "satisfied_acceptance_tier_rank": satisfied_rank,
        },
    }


def candidate(score=7.0):
    return {
        "candidate_score": score,
        "resource_model": {"candidate_plan_sha256": PLAN},
    }


def test_one_reject_soft_only_and_candidate_score_unchanged():
    history = record_outcomes([], scene="job_memory", outer_round=1,
                              incumbent_qps=100, evaluations=[evaluation(90, {"x": 1})])
    kept, pruned, _ = rank_and_prune_candidates([candidate()], history)
    assert not pruned and len(kept) == 1
    assert kept[0]["candidate_score"] == 7.0
    assert kept[0]["plan_replay_pruning_v2"]["soft_ranking_penalty"] == 1


def test_two_distinct_rounds_and_points_with_qps_declines_hard_prune_cross_tier():
    history = record_outcomes([], scene="job_memory", outer_round=1,
                              incumbent_qps=100, evaluations=[evaluation(90, {"x": 1})])
    history = record_outcomes(history, scene="job_memory", outer_round=2,
                              incumbent_qps=100, evaluations=[evaluation(88, {"x": 2})])
    kept, pruned, _ = rank_and_prune_candidates([candidate()], history)
    assert not kept and len(pruned) == 1


def test_accepted_counterexample_prohibits_hard_prune():
    history = []
    for outer, qps, point in ((1, 90, 1), (2, 88, 2)):
        history = record_outcomes(history, scene="job_memory", outer_round=outer,
                                  incumbent_qps=100, evaluations=[evaluation(qps, {"x": point})])
    history = record_outcomes(history, scene="job_memory", outer_round=3,
                              incumbent_qps=100, evaluations=[evaluation(101, {"x": 3}, accepted=True)])
    assert classify_plan(PLAN, history)["hard_prune"] is False


def test_same_parameter_point_in_two_rounds_is_not_enough_to_hard_prune():
    history = []
    for outer, qps in ((1, 90), (2, 88)):
        history = record_outcomes(history, scene="job_memory", outer_round=outer,
                                  incumbent_qps=100, evaluations=[evaluation(qps, {"x": 1})])
    classification = classify_plan(PLAN, history)
    assert classification["distinct_decline_rounds"] == 2
    assert classification["distinct_decline_parameter_points"] == 1
    assert classification["hard_prune"] is False


def test_tier_insufficiency_is_not_learnable_and_positive_qps_is_never_pruned():
    history = record_outcomes([], scene="job_memory", outer_round=1,
                              incumbent_qps=100, evaluations=[evaluation(
                                  133.25, {"x": 1}, reason="target_bottleneck_not_strictly_lower",
                                  required_rank=3, satisfied_rank=1)])
    history = record_outcomes(history, scene="job_memory", outer_round=2,
                              incumbent_qps=100, evaluations=[evaluation(
                                  132, {"x": 2}, reason="target_bottleneck_not_strictly_lower",
                                  required_rank=3, satisfied_rank=1)])
    classification = classify_plan(PLAN, history)
    assert classification["learnable_qps_declines"] == 0
    assert classification["qps_nondecline_counterexamples"] == 2
    assert classification["hard_prune"] is False


def test_positive_qps_tier_insufficient_counterexample_blocks_two_declines():
    history = []
    for outer, qps, point, reason in (
        (1, 90, 1, "qps_below_threshold"),
        (2, 88, 2, "qps_below_threshold"),
        (3, 133.25, 3, "target_bottleneck_not_strictly_lower"),
    ):
        history = record_outcomes(history, scene="job_memory", outer_round=outer,
                                  incumbent_qps=100,
                                  evaluations=[evaluation(
                                      qps, {"x": point}, reason=reason,
                                      required_rank=(3 if outer == 3 else 0),
                                      satisfied_rank=(1 if outer == 3 else 0),
                                  )])
    classification = classify_plan(PLAN, history)
    assert classification["learnable_qps_declines"] == 2
    assert classification["qps_nondecline_counterexamples"] == 1
    assert classification["hard_prune"] is False
