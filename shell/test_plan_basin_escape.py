#!/usr/bin/env python3
"""Focused tests for deterministic plan-basin escape pure logic."""

from __future__ import annotations

import copy
import math
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from plan_basin_escape import (  # noqa: E402
    ESCAPE_BATCH_NAMES,
    PlanBasinEscapeConfig,
    PlanBasinEscapeState,
    advance_escape_state,
    build_escape_batches,
    distinct_fingerprint_count,
    evaluate_escape_batch,
    parse_plan_basin_escape_config,
    round_robin_fingerprint_clusters,
    should_enter_escape,
    state_as_dict,
    state_from_dict,
)

ACTIVE = ("a", "b", "c")
INCUMBENT = {"a": 10.0, "b": 5.0, "c": 2.0, "untuned": 7.0}
RANGES = {
    "a": {"min_value": 1.0, "max_value": 100.0},
    "b": {"min_value": 0.5, "max_value": 50.0},
    "c": {"min_value": 0.2, "max_value": 20.0},
}
LOCAL = [
    {"rank": 1, "optimized_params": {"a": 12.0, "b": 4.0, "c": 2.2, "untuned": 7.0}},
    {"rank": 2, "optimized_params": {"a": 8.0, "b": 6.0, "c": 1.8, "untuned": 7.0}},
]


def _enabled(**overrides):
    return parse_plan_basin_escape_config({"enabled": True, **overrides})


def _all_params(batches):
    for batch in batches:
        for candidate in batch:
            yield candidate["optimized_params"]


def test_default_disabled_is_transparent() -> None:
    original = copy.deepcopy(LOCAL)
    batches = build_escape_batches(
        incumbent_params=INCUMBENT,
        local_candidates=LOCAL,
        param_ranges=RANGES,
        active_params=ACTIVE,
    )
    assert batches == [original]
    assert LOCAL == original
    assert not should_enter_escape(
        candidate_space_exhausted=True,
        distinct_plan_count=0,
        config=PlanBasinEscapeConfig(),
    )


def test_five_batches_are_deterministic_bounded_and_unique() -> None:
    kwargs = {
        "incumbent_params": INCUMBENT,
        "local_candidates": LOCAL,
        "param_ranges": RANGES,
        "active_params": ACTIVE,
        "config": _enabled(seed=41, stratified_probe_count=9),
    }
    first = build_escape_batches(**kwargs)
    assert first == build_escape_batches(**kwargs)
    assert len(first) == len(ESCAPE_BATCH_NAMES) == 5
    assert all(first)
    signatures = set()
    for params in _all_params(first):
        assert params["untuned"] == INCUMBENT["untuned"]
        for name in ACTIVE:
            assert RANGES[name]["min_value"] <= params[name] <= RANGES[name]["max_value"]
        signature = tuple(float(params[name]).hex() for name in ACTIVE)
        assert signature not in signatures
        signatures.add(signature)
        assert signature != tuple(float(INCUMBENT[name]).hex() for name in ACTIVE)
    local_a = LOCAL[0]["optimized_params"]
    expanded_a = first[1][0]["optimized_params"]
    for name in ACTIVE:
        local_delta = math.log(local_a[name]) - math.log(INCUMBENT[name])
        expanded_delta = math.log(expanded_a[name]) - math.log(INCUMBENT[name])
        assert abs(expanded_delta - 2.0 * local_delta) < 1e-12


def test_seen_and_projection_deduplicate() -> None:
    batches = build_escape_batches(
        incumbent_params=INCUMBENT,
        local_candidates=LOCAL + [copy.deepcopy(LOCAL[1])],
        param_ranges=RANGES,
        active_params=ACTIVE,
        seen_candidates=[LOCAL[0]],
        config=_enabled(),
    )
    assert [candidate["rank"] for candidate in batches[0]] == [2]
    signatures = [
        tuple(float(params[name]).hex() for name in ACTIVE)
        for params in _all_params(batches)
    ]
    assert len(signatures) == len(set(signatures))


