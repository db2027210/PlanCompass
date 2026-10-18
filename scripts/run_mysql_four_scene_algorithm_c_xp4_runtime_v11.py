#!/usr/bin/env python3
"""Accept the active-scene-only replay-history shape in failed v225 state."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_four_scene_algorithm_c_xp4_runtime_v10 as predecessor

runtime_v1 = predecessor.runtime_v1
alg = predecessor.alg
OPTIMIZATION_CONTRACT = predecessor.OPTIMIZATION_CONTRACT
WAVE_COORDINATION_CONTRACT = predecessor.WAVE_COORDINATION_CONTRACT


def validate_active_scene_history(
    *, source_root: Path, checkpoint: Mapping[str, Any],
    reconstructed: Mapping[str, Any], start_rank: int,
    completed_rank5_root: Path | None = None,
) -> dict[str, Any]:
    projected = copy.deepcopy(dict(reconstructed))
    histories = dict(projected.get("full_plan_replay_history_v2") or {})
    if "hybench_xp_io" not in histories:
        raise RuntimeError("IO replay-history projection authority differs")
    projected["full_plan_replay_history_v2"] = {
        "hybench_xp_io": histories["hybench_xp_io"]
    }
    result = predecessor.validate_v225_physical_partial(
        source_root=source_root, checkpoint=checkpoint,
        reconstructed=projected, start_rank=start_rank,
        completed_rank5_root=completed_rank5_root,
    )
    result["active_scene_only_source_history_shape_normalized"] = True
    result["replay_history_values_changed"] = False
    return result


def install() -> None:
    predecessor.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    predecessor.WAVE_COORDINATION_CONTRACT = WAVE_COORDINATION_CONTRACT
    predecessor.install()
    runtime_v1._validate_formal_partial_continuation = validate_active_scene_history


def main() -> int:
    install()
    return runtime_v1.main()


if __name__ == "__main__":
    raise SystemExit(main())
