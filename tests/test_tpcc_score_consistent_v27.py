from __future__ import annotations

import math
import statistics

from tools import build_tpcc_exact_plan_repeat_validation_v2 as tpcc
from tools.tpcc_score_consistent_representative import (
    score_consistent_representative,
)


def score(resource):
    return float(
        tpcc.SCORE_CALCULATOR.calculate_utility_score(
            resource,
            tpcc.CAPABILITY,
        )
    )


def test_even_history_matches_median_score_not_component_median() -> None:
    resources = [
        {"cpu": 2.041, "io": 24.167, "memory": 2.514},
        {"cpu": 2.042, "io": 34.719, "memory": 2.589},
    ]
    scores = [score(row) for row in resources]
    componentwise = {
        name: statistics.median(row[name] for row in resources)
        for name in tpcc.RESOURCES
    }
    target = statistics.median(scores)
    representative, robust_score, audit = score_consistent_representative(
        scores,
        resources,
        score,
    )
    assert math.isclose(robust_score, target, rel_tol=0.0, abs_tol=1e-15)
    assert math.isclose(
        score(representative),
        target,
        rel_tol=0.0,
        abs_tol=1e-12,
    )
    assert not math.isclose(
        score(componentwise),
        target,
        rel_tol=0.0,
        abs_tol=1e-3,
    )
    assert audit["method"] == "central_observation_segment_bisection_v1"


def test_odd_history_uses_observed_median_score_resource() -> None:
    resources = [
        {"cpu": 2.04, "io": 24.0, "memory": 2.5},
        {"cpu": 2.04, "io": 31.0, "memory": 2.55},
        {"cpu": 2.04, "io": 35.0, "memory": 2.6},
    ]
    scores = [score(row) for row in resources]
    representative, robust_score, audit = score_consistent_representative(
        scores,
        resources,
        score,
    )
    expected_index = sorted(range(3), key=lambda index: scores[index])[1]
    assert representative == resources[expected_index]
    assert robust_score == scores[expected_index]
    assert audit["method"] == "observed_median_score_resource_v1"


def test_even_history_is_deterministic_under_input_permutation() -> None:
    resources = [
        {"cpu": 2.04, "io": 25.0, "memory": 2.4},
        {"cpu": 2.04, "io": 34.0, "memory": 2.7},
    ]
    scores = [score(row) for row in resources]
    forward = score_consistent_representative(scores, resources, score)
    reverse = score_consistent_representative(
        list(reversed(scores)),
        list(reversed(resources)),
        score,
    )
    assert forward == reverse


if __name__ == "__main__":
    test_even_history_matches_median_score_not_component_median()
    test_odd_history_uses_observed_median_score_resource()
    test_even_history_is_deterministic_under_input_permutation()
    print("TPC-C score-consistent v27 regression passed", flush=True)
