from __future__ import annotations

import math

from scripts import mysql_algorithm_b_trust_diverse_plan_reranker_v9 as v9


def _row(
    name: str, score: float, *, local: bool, support: float = 1.0,
    negative: int = 0,
) -> dict:
    return {
        "subset_id": name,
        "active_subset": [name],
        "optimized_params": {"p": float(len(name) + 1)},
        "incumbent_local_trust_region_probe": local,
        "incumbent_relative_plan_resource_score": score,
        "dynamic_support_weight": support,
        "soft_negative_evidence_count": negative,
        "experience_rank_before_full_plan": len(name),
        "candidate_hard_pruned_by_dynamic_support": False,
    }


def test_low_support_globals_are_retained_but_not_forced_into_top5() -> None:
    locals_ = [_row(f"l{i}", 1.0 - i / 10, local=True) for i in range(6)]
    global_rows = [
        _row("g0", 100.0, local=False, support=0.08),
        _row("g1", 90.0, local=False, support=0.04),
    ]
    rows = [*locals_, *global_rows]
    selected = v9.select_trust_aware_diverse_top5(rows)
    assert len(selected) == 5
    assert all(row["incumbent_local_trust_region_probe"] for row in selected)
    assert all(row["candidate_hard_pruned_by_dynamic_support"] is False for row in global_rows)
    assert all(row in rows for row in global_rows)
    assert selected[0]["trust_diverse_top5_policy"]["candidate_hard_pruning"] is False


def test_at_most_one_supported_global_can_use_fifth_slot() -> None:
    locals_ = [_row(f"l{i}", 1.0 - i / 10, local=True) for i in range(6)]
    globals_ = [
        _row("g0", 10.0, local=False, support=0.9),
        _row("g1", 9.0, local=False, support=0.8),
    ]
    selected = v9.select_trust_aware_diverse_top5([*locals_, *globals_])
    selected_globals = [
        row for row in selected
        if row["incumbent_local_trust_region_probe"] is not True
    ]
    assert len(selected_globals) == 1
    assert selected_globals[0]["subset_id"] == "g0"
    assert len({_row_subset(row) for row in selected if row["incumbent_local_trust_region_probe"]}) >= 4


def _row_subset(row: dict) -> tuple[str, ...]:
    return tuple(row["active_subset"])


def test_local_probe_uses_log_distance_and_does_not_touch_other_params() -> None:
    rows = v9.local_probe_params(
        scene="tpch_sf4_memory",
        params={"a": 1.0, "b": 4.0},
        ranges={
            "a": {"lower": 0.25, "upper": 16.0},
            "b": {"lower": 1.0, "upper": 64.0},
        },
        active=["a", "b"],
    )
    assert len(rows) == 8
    a_upper_008 = next(
        row for row in rows
        if row["active_subset"] == ["a"]
        and row["direction"] == "upper"
        and row["normalized_step_fraction"] == 0.08
    )
    assert math.isclose(
        a_upper_008["optimized_params"]["a"],
        math.exp(0.08 * math.log(16.0)), rel_tol=1e-11,
    )
    assert a_upper_008["optimized_params"]["b"] == 4.0
    assert a_upper_008["incumbent_log_trust_region_probe"] is True


def test_negative_evidence_remains_soft_selection_order_only() -> None:
    rows = [_row(f"l{i}", 10.0 - i, local=True) for i in range(6)]
    rows[0]["soft_negative_evidence_count"] = 1
    selected = v9.select_trust_aware_diverse_top5(rows)
    assert rows[0] not in selected
    assert rows[0]["candidate_hard_pruned_by_dynamic_support"] is False
    assert all(row["trust_diverse_top5_policy"]["qps_used"] is False for row in selected)


def test_v29_controller_restores_all_active_binary_shrink_budget() -> None:
    from scripts import (
        run_mysql_four_scene_algorithm_b_trust_diverse_resume_v29 as controller,
    )

    controller.install()
    assert (
        controller.base.SUBSET_ONLY_SHRINK_EFFECTIVE_OUTER
        > controller.base.MAXIMUM_OUTER_ATTEMPTS
    )
    assert controller.base.rank_scene is controller.plan_ranker.rank_scene
    assert controller.base.ACCEPTANCE_AWARE_SELECTION_EFFECTIVE_OUTER == 1
    assert controller.base.ONLINE_RESOURCE_MODEL_UPDATE_EFFECTIVE_OUTER == 1
