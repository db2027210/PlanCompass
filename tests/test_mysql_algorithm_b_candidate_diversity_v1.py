from types import SimpleNamespace

from scripts import mysql_algorithm_b_candidate_diversity_v1 as subject


def _point(value: float, score: float = 1.0):
    return SimpleNamespace(
        params={"x": value},
        estimated_score=score,
        active_subset=("x",),
    )


def test_top5_spans_parameter_interval_instead_of_clustering():
    points = [
        _point(value, 1.0 - index * 0.001)
        for index, value in enumerate((1.0, 1.01, 1.02, 1.03, 1.04, 2.0, 4.0, 8.0, 10.0))
    ]
    selected = subject.select_diverse_points(
        points,
        ranges={"x": {"lower": 1.0, "upper": 10.0}},
        active=["x"],
        top_k=5,
        negative_count=lambda _point: 0,
    )
    values = [point.params["x"] for point in selected]
    assert values[0] == 1.0
    assert 10.0 in values
    assert len(set(values)) == 5


def test_soft_negative_evidence_remains_primary():
    negative_best = _point(1.0, 1.0)
    clean = [_point(float(value), 0.9) for value in (2, 3, 4, 5, 6)]
    selected = subject.select_diverse_points(
        [negative_best, *clean],
        ranges={"x": {"lower": 1.0, "upper": 10.0}},
        active=["x"],
        top_k=5,
        negative_count=lambda point: int(point is negative_best),
    )
    assert negative_best not in selected
