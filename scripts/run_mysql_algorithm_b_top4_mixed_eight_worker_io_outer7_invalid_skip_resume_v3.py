#!/usr/bin/env python3
"""IO B Outer-7 continuation with the complete split decision-root chain."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_b_top4_mixed_eight_worker_boundary_resume_v2 as boundary_v2
from scripts import run_mysql_algorithm_b_xp_top4_mixed_eight_worker_v6 as runtime_v6


HISTORY = ROOT / "history/mysql_four_scene_20260809"
CONTRACT_ROOT = (
    ROOT
    / "config/formal/frozen_mysql_xp_top4_algorithm_b_mixed_eight_worker_20260827_v6"
)
SOURCE = HISTORY / "v554_to_b_io_mixed_eight_worker_v2_recovered_20260827_xp_io"
OUTER_1_ROOT = (
    HISTORY / "v505_to_b_io_mixed_four_worker_boundary_resume_v71_20260827_xp_io"
)
OUTER_2_TO_5_ROOT = (
    HISTORY / "v521_to_b_io_mixed_four_worker_boundary_resume_v73_20260827_xp_io"
)


def install_runtime():
    impl = boundary_v2.implementation
    impl["runtime8"] = runtime_v6
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
            1: OUTER_1_ROOT,
            **{outer: OUTER_2_TO_5_ROOT for outer in range(2, 6)},
        },
    }
    return impl["install"]("hybench_xp_io")


def main() -> int:
    return int(install_runtime()._implementation_main())


if __name__ == "__main__":
    raise SystemExit(main())
