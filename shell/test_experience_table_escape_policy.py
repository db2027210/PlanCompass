#!/usr/bin/env python3

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experience_table_escape_policy import (  # noqa: E402
    exact_parameter_signature,
    generate_escape_batch,
)


INCUMBENT = {"a": 1.0, "b": 1.0}
RANGES = {
    "a": {"min_value": 0.1, "max_value": 10.0},
    "b": {"min_value": 0.1, "max_value": 10.0},
}
TABLE = {
    "a": {"memory": 0.8},
    "b": {"memory": -0.4},
}
BASE = [
    {"optimized_params": {"a": 2.0, "b": 1.0}},
    {"optimized_params": {"a": 1.0, "b": 0.5}},
]


def _batch(index: int):
    return generate_escape_batch(
        batch_index=index,
        incumbent=INCUMBENT,
        parameter_ranges=RANGES,
        sensitivity_table=TABLE,
        target_resource="memory",
        base_candidates=BASE,
        radius=0.1,
    )


def test_reverse_and_expansion_are_distinct_and_bounded() -> None:
    reverse = _batch(2)
    expanded = _batch(3)
    assert reverse and expanded
    signatures = {
        exact_parameter_signature(row["optimized_params"])
        for row in reverse + expanded
    }
    assert len(signatures) == len(reverse) + len(expanded)
    for row in reverse + expanded:
        for name, value in row["optimized_params"].items():
            assert RANGES[name]["min_value"] <= value <= RANGES[name]["max_value"]


def test_endpoint_batch_covers_both_bounds_per_coordinate() -> None:
    endpoints = _batch(4)
    assert len(endpoints) == 4
    values = {
        (name, row["optimized_params"][name])
        for row in endpoints
        for name in INCUMBENT
        if row["optimized_params"][name] != INCUMBENT[name]
    }
    assert values == {
        ("a", 0.1), ("a", 10.0), ("b", 0.1), ("b", 10.0)
    }


def test_orthogonal_batch_changes_only_one_coordinate() -> None:
    orthogonal = _batch(5)
    assert len(orthogonal) == 4
    assert all(
        sum(
            row["optimized_params"][name] != INCUMBENT[name]
            for name in INCUMBENT
        ) == 1
        for row in orthogonal
    )


def test_seen_candidates_are_not_reemitted() -> None:
    first = _batch(5)
    second = generate_escape_batch(
        batch_index=5,
        incumbent=INCUMBENT,
        parameter_ranges=RANGES,
        sensitivity_table=TABLE,
        target_resource="memory",
        base_candidates=BASE,
        radius=0.1,
        seen_signatures={
            exact_parameter_signature(row["optimized_params"])
            for row in first
        },
    )
    assert second == []


def test_local_candidates_are_automatically_excluded() -> None:
    first = generate_escape_batch(
        batch_index=2,
        incumbent=INCUMBENT,
        parameter_ranges=RANGES,
        sensitivity_table=TABLE,
        target_resource="memory",
        base_candidates=[{"optimized_params": {"a": 2.0, "b": 1.0}}],
    )
    symmetric = [
        {"optimized_params": {"a": 2.0, "b": 1.0}},
        {"optimized_params": first[0]["optimized_params"]},
    ]
    reverse = generate_escape_batch(
        batch_index=2,
        incumbent=INCUMBENT,
        parameter_ranges=RANGES,
        sensitivity_table=TABLE,
        target_resource="memory",
        base_candidates=symmetric,
    )
    assert reverse == []


def test_invalid_incumbent_and_base_candidate_fail_closed() -> None:
    for incumbent, base in (
        ({"a": 0.0, "b": 1.0}, BASE),
        (INCUMBENT, [{"optimized_params": {"a": 20.0, "b": 1.0}}]),
    ):
        try:
            generate_escape_batch(
                batch_index=2,
                incumbent=incumbent,
                parameter_ranges=RANGES,
                sensitivity_table=TABLE,
                target_resource="memory",
                base_candidates=base,
            )
        except ValueError as exc:
            assert "finite, positive, and in range" in str(exc)
        else:
            raise AssertionError("invalid log-space input must fail closed")


if __name__ == "__main__":
    test_reverse_and_expansion_are_distinct_and_bounded()
    test_endpoint_batch_covers_both_bounds_per_coordinate()
    test_orthogonal_batch_changes_only_one_coordinate()
    test_seen_candidates_are_not_reemitted()
    test_local_candidates_are_automatically_excluded()
    test_invalid_incumbent_and_base_candidate_fail_closed()
    print("experience-table escape policy tests passed", flush=True)
