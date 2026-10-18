from pathlib import Path
import json

from scripts import (
    run_mysql_four_scene_algorithm_b_independent_lane_failure_isolation_core_v18
    as core,
)


ROOT = Path(__file__).resolve().parents[1]
OUTER5_XP_IO = ROOT / (
    "history/mysql_four_scene_20260809/"
    "v17_formal_parallel221_outer5_scene_independent_resume_20260818_attempt012/"
    "outer_005/hybench_xp_io.decision.json"
)


def _row(rank: int, score: float, accepted: bool) -> dict:
    return {
        "rank": rank,
        "real_resource_score": score,
        "outer_decision": {"accepted": accepted},
    }


def test_outer6_selects_best_resource_score_within_current_tier_eligible_rows():
    ordered = [
        _row(1, 9.0, False),
        _row(4, 8.0, True),
        _row(5, 7.0, True),
    ]
    chosen, eligible, policy = core.choose_outer_candidate(
        ordered, outer_attempt=6,
    )
    assert chosen["rank"] == 4
    assert [row["rank"] for row in eligible] == [4, 5]
    assert policy == "current_tier_eligible_then_resource_score_v1"


def test_outer1_through_5_preserve_historical_score_first_semantics():
    ordered = [_row(1, 9.0, False), _row(4, 8.0, True)]
    chosen, eligible, policy = core.choose_outer_candidate(
        ordered, outer_attempt=5,
    )
    assert chosen["rank"] == 1
    assert [row["rank"] for row in eligible] == [4]
    assert policy == "historical_resource_score_before_outer_gate_v1"


def test_outer6_without_eligible_row_retains_resource_score_best_as_reject_evidence():
    ordered = [_row(1, 9.0, False), _row(2, 8.0, False)]
    chosen, eligible, policy = core.choose_outer_candidate(
        ordered, outer_attempt=6,
    )
    assert chosen["rank"] == 1
    assert eligible == []
    assert policy == "resource_score_best_no_acceptance_eligible_v1"


def test_regression_outer5_xp_io_contains_eligible_rows_ignored_by_old_policy():
    decision = json.loads(OUTER5_XP_IO.read_text(encoding="utf-8"))
    ordered = sorted(
        decision["evaluations"],
        key=lambda row: (-row["real_resource_score"], row["rank"]),
    )
    historical, eligible, _ = core.choose_outer_candidate(
        ordered, outer_attempt=5,
    )
    successor, _, _ = core.choose_outer_candidate(ordered, outer_attempt=6)
    assert historical["rank"] == 1
    assert [row["rank"] for row in eligible] == [4, 5]
    assert successor["rank"] == 4

