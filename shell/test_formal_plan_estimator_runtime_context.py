#!/usr/bin/env python3
"""Regression tests for formal plan-estimator runtime-memory anchoring."""

from __future__ import annotations

import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from parameter_optimizer import _resolve_plan_estimator_runtime_context


def test_formal_working_set_wins_over_generic_runtime_average() -> None:
    raw = {"cpu": 0.09, "memory": 124_931_569.37, "io": 2_861.0}
    stats = {
        "formal_pg_resource": {
            "aggregate": {
                "memory_working_set_bytes": 2_685_864_244.92,
            }
        }
    }
    resolved_raw, resolved_stats, memory_bytes, source = (
        _resolve_plan_estimator_runtime_context(
            raw,
            stats,
            require_formal_pg_resource=True,
        )
    )

    assert math.isclose(memory_bytes, 2_685_864_244.92)
    assert math.isclose(resolved_raw["memory"], memory_bytes)
    assert resolved_stats == stats
    assert source.endswith("formal_pg_resource.aggregate.memory_working_set_bytes")
    assert raw["memory"] == 124_931_569.37, "input evidence must not be mutated"


def test_formal_context_fails_closed_without_authoritative_working_set() -> None:
    try:
        _resolve_plan_estimator_runtime_context(
            {"memory": 124_931_569.37},
            {},
            require_formal_pg_resource=True,
        )
    except RuntimeError as exc:
        assert "resource_raw.memory fallback is forbidden" in str(exc)
    else:
        raise AssertionError("formal memory fallback must fail closed")


def test_nonformal_context_preserves_legacy_memory() -> None:
    resolved_raw, _, memory_bytes, source = _resolve_plan_estimator_runtime_context(
        {"memory": 512.0},
        {},
        require_formal_pg_resource=False,
    )
    assert resolved_raw["memory"] == 512.0
    assert memory_bytes == 512.0
    assert source == "resource_raw.memory"


def main() -> int:
    test_formal_working_set_wins_over_generic_runtime_average()
    test_formal_context_fails_closed_without_authoritative_working_set()
    test_nonformal_context_preserves_legacy_memory()
    print("formal plan-estimator runtime context tests passed (3/3)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
