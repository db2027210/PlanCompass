from __future__ import annotations

import pytest

from scripts import mysql_algorithm_b_all_scene_incumbent_plan_reranker_v5 as subject


def test_plan_score_matches_algorithm_c_incumbent_relative_formula():
    positive = subject.incumbent_relative_plan_score(
        target="memory",
        incumbent_resources={"cpu": 0.4, "memory": 0.9, "io": 0.2},
        predicted_resources={"cpu": 0.5, "memory": 0.8, "io": 0.3},
        confidence=0.5,
    )
    # raw = (0.9 - 0.8) + .25 * ((.5 - .4) + (.3 - .2)) = .15
    assert positive["raw_score"] == pytest.approx(0.15)
    assert positive["score"] == pytest.approx(0.075)

    negative = subject.incumbent_relative_plan_score(
        target="memory",
        incumbent_resources={"cpu": 0.4, "memory": 0.9, "io": 0.2},
        predicted_resources={"cpu": 0.4, "memory": 1.0, "io": 0.2},
        confidence=0.5,
    )
    assert negative["raw_score"] == pytest.approx(-0.1)
    assert negative["score"] == pytest.approx(-0.2)


def test_local_probes_are_scene_named_and_change_one_coordinate():
    rows = subject.local_probe_params(
        scene="job_memory",
        params={"a": 4.0, "b": 20.0},
        ranges={
            "a": {"lower": 0.0, "upper": 8.0},
            "b": {"lower": 10.0, "upper": 30.0},
        },
        active=["a", "b"],
    )
    assert len(rows) == 8
    assert all(row["subset_id"].startswith("job_memory_incumbent_local_") for row in rows)
    for row in rows:
        changed = [
            name for name, value in row["optimized_params"].items()
            if value != {"a": 4.0, "b": 20.0}[name]
        ]
        assert changed == row["active_subset"]


def _row(index: int, *, local: bool, score: float) -> dict:
    return {
        "rank": index,
        "experience_rank_before_full_plan": index,
        "optimized_params": {"io_block_read_cost": float(index)},
        "incumbent_local_trust_region_probe": local,
        "soft_negative_evidence_count": 0,
        "incumbent_relative_plan_resource_score": score,
    }


def test_outer_rerank_keeps_three_global_two_local_by_common_plan_score():
    rows = [
        *[_row(index, local=False, score=10.0 - index) for index in range(1, 7)],
        *[_row(index, local=True, score=1.0 / index) for index in range(101, 105)],
    ]
    selected = subject.select_global_and_local_top5(rows)

    assert len(selected) == 5
    assert sum(row["incumbent_local_trust_region_probe"] for row in selected) == 2
    assert [row["rank"] for row in selected] == [1, 2, 3, 4, 5]
    assert all(row["global_local_top5_quota"]["qps_used"] is False for row in selected)
    assert all(
        row["global_local_top5_quota"]["candidate_hard_pruning"] is False
        for row in selected
    )
