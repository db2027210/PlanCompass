from gradient_replay_metrics import build_top5_prediction_metrics


CAPABILITY = {"cpu": 10.0, "memory": 10.0, "io": 10.0}


def _rows(actual_best_rank):
    rows = []
    for rank in range(1, 6):
        predicted = 4.0 - (rank - 1) * 0.005
        actual = predicted - 0.02
        if rank == actual_best_rank:
            actual = 4.01
        rows.append(
            {
                "rank": rank,
                "predicted_score": predicted,
                "actual_score": actual,
                "predicted_resource": {
                    "cpu": 4.0,
                    "memory": 4.0,
                    "io": 8.7,
                },
                "actual_resource": {
                    "cpu": 4.0,
                    "memory": 4.0,
                    "io": 8.7,
                },
                "incumbent_update_eligible": True,
            }
        )
    return rows


def test_uncertainty_tie_can_pass_when_actual_best_is_top_two():
    metrics = build_top5_prediction_metrics(_rows(2), CAPABILITY)
    assert metrics["uncertainty"]["mode"] == "uncertainty_tie"
    assert metrics["readiness_gate"]["uncertainty_tie_passed"] is True
    assert metrics["readiness_gate"]["passed"] is True


def test_uncertainty_tie_blocks_when_actual_best_is_rank_three():
    metrics = build_top5_prediction_metrics(_rows(3), CAPABILITY)
    assert metrics["readiness_gate"]["uncertainty_tie_passed"] is False
    assert metrics["readiness_gate"]["passed"] is False
