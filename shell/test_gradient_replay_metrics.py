#!/usr/bin/env python3
"""Regression tests for strict Top-5 replay prediction metrics."""

from __future__ import annotations

import math
from pathlib import Path
import sys
from typing import Dict, List


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gradient_replay_metrics import build_top5_prediction_metrics  # noqa: E402


CAPABILITY = {"cpu": 10.0, "memory": 20.0, "io": 40.0}


def rows(actual_scores: List[float]) -> List[Dict]:
    result = []
    for rank, actual_score in enumerate(actual_scores, 1):
        utilization = {
            "cpu": 0.30 + rank * 0.01,
            "memory": 0.40 + rank * 0.01,
            "io": 0.80 + rank * 0.01,
        }
        predicted = {
            name: utilization[name] * CAPABILITY[name]
            for name in CAPABILITY
        }
        result.append(
            {
                "rank": rank,
                "predicted_score": actual_score,
                "actual_score": actual_score,
                "predicted_resource": predicted,
                "actual_utilization": utilization,
                "algorithmic_gate_rejection": False,
                "incumbent_update_eligible": True,
            }
        )
    return result


def test_perfect_and_reverse_rankings() -> None:
    perfect = build_top5_prediction_metrics(
        rows([5.0, 4.0, 3.0, 2.0, 1.0]),
        CAPABILITY,
    )
    assert perfect["complete_top5"]
    assert perfect["score_mae"] == 0.0
    assert all(
        math.isclose(perfect["resource_mae_pp"][name], 0.0, abs_tol=1e-12)
        for name in CAPABILITY
    )
    assert perfect["actual_best_predicted_rank"] == 1
    assert math.isclose(perfect["spearman"]["value"], 1.0)

    reverse = build_top5_prediction_metrics(
        rows([1.0, 2.0, 3.0, 4.0, 5.0]),
        CAPABILITY,
    )
    assert reverse["actual_best_predicted_rank"] == 5
    assert math.isclose(reverse["spearman"]["value"], -1.0)


def test_resource_score_errors_and_ties() -> None:
    replay_rows = rows([5.0, 4.0, 4.0, 2.0, 1.0])
    replay_rows[0]["predicted_score"] = 5.5
    replay_rows[0]["predicted_resource"]["cpu"] += 1.0
    metrics = build_top5_prediction_metrics(replay_rows, CAPABILITY)
    assert math.isclose(metrics["score_mae"], 0.1)
    assert math.isclose(metrics["resource_mae_pp"]["cpu"], 2.0)
    assert metrics["actual_best_predicted_rank_ties"] == [1]
    assert metrics["spearman"]["defined"]

    all_tied = build_top5_prediction_metrics(
        rows([1.0, 1.0, 1.0, 1.0, 1.0]),
        CAPABILITY,
    )
    assert not all_tied["spearman"]["defined"]
    assert (
        all_tied["spearman"]["reason"]
        == "actual_scores_have_zero_rank_variance"
    )
    assert all_tied["actual_best_predicted_rank_ties"] == [1, 2, 3, 4, 5]


def test_incomplete_and_invalid_inputs() -> None:
    incomplete = build_top5_prediction_metrics(
        rows([4.0, 3.0, 2.0, 1.0]),
        CAPABILITY,
    )
    assert incomplete["sample_count"] == 4
    assert not incomplete["complete_top5"]

    duplicate = rows([2.0, 1.0])
    duplicate[1]["rank"] = 1
    try:
        build_top5_prediction_metrics(duplicate, CAPABILITY)
    except ValueError as exc:
        assert "duplicate" in str(exc)
    else:
        raise AssertionError("duplicate ranks must fail closed")

    try:
        build_top5_prediction_metrics(rows([1.0]), {**CAPABILITY, "io": 0.0})
    except ValueError as exc:
        assert "positive" in str(exc)
    else:
        raise AssertionError("zero capability must fail closed")


def main() -> None:
    test_perfect_and_reverse_rankings()
    test_resource_score_errors_and_ties()
    test_incomplete_and_invalid_inputs()
    print("gradient replay metrics tests passed", flush=True)


if __name__ == "__main__":
    main()
