from __future__ import annotations

import pytest

from formal_resource_sampler_20260721 import (
    ResourceError, combined_limited_io_utilization,
)


def test_io_is_combined_read_write_over_total_capability() -> None:
    assert combined_limited_io_utilization(80.0, 20.0, 100, 300) == pytest.approx(0.25)
    # The former max(read/read_cap, write/write_cap) rule would return 0.8.
    assert combined_limited_io_utilization(80.0, 20.0, 100, 300) != pytest.approx(0.8)


def test_nonpositive_total_io_capability_fails_closed() -> None:
    with pytest.raises(ResourceError, match="combined IO capability"):
        combined_limited_io_utilization(1.0, 1.0, 0, 0)
