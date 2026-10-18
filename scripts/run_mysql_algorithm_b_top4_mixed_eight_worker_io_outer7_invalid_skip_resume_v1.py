#!/usr/bin/env python3
"""Resume IO B after committed Outer-7 with invalid-candidate skip v4."""

from pathlib import Path

from scripts import run_mysql_algorithm_b_top4_mixed_eight_worker_boundary_resume_v2 as boundary_v2
from scripts import run_mysql_algorithm_b_xp_top4_mixed_eight_worker_v4 as runtime_v4


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
CONTRACT_ROOT = (
    ROOT
    / "config/formal/frozen_mysql_xp_top4_algorithm_b_mixed_eight_worker_20260827_v4"
)
SOURCE = HISTORY / "v554_to_b_io_mixed_eight_worker_v2_recovered_20260827_xp_io"
EARLY_DECISIONS = (
    HISTORY / "v521_to_b_io_mixed_four_worker_boundary_resume_v73_20260827_xp_io"
)


def install_runtime():
    impl = boundary_v2.implementation
    impl["runtime8"] = runtime_v4
    impl["CONTRACT_ROOT"] = CONTRACT_ROOT
    impl["EXPECTED_ERROR"] = (
        "RuntimeError: Algorithm B outer 8 rank 2 failed closed"
    )
    impl["CONFIG"]["hybench_xp_io"] = {
        "source": SOURCE,
        "accepted": 2,
        "rejected": 5,
        "outer": 7,
        "decision_roots": {
            outer: EARLY_DECISIONS for outer in range(1, 6)
        },
    }
    return impl["install"]("hybench_xp_io")


def main() -> int:
    return int(install_runtime()._implementation_main())


if __name__ == "__main__":
    raise SystemExit(main())
