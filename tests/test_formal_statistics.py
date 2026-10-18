import pytest

from formal_statistics import summarize_replays


def test_three_replay_summary_is_deterministic():
    left = summarize_replays([10.0, 11.0, 12.0], seed=20260721)
    right = summarize_replays([10.0, 11.0, 12.0], seed=20260721)
    assert left == right
    assert left.mean == 11.0
    assert left.sample_std == 1.0
    assert left.bootstrap_95_ci_lower <= left.mean <= left.bootstrap_95_ci_upper


def test_requires_exactly_three_replays():
    with pytest.raises(ValueError, match="exactly three"):
        summarize_replays([1.0, 2.0], seed=20260721)
