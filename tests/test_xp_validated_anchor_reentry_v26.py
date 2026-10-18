#!/usr/bin/env python3
"""Offline regression checks for bounded clean-anchor XP range reentry."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path


BUNDLE = Path(
    "/tmp/pg_xp_unattended_20260730_1235/"
    "bundle_fix_v26_validated_anchor_reentry"
)
SELECTION = Path(
    "/home/anonymous/program/resource_adaptive/history/"
    "xp_final_algorithm_fresh10_v26_20260731T1935/"
    "batches/cont051/selection.json"
)
REPEAT_SELECTION = Path(
    "/home/anonymous/program/resource_adaptive/history/"
    "xp_final_algorithm_fresh10_v26_20260731T1935/"
    "batches/cont052/selection.json"
)
EXPECTED_CONTRACT = "validated_bounded_clean_anchor_range_reentry_v2"


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"expected object: {path}")
    return value


def main() -> int:
    sys.path.insert(0, str(BUNDLE))
    from build_xp_exact_plan_repeat_selection import (
        validated_range_reentry_source,
    )

    candidates = load(SELECTION)["top5"]
    global_probe = next(
        item
        for item in candidates
        if item.get("candidate_origin")
        == "global_range_exploration_uncertain_probe"
    )
    axis_probe = next(
        item
        for item in candidates
        if item.get("candidate_origin")
        == "safe_frontier_axis_halton_explain_probe"
    )

    global_audit = validated_range_reentry_source(global_probe)
    axis_audit = validated_range_reentry_source(axis_probe)
    assert global_audit is not None
    assert axis_audit is not None
    assert global_audit["contract"] == EXPECTED_CONTRACT
    assert axis_audit["contract"] == EXPECTED_CONTRACT

    repeated_axis_probe = next(
        item
        for item in load(REPEAT_SELECTION)["top5"]
        if item.get("candidate_origin")
        == "safe_frontier_axis_halton_explain_probe"
    )
    repeated_axis_audit = validated_range_reentry_source(repeated_axis_probe)
    assert repeated_axis_audit is not None
    assert repeated_axis_audit["contract"] == EXPECTED_CONTRACT

    corner = copy.deepcopy(axis_probe)
    corner["boundary_corner"] = True
    assert validated_range_reentry_source(corner) is None

    stale_explain = copy.deepcopy(axis_probe)
    stale_explain["safe_frontier_generation"]["fresh_explain"] = False
    assert validated_range_reentry_source(stale_explain) is None

    oversized = copy.deepcopy(axis_probe)
    oversized["safe_frontier_generation"][
        "observed_maximum_relative_parameter_step"
    ] = 0.8000001
    assert validated_range_reentry_source(oversized) is None

    non_axis_mutation = copy.deepcopy(axis_probe)
    non_axis_mutation["candidate_params"]["cpu_tuple_cost"] *= 1.01
    assert validated_range_reentry_source(non_axis_mutation) is None

    state_mutating = copy.deepcopy(axis_probe)
    state_mutating["safe_frontier_generation"][
        "exploration_anchor_updates_accepted_incumbent_range_or_gradient"
    ] = True
    assert validated_range_reentry_source(state_mutating) is None

    invalid_fingerprint = copy.deepcopy(axis_probe)
    invalid_fingerprint["three_level_fingerprint"]["valid"] = False
    assert validated_range_reentry_source(invalid_fingerprint) is None

    unknown_route = copy.deepcopy(axis_probe)
    unknown_route["candidate_origin"] = "manual_candidate"
    assert validated_range_reentry_source(unknown_route) is None

    print("XP bounded clean-anchor range reentry v26 regression passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
