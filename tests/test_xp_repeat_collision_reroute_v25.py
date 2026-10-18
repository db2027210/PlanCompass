#!/usr/bin/env python3
"""Regression test for XP exact-repeat global fingerprint collisions."""

from __future__ import annotations

import json
import sys
from pathlib import Path


BUNDLE = Path(
    "/tmp/pg_xp_unattended_20260730_1235/"
    "bundle_fix_v25_repeat_collision_reexplore"
)
CAMPAIGN = Path(
    "/home/anonymous/program/resource_adaptive/history/"
    "xp_final_algorithm_fresh10_v26_20260731T1935"
)
EXPECTED_COLLISION = (
    "three-level-sha256:"
    "803f0b28523780740e8713a28483b98ac2761943f5a96a5adfbae79e71f7da0d"
)


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"expected object: {path}")
    return value


def main() -> int:
    sys.path.insert(0, str(BUNDLE))
    from xp_unattended_adapters import _repeat_replay_collisions

    state = load(CAMPAIGN / "state.json")
    repeat = load(
        CAMPAIGN
        / "batches/cont051/exact_plan_repeat/selection.json"
    )
    collisions = _repeat_replay_collisions(repeat, state)
    assert collisions == [EXPECTED_COLLISION], collisions

    without_collision = dict(state)
    without_collision["replayed_fingerprints"] = [
        identifier
        for identifier in state["replayed_fingerprints"]
        if identifier != EXPECTED_COLLISION
    ]
    assert _repeat_replay_collisions(repeat, without_collision) == []

    duplicate = dict(repeat)
    duplicate["top5"] = list(repeat["top5"]) + [repeat["top5"][3]]
    assert _repeat_replay_collisions(duplicate, state) == [EXPECTED_COLLISION]
    print("XP repeat-collision reroute v25 regression passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