def test_zero_lower_bound_uses_normalized_linear_coordinate() -> None:
    incumbent = {"parallel_setup_cost": 1000.0, "seq_page_cost": 1.0}
    ranges = {
        "parallel_setup_cost": {"min_value": 0.0, "max_value": 10000.0},
        "seq_page_cost": {"min_value": 0.1, "max_value": 100.0},
    }
    local = [{
        "optimized_params": {
            "parallel_setup_cost": 1500.0,
            "seq_page_cost": 2.0,
        }
    }]
    batches = build_escape_batches(
        incumbent_params=incumbent,
        local_candidates=local,
        param_ranges=ranges,
        active_params=("parallel_setup_cost", "seq_page_cost"),
        config=_enabled(),
    )
    assert len(batches) == 5
    expanded = batches[1][0]["optimized_params"]
    assert abs(expanded["parallel_setup_cost"] - 2000.0) < 1e-9
    assert abs(math.log(expanded["seq_page_cost"]) - (
        math.log(incumbent["seq_page_cost"])
        + 2.0 * (
            math.log(local[0]["optimized_params"]["seq_page_cost"])
            - math.log(incumbent["seq_page_cost"])
        )
    )) < 1e-12
    metadata = batches[1][0]["plan_basin_escape"]
    assert metadata["coordinate_space"] == "normalized_log_linear"
    assert metadata["parameter_transforms"] == {
        "parallel_setup_cost": "linear",
        "seq_page_cost": "log",
    }


def test_state_machine_shrinks_only_on_real_acceptance() -> None:
    config = _enabled()
    state = PlanBasinEscapeState(accepted_shrinks=4)
    for expected_index in range(1, 5):
        state = advance_escape_state(state, outcome="rejected", config=config)
        assert state.batch_index == expected_index
        assert state.accepted_shrinks == 4
        assert not state.basin_exhausted
    state = advance_escape_state(state, outcome="rejected", config=config)
    assert state.basin_exhausted and state.status == "basin_exhausted"
    assert state.accepted_shrinks == 4
    accepted = advance_escape_state(state, outcome="accepted", config=config)
    assert accepted.batch_index == 0
    assert accepted.accepted_shrinks == 5
    assert not accepted.basin_exhausted


def test_state_selects_one_escape_batch_at_a_time() -> None:
    config = _enabled()
    batches = build_escape_batches(
        incumbent_params=INCUMBENT,
        local_candidates=LOCAL,
        param_ranges=RANGES,
        active_params=ACTIVE,
        config=config,
    )
    state = advance_escape_state(
        PlanBasinEscapeState(),
        outcome="single_plan",
        config=config,
    )
    selected = batches[state.batch_index]
    assert state.batch_index == 1
    assert selected
    assert {
        candidate["plan_basin_escape"]["batch_index"]
        for candidate in selected
    } == {1}
    assert not any(
        candidate["plan_basin_escape"]["batch_index"] > 1
        for candidate in selected
    )


def test_single_plan_trigger_and_multi_plan_round_robin_quota() -> None:
    config = _enabled(min_distinct_plans=2)
    assert should_enter_escape(
        candidate_space_exhausted=False,
        distinct_plan_count=1,
        config=config,
    )
    assert not should_enter_escape(
        candidate_space_exhausted=False,
        distinct_plan_count=2,
        config=config,
    )
    candidates = [
        {"rank": 1, "workload_plan_fingerprint": "A"},
        {"rank": 2, "workload_plan_fingerprint": "A"},
        {"rank": 3, "workload_plan_fingerprint": "A"},
        {"rank": 4, "workload_plan_fingerprint": "B"},
        {"rank": 5, "workload_plan_fingerprint": "B"},
        {"rank": 6, "workload_plan_fingerprint": "C"},
    ]
    ordered = round_robin_fingerprint_clusters(candidates)
    assert [item["rank"] for item in ordered] == [1, 4, 6, 2, 5, 3]
    limited = round_robin_fingerprint_clusters(candidates, limit=4)
    assert [item["rank"] for item in limited] == [1, 4, 6, 2]
    assert distinct_fingerprint_count(candidates) == 3


