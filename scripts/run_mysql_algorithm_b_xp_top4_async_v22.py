#!/usr/bin/env python3
"""CPU-B rank-13 continuation bound to the frozen 300-second drain runner."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_b_xp_top4_async_v16 as predecessor


CONTRACT_ROOT = (
    ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_20260826_v31"
)
HYBENCH_ADAPTIVE_RUNNER_V4 = (
    ROOT / "config/formal/frozen_mysql_async_outer_20260820_v38/runtime"
    / "hybench-adaptive-runner-v4.jar"
)


def main() -> int:
    predecessor.CONTRACT_ROOT = CONTRACT_ROOT
    predecessor._core()._impl["alg"].HYBENCH_ADAPTIVE_RUNNER_JAR = (
        HYBENCH_ADAPTIVE_RUNNER_V4
    )
    return predecessor.main()


if __name__ == "__main__":
    raise SystemExit(main())
