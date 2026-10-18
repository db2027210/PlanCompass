from __future__ import annotations

import copy

from scripts import mysql_algorithm_b_pg_outer_policy_v31 as policy
from scripts import run_mysql_four_scene_algorithm_b_pg_outer_v31 as controller


P = "io_block_read_cost"
Q = "memory_block_read_cost"


def _candidate(value: float, *, rank: int, plan: str, active=(P,)) -> dict:
    return {
        "rank": rank,
        "optimized_params": {P: value, Q: 0.25},
        "active_subset": list(active),
        "resource_model": {"candidate_plan_sha256": plan * 64},
    }


def _measurement(candidate: dict, *, worker: str, tier: str = "strict") -> dict:
    if tier == "strict":
        resources = {"cpu": 0.51, "memory": 0.60, "io": 0.31}
    elif tier == "secondary":
        resources = {"cpu": 0.49, "memory": 0.60, "io": 0.29}
    else:
        resources = {"cpu": 0.49, "memory": 0.72, "io": 0.29}
    return {
        "worker_id": worker,
        "candidate_ranking": copy.deepcopy(candidate),
        "db_qps": 110.0,
        "p95_ms": 10.0,
        "p99_ms": 20.0,
        "resource_means": resources,
        "hard_gate_passed": True,
        "coverage_complete": True,
    }


def _baseline() -> dict:
    return {
        "db_qps": 100.0,
        "p95_ms": 10.0,
        "p99_ms": 20.0,
        "resource_means": {"cpu": 0.50, "memory": 0.70, "io": 0.30},
    }


def test_partial_top5_and_pg_replay_waves_do_not_pad_duplicate_plans() -> None:
    rows = [
        _candidate(1.1, rank=1, plan="a"),
        _candidate(1.2, rank=2, plan="a"),
        _candidate(1.3, rank=3, plan="b"),
    ]
    selected, audit = policy.admit_partial_topk(
        rows, incumbent_params={P: 1.0, Q: 0.25},
    )
    assert len(selected) == 2
    assert audit["partial_top_k"] is True
    assert audit["excluded"]["duplicate_full_plan"] == 1
    assert [len(batch) for batch in policy.replay_batches(selected)] == [2]

    five = [
        _candidate(1.0 + index / 10.0, rank=index, plan=chr(96 + index))
        for index in range(1, 6)
    ]
    assert [len(batch) for batch in policy.replay_batches(five)] == [2, 2, 1]


def test_same_batch_selection_prefers_pg_tier_over_predicted_rank() -> None:
    fallback = _measurement(
        _candidate(1.1, rank=1, plan="a"), worker="w1", tier="fallback",
    )
    strict = _measurement(
        _candidate(1.2, rank=2, plan="b"), worker="w2", tier="strict",
    )
    chosen, evaluations = policy.evaluate_batch(
        [fallback, strict], _baseline(), target="memory",
    )
    assert len(evaluations) == 2
    assert chosen is not None
    assert chosen["decision"]["acceptance_tier"] == "strict"
    assert chosen["row"]["candidate_ranking"]["rank"] == 2


def test_subset_shrink_excludes_unchanged_and_undeclared_dimensions() -> None:
    subset = policy.moved_shrink_subset(
        incumbent_params={P: 1.0, Q: 0.25},
        candidate_ranking=_candidate(1.2, rank=1, plan="a", active=(P,)),
        active_params=[P, Q],
    )
    assert subset == [P]


class FakeAdapter:
    def __init__(self) -> None:
        self.batches: list[list[int]] = []
        self.restores: list[str] = []

    def generate_rankings(self, _scene, state, round_index):
        # One accepted point per round is sufficient to exercise accepted=10.
        return [_candidate(
            float(state["incumbent_params"][P]) + 0.01,
            rank=1,
            plan=chr(96 + round_index),
        )]

    def cached_valid_rows(self, *_args):
        return []

    def replay_batch(self, _scene, state, candidates, _round, _batch):
        self.batches.append([int(row["rank"]) for row in candidates])
        rows = []
        for candidate in candidates:
            row = _measurement(candidate, worker="w1", tier="strict")
            next_accept = int(state["accepted"]) + 1
            row["db_qps"] = 100.0 + next_accept
            row["resource_means"]["memory"] = 0.70 - next_accept * 0.01
            row["resource_means"]["cpu"] = 0.50 + next_accept * 0.001
            rows.append(row)
        return rows

    def expansion_candidates(self, *_args):
        return []

    def restore_clean_baseline(self, _scene, _state, reason):
        self.restores.append(reason)


def test_end_to_end_pg_search_outer_stops_at_tenth_accept_without_stability() -> None:
    adapter = FakeAdapter()
    state = controller.initialize_scene_state(
        scene="job_memory",
        target="memory",
        incumbent_params={P: 1.0, Q: 0.25},
        ranges={
            P: {"lower": 0.5, "upper": 2.0, "transform": "log"},
            Q: {"lower": 0.125, "upper": 0.5, "transform": "log"},
        },
        active_params=[P, Q],
        canonical_baseline=_baseline(),
    )

    result = controller.AlgorithmBPGOuterController(adapter).run_scene(state)

    assert result["status"] == "COMPLETE"
    assert result["accepted"] == 10
    assert result["round_index"] == 10
    assert result["normalized_width"] == 0.5 ** 10
    assert result["ranges"][Q] == state["ranges"][Q]
    assert result["completion_reason"] == "accepted_count_reached_10"
    assert result["post_search_stability_confirmation"] is False
    assert adapter.batches == [[1]] * 10
    assert adapter.restores[-1] == "accepted_target_completion"


def test_contract_is_not_mislabeled_as_formally_launchable() -> None:
    contract = controller.controller_contract()
    assert contract["outer_loop"]["required_acceptance_tier"] == "fallback"
    assert contract["outer_loop"]["accepted_range_shrink"] == (
        "declared_active_subset_intersect_moved_subset"
    )
    assert contract["outer_loop"]["post_search_stability_confirmation"] is False
    assert contract["existing_mysql_c_source_modified"] is False
    assert contract["formal_launch_authorized"] is False