def test_missing_and_similarity_only_are_not_exact_clusters() -> None:
    config = _enabled(min_distinct_plans=2)
    all_missing = [{"rank": 1}, {"rank": 2}]
    exact_plus_missing = [
        {"rank": 1, "workload_plan_fingerprint": "exact-A"},
        {"rank": 2},
        {"rank": 3},
    ]
    similarity_only = [
        {"rank": 1, "plan_similarity_cluster_id": "similar-A"},
        {"rank": 2, "plan_similarity_cluster_id": "similar-B"},
    ]
    assert distinct_fingerprint_count(all_missing) == 0
    assert distinct_fingerprint_count(exact_plus_missing) == 1
    assert distinct_fingerprint_count(similarity_only) == 0
    for rows in (all_missing, exact_plus_missing, similarity_only):
        assert should_enter_escape(
            candidate_space_exhausted=False,
            distinct_plan_count=distinct_fingerprint_count(rows),
            config=config,
        )
    explicitly_exact_similarity = [
        {
            "plan_similarity_cluster_id": "exact-A",
            "plan_similarity_cluster_kind": "exact",
        },
        {
            "plan_similarity_cluster_id": "exact-B",
            "plan_similarity_cluster_exact": True,
        },
    ]
    assert distinct_fingerprint_count(explicitly_exact_similarity) == 2


def test_missing_fingerprints_do_not_consume_exact_plan_quota() -> None:
    candidates = [
        {"rank": 1, "workload_plan_fingerprint": "A"},
        {"rank": 2, "workload_plan_fingerprint": "A"},
        {"rank": 3, "workload_plan_fingerprint": "B"},
        {"rank": 4},
        {"rank": 5},
    ]
    ordered = round_robin_fingerprint_clusters(candidates)
    assert [item["rank"] for item in ordered] == [1, 3, 2, 4, 5]
    limited = round_robin_fingerprint_clusters(candidates, limit=3)
    assert [item["rank"] for item in limited] == [1, 3, 2]
    all_missing = [{"rank": 6}, {"rank": 7}]
    assert round_robin_fingerprint_clusters(all_missing) == all_missing


def test_state_round_trip_is_strict_and_auditable() -> None:
    state = PlanBasinEscapeState(
        batch_index=3,
        accepted_shrinks=4,
        status="reverse_orthogonal",
    )
    payload = state_as_dict(state)
    assert payload["batch_name"] == "reverse_orthogonal"
    assert state_from_dict(payload) == state
    try:
        state_from_dict({"batch_index": 5})
    except ValueError as exc:
        assert "out of range" in str(exc)
    else:
        raise AssertionError("invalid state was accepted")


def test_escape_estimation_fails_closed_and_ranks_exact_plans() -> None:
    candidates = [
        {
            "optimized_params": {"a": 1.0},
            "plan_basin_escape": {"parameter_signature_sha256": "b"},
        },
        {
            "optimized_params": {"a": 2.0},
            "plan_basin_escape": {"parameter_signature_sha256": "a"},
        },
        {
            "optimized_params": {"a": 3.0},
            "plan_basin_escape": {"parameter_signature_sha256": "c"},
        },
    ]

    def estimate(params):
        value = params["a"]
        metadata = {} if value == 3.0 else {"workload_plan_fingerprint": f"p{value}"}
        return {"cpu": value, "memory": 1.0, "io": 1.0}, None, metadata

    admitted, audit = evaluate_escape_batch(
        candidates,
        estimate_candidate=estimate,
        score_candidate=lambda resource: resource["cpu"],
    )
    assert [item["optimized_params"]["a"] for item in admitted] == [2.0, 1.0]
    assert [item["rank"] for item in admitted] == [1, 2]
    assert [item["workload_plan_fingerprint"] for item in admitted] == ["p2.0", "p1.0"]
    assert audit["admitted_candidate_count"] == 2
    assert audit["failed_closed_candidate_count"] == 1
    assert audit["missing_plan_policy"] == "fail_closed_no_cluster"


def main() -> int:
    tests = [
        test_default_disabled_is_transparent,
        test_five_batches_are_deterministic_bounded_and_unique,
        test_seen_and_projection_deduplicate,
        test_zero_lower_bound_uses_normalized_linear_coordinate,
        test_state_machine_shrinks_only_on_real_acceptance,
        test_state_selects_one_escape_batch_at_a_time,
        test_single_plan_trigger_and_multi_plan_round_robin_quota,
        test_missing_and_similarity_only_are_not_exact_clusters,
        test_missing_fingerprints_do_not_consume_exact_plan_quota,
        test_state_round_trip_is_strict_and_auditable,
        test_escape_estimation_fails_closed_and_ranks_exact_plans,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}", flush=True)
    print(f"PASS {len(tests)} focused plan-basin escape tests", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
