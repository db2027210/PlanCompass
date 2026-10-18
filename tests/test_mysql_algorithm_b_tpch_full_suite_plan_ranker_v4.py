from __future__ import annotations

from scripts import mysql_algorithm_b_tpch_full_suite_plan_ranker_v4 as subject


def test_local_probes_change_exactly_one_coordinate_and_stay_in_range():
    incumbent = {"a": 4.0, "b": 20.0}
    ranges = {
        "a": {"lower": 0.0, "upper": 8.0},
        "b": {"lower": 10.0, "upper": 30.0},
    }

    rows = subject.local_probe_params(incumbent, ranges, ["a", "b"])

    assert len(rows) == 8
    assert len({tuple(sorted(row["optimized_params"].items())) for row in rows}) == 8
    for row in rows:
        changed = [
            name for name in incumbent
            if row["optimized_params"][name] != incumbent[name]
        ]
        assert changed == row["active_subset"]
        name = changed[0]
        assert ranges[name]["lower"] <= row["optimized_params"][name] <= ranges[name]["upper"]
        assert row["normalized_step_fraction"] in subject.LOCAL_FRACTIONS


def _ranking_row(index: int, *, local: bool, score: float) -> dict:
    return {
        "experience_rank_before_full_plan": index,
        "full_suite_plan_resource_score": score,
        "soft_negative_evidence_count": 0,
        "optimized_params": {"io_block_read_cost": float(index)},
        "incumbent_local_trust_region_probe": local,
        "resource_model": {
            "raw_plan_work_ratios": {
                "cpu": 1.0,
                "memory": 1.0,
                "io": 1.0,
            },
        },
    }


def test_top5_keeps_three_global_and_two_local_even_when_local_scores_are_lower():
    global_rows = [
        _ranking_row(index, local=False, score=10.0 - index)
        for index in range(1, 7)
    ]
    local_rows = [
        _ranking_row(index, local=True, score=0.01)
        for index in range(101, 105)
    ]

    selected = subject.select_global_and_local_top_k([
        *global_rows, *local_rows,
    ])

    assert len(selected) == 5
    assert sum(
        row["incumbent_local_trust_region_probe"] is True
        for row in selected
    ) == 2
    assert sum(
        row["incumbent_local_trust_region_probe"] is False
        for row in selected
    ) == 3
    assert [row["rank"] for row in selected] == [1, 2, 3, 4, 5]
    assert all(row["candidate_hard_pruned_by_plan_risk"] is False for row in selected)
    assert all(row["global_local_top5_quota"]["qps_used"] is False for row in selected)
    assert all(
        row["global_local_top5_quota"]["candidate_hard_pruning"] is False
        for row in selected
    )
