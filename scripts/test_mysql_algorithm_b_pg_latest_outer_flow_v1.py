#!/usr/bin/env python3
"""Static and installation-boundary checks for MySQL Algorithm B v104."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_four_scene_algorithm_b_pg_grid_v30 as inner
from scripts import run_mysql_algorithm_b_four_scene_client_primary_pg_latest_v1 as b


def main() -> int:
    contract = b.validate_contract()
    assert contract["strategy"] == b.strategy_contract()
    assert b.strategy_contract()["only_algorithmic_difference_from_c"] == (
        "inner_candidate_generation"
    )
    assert b.strategy_contract()["coverage_domain"] == (
        "current_formal_interval_only"
    )
    assert b.strategy_contract()["original_domain_reopened_after_shrink"] is False
    assert b.strategy_contract()["strict_higher_qps_acceptance"] is True
    assert b.strategy_contract()["two_rejections_before_incumbent_shrink"] is True
    assert inner.GRID_POINTS == 15
    assert inner.MAX_PASSES == 20
    assert inner.NO_IMPROVEMENT_PATIENCE == 2
    assert (inner.COMBINATION_MIN_SIZE, inner.COMBINATION_MAX_SIZE) == (2, 4)
    assert inner.COMBINATION_MAX_CANDIDATES == 0

    original_outer_main = b.outer.main
    original_predecessor_main = b.predecessor.main
    observed: dict[str, bool] = {}

    def fake_outer_main() -> int:
        observed.update({
            "rank_scene": b.outer._rank_scene is inner.rank_scene,
            "candidate_rankings": (
                b.outer.alg.candidate_rankings is inner.candidate_rankings
            ),
            "merge": (
                b.outer.alg.merge_candidate_rankings
                is inner.merge_candidate_rankings
            ),
            "current_outer_source": Path(b.outer.__file__).name
                == "run_mysql_four_scene_algorithm_c_boundary_resume_v41.py",
        })
        return 0

    b.outer.main = fake_outer_main
    b.predecessor.main = lambda: b.outer.main()
    try:
        assert b.main() == 0
    finally:
        b.outer.main = original_outer_main
        b.predecessor.main = original_predecessor_main
    assert observed and all(observed.values()), observed
    print("PASS MySQL Algorithm B PG-latest shared-outer checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
