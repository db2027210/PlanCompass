from __future__ import annotations

import pytest

from scripts.mysql_cgroup_resource_readback_v2 import (
    _memory_limit_matches,
    _page_aligned_memory_limit,
)


def test_accepts_observed_xp_io_page_rounding() -> None:
    requested = 6_871_947_674
    actual = 6_871_945_216
    assert _page_aligned_memory_limit(requested, 4096) == actual
    assert _memory_limit_matches(actual, requested, page_size=4096)


def test_rejects_a_full_page_of_resource_drift() -> None:
    requested = 6_871_947_674
    actual = 6_871_945_216 - 4096
    assert not _memory_limit_matches(actual, requested, page_size=4096)


@pytest.mark.parametrize("requested,page_size", [(0, 4096), (1, 0), (-1, 4096)])
def test_rejects_invalid_alignment_inputs(requested: int, page_size: int) -> None:
    with pytest.raises(ValueError):
        _page_aligned_memory_limit(requested, page_size)

