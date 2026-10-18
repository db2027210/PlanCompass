#!/usr/bin/env python3
"""Deterministic self-tests for the TPC-C score-ladder acquisition policy."""

from __future__ import annotations

import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.build_tpcc_round14_robust_regime_top5_v24 import (
    compute_score_ladder,
)


def main() -> int:
    current = compute_score_ladder(
        incumbent_score=4.708709193584568,
        accepted_count=2,
        credible_frontier_score=4.763890853614869,
        observed_noise_floor=0.01,
    )
    expected_gain = (
        4.763890853614869 - 4.708709193584568
    ) / 10.0
    assert current["remaining_accepts"] == 8
    assert math.isclose(
        float(current["target_gain"]),
        expected_gain,
        rel_tol=1e-12,
        abs_tol=1e-12,
    )
    assert float(current["target_score"]) < 4.72
    assert current["manual_candidate_injection"] is False

    exhausted_frontier = compute_score_ladder(
        incumbent_score=4.8,
        accepted_count=9,
        credible_frontier_score=4.7,
        observed_noise_floor=0.02,
    )
    assert exhausted_frontier["remaining_accepts"] == 1
    assert float(exhausted_frontier["frontier_gain"]) == 0.0
    assert float(exhausted_frontier["target_gain"]) == 0.0
    assert float(exhausted_frontier["target_score"]) == 4.8

    first = compute_score_ladder(4.0, 0, 5.0, 0.0)
    second = compute_score_ladder(4.0, 0, 5.0, 0.0)
    assert first == second
    print("TPC-C v24 score-ladder self-test passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
