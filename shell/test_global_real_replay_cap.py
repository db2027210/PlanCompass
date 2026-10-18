#!/usr/bin/env python3
"""Global five-candidate real replay ceiling regression tests."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adaptive_replay_per_batch_runner import run_adaptive_replay_batches  # noqa: E402
from common_outer_loop import cap_real_replay_top_k  # noqa: E402
from double_loop_search_common import OuterLoopConfig  # noqa: E402
from shell.test_iter104_adaptive_replay_per_batch_runner import (  # noqa: E402
    BASELINE,
    CAPABILITY,
    _build_initial_like_candidates,
    _replay_callback,
    _summary_callback_fixed,
)


def main() -> None:
    assert cap_real_replay_top_k(20) == 5
    grid_config = OuterLoopConfig(
        real_validation_top_k=30,
        adaptive_topk_min_k=16,
    )
    assert grid_config.real_validation_top_k == 5
    assert grid_config.adaptive_topk_min_k == 5

    result = run_adaptive_replay_batches(
        candidates=_build_initial_like_candidates(),
        replay_callback=_replay_callback,
        summary_callback=_summary_callback_fixed(),
        target_resource="memory",
        baseline_resource=BASELINE,
        capability=CAPABILITY,
        max_budget=24,
        min_evidence=12,
        batch_size=6,
        max_expansions=2,
    )
    assert len(result.replayed_evaluations) <= 5
    assert len(result.replayed_indices) <= 5
    assert result.expansions_used == 0
    print("global real replay cap tests passed", flush=True)


if __name__ == "__main__":
    main()
