"""Regression checks for the fresh Algorithm C September control flow."""

from __future__ import annotations

import copy

import pytest

from scripts import mysql_algorithm_c_interval_control_v1 as interval
from scripts import mysql_algorithm_c_pg_outer_policy_v1 as policy
from scripts import mysql_outer_rejection_cycle_v2 as rejection
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v30 as entry
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v46 as runtime


def _row(qps: float, memory: float, cpu: float, *, rank: int) -> dict:
    return {
        "db_qps": qps,
        "resource_means": {"cpu": cpu, "memory": memory, "io": 0.30},
        "hard_gate_passed": True,
        "coverage_complete": True,
        "candidate_ranking": {"rank": rank, "optimized_params": {"x": float(rank)}},
    }


def test_three_tiers_use_strict_last_accepted_qps_and_non_target_safety() -> None:
    baseline = _row(100.0, 0.90, 0.40, rank=0)
    incumbent = _row(110.0, 0.90, 0.40, rank=0)
    strict = _row(111.0, 0.80, 0.50, rank=1)
    secondary = _row(112.0, 0.80, 0.40, rank=2)
    fallback = _row(113.0, 0.90, 0.40, rank=3)
    chosen, decisions = policy.evaluate_batch(
        [fallback, secondary, strict], incumbent,
        canonical_baseline=baseline, target="memory",
    )
    assert chosen is not None and chosen["row"]["candidate_ranking"]["rank"] == 1
    assert {row["decision"]["acceptance_tier"] for row in decisions} == {
        "strict", "secondary", "fallback",
    }
    assert not policy.outer_semantic_decision(
        _row(110.0, 0.70, 0.40, rank=4), incumbent,
        canonical_baseline=baseline, target="memory",
    )["accepted"]
    assert not policy.outer_semantic_decision(
        _row(115.0, 0.70, 0.80, rank=5), incumbent,
        canonical_baseline=baseline, target="memory",
    )["accepted"]


def test_first_miss_expansion_prefers_new_plans_then_new_same_plan_identities() -> None:
    def candidate(x: int, plan: str) -> dict:
        return {"optimized_params": {"x": float(x)},
                "candidate_physical_plan_sha256": plan}

    selected, audit = interval.select_expanded_top5(
        coverage_rankings=[candidate(1, "a"), candidate(2, "a"),
                           candidate(3, "b"), candidate(4, "c")],
        local_rankings=[candidate(5, "d"), candidate(6, "e")],
        incumbent_params={"x": 0.0},
        replayed_signatures={policy.parameter_signature({"x": 3.0})},
        accepted_signatures=set(),
        policy=policy,
        optimizer_params_key=policy.parameter_signature,
    )
    assert [row["optimized_params"]["x"] for row in selected] == [1, 4, 5, 6, 2]
    assert [row["rank"] for row in selected] == [1, 2, 3, 4, 5]
    assert audit["excluded_previously_replayed"] == 1
    assert audit["qps_used_for_generation_or_ranking"] is False


def test_incomplete_or_invalid_batch_never_grants_a_miss() -> None:
    rows = [_row(101.0, 0.80, 0.40, rank=1)]
    interval.require_complete_batch(
        selected_count=1, rows=rows, technical_exclusions=[],
        physical_valid=policy.physical_valid,
    )
    with pytest.raises(RuntimeError, match="incomplete"):
        interval.require_complete_batch(
            selected_count=2, rows=rows, technical_exclusions=[],
            physical_valid=policy.physical_valid,
        )
    with pytest.raises(RuntimeError, match="technically invalid"):
        interval.require_complete_batch(
            selected_count=1, rows=rows,
            technical_exclusions=[{"candidate_ranks": [2]}],
            physical_valid=policy.physical_valid,
        )


def test_second_valid_miss_bisects_incumbent_and_checks_real_width() -> None:
    initial = {"a": {"lower": 1.0, "upper": 16.0},
               "b": {"lower": 1.0, "upper": 16.0}}
    incumbent = {"a": 2.0, "b": 8.0}
    first_ranges, first = rejection.advance_rejection_cycle(
        accepted=False, consecutive_rejections=0, ranges=initial,
        incumbent_params=incumbent, active_params=["a", "b"],
    )
    assert first_ranges == initial
    assert first["consecutive_rejections_after"] == 1
    second_ranges, second = rejection.advance_rejection_cycle(
        accepted=False, consecutive_rejections=1, ranges=first_ranges,
        incumbent_params=incumbent, active_params=["a", "b"],
    )
    assert second["forced_incumbent_shrink"] is True
    assert second["accepted_round_credit"] == 0
    assert interval.require_binary_progress(initial, second_ranges, ["a", "b"])[
        "strictly_shrunk"
    ] == ["a", "b"]
    assert rejection.convergence_document(
        ranges=second_ranges, initial_ranges=initial, active_params=["a", "b"],
    )["normalized_width"] == pytest.approx(0.5)
    bad = copy.deepcopy(initial)
    with pytest.raises(RuntimeError, match="no actual interval progress"):
        interval.require_binary_progress(initial, bad, ["a", "b"])


def test_retained_region_reopens_only_inside_initial_domain() -> None:
    initial = {"a": {"lower": 1.0, "upper": 16.0},
               "b": {"lower": 1.0, "upper": 16.0}}
    narrowed = {"a": {"lower": 4.0, "upper": 8.0},
                "b": {"lower": 1.0, "upper": 16.0}}
    queue = interval.retained_complement_regions(
        initial, narrowed, ["a", "b"], resource_potential=0.25,
    )
    assert {(row["parameter"], row["side"]) for row in queue} == {
        ("a", "lower"), ("a", "upper"),
    }
    expanded, remaining, selected = interval.take_retained_region(
        queue, narrowed, initial, {"a": 6.0, "b": 8.0}, ["a", "b"],
    )
    assert selected is not None
    assert len(remaining) == 1
    assert expanded["a"]["lower"] == 1.0
    assert expanded["a"]["upper"] == 8.0
    assert expanded["b"] == narrowed["b"]


def test_new_entry_binds_new_runtime_without_changing_historical_modules() -> None:
    assert entry.validate_contract()["formal_scope"] == "mysql_only"
    assert entry.predecessor.runtime is entry.old_runtime
    with entry.bind_runtime():
        assert entry.predecessor.runtime is runtime
        assert runtime.rejection_cycle is rejection
        assert runtime.pg_outer is policy
        assert runtime.INDEPENDENT_SCENE_EXECUTION is True
        assert runtime.NO_LEASE_RUNTIME is True
    assert entry.predecessor.runtime is entry.old_runtime
    assert "fallback_sets: dict" not in runtime._source
    assert "interval_control.require_complete_batch" in runtime._source
